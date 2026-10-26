#!/usr/bin/env python3
"""Platform lane module tests through the core harness.

The ELF, PE and WebAssembly fixtures are assembled here byte by byte (an
aarch64 shared object with .dynsym/.dynstr/.dynamic, a PE32+ DLL with export
and import directories, a WebAssembly module with an import and exports), so
the readers are tested against containers whose contents the test states, on
any host. The iOS-simulator and Android modes need a host; their comparators
are tested over observations, and the runner's refusal to report without a
host is tested with a stand-in adb.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parity_harness as ph  # noqa: E402

platforms = ph.load_lane_module(HERE / "lanes" / "platforms.py")
RUNNER = HERE / "lanes" / "platform_runner.py"
SCAN = HERE / "corpus" / "fixtures" / "platform" / "scan.json"
HAVE_NODE = shutil.which("node") is not None


# ── fixture builders ────────────────────────────────────────────────────────


def strtab(names: list[str]) -> tuple[bytes, dict[str, int]]:
    blob, at = b"\0", {}
    for n in names:
        at[n] = len(blob)
        blob += n.encode() + b"\0"
    return blob, at


def elf_so(exports: list[str], needed: list[str], internal: list[str] = ()) -> bytes:
    """A little-endian ELF64 aarch64 ET_DYN image: the sections a dynamic
    loader and the reader look at, and nothing else."""
    dynstr, dat = strtab([*exports, *internal, *needed])
    syms = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)
    for n in exports:
        syms += struct.pack("<IBBHQQ", dat[n], 0x12, 0, 1, 0x1000, 4)  # GLOBAL FUNC, defined
    for n in internal:
        syms += struct.pack("<IBBHQQ", dat[n], 0x02, 2, 1, 0x1000, 4)  # LOCAL, hidden
    dynamic = b"".join(struct.pack("<qQ", 1, dat[n]) for n in needed) + struct.pack("<qQ", 0, 0)
    shstr, sat = strtab([".dynstr", ".dynsym", ".dynamic", ".shstrtab"])
    body, offsets = b"", {}
    for name, blob in ((".dynstr", dynstr), (".dynsym", syms), (".dynamic", dynamic), (".shstrtab", shstr)):
        offsets[name] = 64 + len(body)
        body += blob + b"\0" * (-len(blob) % 8)
    shoff = 64 + len(body)
    header = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    header += struct.pack("<HHIQQQIHHHHHH", 3, 183, 1, 0, 0, shoff, 0, 64, 0, 0, 64, 5, 4)
    sec = struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    for name, typ, size, link, entsize in ((".dynstr", 3, len(dynstr), 0, 0), (".dynsym", 11, len(syms), 1, 24),
                                            (".dynamic", 6, len(dynamic), 1, 16), (".shstrtab", 3, len(shstr), 0, 0)):
        sec += struct.pack("<IIQQQQIIQQ", sat[name], typ, 0, 0, offsets[name], size, link, 0, 8, entsize)
    return header + body + sec


def pe_dll(machine: int, exports: list[str], imports: list[str], dll: bool = True) -> bytes:
    """A PE32+ image with one section holding the export and import tables."""
    va = 0x1000
    blob = bytearray()

    def put(data: bytes) -> int:
        at = len(blob)
        blob.extend(data + b"\0" * (-len(data) % 4))
        return va + at

    name_rvas = [put(n.encode() + b"\0") for n in exports]
    names_table = put(b"".join(struct.pack("<I", r) for r in name_rvas))
    module = put(b"fixture.dll\0")
    export_dir = put(struct.pack("<IIHHIIIIIII", 0, 0, 0, 0, module, 1, 0, len(exports), 0, names_table, 0))
    dll_rvas = [put(n.encode() + b"\0") for n in imports]
    import_dir = put(b"".join(struct.pack("<IIIII", 0, 0, 0, r, 0) for r in dll_rvas) + b"\0" * 20)
    dirs = [(export_dir, 40), (import_dir, 20 * (len(imports) + 1))] + [(0, 0)] * 14
    optional = struct.pack("<H", 0x20B) + b"\0" * 66 + struct.pack("<HH", 3, 0x140) + b"\0" * 36
    optional += struct.pack("<I", 16) + b"".join(struct.pack("<II", *d) for d in dirs)
    coff = struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, len(optional), 0x2022 if dll else 0x0022)
    section = b".rdata\0\0" + struct.pack("<IIIIIIHHI", len(blob), va, len(blob), 0x200, 0, 0, 0, 0, 0x40000040)
    headers = b"MZ" + b"\0" * 58 + struct.pack("<I", 0x40) + b"PE\0\0" + coff + optional + section
    return headers + b"\0" * (0x200 - len(headers)) + bytes(blob)


def uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def wasm_module(exports: list[str], import_module: str = "env") -> bytes:
    def name(s: str) -> bytes:
        return uleb(len(s)) + s.encode()

    def section(sid: int, body: bytes) -> bytes:
        return bytes([sid]) + uleb(len(body)) + body

    types = section(1, uleb(1) + b"\x60\x00\x01\x7f")
    imports = section(2, uleb(1) + name(import_module) + name("host") + b"\x00" + uleb(0))
    funcs = section(3, uleb(1) + uleb(0))
    memory = section(5, uleb(1) + b"\x00" + uleb(1))
    exp = section(7, uleb(len(exports) + 1) + name("memory") + b"\x02" + uleb(0)
                  + b"".join(name(e) + b"\x00" + uleb(1) for e in exports))
    body = b"\x00\x41\x00\x0b"
    code = section(10, uleb(1) + uleb(len(body)) + body)
    return b"\0asm\x01\0\0\0" + types + imports + funcs + memory + exp + code


NODE_CHECK = """import { readFileSync } from "node:fs";
const m = new WebAssembly.Module(readFileSync(process.argv[2]));
const names = WebAssembly.Module.exports(m).map(e => e.name);
if (names.includes("clap_entry")) { console.log("PASS: clap_entry exported"); }
else { console.log("FAIL: missing exports: clap_entry"); process.exit(1); }
"""


def git_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "fixture"], check=True)
    return repo


def harness_fixture(root: Path, cases: list[dict]) -> tuple[Path, Path, dict[str, Path]]:
    impl = json.loads((HERE / "implementations" / "pulp-platform.json").read_text(encoding="utf-8"))
    impl["tools"] = {"runner": str(RUNNER), "host-runner": str(HERE / "lanes" / "host_runner.py")}
    impl_path = root / "impl.json"
    impl_path.write_text(json.dumps(impl), encoding="utf-8")
    corpus_dir = root / "corpus"
    (corpus_dir / "fixtures" / "platform").mkdir(parents=True)
    shutil.copy2(SCAN, corpus_dir / "fixtures" / "platform")
    base = {"source": "fixtures/platform/scan.json", "source_base": "corpus", "width": 1, "height": 1,
            "source_kind": "platform"}
    corpus_path = corpus_dir / "corpus.json"
    corpus_path.write_text(json.dumps({"schema": ph.CORPUS_SCHEMA, "cases": [dict(base, **c) for c in cases]}),
                           encoding="utf-8")
    other = root / "other"
    other.mkdir()
    (other / "readme.txt").write_text("identity root\n", encoding="utf-8")
    return corpus_path, impl_path, {"checkout": git_repo(root), "artifact": root / "artifact", "other": other}


def static_case(cid: str, lane: str, sub: str, expect: dict, controls: dict) -> dict:
    return {"case_id": cid, "lanes": [lane], "expect": expect, "controls": controls,
            "args": {"mode": ["static"], "artifact": ["--artifact", "{root:artifact}/" + sub]}}


LINUX_EXPECT = {"machine": "aarch64", "required": ["Gain.clap", "Gain.build-info.json"],
                "images": {"Gain.clap": {"type": "dyn", "require_exports": ["clap_entry"],
                                         "require_linked": ["libc.so.6"], "forbid_linked": ["vulkan"],
                                         "forbid_families": ["skia", "wgpu"], "require_families": ["pulp-format"]}}}
LINUX_CONTROLS = {"machine": "elf-machine", "rename_export": "elf-rename-export:clap_entry=clap_entrz",
                  "rename_needed": "elf-rename-needed:libstdc++.so.6=libvulkan.so.1",
                  "drop": "drop:Gain.build-info.json"}
WINDOWS_EXPECT = {"machine": "x86_64", "required": ["wgpu_native.dll"],
                  "images": {"wgpu_native.dll": {"dll": True, "require_exports": ["wgpuCreateInstance"],
                                                 "require_linked": ["KERNEL32.dll"],
                                                 "forbid_linked": [r"^vcruntime\d+d\.dll$"],
                                                 "require_families": ["wgpu"]}}}
WINDOWS_CONTROLS = {"machine": "pe-machine",
                    "rename_export": "pe-rename-export:wgpuCreateInstance=wgpuCreateInstancz",
                    "rename_import": "pe-rename-import:KERNEL32.dll=KERNEL33.dll"}


class PlatformReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def observe(self, artifact: Path, *faults: str) -> dict:
        out = self.root / "obs.json"
        argv = [sys.executable, str(RUNNER), "static", "--artifact", str(artifact), "--scan", str(SCAN),
                "--out", str(out), "--work", str(self.root / "work")]
        for f in faults:
            argv += ["--fault", f]
        subprocess.run(argv, check=True, capture_output=True)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_elf_reader_sees_machine_needed_exports_and_families(self):
        so = self.root / "Gain.clap"
        so.write_bytes(elf_so(["clap_entry"], ["libc.so.6"], ["_ZN4pulp6format5entryEv"]))
        img = self.observe(so)["images"]["Gain.clap"]
        self.assertEqual((img["machine"], img["type"], img["class"]), ("aarch64", "dyn", 64))
        self.assertEqual(img["needed"], ["libc.so.6"])
        self.assertEqual(img["exports"], ["clap_entry"])  # the hidden local symbol is not an export
        self.assertEqual(img["families"]["pulp-format"], 1)

    def test_pe_reader_sees_machine_exports_imports(self):
        dll = self.root / "x.dll"
        dll.write_bytes(pe_dll(0xAA64, ["wgpuCreateInstance", "wgpuGetProcAddress"], ["KERNEL32.dll", "ntdll.dll"]))
        img = self.observe(dll)["images"]["x.dll"]
        self.assertEqual((img["machine"], img["dll"], img["subsystem"]), ("arm64", True, "windows-cui"))
        self.assertEqual(img["exports"], ["wgpuCreateInstance", "wgpuGetProcAddress"])
        self.assertEqual(img["imports"], ["KERNEL32.dll", "ntdll.dll"])
        self.assertEqual(img["families"]["wgpu"], 2)

    def test_wasm_reader_sees_imports_and_exports(self):
        mod = self.root / "m.wasm"
        mod.write_bytes(wasm_module(["clap_entry"], "wasi_snapshot_preview1"))
        img = self.observe(mod)["images"]["m.wasm"]
        self.assertEqual(img["import_modules"], ["wasi_snapshot_preview1"])
        self.assertEqual(img["exports"], ["func:clap_entry", "memory:memory"])

    def test_faults_change_the_bytes_they_name_and_nothing_else(self):
        so = self.root / "Gain.clap"
        so.write_bytes(elf_so(["clap_entry"], ["libstdc++.so.6"]))
        img = self.observe(so, "elf-rename-export:clap_entry=clap_entrz",
                           "elf-rename-needed:libstdc++.so.6=libvulkan.so.1")["images"]["Gain.clap"]
        self.assertEqual((img["exports"], img["needed"]), (["clap_entrz"], ["libvulkan.so.1"]))
        self.assertEqual(so.read_bytes(), elf_so(["clap_entry"], ["libstdc++.so.6"]))  # the original is untouched

    def test_a_rename_that_finds_nothing_refuses(self):
        so = self.root / "Gain.clap"
        so.write_bytes(elf_so(["clap_entry"], []))
        res = subprocess.run([sys.executable, str(RUNNER), "static", "--artifact", str(so), "--scan", str(SCAN),
                              "--out", str(self.root / "o.json"), "--work", str(self.root / "w"),
                              "--fault", "elf-rename-export:GetPluginFactory=GetPluginFactorz"],
                             capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not found", res.stderr)

    def test_android_mode_without_a_device_is_skipped_not_passed(self):
        apk = self.root / "app.apk"
        apk.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        adb = self.root / "adb"
        adb.write_text("#!/bin/sh\necho unknown\n", encoding="utf-8")
        adb.chmod(0o755)
        res = subprocess.run([sys.executable, str(RUNNER), "android", "--artifact", str(apk), "--scan", str(SCAN),
                              "--out", str(self.root / "o.json"), "--device", "emulator-0", "--adb", str(adb),
                              "--activity", "a/b"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 77, res.stderr)
        self.assertFalse((self.root / "o.json").exists())


class PlatformComparatorTest(unittest.TestCase):
    def test_android_jni_export_missing_is_named(self):
        case = {"expect": {"jni_exports": {"lib/arm64-v8a/libpulp.so": ["JNI_OnLoad", "Java_a_B_c"]},
                           "markers": ["ready"]}}
        obs = {"jni_exports": {"lib/arm64-v8a/libpulp.so": ["JNI_OnLoad"]}, "install": {"exit": 0},
               "start": {"ok": True}, "alive_after_settle": True, "fatal": [], "tag_lines": ["I/Pulp: ready"]}
        reasons = platforms.android_side(case, obs, "candidate")
        self.assertEqual(len(reasons), 1)
        self.assertIn("does not export JNI symbol Java_a_B_c", reasons[0])
        obs["jni_exports"]["lib/arm64-v8a/libpulp.so"].append("Java_a_B_c")
        self.assertEqual(platforms.android_side(case, obs, "candidate"), [])

    def test_android_crash_after_markers_still_fails(self):
        case = {"expect": {"markers": ["ready"]}}
        obs = {"install": {"exit": 0}, "start": {"ok": True}, "alive_after_settle": False,
               "fatal": ["E/AndroidRuntime: FATAL EXCEPTION: main"], "tag_lines": ["I/Pulp: ready"]}
        reasons = platforms.android_side(case, obs, "candidate")
        self.assertTrue(any("not alive" in r for r in reasons) and any("fatal exception" in r for r in reasons))

    def test_ios_install_that_succeeds_with_a_broken_seal_still_fails(self):
        # The simulator installs and registers bundles whose seal does not
        # verify; a device does not. The lane must not read the install as proof.
        case = {"expect": {"extensions": ["X.appex"], "extension_point": "com.apple.AudioUnit-UI"}}
        obs = {"app": {"codesign": {"exit": 1, "stderr_tail": "a sealed resource is missing or invalid"}},
               "extensions": {"X.appex": {"codesign": {"exit": 0}, "extension_point": "com.apple.AudioUnit-UI"}},
               "install": {"exit": 0}, "installed_app_plugins": ["X.appex"], "pluginkit": {"X.appex": {"listed": True}}}
        reasons = platforms.ios_side(case, obs, "candidate")
        self.assertEqual(len(reasons), 1)
        self.assertIn("codesign (host app)", reasons[0])

    def test_ios_extension_whose_seal_does_not_verify_fails(self):
        case = {"expect": {"extensions": ["X.appex"], "extension_point": "com.apple.AudioUnit-UI"}}
        obs = {"app": {"codesign": {"exit": 0}},
               "extensions": {"X.appex": {"codesign": {"exit": 1, "stderr_tail": "code object is not signed at all"},
                                          "extension_point": "com.apple.AudioUnit-UI"}},
               "install": {"exit": 0}, "installed_app_plugins": ["X.appex"], "pluginkit": {"X.appex": {"listed": True}}}
        reasons = platforms.ios_side(case, obs, "candidate")
        self.assertEqual(len(reasons), 1)
        self.assertIn("codesign (X.appex) verification exited 1", reasons[0])

    def test_a_static_artifact_with_no_image_of_the_lane_kind_is_vacuous(self):
        # The other platform's image must not stand in for the lane's own.
        for kind, other in (("elf", {"Gain.dll": {"kind": "pe"}}), ("pe", {"Gain.so": {"kind": "elf"}})):
            with self.subTest(kind=kind):
                obs = {"images": other, "files": []}
                reasons = platforms.image_side({"expect": {}}, obs, "candidate", kind)
                self.assertEqual(reasons, [f"candidate: no {kind.upper()} image in the artifact (vacuous)"])


class PlatformCalibrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "artifact").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def calibrate(self, cases: list[dict]) -> dict:
        corpus, impl, roots = harness_fixture(self.root, cases)
        return ph.calibrate(corpus, self.root, impl, self.root / "out", roots=roots)

    def lane_controls(self, report: dict, prefix: str) -> list[dict]:
        return [c for c in report["controls"] if c["control"].startswith(prefix)]

    def write_linux(self, exports: list[str]) -> None:
        d = self.root / "artifact" / "linux"
        d.mkdir()
        (d / "Gain.clap").write_bytes(elf_so(exports, ["libstdc++.so.6", "libc.so.6"], ["_ZN4pulp6format5entryEv"]))
        (d / "Gain.build-info.json").write_text("{}", encoding="utf-8")

    def test_linux_calibrates_and_every_control_rejects(self):
        self.write_linux(["clap_entry"])
        report = self.calibrate([static_case("linux-clap", "linux-elf-static", "linux", LINUX_EXPECT, LINUX_CONTROLS)])
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"]["reasons"])
        lane = self.lane_controls(report, "linux-elf-")
        self.assertEqual(len(lane), 4)
        self.assertTrue(all(c["outcome"] == "REJECTED as required" for c in lane), lane)
        self.assertTrue(report["calibrated"])

    def test_linux_without_the_entry_export_fails_the_positive(self):
        # Negative control for the lane itself: the same corpus over an image
        # that does not export clap_entry must not calibrate.
        self.write_linux(["clap_init"])
        report = self.calibrate([static_case("linux-clap", "linux-elf-static", "linux", LINUX_EXPECT, LINUX_CONTROLS)])
        self.assertFalse(report["positive"]["verdict"]["accepting"])
        self.assertFalse(report["calibrated"])

    def test_a_control_whose_fault_cannot_apply_is_not_counted(self):
        self.write_linux(["clap_entry"])
        controls = dict(LINUX_CONTROLS, rename_export="elf-rename-export:GetPluginFactory=GetPluginFactorz")
        report = self.calibrate([static_case("linux-clap", "linux-elf-static", "linux", LINUX_EXPECT, controls)])
        outcome = next(c for c in report["controls"] if c["control"] == "linux-elf-entry-export-missing")["outcome"]
        self.assertEqual(outcome, "CONTROL DID NOT FAIL")
        self.assertFalse(report["calibrated"])

    def test_windows_calibrates_and_every_control_rejects(self):
        d = self.root / "artifact" / "win"
        d.mkdir()
        (d / "wgpu_native.dll").write_bytes(pe_dll(0x8664, ["wgpuCreateInstance"], ["KERNEL32.dll", "ntdll.dll"]))
        report = self.calibrate([static_case("win", "windows-sdk-pe", "win", WINDOWS_EXPECT, WINDOWS_CONTROLS)])
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"]["reasons"])
        lane = self.lane_controls(report, "windows-pe-")
        self.assertEqual(len(lane), 3)
        self.assertTrue(all(c["outcome"] == "REJECTED as required" for c in lane), lane)
        self.assertTrue(report["calibrated"])

    @unittest.skipUnless(HAVE_NODE, "needs node")
    def test_web_calibrates_and_the_node_check_rejects_the_broken_module(self):
        d = self.root / "artifact" / "web"
        d.mkdir()
        (d / "m.wasm").write_bytes(wasm_module(["clap_entry"]))
        check = self.root / "check.mjs"
        check.write_text(NODE_CHECK, encoding="utf-8")
        case = {"case_id": "web", "lanes": ["web-wam-wclap"],
                "args": {"mode": ["node"], "artifact": ["--artifact", "{root:artifact}/web"],
                         "host": ["--check", "m.wasm@probe=" + str(check) + "\x1f@WASM@"]},
                "expect": {"images": {"m.wasm": {"require_exports": ["func:clap_entry"],
                                                 "allowed_import_modules": ["env"]}},
                           "node_checks": ["m.wasm@probe"]},
                "controls": {"rename_export": "wasm-rename-export:clap_entry=clap_entrz"}}
        report = self.calibrate([case])
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"]["reasons"])
        lane = self.lane_controls(report, "web-")
        self.assertEqual(len(lane), 2)
        self.assertTrue(all(c["outcome"] == "REJECTED as required" for c in lane), lane)
        self.assertTrue(report["calibrated"])


if __name__ == "__main__":
    unittest.main()
