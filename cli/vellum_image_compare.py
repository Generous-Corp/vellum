#!/usr/bin/env python3
"""Deterministic, dependency-free PNG comparison for visual proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vellum_png import PngError, RgbaImage, read_png, write_png


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    width: int
    height: int


def _validate_crop(crop: Crop, image: RgbaImage) -> None:
    if any(isinstance(value, bool) for value in (crop.x, crop.y, crop.width, crop.height)):
        raise PngError("Comparison crop coordinates must be integers")
    if crop.x < 0 or crop.y < 0 or crop.width <= 0 or crop.height <= 0:
        raise PngError("Comparison crop must be positive and non-negative")
    if crop.x + crop.width > image.width or crop.y + crop.height > image.height:
        raise PngError("Comparison crop exceeds image dimensions")


def _pixel(image: RgbaImage, x: int, y: int) -> bytes:
    offset = (y * image.width + x) * 4
    return image.pixels[offset:offset + 4]


def compare_images(reference: RgbaImage, actual: RgbaImage, *, threshold: int = 0,
                   crop: Crop | None = None) -> tuple[dict[str, Any], RgbaImage]:
    if reference.width != actual.width or reference.height != actual.height:
        raise PngError("Reference and actual image dimensions differ")
    if isinstance(threshold, bool) or threshold < 0 or threshold > 255:
        raise PngError("Comparison threshold must be between 0 and 255")
    selected = crop or Crop(0, 0, reference.width, reference.height)
    _validate_crop(selected, reference)
    _validate_crop(selected, actual)

    differing = 0
    total_error = 0
    maximum = 0
    diff = bytearray(selected.width * selected.height * 4)
    for row in range(selected.height):
        for column in range(selected.width):
            left = _pixel(reference, selected.x + column, selected.y + row)
            right = _pixel(actual, selected.x + column, selected.y + row)
            errors = [abs(a - b) for a, b in zip(left, right)]
            peak = max(errors)
            total_error += sum(errors)
            maximum = max(maximum, peak)
            destination = (row * selected.width + column) * 4
            if peak > threshold:
                differing += 1
                diff[destination:destination + 4] = bytes((*errors[:3], 255))
            else:
                diff[destination:destination + 4] = b"\0\0\0\xff"

    pixels = selected.width * selected.height
    channels = pixels * 4
    mean_error = total_error / channels
    similarity = 1.0 - (mean_error / 255.0)
    report = {
        "schema": "vellum.pixel-comparison.v1",
        "dimensions": {"width": reference.width, "height": reference.height},
        "crop": {"x": selected.x, "y": selected.y, "width": selected.width, "height": selected.height},
        "compared_pixels": pixels,
        "differing_pixels": differing,
        "mean_absolute_error": mean_error,
        "max_channel_error": maximum,
        "similarity": similarity,
        "threshold": threshold,
        "passed": differing == 0,
    }
    return report, RgbaImage(selected.width, selected.height, bytes(diff))


def compare_paths(reference_path, actual_path, *, threshold: int = 0,
                  crop: Crop | None = None, diff_path=None) -> dict[str, Any]:
    reference = read_png(reference_path)
    actual = read_png(actual_path)
    report, diff = compare_images(reference, actual, threshold=threshold, crop=crop)
    if diff_path is not None:
        write_png(diff_path, diff)
        report["diff_png"] = str(diff_path)
    return report
