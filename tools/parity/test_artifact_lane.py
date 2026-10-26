#!/usr/bin/env python3
"""Artifact lane module tests through the core harness, over real Mach-O bundles.

The fixture bundles are built here with clang as universal (arm64 + x86_64)
images: an audio-only one whose symbols are pulp::format only, and a GPU-UI
one that also carries pulp::view, a GPU window host, Skia and wgpu names and
bundles a universal libwgpu_native. The lane reads them with the real
artifact runner, so slices, signatures and symbol families are measured.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parity_harness as ph  # noqa: E402

artifact = ph.load_lane_module(HERE / "lanes" / "artifact.py")
HAVE_TOOLCHAIN = sys.platform == "darwin" and all(shutil.which(t) for t in ("clang", "lipo", "otool", "nm", "codesign"))
ARCHS = ["arm64", "x86_64"]
required_paths = ["Contents/Resources/pulp-build-info.json", "Contents/Info.plist"]

AUDIO_ONLY = "namespace pulp { namespace format { int entry() { return 1; } } }\n"
GPU_UI = AUDIO_ONLY + (
    'extern "C" int wgpuCreateInstance(void);\n'
    "namespace pulp { namespace view { int draw() { return wgpuCreateInstance(); } }\n"
    "  namespace render { int frame() { return 3; } } }\n"
    "struct MacGpuWindowHost { int open(); }; int MacGpuWindowHost::open() { return 4; }\n"
    "struct SkCanvas { int clear(); }; int SkCanvas::clear() { return 5; }\n")


def clang(source: str, out: Path, *args: str, lang: str = "c++") -> None:
    src = out.with_suffix(".src")
    src.write_text(source, encoding="utf-8")
    cmd = ["clang", *[x for a in ARCHS for x in ("-arch", a)], *args, "-x", lang, str(src), "-o", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    src.unlink()


def sign(image: Path) -> None:
    # The linker signs only the arm64 slice; a build that ships signs them all.
    subprocess.run(["codesign", "-s", "-", "-f", str(image)], check=True, capture_output=True)


def bundle(path: Path, source: str, runtime: Path | None = None) -> Path:
    macos = path / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (path / "Contents" / "Resources").mkdir()
    (path / "Contents" / "Resources" / "pulp-build-info.json").write_text("{}", encoding="utf-8")
    (path / "Contents" / "PkgInfo").write_text("BNDL????", encoding="utf-8")
    import plistlib
    (path / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleExecutable": path.stem, "CFBundleIdentifier": f"dev.test.{path.stem}",
         "CFBundlePackageType": "BNDL"}))
    args = ["-dynamiclib", "-lc++"]
    if runtime is not None:
        shutil.copy2(runtime, macos / runtime.name)
        args += ["-Wl,-rpath,@loader_path", str(macos / runtime.name)]
    loose = path.parent / f"{path.stem}.image"
    clang(source, loose, *args)
    sign(loose)
    loose.rename(macos / path.stem)
    return path


def fixture(root: Path, gpu_expect: dict | None = None) -> tuple[Path, Path, dict[str, str]]:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "fixture"], check=True)
    build = root / "build"
    build.mkdir()
    runtime = build / "libwgpu_native.dylib"
    clang("int wgpuCreateInstance(void) { return 2; }\n", runtime, "-dynamiclib",
          "-install_name", "@rpath/libwgpu_native.dylib", lang="c")
    sign(runtime)
    roots = {"checkout": str(repo),
             "gain.clap": str(bundle(build / "Gain.clap", AUDIO_ONLY)),
             "gpu.clap": str(bundle(build / "Gpu.clap", GPU_UI, runtime))}
    impl = json.loads((HERE / "implementations" / "pulp-artifact-macos.json").read_text(encoding="utf-8"))
    paths = ["{root:gain.clap}", "{root:gpu.clap}"]
    impl["probes"] = [{"name": p["name"], "argv": p["argv"][:3] + (["{root:checkout}"] if p["name"] == "build" else [])
                       + paths} for p in impl["probes"]]
    impl["content_roots"] = ["gain.clap", "gpu.clap"]
    impl["tools"] = {"runner": str(HERE / "lanes" / "artifact_runner.py"),
                     "host-runner": str(HERE / "lanes" / "host_runner.py")}
    impl_path = root / "impl.json"
    impl_path.write_text(json.dumps(impl), encoding="utf-8")
    corpus_dir = root / "corpus"
    (corpus_dir / "fixtures" / "artifact").mkdir(parents=True)
    shutil.copy2(HERE / "corpus" / "fixtures" / "artifact" / "scan.json", corpus_dir / "fixtures" / "artifact")
    base = {"source": "fixtures/artifact/scan.json", "source_base": "corpus", "width": 1, "height": 1,
            "source_kind": "artifact"}
    lanes = ["artifact-slices", "artifact-bundle", "artifact-signature", "artifact-linkfloor"]
    required = required_paths
    corpus = {"schema": ph.CORPUS_SCHEMA, "cases": [
        dict(base, case_id="audio-only", lanes=lanes, args={"artifact": ["--artifact", "{root:gain.clap}"]},
             controls={"swap_floor": "gpu.clap"},
             expect={"archs": ARCHS, "platform": 1, "signed": "image", "required": required,
                     "forbid": ["pulp-view", "gpu-host", "skia", "wgpu"], "require": ["pulp-format"],
                     "forbid_load": ["libwgpu_native"]}),
        dict(base, case_id="gpu-ui", lanes=lanes[:1] + ["artifact-runtimes"] + lanes[1:],
             args={"artifact": ["--artifact", "{root:gpu.clap}"]},
             controls={"swap_floor": "gain.clap"},
             expect=gpu_expect or {"archs": ARCHS, "platform": 1, "signed": "image",
                                   "runtimes": ["Contents/MacOS/libwgpu_native.dylib"], "required": required,
                                   "require": ["pulp-format", "pulp-view", "pulp-render", "gpu-host", "skia", "wgpu"],
                                   "require_load_once": ["libwgpu_native"]}),
    ]}
    corpus_path = corpus_dir / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    return corpus_path, impl_path, roots


class ArtifactComparatorTest(unittest.TestCase):
    # Synthetic observations, so these run without a toolchain and pin the
    # guards that only the corpus's fault controls would otherwise exercise.

    def test_an_artifact_with_no_binary_image_is_vacuous(self):
        case = {"expect": {"archs": ARCHS, "main_image": False}}
        obs = {"files": [{"path": "Contents/Info.plist", "kind": "other"}], "main_image": None}
        reasons = artifact.slices_side(case, obs, "candidate")[0]
        self.assertEqual(reasons, ["candidate: no binary image in the artifact (vacuous)"])

    def test_a_bundle_whose_seal_does_not_verify_fails_the_signature_lane(self):
        case = {"expect": {"signed": "bundle"}}
        obs = {"codesign": {"exit": 1, "stderr_tail": "a sealed resource is missing or invalid"},
               "codesign_image": {arch: {"exit": 0} for arch in ARCHS}}
        reasons = artifact.signature_side(case, obs, "candidate")
        self.assertEqual(len(reasons), 1)
        self.assertIn("codesign (bundle) verification exited 1", reasons[0])


@unittest.skipUnless(HAVE_TOOLCHAIN, "needs macOS with clang, lipo, otool, nm and codesign")
class ArtifactLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def calibrate(self, **kw):
        corpus, impl, roots = fixture(self.root, **kw)
        return ph.calibrate(corpus, self.root, impl, self.root / "out", roots={k: Path(v) for k, v in roots.items()})

    def outcome(self, report, name):
        return next(c for c in report["controls"] if c["control"] == name)["outcome"]

    def test_universal_bundles_calibrate_and_every_control_rejects(self):
        report = self.calibrate()
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"]["reasons"])
        self.assertEqual(report["control_failures"], [])
        lane = [c for c in report["controls"] if c["control"].startswith("artifact-")]
        self.assertEqual(len(lane), 8)
        self.assertTrue(all(c["outcome"] == "REJECTED as required" for c in lane), lane)
        self.assertTrue(report["calibrated"])

    def test_a_link_floor_that_forbids_nothing_cannot_reject_the_swap(self):
        # The swap control puts the audio-only bundle where the GPU one was.
        # The two sides then differ, but without required families the GPU
        # case's own floor has nothing to reject it on, and the control must
        # say so rather than count the cross-side difference.
        report = self.calibrate(gpu_expect={"archs": ARCHS, "platform": 1, "signed": "image", "required": required_paths,
                                            "runtimes": ["Contents/MacOS/libwgpu_native.dylib"],
                                            "require": ["pulp-format"]})
        self.assertEqual(self.outcome(report, "artifact-linkfloor-swapped-for-other-link"), "CONTROL DID NOT FAIL")
        self.assertFalse(report["calibrated"])

    def test_a_thin_bundled_runtime_fails_the_positive(self):
        corpus, impl, roots = fixture(self.root)
        nested = Path(roots["gpu.clap"]) / "Contents" / "MacOS" / "libwgpu_native.dylib"
        subprocess.run(["lipo", str(nested), "-remove", "x86_64", "-output", str(nested)], check=True)
        report = ph.calibrate(corpus, self.root, impl, self.root / "out", roots={k: Path(v) for k, v in roots.items()})
        reasons = report["positive"]["verdict"]["reasons"]
        self.assertTrue(any("gpu-ui/artifact-slices: fail" in r for r in reasons), reasons)
        self.assertTrue(any("gpu-ui/artifact-runtimes: fail" in r for r in reasons), reasons)


if __name__ == "__main__":
    unittest.main()
