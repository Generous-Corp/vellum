"""GPU lanes: the Skia Graphite render on Dawn, its render receipt, and the
GPU provider census of the process that produced it.

Load with ``--lane-module tools/parity/lanes/gpu.py``. The implementation must
add a step that writes three files beside the CPU observations:

  gpu-render.png       the render through Skia Graphite on a Dawn device
  render-receipt.json  what the renderer says it did (backend requested and
                       used, adapter, frame outcome, readback, device loss,
                       Skia milestone and Dawn revision)
  dyld-images.txt      every image the dynamic loader mapped into that process,
                       captured by the step wrapper with DYLD_PRINT_LIBRARIES,
                       not reported by the renderer

The step sets ``DYLD_PRINT_LIBRARIES=1`` and
``DYLD_INSERT_LIBRARIES={param:insert_libraries}`` (cases default it to empty),
keeps its stderr as ``dyld-images.txt`` and passes ``@case_args:gpu_fault``;
the seeded controls set those two.

Lanes:

  gpu-raster       exact pixels plus the ink floor, like ``raster``, on the
                   GPU render
  gpu-receipt      each side rendered on a live, native GPU adapter, drew and
                   read back a frame and kept its device; both sides report the
                   same adapter, Skia milestone and Dawn revision
  provider-census  read from the loaded images themselves: exactly one image
                   carries Dawn's implementation, exactly one carries Skia, a
                   GPU driver was loaded, any further WebGPU C API exporter is
                   one the case explicitly allows; and both sides have the same
                   provider shape

The receipt is the renderer's own account, so it is never the only witness.
A renderer that falls back to the CPU and still says ``gpu`` is caught by the
pixels and by the census (no GPU driver loaded); a second Dawn copy is caught by
the census whatever the renderer reports. The census reads Mach-O images with
``nm`` and the loader's own image list, so it is macOS-only; elsewhere the lane
is blocked rather than passed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

if "parity_harness" not in sys.modules:  # run as a script for its helper commands
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parity_harness as ph  # noqa: E402

RECEIPT_SCHEMA = "pulp-design-ir-render-receipt-v1"
GPU_ARTIFACTS = {
    "gpu-raster": ("gpu-render.png",),
    "gpu-receipt": ("render-receipt.json", "gpu-render.png"),
    "provider-census": ("dyld-images.txt",),
}
DRAWN_OUTCOMES = ("offscreen", "presented")
ADAPTER_IDENTITY = ("backend_type", "name", "vendor", "architecture", "type")
SYSTEM_PREFIXES = ("/System/", "/usr/lib/", "/Library/Apple/")
GPU_DRIVER = re.compile(r"^/System/Library/Extensions/[^/]+\.bundle/")
DYLD_LINE = re.compile(r"^dyld\[\d+\]: <[0-9A-Fa-f-]+> (?P<path>/.*)$")
# A symbol from Dawn's native implementation (dawn::native::*), and from Skia's
# core canvas. Their presence marks an image as carrying that provider.
DAWN_NATIVE = "__ZN4dawn6native"
SKIA_CORE = "SkCanvas"
WEBGPU_ENTRY = "_wgpuCreateInstance"


# ── gpu-raster ─────────────────────────────────────────────────────────────


def compare_gpu_raster(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    return ph.compare_png_pair(ref / "gpu-render.png", cand / "gpu-render.png",
                               case.get("raster_threshold", 0), case.get("min_ink_pixels", 1))


# ── gpu-receipt ────────────────────────────────────────────────────────────


def png_size(path: Path) -> tuple[int, int] | None:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def receipt_errors(receipt: Any, png: Path) -> list[str]:
    """Why one side's receipt cannot vouch for a GPU render."""
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        return [f"render receipt is not {RECEIPT_SCHEMA}"]
    errors = []
    if receipt.get("backend_requested") != "gpu":
        errors.append(f"backend_requested {receipt.get('backend_requested')!r}, not 'gpu'")
    if receipt.get("backend_used") != "gpu":
        errors.append(f"backend_used {receipt.get('backend_used')!r}: the render fell back off the GPU")
    if receipt.get("ok") is not True:
        errors.append(f"render not ok: {receipt.get('error')!r}")
    if receipt.get("seeded_fault", "none") != "none":
        errors.append(f"a seeded fault was active: {receipt.get('seeded_fault')!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("dawn_version", ""))):
        errors.append("no Dawn revision recorded")
    if not isinstance(receipt.get("skia_milestone"), int) or receipt["skia_milestone"] <= 0:
        errors.append("no Skia milestone recorded")
    gpu = receipt.get("gpu")
    if not isinstance(gpu, dict):
        return errors + ["no gpu block in the receipt"]
    if gpu.get("surface_created") is not True:
        errors.append("no GPU surface was created")
    if gpu.get("frame_outcome") not in DRAWN_OUTCOMES:
        errors.append(f"frame_outcome {gpu.get('frame_outcome')!r}: no frame reached its output")
    if gpu.get("readback_completed") is not True:
        errors.append("GPU readback did not complete")
    if gpu.get("device_lost") is not False:
        errors.append("the Dawn device was lost")
    adapter = gpu.get("adapter") or {}
    if adapter.get("available") is not True:
        errors.append("no adapter was available")
    if adapter.get("null_backend") is not False:
        errors.append("Dawn Null backend: commands accepted, nothing rendered")
    size = png_size(png) if png.is_file() else None
    if size != (gpu.get("pixel_width"), gpu.get("pixel_height")):
        errors.append(f"receipt pixel size {(gpu.get('pixel_width'), gpu.get('pixel_height'))} "
                      f"!= render {size}: the receipt is not about this render")
    return errors


def receipt_identity(receipt: dict[str, Any]) -> dict[str, Any]:
    adapter = (receipt.get("gpu") or {}).get("adapter") or {}
    return {"dawn_version": receipt.get("dawn_version"),
            "skia_milestone": receipt.get("skia_milestone"),
            "adapter": {key: adapter.get(key) for key in ADAPTER_IDENTITY}}


def compare_gpu_receipt(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    try:
        a = ph.load_json(ref / "render-receipt.json")
        b = ph.load_json(cand / "render-receipt.json")
    except (OSError, json.JSONDecodeError) as error:
        return ph.lane_result("fail", reasons=[f"unreadable render receipt: {error}"])
    reasons = [f"reference: {e}" for e in receipt_errors(a, ref / "gpu-render.png")]
    reasons += [f"candidate: {e}" for e in receipt_errors(b, cand / "gpu-render.png")]
    identities = [receipt_identity(r) if isinstance(r, dict) else {} for r in (a, b)]
    reasons += [f"provider/adapter identity {d}" for d in ph.json_differences(*identities)]
    return ph.lane_result("fail" if reasons else "pass", reasons=reasons,
                          reference_identity=identities[0], candidate_identity=identities[1])


# ── provider-census ────────────────────────────────────────────────────────


def loaded_images(listing: Path) -> list[str]:
    images = []
    for line in listing.read_text(encoding="utf-8", errors="replace").splitlines():
        match = DYLD_LINE.match(line.strip())
        if match:
            images.append(match.group("path"))
    return images


def defined_symbols(image: str) -> list[str] | None:
    try:
        result = subprocess.run(["nm", "-U", image], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line.rsplit(" ", 1)[-1] for line in result.stdout.splitlines() if line.strip()]


def census(listing: Path) -> dict[str, Any]:
    """Classify every non-system image the process loaded."""
    images = loaded_images(listing)
    record: dict[str, Any] = {"images_loaded": len(images), "providers": [], "unreadable": [],
                              "gpu_drivers": sorted({Path(p).name for p in images if GPU_DRIVER.match(p)})}
    main = images[0] if images else None
    for index, path in enumerate(images):
        if path.startswith(SYSTEM_PREFIXES):
            continue
        symbols = defined_symbols(path)
        if symbols is None:
            record["unreadable"].append(path)
            continue
        roles = []
        if any(s.startswith(DAWN_NATIVE) for s in symbols):
            roles.append("dawn")
        if any(SKIA_CORE in s for s in symbols):
            roles.append("skia")
        if WEBGPU_ENTRY in symbols:
            roles.append("webgpu-c-api")
        if roles:
            record["providers"].append({
                "image": "<main>" if index == 0 and path == main else Path(path).name,
                "path": path, "sha256": ph.sha256_file(Path(path)), "roles": roles})
    return record


def census_errors(record: dict[str, Any], case: dict[str, Any]) -> list[str]:
    """The one-provider-per-final-image rule, applied to what was loaded."""
    errors = []
    if record["images_loaded"] == 0:
        return ["the loader listed no images (the census did not run)"]
    if record["unreadable"]:
        errors.append(f"images the census could not read: {record['unreadable']}")
    for role, label in (("dawn", "Dawn"), ("skia", "Skia")):
        owners = [p["image"] for p in record["providers"] if role in p["roles"]]
        if len(owners) != 1:
            errors.append(f"{len(owners)} images carry {label} {owners}; exactly one is allowed")
    dawn_images = {p["image"] for p in record["providers"] if "dawn" in p["roles"]}
    allowed = case.get("params", {}).get("allow_extra_webgpu_api", "")
    extra = [p["image"] for p in record["providers"]
             if "webgpu-c-api" in p["roles"] and p["image"] not in dawn_images]
    for image in extra:
        if image != allowed:
            errors.append(f"{image} also exports the WebGPU C API and is not allowed by the case")
    if not record["gpu_drivers"]:
        errors.append("no GPU driver was loaded: nothing in this process rendered on a GPU")
    return errors


def census_shape(record: dict[str, Any]) -> list[Any]:
    return sorted([p["image"], sorted(p["roles"])] for p in record["providers"])


def compare_provider_census(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    if os.uname().sysname != "Darwin":
        return ph.lane_result("blocked", reasons=["provider census reads Mach-O images; macOS only"])
    if case.get("params", {}).get("allow_extra_webgpu_api") and not str(
            case.get("params", {}).get("allow_extra_webgpu_api_justification", "")).strip():
        return ph.lane_result("fail", reasons=["allow_extra_webgpu_api needs a justification"])
    a, b = census(ref / "dyld-images.txt"), census(cand / "dyld-images.txt")
    reasons = [f"reference: {e}" for e in census_errors(a, case)]
    reasons += [f"candidate: {e}" for e in census_errors(b, case)]
    shapes = census_shape(a), census_shape(b)
    if shapes[0] != shapes[1]:
        reasons.append(f"provider shape differs: {shapes[0]} != {shapes[1]}")
    return ph.lane_result("fail" if reasons else "pass", reasons=reasons,
                          reference=a, candidate=b)


# ── seeded controls ────────────────────────────────────────────────────────


# Artifact controls apply only where the baseline produced the file: a blocked
# baseline is the positive's failure to report, not something to crash on here.
def unlink(path: Path) -> bool:
    if not path.is_file():
        return False
    path.unlink()
    return True


def mutate_png(path: Path, transform) -> bool:
    if not path.is_file():
        return False
    ph.mutate_png(path, transform)
    return True


def edit_receipt(path: Path, edit) -> bool:
    if not path.is_file():
        return False
    receipt = ph.load_json(path)
    edit(receipt)
    ph.write_json(path, receipt)
    return True


def flip_hex(value: str) -> str:
    return ("0" if value[:1] != "0" else "1") + value[1:]


def change_adapter(receipt: dict[str, Any]) -> None:
    receipt["gpu"]["adapter"]["name"] = receipt["gpu"]["adapter"]["name"] + " (other)"


def add_second_dawn_image(side: Path) -> bool:
    """List a second image carrying Dawn in the loader record: a copy of the
    image that already carries it, so the duplicate is real Dawn code."""
    listing = side / "dyld-images.txt"
    if not listing.is_file():
        return False
    record = census(listing)
    dawn = [p for p in record["providers"] if "dawn" in p["roles"]]
    if not dawn:
        return False
    copy = side / "second-dawn-provider.bin"
    shutil.copy2(dawn[0]["path"], copy)
    with listing.open("a", encoding="utf-8") as stream:
        stream.write(f"dyld[0]: <00000000-0000-0000-0000-000000000000> {copy.resolve()}\n")
    return True


def drop_gpu_drivers(side: Path) -> bool:
    listing = side / "dyld-images.txt"
    if not listing.is_file():
        return False
    lines = listing.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines
            if not ((m := DYLD_LINE.match(line.strip())) and GPU_DRIVER.match(m.group("path")))]
    if len(kept) == len(lines):
        return False
    listing.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True


def with_fault(fault: str, *, needs_distinct_pixels: bool = False):
    def prepare(case: dict[str, Any], _source_root: Path, _work_dir: Path | None = None):
        if not any(lane in case["lanes"] for lane in GPU_ARTIFACTS):
            return None
        params = case.get("params", {})
        if needs_distinct_pixels and params.get("gpu_pixels_match_cpu") == "true":
            # A declared, justified blind spot: the two rasterizers agree on this
            # case, so its pixels cannot show a fallback. Other lanes still must.
            if not str(params.get("gpu_pixels_match_cpu_justification", "")).strip():
                raise ph.HarnessError(f"{case['case_id']}: gpu_pixels_match_cpu needs a justification")
            return None
        return dict(case, args={**case.get("args", {}), "gpu_fault": ["--gpu-fault", fault]}), {}
    return prepare


def reason_contains(text: str):
    return lambda result: any(text in reason for reason in result.get("reasons", []))


def second_dawn_prepare(case: dict[str, Any], _source_root: Path, _work_dir: Path | None = None):
    """Insert a real second Dawn into the candidate process. The library is
    named by VELLUM_PARITY_SECOND_DAWN_DYLIB (build it with
    `lanes/gpu.py build-second-dawn`); without it the control applies to no
    case, which calibration counts as a failure rather than a pass."""
    library = os.environ.get("VELLUM_PARITY_SECOND_DAWN_DYLIB", "")
    if "provider-census" not in case["lanes"] or not library or not Path(library).is_file():
        return None
    return dict(case, params={**case.get("params", {}), "insert_libraries": library}), {}


ph.register_lane("gpu-raster", GPU_ARTIFACTS["gpu-raster"], compare_gpu_raster)
ph.register_lane("gpu-receipt", GPU_ARTIFACTS["gpu-receipt"], compare_gpu_receipt)
ph.register_lane("provider-census", GPU_ARTIFACTS["provider-census"], compare_provider_census)

ph.register_controls(
    artifact={
        "gpu-raster-one-pixel": {"lane": "gpu-raster", "candidate": lambda d: mutate_png(
            d / "gpu-render.png", ph.flip_center_pixel)},
        "gpu-raster-blank-both": {"lane": "gpu-raster", "both": lambda d: mutate_png(
            d / "gpu-render.png", ph.uniform_image)},
        "gpu-render-missing": {"lane": "gpu-raster", "expect": "blocked",
                               "candidate": lambda d: unlink(d / "gpu-render.png")},
        "gpu-receipt-missing": {"lane": "gpu-receipt", "expect": "blocked",
                                "candidate": lambda d: unlink(d / "render-receipt.json")},
        "gpu-receipt-dawn-revision": {"lane": "gpu-receipt", "candidate": lambda d: edit_receipt(
            d / "render-receipt.json", lambda r: r.update(dawn_version=flip_hex(r["dawn_version"])))},
        "gpu-receipt-adapter-changed": {"lane": "gpu-receipt", "candidate": lambda d: edit_receipt(
            d / "render-receipt.json", change_adapter)},
        "census-second-dawn-image": {"lane": "provider-census", "candidate": add_second_dawn_image,
                                     "bites": reason_contains("2 images carry Dawn")},
        "census-no-gpu-driver-both": {"lane": "provider-census", "both": drop_gpu_drivers,
                                      "bites": reason_contains("no GPU driver was loaded")},
        "census-missing": {"lane": "provider-census", "expect": "blocked",
                           "candidate": lambda d: unlink(d / "dyld-images.txt")},
    },
    rerun={
        "gpu-device-lost": {"lane": "gpu-receipt", "expect": "blocked",
                            "prepare": with_fault("device-destroyed")},
        "gpu-no-presented-frame": {"lane": "gpu-receipt", "prepare": with_fault("no-frame"),
                                   "bites": reason_contains("no frame reached its output")},
        "gpu-reported-raster-fallback": {"lane": "gpu-receipt", "prepare": with_fault("raster-fallback"),
                                         "bites": reason_contains("fell back off the GPU")},
        "gpu-silent-raster-fallback": {"lane": "gpu-raster",
                                       "prepare": with_fault("raster-fallback-unreported",
                                                             needs_distinct_pixels=True),
                                       "bites": reason_contains("pixels differ")},
        "gpu-silent-raster-fallback-census": {"lane": "provider-census",
                                              "prepare": with_fault("raster-fallback-unreported"),
                                              "bites": reason_contains("no GPU driver was loaded")},
        "second-dawn-inserted": {"lane": "provider-census", "expect": "blocked",
                                 "prepare": second_dawn_prepare},
    },
)


def build_second_dawn(archive: Path, out: Path) -> int:
    """Link a Dawn static archive into a dylib for the second-dawn-inserted
    control. Use the archive the implementation under test links, so the
    inserted copy is the same Dawn, not merely some WebGPU."""
    argv = ["clang++", "-dynamiclib", "-Wl,-all_load", str(archive), "-o", str(out),
            "-undefined", "dynamic_lookup", "-install_name", f"@rpath/{out.name}"]
    for framework in ("Metal", "QuartzCore", "IOSurface", "Foundation", "CoreFoundation", "IOKit",
                      "CoreGraphics"):
        argv += ["-framework", framework]
    result = subprocess.run(argv)
    if result.returncode == 0:
        print(json.dumps({"dylib": str(out), "sha256": ph.sha256_file(out),
                          "archive": str(archive), "archive_sha256": ph.sha256_file(archive)}))
    return result.returncode


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GPU lane helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-second-dawn")
    build.add_argument("--dawn-archive", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    sys.exit(build_second_dawn(args.dawn_archive, args.out))
