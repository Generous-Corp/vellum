#!/usr/bin/env python3
"""Pixel and semantic comparison for visual proof.

Two comparisons live here. Neither needs anything beyond the standard library
and ``cli/vellum_png.py``.

* Pixel: ``compare_pixels`` returns a thresholded similarity verdict for two
  PNGs, ``diff_image`` renders where they disagree, ``diff_bounds`` boxes the
  disagreement, and ``content_stats`` rejects a capture that is a stable empty
  frame, which is the one input a similarity score cannot tell from a match.
* Semantic: ``compare`` walks two JSON documents with a numeric tolerance, for
  layout and tree snapshots.

A capture is judged against a similarity threshold and a per-channel tolerance
together. Tolerance decides whether one pixel counts as different; similarity
decides how many different pixels a render may carry.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from vellum_png import PngError, RgbaImage, decode_png_bytes  # noqa: E402

# Matches the threshold import validation uses for "close enough to the source
# design". Goldens use their own, stricter fixture values (see runner.py).
DEFAULT_SIMILARITY_THRESHOLD = 0.85
DEFAULT_CHANNEL_TOLERANCE = 32
MAX_TRACKED_COLORS = 65536


# --- semantic comparison ---------------------------------------------------

@dataclass(frozen=True)
class Difference:
    path: str
    expected: Any
    actual: Any
    message: str

    def format(self) -> str:
        return (
            f"{self.path}: {self.message}; "
            f"expected={self.expected!r} actual={self.actual!r}"
        )


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tolerance_from_fixture(fixture: dict[str, Any] | None, default: float = 0.001) -> float:
    if not fixture:
        return default
    tolerance = fixture.get("tolerance")
    if not isinstance(tolerance, dict):
        return default
    value = tolerance.get("semantic", tolerance.get("rect", tolerance.get("number", default)))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compare(expected: Any, actual: Any, *, tolerance: float = 0.001) -> list[Difference]:
    differences: list[Difference] = []
    _compare_value(expected, actual, "$", tolerance, differences)
    return differences


def diff_files(expected_path: Path, actual_path: Path, *, tolerance: float = 0.001) -> list[Difference]:
    return compare(load_json(expected_path), load_json(actual_path), tolerance=tolerance)


def format_differences(differences: list[Difference], *, limit: int = 20) -> str:
    if not differences:
        return ""
    lines = [d.format() for d in differences[:limit]]
    if len(differences) > limit:
        lines.append(f"... {len(differences) - limit} more difference(s)")
    return "\n".join(lines)


def format_byte_difference(expected: bytes, actual: bytes) -> str:
    """Compact exact-byte mismatch summary; empty when the bytes are equal."""
    if expected == actual:
        return ""
    for index in range(min(len(expected), len(actual))):
        if expected[index] != actual[index]:
            return (
                f"byte mismatch at offset {index}: "
                f"expected=0x{expected[index]:02x} actual=0x{actual[index]:02x}; "
                f"expected_len={len(expected)} actual_len={len(actual)}"
            )
    return f"length mismatch: expected_len={len(expected)} actual_len={len(actual)}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare_value(expected: Any, actual: Any, path: str, tolerance: float,
                   differences: list[Difference]) -> None:
    if isinstance(expected, bool) or isinstance(actual, bool):
        if expected is not actual:
            differences.append(Difference(path, expected, actual, "boolean mismatch"))
        return
    if _is_number(expected) and _is_number(actual):
        delta = abs(float(expected) - float(actual))
        if delta > tolerance:
            differences.append(
                Difference(path, expected, actual, f"numeric delta {delta:.6g} > {tolerance:g}")
            )
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) - set(actual)):
            differences.append(Difference(f"{path}.{key}", expected[key], None, "missing key"))
        for key in sorted(set(actual) - set(expected)):
            differences.append(Difference(f"{path}.{key}", None, actual[key], "unexpected key"))
        for key in sorted(set(expected) & set(actual)):
            _compare_value(expected[key], actual[key], f"{path}.{key}", tolerance, differences)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            differences.append(Difference(path, len(expected), len(actual), "array length mismatch"))
        for index, (left, right) in enumerate(zip(expected, actual)):
            _compare_value(left, right, f"{path}[{index}]", tolerance, differences)
        return
    if expected != actual:
        differences.append(Difference(path, expected, actual, "value mismatch"))


# --- pixel comparison ------------------------------------------------------

@dataclass(frozen=True)
class CompareResult:
    valid: bool = False
    similarity: float = 0.0
    total_pixels: int = 0
    diff_pixels: int = 0
    mean_error: float = 0.0
    same_size: bool = False
    error: str = ""

    def passes(self, threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> bool:
        return self.valid and self.similarity >= threshold

    def as_report(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "similarity": self.similarity,
            "total_pixels": self.total_pixels,
            "diff_pixels": self.diff_pixels,
            "mean_error": self.mean_error,
            "same_size": self.same_size,
            "error": self.error,
        }


@dataclass(frozen=True)
class DiffBounds:
    valid: bool = False
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    diff_pixels: int = 0


@dataclass(frozen=True)
class ContentStats:
    valid: bool = False
    width: int = 0
    height: int = 0
    total_pixels: int = 0
    unique_colors: int = 0
    unique_colors_capped: bool = False
    luminance_mean: float = 0.0
    luminance_stddev: float = 0.0
    alpha_mean: float = 0.0
    opaque_coverage: float = 0.0
    non_background_coverage: float = 0.0
    error: str = ""

    def passes_content_floor(self, min_unique_colors: int = 16, min_luminance_stddev: float = 1.0,
                             min_non_background_coverage: float = 0.05,
                             min_opaque_coverage: float = 0.95) -> bool:
        return (
            self.valid
            and self.unique_colors >= min_unique_colors
            and self.luminance_stddev >= min_luminance_stddev
            and self.non_background_coverage >= min_non_background_coverage
            and self.opaque_coverage >= min_opaque_coverage
        )


def _decode(png: bytes | RgbaImage) -> RgbaImage:
    return png if isinstance(png, RgbaImage) else decode_png_bytes(png)


def _check_tolerance(tolerance: int) -> None:
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 255:
        raise ValueError("pixel tolerance must be an integer from 0 to 255")


def _differs(left: bytes, right: bytes, tolerance: int) -> bool:
    # Alpha is a channel: a capture that lost its transparency is not a match.
    return (
        abs(left[0] - right[0]) > tolerance or abs(left[1] - right[1]) > tolerance
        or abs(left[2] - right[2]) > tolerance or abs(left[3] - right[3]) > tolerance
    )


def compare_pixels(reference: bytes | RgbaImage, rendered: bytes | RgbaImage,
                   tolerance: int = DEFAULT_CHANNEL_TOLERANCE) -> CompareResult:
    """Compare two PNGs.

    A pixel differs when any channel is further apart than ``tolerance``.
    ``similarity`` is the share of overlapping pixels that do not differ. When
    the sizes disagree only the overlap is compared and the similarity is scaled
    by the overlap's share of the larger image, so a wrong-sized render can
    never score as a clean match.
    """
    _check_tolerance(tolerance)
    try:
        ref = _decode(reference)
    except PngError as error:
        return CompareResult(error=f"Failed to decode reference image: {error}")
    try:
        ren = _decode(rendered)
    except PngError as error:
        return CompareResult(error=f"Failed to decode rendered image: {error}")

    width = min(ref.width, ren.width)
    height = min(ref.height, ren.height)
    total = width * height
    same_size = (ref.width, ref.height) == (ren.width, ren.height)
    diff_count = 0
    error_sum = 0
    if same_size and ref.pixels == ren.pixels:
        pass
    else:
        for row in range(height):
            ref_row = ref.pixels[row * ref.width * 4:(row * ref.width + width) * 4]
            ren_row = ren.pixels[row * ren.width * 4:(row * ren.width + width) * 4]
            if ref_row == ren_row:
                continue
            for column in range(width):
                offset = column * 4
                left = ref_row[offset:offset + 4]
                right = ren_row[offset:offset + 4]
                if left == right:
                    continue
                error_sum += (
                    abs(left[0] - right[0]) + abs(left[1] - right[1]) + abs(left[2] - right[2])
                )
                if _differs(left, right, tolerance):
                    diff_count += 1
    similarity = 1.0 - diff_count / total
    if not same_size:
        similarity *= total / max(ref.width * ref.height, ren.width * ren.height)
    return CompareResult(
        valid=True, similarity=similarity, total_pixels=total, diff_pixels=diff_count,
        mean_error=error_sum / 3.0 / total, same_size=same_size,
    )


def diff_image(reference: bytes | RgbaImage, rendered: bytes | RgbaImage,
               tolerance: int = DEFAULT_CHANNEL_TOLERANCE) -> RgbaImage:
    """Render where two captures disagree.

    Matching pixels are dimmed to a third of the reference, differing pixels are
    red, and any area outside the overlap is magenta so a size mismatch is
    visible rather than clipped away.
    """
    _check_tolerance(tolerance)
    ref = _decode(reference)
    ren = _decode(rendered)
    out_w, out_h = max(ref.width, ren.width), max(ref.height, ren.height)
    cmp_w, cmp_h = min(ref.width, ren.width), min(ref.height, ren.height)
    pixels = bytearray(out_w * out_h * 4)
    for y in range(out_h):
        for x in range(out_w):
            destination = (y * out_w + x) * 4
            if x >= cmp_w or y >= cmp_h:
                pixels[destination:destination + 4] = b"\xff\x00\xff\xff"
                continue
            left = ref.pixels[(y * ref.width + x) * 4:(y * ref.width + x) * 4 + 4]
            right = ren.pixels[(y * ren.width + x) * 4:(y * ren.width + x) * 4 + 4]
            if _differs(left, right, tolerance):
                pixels[destination:destination + 4] = bytes((
                    255, min(80 + abs(left[0] - right[0]), 255),
                    min(80 + abs(left[2] - right[2]), 255), 255,
                ))
            else:
                pixels[destination:destination + 4] = bytes((left[0] // 3, left[1] // 3, left[2] // 3, 255))
    return RgbaImage(out_w, out_h, bytes(pixels))


def diff_bounds(reference: bytes | RgbaImage, rendered: bytes | RgbaImage,
                tolerance: int = DEFAULT_CHANNEL_TOLERANCE) -> DiffBounds:
    """Bounding box of the differing pixels in the overlap; invalid when none differ."""
    _check_tolerance(tolerance)
    try:
        ref = _decode(reference)
        ren = _decode(rendered)
    except PngError:
        return DiffBounds()
    width, height = min(ref.width, ren.width), min(ref.height, ren.height)
    min_x, min_y, max_x, max_y, count = width, height, 0, 0, 0
    for y in range(height):
        for x in range(width):
            left = ref.pixels[(y * ref.width + x) * 4:(y * ref.width + x) * 4 + 4]
            right = ren.pixels[(y * ren.width + x) * 4:(y * ren.width + x) * 4 + 4]
            if _differs(left, right, tolerance):
                count += 1
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    if not count:
        return DiffBounds()
    return DiffBounds(True, min_x, min_y, max_x - min_x + 1, max_y - min_y + 1, count)


def content_stats(png: bytes | RgbaImage) -> ContentStats:
    """Summarize a capture so an empty frame cannot pass as a stable match."""
    try:
        image = _decode(png)
    except PngError as error:
        return ContentStats(error=f"Failed to decode screenshot image: {error}")
    total = image.width * image.height
    counts: dict[bytes, int] = {}
    capped = False
    luminance_sum = luminance_sq = alpha_sum = 0.0
    opaque = 0
    for index in range(total):
        key = image.pixels[index * 4:index * 4 + 4]
        if key in counts:
            counts[key] += 1
        elif len(counts) < MAX_TRACKED_COLORS:
            counts[key] = 1
        else:
            capped = True
        luminance = 0.2126 * key[0] + 0.7152 * key[1] + 0.0722 * key[2]
        luminance_sum += luminance
        luminance_sq += luminance * luminance
        alpha_sum += key[3]
        if key[3] >= 250:
            opaque += 1
    mean = luminance_sum / total
    variance = max(0.0, luminance_sq / total - mean * mean)
    dominant = max(counts.values())
    return ContentStats(
        valid=True, width=image.width, height=image.height, total_pixels=total,
        unique_colors=len(counts), unique_colors_capped=capped, luminance_mean=mean,
        luminance_stddev=variance ** 0.5, alpha_mean=alpha_sum / total,
        opaque_coverage=opaque / total, non_background_coverage=1.0 - dominant / total,
    )
