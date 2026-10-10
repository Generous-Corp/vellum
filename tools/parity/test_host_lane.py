#!/usr/bin/env python3
"""Host lane tests against a fake probe, validator and standalone app.

The fakes behave like the real tools where the lane's controls depend on it
(they honour the editor/GPU kill switches, refuse a truncated bundle, and fail
validation of one). Variants that ignore one of those inputs must make
calibration report NOT CALIBRATED: a control that cannot bite proves nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import host_lane  # noqa: E402

SHA = "a" * 40

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
    elif a in ("--block-size", "--sample-rate", "--opens", "--watch-ms", "--resize-by"): i += 2
    else: i += 1
if not plugins or out is None: sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
def png(path, w, h, seed):
    raw = b"".join(b"\x00" + b"".join(bytes(((x * 7 + y * 3 + seed) % 200 + 20, (x + seed) % 250, (y * 5) % 250)) for x in range(w)) for y in range(h))
    def chunk(k, d): return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
editor_off = os.environ.get("PULP_DISABLE_PLUGIN_EDITOR") == "1" and "editor-env" not in IGNORE
gpu_off = os.environ.get("PULP_DISABLE_PLUGIN_GPU") == "1" and "gpu-env" not in IGNORE
null_parent = null_parent and "null-parent" not in IGNORE
doc = {"schema": "pulp.host-lane.observation.v1",
       "settings": {"blocks": blocks, "block_size": 512, "sample_rate": 48000, "opens": 2, "watch_ms": 1,
                    "resize_by": [120, 80], "fault_null_parent": null_parent},
       "plugins": [], "events": [], "dyld_images": []}
for idx, p in enumerate(plugins):
    b = Path(p)
    exe = b / "Contents" / "MacOS" / b.stem
    if not exe.is_file() or exe.stat().st_size < 4096: sys.stderr.write(f"load {p} failed\n"); sys.exit(2)
    doc["dyld_images"].append(str(exe))
    if idx == 0: doc["dyld_images"].append(str(b / "Contents" / "MacOS" / "libruntime.dylib"))
    audio = {}
    for tag in ("initial", "closed", "open", "reclosed"):
        data = hashlib.sha256((b.stem + str(blocks)).encode()).digest() * (blocks * 4)
        f = out / f"audio-{idx}-{tag}.f32"; f.write_bytes(data)
        audio[tag] = {"sha256": hashlib.sha256(data).hexdigest(), "frames": blocks * 512, "channels": 2,
                      "peak": 0.5, "rms": 0.1, "finite": True, "file": str(f)}
    doc["plugins"].append({"index": idx, "path": str(b), "format": b.suffix[1:].upper(), "name": b.stem,
                           "has_editor": True, "latency_samples": 0, "parameters": 4, "audio": audio})
seed = {p: sum(map(ord, Path(p).stem)) for p in plugins}
for n in range(2):
    for idx, p in enumerate(plugins):
        attached = not editor_off and not null_parent
        if attached: sys.stderr.write(f"[pulp:info]  [plugin-gpu-host] adapter mode=custom use_gpu={'false' if gpu_off else 'true'} x\n")
        doc["events"].append({"plugin": idx, "open": n, "event": "create", "attached": attached,
                              **({"width": 64, "height": 48, "resizable": True} if attached else {})})
    for idx, p in enumerate(plugins):
        f = out / f"editor-{idx}-open{n}.png"
        if editor_off or null_parent:
            doc["events"].append({"plugin": idx, "open": n, "event": "frame", "frame": {"read": False}}); continue
        png(f, 64, 48, seed[p] + (7 if gpu_off else 0))
        doc["events"].append({"plugin": idx, "open": n, "event": "frame",
                              "frame": {"read": True, "width": 64, "height": 48, "png": str(f)}})
    if n == 0:
        for idx, p in enumerate(plugins):
            if editor_off or null_parent: continue
            doc["events"].append({"plugin": idx, "event": "resize", "requested": [184, 128], "accepted": False,
                                  "size": [64, 48]})
    for idx, p in enumerate(plugins):
        doc["events"].append({"plugin": idx, "open": n, "event": "close", "had_editor": not (editor_off or null_parent)})
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
import os, struct, sys, zlib
from pathlib import Path
seed = SEED
w, h = 32, 24
raw = b"".join(b"\x00" + b"".join(bytes(((x * 5 + y + seed) % 230 + 10, (x * 9) % 250, (y * 11 + seed) % 250)) for x in range(w)) for y in range(h))
def chunk(k, d): return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
Path(os.environ["PULP_SCREENSHOT"]).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
'''


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def bundle(root: Path, name: str, *, dirty: bool = False, sha: str = SHA) -> Path:
    b = root / name
    exe = b / "Contents" / "MacOS" / b.stem
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(bytes(range(256)) * 32)
    (b / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
    (b / "Contents" / "Resources" / "pulp-build-info.json").write_text(json.dumps(
        {"product": {"source_git_sha": sha, "source_git_dirty": dirty}, "build": {"type": "Release"}}),
        encoding="utf-8")
    return b


def fixture(root: Path, *, dirty: bool = False) -> tuple[Path, dict]:
    executable(root / "probe", FAKE_PROBE)
    executable(root / "validator", FAKE_VALIDATOR)
    artifacts = {}
    for name in ("alpha.clap", "beta.clap", "alpha.vst3"):
        artifacts[name] = str(bundle(root, name, dirty=dirty))
    for name, seed in (("alpha.app", 3), ("beta.app", 90)):
        app = bundle(root, name, dirty=dirty)
        executable(app / "Contents" / "MacOS" / app.stem, FAKE_APP.replace("SEED", str(seed)))
        artifacts[name] = str(app)
    impl = {"schema": host_lane.HOST_IMPLEMENTATION_SCHEMA, "name": "fake", "repository": "example/fake",
            "build_mode": "fake", "tools": {"probe": str(root / "probe"), "pluginval": str(root / "validator")},
            "artifacts": artifacts,
            "probe_argv": ["{tool:probe}", "@plugins", "--out", "{out}", "--blocks", "94"],
            "validators": {"pluginval": ["{tool:pluginval}", "--validate", "{artifact}"]},
            "validator_success": {"pluginval": "SUCCESS"}}
    corpus = {"schema": host_lane.HOST_CORPUS_SCHEMA, "cases": [
        {"case_id": "alpha-clap", "kind": "probe", "plugins": ["alpha.clap"], "lanes": ["load", "editor", "audio"],
         "expect": {"editor": True, "gpu": True}},
        {"case_id": "pair", "kind": "probe", "plugins": ["alpha.clap", "beta.clap"],
         "lanes": ["load", "editor", "audio", "coexist"], "expect": {"editor": True, "gpu": True}},
        {"case_id": "alpha-pluginval", "kind": "validator", "validator": "pluginval", "artifact": "alpha.vst3",
         "lanes": ["validator"]},
        {"case_id": "alpha-standalone", "kind": "standalone", "artifact": "alpha.app", "lanes": ["standalone"]},
    ], "declared_gaps": [{"row": "lv2", "reason": "fake"}]}
    path = root / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")
    return path, impl


class HostLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ.pop("FAKE_IGNORE", None)

    def tearDown(self) -> None:
        os.environ.pop("FAKE_IGNORE", None)
        self.tmp.cleanup()

    def calibrate(self, **kw):
        corpus, impl = fixture(self.root, **kw)
        return host_lane.calibrate(corpus, impl, SHA, self.root / "out")

    def outcome(self, report, name):
        return next(c for c in report["controls"] if c["control"] == name)["outcome"]

    def test_faithful_fakes_calibrate(self):
        report = self.calibrate()
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"]["reasons"])
        bad = [c for c in report["controls"] if c["outcome"] != "REJECTED"]
        self.assertEqual(bad, [])
        self.assertTrue(report["calibrated"])
        self.assertEqual(report["positive"]["declared_gaps"][0]["row"], "lv2")

    def test_probe_that_ignores_the_editor_kill_switch_is_not_calibrated(self):
        os.environ["FAKE_IGNORE"] = "editor-env"
        report = self.calibrate()
        self.assertEqual(self.outcome(report, "editor-disabled-by-plugin-env"), "NOT REJECTED")
        self.assertFalse(report["calibrated"])

    def test_probe_that_ignores_a_null_parent_is_not_calibrated(self):
        os.environ["FAKE_IGNORE"] = "null-parent"
        report = self.calibrate()
        self.assertEqual(self.outcome(report, "editor-attach-null-parent"), "NOT REJECTED")
        self.assertFalse(report["calibrated"])

    def test_silent_cpu_fallback_is_caught(self):
        os.environ["FAKE_IGNORE"] = "gpu-env"
        report = self.calibrate()
        self.assertEqual(self.outcome(report, "gpu-host-silently-disabled"), "NOT REJECTED")
        self.assertFalse(report["calibrated"])

    def test_dirty_build_is_never_accepting(self):
        report = self.calibrate(dirty=True)
        self.assertFalse(report["positive"]["verdict"]["accepting"])
        self.assertTrue(any("dirty tree" in r for r in report["positive"]["verdict"]["reasons"]))

    def test_parity_mode_refuses_the_same_bundles(self):
        corpus, impl = fixture(self.root)
        receipt = host_lane.run_corpus(corpus, impl, dict(impl, name="other"), self.root / "run", mode="parity",
                                       ref_sha=SHA, cand_sha=SHA)
        self.assertFalse(receipt["verdict"]["accepting"])
        self.assertTrue(any("same bundle bytes" in r for r in receipt["verdict"]["reasons"]))

    def test_missing_validator_is_blocked_not_pass(self):
        corpus, impl = fixture(self.root)
        impl["tools"]["pluginval"] = str(self.root / "absent")
        receipt = host_lane.run_corpus(corpus, impl, impl, self.root / "run", mode="calibration",
                                       ref_sha=SHA, cand_sha=SHA)
        case = next(c for c in receipt["cases"] if c["case_id"] == "alpha-pluginval")
        self.assertEqual(case["lanes"]["validator"]["status"], "blocked")
        self.assertFalse(receipt["verdict"]["accepting"])

    def test_corpus_rejects_lane_of_wrong_kind(self):
        with self.assertRaises(host_lane.HarnessError):
            host_lane.validate_corpus({"schema": host_lane.HOST_CORPUS_SCHEMA, "cases": [
                {"case_id": "x", "kind": "validator", "validator": "v", "artifact": "a", "lanes": ["editor"]}]})


if __name__ == "__main__":
    unittest.main()
