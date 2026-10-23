#!/usr/bin/env python3
"""Host lane module tests through the core harness, with fake tools.

The fake probe, validator and standalone apps behave like the real ones where
the host lane's controls depend on it: they honour the editor and GPU kill
switches and a null parent, refuse a truncated bundle, fail validation of
one, and report the loaded-image census the way dyld does (the first copy of
a shared runtime install name wins). The fake plug-ins are real Mach-O
dylibs that link a runtime by install name, so the runtime-binding
observation is measured, not stubbed. A probe that ignores one control's
input must make calibration NOT CALIBRATED.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parity_harness as ph  # noqa: E402

ph.load_lane_module(HERE / "lanes" / "host.py")
RUNNER = HERE / "lanes" / "host_runner.py"
HAVE_TOOLCHAIN = sys.platform == "darwin" and all(shutil.which(t) for t in ("clang", "otool", "dyld_info", "codesign"))

FAKE_PROBE = r'''#!/usr/bin/env python3
import hashlib, json, os, struct, sys, zlib
from pathlib import Path
IGNORE = set(os.environ.get("FAKE_IGNORE", "").split(","))
args = sys.argv[1:]
plugins, out, blocks, null_parent = [], None, 94, False
i = 0
while i < len(args):
    a = args[i]
    if a == "--plugin": plugins.append(args[i + 1]); i += 2
    elif a == "--out": out = Path(args[i + 1]); i += 2
    elif a == "--blocks": blocks = int(args[i + 1]); i += 2
    elif a == "--fault-null-parent": null_parent = True; i += 1
    elif a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"): i += 2
    else: i += 1
if not plugins or out is None: sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
def png(path, w, h, seed):
    raw = b"".join(b"\x00" + b"".join(bytes(((x * 7 + y * 3 + seed) % 200 + 20, (x + seed) % 250, (y * 5) % 250)) for x in range(w)) for y in range(h))
    def chunk(k, d): return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
editor_off = os.environ.get("PULP_DISABLE_PLUGIN_EDITOR") == "1" and "editor-env" not in IGNORE
gpu_off = os.environ.get("PULP_DISABLE_PLUGIN_GPU") == "1" and "gpu-env" not in IGNORE
null_parent = null_parent and "null-parent" not in IGNORE
doc = {"schema": "pulp.host-lane.observation.v1",
       "settings": {"blocks": blocks, "block_size": 512, "sample_rate": 48000, "opens": 2, "watch_ms": 1,
                    "resize_by": [120, 80], "fault_null_parent": null_parent},
       "plugins": [], "events": [], "dyld_images": []}
runtime_loaded = False
for idx, p in enumerate(plugins):
    b = Path(p)
    exe = b / "Contents" / "MacOS" / b.stem
    if not exe.is_file() or exe.stat().st_size < 4096: sys.stderr.write(f"load {p} failed\n"); sys.exit(2)
    doc["dyld_images"].append(str(exe))
    rt = b / "Contents" / "MacOS" / "libwgpu_native.dylib"
    if rt.is_file() and not runtime_loaded:
        doc["dyld_images"].append(str(rt)); runtime_loaded = True
    audio = {}
    for tag in ("initial", "closed", "open", "reclosed"):
        data = hashlib.sha256((b.stem + str(blocks)).encode()).digest() * (blocks * 4)
        f = out / f"audio-{idx}-{tag}.f32"; f.write_bytes(data)
        audio[tag] = {"sha256": hashlib.sha256(data).hexdigest(), "frames": blocks * 512, "channels": 2,
                      "peak": 0.5, "rms": 0.1, "finite": True, "file": str(f)}
    doc["plugins"].append({"index": idx, "path": str(b), "format": b.suffix[1:].upper(), "name": b.stem,
                           "has_editor": True, "latency_samples": 0, "parameters": 4, "audio": audio})
seed = {p: sum(map(ord, Path(p).stem)) for p in plugins}
attached = not editor_off and not null_parent
for n in range(2):
    for idx, p in enumerate(plugins):
        if attached: sys.stderr.write(f"[pulp:info]  [plugin-gpu-host] adapter mode=custom use_gpu={'false' if gpu_off else 'true'} x\n")
        doc["events"].append({"plugin": idx, "open": n, "event": "create", "attached": attached,
                              **({"width": 64, "height": 48, "resizable": True} if attached else {})})
    for idx, p in enumerate(plugins):
        f = out / f"editor-{idx}-open{n}.png"
        if not attached:
            doc["events"].append({"plugin": idx, "open": n, "event": "frame", "frame": {"read": False}}); continue
        png(f, 64, 48, seed[p] + (7 if gpu_off else 0))
        doc["events"].append({"plugin": idx, "open": n, "event": "frame",
                              "frame": {"read": True, "width": 64, "height": 48, "png": str(f)}})
    if n == 0 and attached:
        for idx, p in enumerate(plugins):
            doc["events"].append({"plugin": idx, "event": "resize", "requested": [184, 128], "accepted": False,
                                  "size": [64, 48]})
    for idx, p in enumerate(plugins):
        doc["events"].append({"plugin": idx, "open": n, "event": "close", "had_editor": attached})
(out / "host-observation.json").write_text(json.dumps(doc))
'''

FAKE_VALIDATOR = r'''#!/usr/bin/env python3
import sys
from pathlib import Path
b = Path(sys.argv[sys.argv.index("--validate") + 1])
exe = b / "Contents" / "MacOS" / b.stem
ok = exe.is_file() and exe.stat().st_size >= 4096
print("checking", b)
print("SUCCESS" if ok else "FAILED")
sys.exit(0 if ok else 1)
'''

FAKE_APP = r'''#!/usr/bin/env python3
import os, struct, zlib
from pathlib import Path
seed = SEED
w, h = 64, 48
raw = b"".join(b"\x00" + b"".join(bytes(((x * 5 + y + seed) % 230 + 10, (x * 9) % 250, (y * 11 + seed) % 250)) for x in range(w)) for y in range(h))
def chunk(k, d): return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
Path(os.environ["PULP_SCREENSHOT"]).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
'''


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def clang(source: str, out: Path, *flags: str) -> None:
    src = out.with_suffix(".c")
    src.write_text(source, encoding="utf-8")
    subprocess.run(["clang", "-arch", "arm64", *flags, "-o", str(out), str(src)], check=True)
    src.unlink()


def fixture(root: Path) -> tuple[Path, Path, dict[str, str], dict[str, str]]:
    repo = root / "checkout"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README").write_text("fake\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
                   check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    build = root / "build"
    runtime = build / "libwgpu_native.dylib"
    build.mkdir()
    clang("int rt_marker(void){return 24;}\n", runtime, "-dynamiclib", "-install_name", "@rpath/libwgpu_native.dylib")
    skew = root / "skew" / "libwgpu_native.dylib"
    skew.parent.mkdir()
    clang("int rt_marker(void){return 22;}\n", skew, "-dynamiclib", "-install_name", "@rpath/libwgpu_native.dylib")
    executable(root / "probe", FAKE_PROBE)
    executable(root / "validator", FAKE_VALIDATOR)
    roots = {"checkout": str(repo), "skew-runtime": str(skew)}
    tools = {"runner": str(RUNNER), "probe": str(root / "probe"), "pluginval": str(root / "validator")}
    for name in ("alpha.clap", "beta.clap", "alpha.vst3"):
        b = build / name
        macos = b / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        shutil.copy2(runtime, macos / "libwgpu_native.dylib")
        exe = macos / b.stem
        clang("int plugin_entry(void){return 1;}\n" + "static const char pad[8192] = {1};\n"
              "const char* plugin_pad(void){return pad;}\n", exe, "-dynamiclib",
              "-Wl,-rpath,@loader_path", str(runtime))
        (b / "Contents" / "pulp-build-info.json").write_text(json.dumps(
            {"product": {"source_git_sha": head, "source_git_dirty": False}, "build": {"type": "Release"}}),
            encoding="utf-8")
        roots[name] = str(b)
        tools[f"exe.{name}"] = str(exe)
    for name, seed in (("alpha.app", 3), ("beta.app", 90)):
        app = build / name
        executable(app / "Contents" / "MacOS" / app.stem, FAKE_APP.replace("SEED", str(seed)))
        roots[name] = str(app)
        tools[f"exe.{name}"] = str(app / "Contents" / "MacOS" / app.stem)
    all_roots = ["{root:%s}" % n for n in ("alpha.clap", "beta.clap", "alpha.vst3", "alpha.app", "beta.app")]
    impl = {
        "schema": ph.IMPLEMENTATION_SCHEMA, "name": "fake-host", "repository": "example/fake", "source_sha": None, "authority": "native-render",
        "tools": tools,
        "probes": [{"name": "build", "argv": ["{tool:runner}", "identity", "build", "{root:checkout}", *all_roots]},
                   {"name": "provider", "argv": ["{tool:runner}", "identity", "provider", *all_roots]},
                   {"name": "fonts", "argv": ["{tool:runner}", "identity", "fonts", *all_roots]}],
        "source_kinds": {"host-probe": [], "host-pair": [], "host-validator": [], "host-standalone": []},
        "steps": json.loads((HERE / "implementations" / "pulp-host-in-tree.json").read_text(encoding="utf-8"))["steps"],
    }
    impl_path = root / "impl.json"
    impl_path.write_text(json.dumps(impl), encoding="utf-8")
    corpus_dir = root / "corpus"
    (corpus_dir / "fixtures" / "host").mkdir(parents=True)
    shutil.copy2(HERE / "corpus" / "fixtures" / "host" / "stimulus.json", corpus_dir / "fixtures" / "host")
    base = {"source": "fixtures/host/stimulus.json", "source_base": "corpus", "width": 64, "height": 48}

    def P(name: str) -> list[str]:
        return ["--plugin", "{root:%s}" % name]
    corpus = {"schema": ph.CORPUS_SCHEMA, "cases": [
        dict(base, case_id="alpha-clap", source_kind="host-probe", lanes=["host-load", "host-editor", "host-audio"],
             args={"plugins": P("alpha.clap")}, controls={"swap": "beta.clap"}, expect={"editor": True, "gpu": True}),
        dict(base, case_id="pair", source_kind="host-pair",
             lanes=["host-load", "host-editor", "host-audio", "host-coexist"],
             args={"plugins": P("alpha.clap") + P("beta.clap"), "solo0": P("alpha.clap"), "solo1": P("beta.clap")},
             controls={"skew_runtime_root": "skew-runtime"}, expect={"editor": True, "gpu": True}),
        dict(base, case_id="alpha-pluginval", source_kind="host-validator", lanes=["host-validator"],
             args={"bundle": ["--bundle", "{root:alpha.vst3}"]}),
        dict(base, case_id="alpha-standalone", source_kind="host-standalone", lanes=["host-standalone"],
             args={"app": ["--app", "{root:alpha.app}"]}, controls={"swap": "beta.app"}),
    ]}
    corpus_path = corpus_dir / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    return corpus_path, impl_path, roots, tools


@unittest.skipUnless(HAVE_TOOLCHAIN, "needs macOS with clang, otool, dyld_info and codesign")
class HostLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ.pop("FAKE_IGNORE", None)

    def tearDown(self) -> None:
        os.environ.pop("FAKE_IGNORE", None)
        self.tmp.cleanup()

    def calibrate(self):
        corpus, impl, roots, _ = fixture(self.root)
        return ph.calibrate(corpus, self.root, impl, self.root / "out", roots={k: Path(v) for k, v in roots.items()})

    def outcome(self, report, name):
        return next(c for c in report["controls"] if c["control"] == name)["outcome"]

    def test_faithful_fakes_calibrate(self):
        report = self.calibrate()
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"]["reasons"])
        self.assertEqual(report["control_failures"], [])
        self.assertTrue(report["calibrated"])
        host = [c for c in report["controls"] if c["control"].startswith("host-")]
        self.assertGreaterEqual(len(host), 20)
        self.assertTrue(all(c["outcome"] == "REJECTED as required" for c in host))

    def test_probe_ignoring_the_editor_kill_switch_is_not_calibrated(self):
        os.environ["FAKE_IGNORE"] = "editor-env"
        report = self.calibrate()
        self.assertEqual(self.outcome(report, "host-editor-disabled-by-plugin-env"), "CONTROL DID NOT FAIL")
        self.assertFalse(report["calibrated"])

    def test_probe_ignoring_a_null_parent_is_not_calibrated(self):
        os.environ["FAKE_IGNORE"] = "null-parent"
        report = self.calibrate()
        self.assertEqual(self.outcome(report, "host-editor-attach-null-parent"), "CONTROL DID NOT FAIL")
        self.assertFalse(report["calibrated"])

    def test_silent_cpu_fallback_is_not_calibrated(self):
        os.environ["FAKE_IGNORE"] = "gpu-env"
        report = self.calibrate()
        self.assertEqual(self.outcome(report, "host-gpu-host-silently-disabled"), "CONTROL DID NOT FAIL")
        self.assertFalse(report["calibrated"])

    def test_runtime_skew_is_measured_not_stubbed(self):
        report = self.calibrate()
        control = next(c for c in report["controls"] if c["control"] == "host-coexist-runtime-version-skew")
        reasons = control["lane_evidence"]["pair/host-coexist"]["reasons"]
        self.assertTrue(any("runs on a copy with different bytes" in r for r in reasons), reasons)



class StandaloneBackingScaleTest(unittest.TestCase):
    """compare_standalone accepts a frame at a whole multiple of the case size
    (the display's backing scale) and nothing else."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sys.path.insert(0, str(HERE.parents[1] / "cli"))
        from vellum_png import RgbaImage, encode_png
        self.image, self.encode = RgbaImage, encode_png
        self.host = ph.load_lane_module(HERE / "lanes" / "host.py")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def frame(self, side: str, width: int, height: int) -> Path:
        # Sixteen colours, so the frame is not blank.
        px = bytearray()
        for y in range(height):
            for x in range(width):
                px += bytes(((x * 16) % 256, (y * 16) % 256, ((x + y) * 8) % 256, 255))
        d = self.root / side
        d.mkdir(exist_ok=True)
        (d / "standalone.png").write_bytes(self.encode(self.image(width, height, bytes(px))))
        return d

    def compare(self, width: int, height: int) -> dict:
        case = {"width": 40, "height": 30}
        return self.host.compare_standalone(self.frame("reference", width, height),
                                            self.frame("candidate", width, height), case)

    def test_a_2x_backing_scale_is_accepted_and_recorded(self):
        result = self.compare(80, 60)
        self.assertEqual(result["status"], "pass", result.get("reasons"))
        self.assertEqual(result["backing_scale"], {"reference": 2, "candidate": 2})

    def test_a_non_integer_multiple_is_rejected(self):
        result = self.compare(60, 45)  # 1.5x
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("not a whole multiple" in r for r in result["reasons"]), result["reasons"])

    def test_an_aspect_mismatch_is_rejected(self):
        result = self.compare(80, 30)  # 2x wide, 1x tall
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("not a whole multiple" in r for r in result["reasons"]), result["reasons"])

if __name__ == "__main__":
    unittest.main()
