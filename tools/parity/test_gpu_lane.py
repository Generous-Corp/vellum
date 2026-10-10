#!/usr/bin/env python3
"""Unit tests for the GPU lane module (lanes/gpu.py).

The comparators are exercised against crafted observations: receipts written
here, PNGs encoded here, and loader listings naming tiny Mach-O libraries that
are compiled here with the symbols the census looks for. No GPU and no Pulp
toolchain are needed; the census tests need clang and nm, so they run on macOS
only. Each check is paired with the defect it must reject, and the last test
shows that removing a check stops its control from biting.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parity_harness as ph  # noqa: E402

# Loaded the way load_lane_module loads it, keeping the module object for the
# tests; the path still enters the harness fingerprint.
GPU_MODULE = (Path(__file__).resolve().parent / "lanes" / "gpu.py").resolve()
_spec = importlib.util.spec_from_file_location("parity_lane_gpu", GPU_MODULE)
gpu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gpu)
ph.LANE_MODULES.append(GPU_MODULE)

DARWIN = os.uname().sysname == "Darwin" and shutil.which("clang++") and shutil.which("nm")
LIBRARIES = {
    # Dawn's implementation, carrying its own WebGPU C entry points.
    "libfake_dawn.dylib": 'namespace dawn::native { int Proc() { return 1; } }\n'
                          'extern "C" void* wgpuCreateInstance(const void*) { return nullptr; }\n',
    "libfake_skia.dylib": 'struct SkCanvas { int draw(); };\nint SkCanvas::draw() { return 2; }\n',
    # A non-Dawn WebGPU implementation (the wgpu-native shape).
    "libfake_wgpu.dylib": 'extern "C" void* wgpuCreateInstance(const void*) { return nullptr; }\n',
}
DRIVER = "/System/Library/Extensions/AGXMetalG17X.bundle/Contents/MacOS/AGXMetalG17X"


def receipt(**changes) -> dict:
    base = {
        "schema": gpu.RECEIPT_SCHEMA, "backend_requested": "gpu", "backend_used": "gpu", "ok": True,
        "error": "", "seeded_fault": "none", "skia_milestone": 153,
        "dawn_version": "f91da75afe31d4d6f47a6da307e1fbabd1b1691a",
        "gpu": {"surface_created": True, "frame_outcome": "offscreen", "readback_completed": True,
                "device_lost": False, "pixel_width": 8, "pixel_height": 6,
                "adapter": {"available": True, "null_backend": False, "type": "integrated-gpu",
                            "backend": "Dawn/WebGPU", "backend_type": "Metal", "name": "Test GPU",
                            "vendor": "test", "architecture": "test-1"}},
    }
    for path, value in changes.items():
        node = base
        keys = path.split("__")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return base


def write_png(path: Path, width: int = 8, height: int = 6, ink: bool = True) -> None:
    pixels = bytearray(b"\x10\x10\x10\xff" * (width * height))
    if ink:
        pixels[0 + 4 * (width + 1):4 + 4 * (width + 1)] = b"\xc0\x40\x20\xff"
    path.write_bytes(ph.encode_png(ph.RgbaImage(width, height, bytes(pixels))))


class ReceiptLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ref = Path(self._tmp.name) / "ref"
        self.cand = Path(self._tmp.name) / "cand"
        for side in (self.ref, self.cand):
            side.mkdir()
            write_png(side / "gpu-render.png")
            ph.write_json(side / "render-receipt.json", receipt())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def lane(self, **candidate_changes) -> dict:
        ph.write_json(self.cand / "render-receipt.json", receipt(**candidate_changes))
        return gpu.compare_gpu_receipt(self.ref, self.cand, {})

    def test_matching_receipts_pass(self) -> None:
        self.assertEqual(self.lane()["status"], "pass")

    def test_each_defect_fails(self) -> None:
        for changes, expected in (
            ({"backend_used": "skia"}, "fell back off the GPU"),
            ({"ok": False}, "render not ok"),
            ({"seeded_fault": "no-frame"}, "seeded fault"),
            ({"gpu__frame_outcome": "not-drawn"}, "no frame reached its output"),
            ({"gpu__frame_outcome": "failed"}, "no frame reached its output"),
            ({"gpu__readback_completed": False}, "readback did not complete"),
            ({"gpu__device_lost": True}, "device was lost"),
            ({"gpu__adapter__null_backend": True}, "Null backend"),
            ({"gpu__pixel_width": 16}, "not about this render"),
            ({"dawn_version": "0" * 40}, "dawn_version"),
            ({"skia_milestone": 152}, "skia_milestone"),
            ({"gpu__adapter__name": "Other GPU"}, "adapter/name"),
            ({"schema": "other"}, "render receipt is not"),
        ):
            with self.subTest(changes=changes):
                result = self.lane(**changes)
                self.assertEqual(result["status"], "fail")
                self.assertIn(expected, " ".join(result["reasons"]))

    def test_gpu_raster_is_exact_and_needs_ink(self) -> None:
        self.assertEqual(gpu.compare_gpu_raster(self.ref, self.cand, {})["status"], "pass")
        ph.mutate_png(self.cand / "gpu-render.png", ph.flip_center_pixel)
        self.assertEqual(gpu.compare_gpu_raster(self.ref, self.cand, {})["status"], "fail")
        for side in (self.ref, self.cand):
            write_png(side / "gpu-render.png", ink=False)
        result = gpu.compare_gpu_raster(self.ref, self.cand, {})
        self.assertEqual(result["status"], "fail")
        self.assertIn("ink coverage", result["reasons"][0])


@unittest.skipUnless(DARWIN, "the census reads Mach-O images with nm; needs macOS and clang++")
class CensusLaneTests(unittest.TestCase):
    built: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.built = Path(cls._tmp.name) / "lib"
        cls.built.mkdir()
        for name, source in LIBRARIES.items():
            src = cls.built / (name + ".cpp")
            src.write_text(source)
            subprocess.run(["clang++", "-std=c++17", "-dynamiclib", str(src), "-o", str(cls.built / name)],
                           check=True)
        # The main image carries Dawn and Skia, as a statically linked renderer does.
        main = cls.built / "main.cpp"
        main.write_text(LIBRARIES["libfake_dawn.dylib"] + LIBRARIES["libfake_skia.dylib"]
                        + "int main() { return 0; }\n")
        subprocess.run(["clang++", "-std=c++17", str(main), "-o", str(cls.built / "observer")], check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self._side = tempfile.TemporaryDirectory()
        self.ref = Path(self._side.name) / "ref"
        self.cand = Path(self._side.name) / "cand"
        self.ref.mkdir()
        self.cand.mkdir()

    def tearDown(self) -> None:
        self._side.cleanup()

    def listing(self, side: Path, *images: str, driver: bool = True) -> None:
        paths = [str(self.built / "observer"), *(str(self.built / i) for i in images),
                 "/usr/lib/libSystem.B.dylib"]
        if driver:
            paths.append(DRIVER)
        side.joinpath("dyld-images.txt").write_text(
            "".join(f"dyld[1]: <00000000-0000-0000-0000-000000000000> {p}\n" for p in paths))

    def lane(self, case: dict | None = None) -> dict:
        return gpu.compare_provider_census(self.ref, self.cand, case or {})

    def both(self, *images: str, driver: bool = True) -> None:
        self.listing(self.ref, *images, driver=driver)
        self.listing(self.cand, *images, driver=driver)

    def test_one_provider_each_passes(self) -> None:
        self.both()
        result = self.lane()
        self.assertEqual(result["status"], "pass", result["reasons"])
        self.assertEqual(gpu.census_shape(result["reference"]), [["<main>", ["dawn", "skia", "webgpu-c-api"]]])

    def test_second_dawn_fails_even_on_both_sides(self) -> None:
        self.both("libfake_dawn.dylib")
        result = self.lane()
        self.assertEqual(result["status"], "fail")
        self.assertIn("2 images carry Dawn", " ".join(result["reasons"]))

    def test_second_skia_fails(self) -> None:
        self.both("libfake_skia.dylib")
        self.assertIn("2 images carry Skia", " ".join(self.lane()["reasons"]))

    def test_extra_webgpu_exporter_needs_an_allowance_with_a_reason(self) -> None:
        self.both("libfake_wgpu.dylib")
        self.assertIn("also exports the WebGPU C API", " ".join(self.lane()["reasons"]))
        unjustified = {"params": {"allow_extra_webgpu_api": "libfake_wgpu.dylib"}}
        self.assertIn("needs a justification", " ".join(self.lane(unjustified)["reasons"]))
        allowed = {"params": {"allow_extra_webgpu_api": "libfake_wgpu.dylib",
                              "allow_extra_webgpu_api_justification": "recorded for the test"}}
        self.assertEqual(self.lane(allowed)["status"], "pass")

    def test_no_gpu_driver_fails_even_on_both_sides(self) -> None:
        self.both(driver=False)
        self.assertIn("no GPU driver was loaded", " ".join(self.lane()["reasons"]))

    def test_shape_difference_fails(self) -> None:
        allowed = {"params": {"allow_extra_webgpu_api": "libfake_wgpu.dylib",
                              "allow_extra_webgpu_api_justification": "recorded for the test"}}
        self.listing(self.ref)
        self.listing(self.cand, "libfake_wgpu.dylib")
        self.assertIn("provider shape differs", " ".join(self.lane(allowed)["reasons"]))

    def test_empty_listing_fails(self) -> None:
        self.both()
        self.cand.joinpath("dyld-images.txt").write_text("")
        self.assertIn("listed no images", " ".join(self.lane()["reasons"]))

    def test_second_dawn_image_control_uses_real_dawn_code(self) -> None:
        self.both()
        self.assertTrue(gpu.add_second_dawn_image(self.cand))
        self.assertTrue((self.cand / "second-dawn-provider.bin").is_file())
        self.assertIn("2 images carry Dawn", " ".join(self.lane()["reasons"]))

    def test_controls_on_the_controls(self) -> None:
        """Remove a check and its control stops biting: the control measures the
        check, not something incidental."""
        self.both(driver=False)
        original = gpu.GPU_DRIVER
        try:
            gpu.GPU_DRIVER = re.compile(r".*")  # every image now looks like a driver
            self.assertEqual(self.lane()["status"], "pass")
        finally:
            gpu.GPU_DRIVER = original
        self.assertEqual(self.lane()["status"], "fail")


class RegistrationTests(unittest.TestCase):
    def test_lanes_and_controls_are_registered(self) -> None:
        for lane in ("gpu-raster", "gpu-receipt", "provider-census"):
            self.assertIn(lane, ph.LANES)
        for name in ("gpu-device-lost", "gpu-no-presented-frame", "gpu-reported-raster-fallback",
                     "gpu-silent-raster-fallback", "gpu-silent-raster-fallback-census",
                     "second-dawn-inserted"):
            self.assertIn(name, ph.RERUN_CONTROLS)
        for name in ("gpu-raster-one-pixel", "census-second-dawn-image", "census-no-gpu-driver-both",
                     "gpu-receipt-dawn-revision", "gpu-receipt-adapter-changed"):
            self.assertIn(name, ph.ARTIFACT_CONTROLS)

    def test_fault_controls_skip_cases_without_gpu_lanes(self) -> None:
        prepare = ph.RERUN_CONTROLS["gpu-no-presented-frame"]["prepare"]
        self.assertIsNone(prepare({"lanes": ["raster"], "params": {}}, Path("."), None))
        run_case, _ = prepare({"lanes": ["gpu-receipt"], "params": {}}, Path("."), None)
        self.assertEqual(run_case["args"]["gpu_fault"], ["--gpu-fault", "no-frame"])

    def test_declared_pixel_blind_spot_needs_a_reason_and_skips_only_the_pixel_control(self) -> None:
        case = {"case_id": "c", "lanes": ["gpu-raster", "provider-census"],
                "params": {"gpu_pixels_match_cpu": "true"}}
        pixels = ph.RERUN_CONTROLS["gpu-silent-raster-fallback"]["prepare"]
        census = ph.RERUN_CONTROLS["gpu-silent-raster-fallback-census"]["prepare"]
        with self.assertRaises(ph.HarnessError):
            pixels(case, Path("."), None)
        case["params"]["gpu_pixels_match_cpu_justification"] = "measured identical"
        self.assertIsNone(pixels(case, Path("."), None))
        self.assertIsNotNone(census(case, Path("."), None))

    def test_second_dawn_control_without_a_library_applies_to_nothing(self) -> None:
        saved = os.environ.pop("VELLUM_PARITY_SECOND_DAWN_DYLIB", None)
        try:
            prepare = ph.RERUN_CONTROLS["second-dawn-inserted"]["prepare"]
            self.assertIsNone(prepare({"lanes": ["provider-census"], "params": {}}, Path("."), None))
        finally:
            if saved is not None:
                os.environ["VELLUM_PARITY_SECOND_DAWN_DYLIB"] = saved

    def test_module_enters_the_harness_fingerprint(self) -> None:
        self.assertIn("gpu.py", ph.harness_fingerprint()["files"])


if __name__ == "__main__":
    unittest.main()
