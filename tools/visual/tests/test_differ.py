#!/usr/bin/env python3
"""Proof for tools/visual/differ.py: thresholded pixel and semantic comparison."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "visual"))
sys.path.insert(0, str(ROOT / "cli"))

import differ  # noqa: E402
from vellum_png import RgbaImage, encode_png  # noqa: E402


def solid(width: int, height: int, color: tuple[int, int, int, int]) -> RgbaImage:
    return RgbaImage(width, height, bytes(color) * (width * height))


def with_pixel(image: RgbaImage, x: int, y: int, color: tuple[int, int, int, int]) -> RgbaImage:
    pixels = bytearray(image.pixels)
    offset = (y * image.width + x) * 4
    pixels[offset:offset + 4] = bytes(color)
    return RgbaImage(image.width, image.height, bytes(pixels))


def png(image: RgbaImage) -> bytes:
    return encode_png(image)


class PixelComparisonTests(unittest.TestCase):
    def test_identical_images_are_a_perfect_match(self) -> None:
        image = solid(4, 4, (10, 20, 30, 255))
        result = differ.compare_pixels(png(image), png(image))
        self.assertTrue(result.valid)
        self.assertEqual((result.diff_pixels, result.similarity, result.mean_error), (0, 1.0, 0.0))
        self.assertTrue(result.same_size)

    def test_tolerance_boundary_is_exclusive(self) -> None:
        base = solid(2, 2, (100, 100, 100, 255))
        off_by_ten = with_pixel(base, 0, 0, (110, 100, 100, 255))
        self.assertEqual(differ.compare_pixels(base, off_by_ten, 9).diff_pixels, 1)
        self.assertEqual(differ.compare_pixels(base, off_by_ten, 10).diff_pixels, 0)

    def test_similarity_is_the_share_of_matching_pixels(self) -> None:
        base = solid(2, 2, (0, 0, 0, 255))
        changed = with_pixel(base, 1, 1, (255, 0, 0, 255))
        result = differ.compare_pixels(base, changed, 0)
        self.assertEqual(result.diff_pixels, 1)
        self.assertAlmostEqual(result.similarity, 0.75)
        self.assertAlmostEqual(result.mean_error, 255 / 3 / 4)

    def test_passes_uses_the_threshold_inclusively(self) -> None:
        base = solid(2, 2, (0, 0, 0, 255))
        result = differ.compare_pixels(base, with_pixel(base, 0, 0, (255, 255, 255, 255)), 0)
        self.assertTrue(result.passes(0.75))
        self.assertFalse(result.passes(0.76))
        self.assertEqual(differ.DEFAULT_SIMILARITY_THRESHOLD, 0.85)
        self.assertFalse(result.passes())

    def test_alpha_only_difference_counts(self) -> None:
        base = solid(1, 1, (20, 30, 40, 255))
        result = differ.compare_pixels(base, solid(1, 1, (20, 30, 40, 100)), 0)
        self.assertEqual(result.diff_pixels, 1)

    def test_wrong_size_cannot_score_as_a_clean_match(self) -> None:
        small = solid(2, 2, (5, 5, 5, 255))
        large = solid(4, 4, (5, 5, 5, 255))
        result = differ.compare_pixels(small, large, 0)
        self.assertTrue(result.valid)
        self.assertFalse(result.same_size)
        self.assertEqual(result.diff_pixels, 0)
        self.assertAlmostEqual(result.similarity, 0.25)

    def test_undecodable_input_is_invalid_not_a_match(self) -> None:
        good = png(solid(1, 1, (0, 0, 0, 255)))
        broken = differ.compare_pixels(b"not a png", good)
        self.assertFalse(broken.valid)
        self.assertIn("reference", broken.error)
        self.assertFalse(broken.passes(0.0))
        self.assertIn("rendered", differ.compare_pixels(good, b"").error)

    def test_tolerance_must_be_a_byte(self) -> None:
        image = solid(1, 1, (0, 0, 0, 255))
        for bad in (-1, 256, True, 1.5):
            with self.assertRaises(ValueError):
                differ.compare_pixels(image, image, bad)  # type: ignore[arg-type]


class DiffArtifactTests(unittest.TestCase):
    def test_diff_image_dims_matches_and_reddens_differences(self) -> None:
        base = solid(2, 1, (90, 60, 30, 255))
        changed = with_pixel(base, 1, 0, (190, 60, 130, 255))
        diff = differ.diff_image(base, changed, 0)
        self.assertEqual(diff.pixels[0:4], bytes((30, 20, 10, 255)))
        self.assertEqual(diff.pixels[4:8], bytes((255, 180, 180, 255)))

    def test_diff_image_marks_area_outside_the_overlap_magenta(self) -> None:
        diff = differ.diff_image(solid(1, 1, (0, 0, 0, 255)), solid(2, 1, (0, 0, 0, 255)), 0)
        self.assertEqual((diff.width, diff.height), (2, 1))
        self.assertEqual(diff.pixels[4:8], bytes((255, 0, 255, 255)))

    def test_diff_bounds_boxes_the_changed_pixels(self) -> None:
        base = solid(6, 6, (0, 0, 0, 255))
        changed = with_pixel(with_pixel(base, 1, 2, (255, 0, 0, 255)), 4, 3, (0, 255, 0, 255))
        bounds = differ.diff_bounds(base, changed, 0)
        self.assertTrue(bounds.valid)
        self.assertEqual((bounds.x, bounds.y, bounds.width, bounds.height, bounds.diff_pixels), (1, 2, 4, 2, 2))

    def test_diff_bounds_is_invalid_when_nothing_differs_or_input_is_bad(self) -> None:
        base = solid(3, 3, (0, 0, 0, 255))
        self.assertFalse(differ.diff_bounds(base, base, 0).valid)
        self.assertFalse(differ.diff_bounds(b"junk", base, 0).valid)


class ContentStatsTests(unittest.TestCase):
    def test_a_stable_empty_frame_fails_the_content_floor(self) -> None:
        stats = differ.content_stats(png(solid(8, 8, (30, 30, 30, 255))))
        self.assertTrue(stats.valid)
        self.assertEqual(stats.unique_colors, 1)
        self.assertEqual(stats.non_background_coverage, 0.0)
        self.assertFalse(stats.passes_content_floor())
        # Two identical empty frames match each other perfectly; the floor is
        # the only thing that tells them from a real capture.
        empty = png(solid(8, 8, (30, 30, 30, 255)))
        self.assertEqual(differ.compare_pixels(empty, empty).similarity, 1.0)

    def test_a_varied_frame_passes_the_content_floor(self) -> None:
        pixels = bytearray()
        for index in range(32 * 32):
            pixels += bytes((index % 256, (index * 7) % 256, (index * 13) % 256, 255))
        stats = differ.content_stats(RgbaImage(32, 32, bytes(pixels)))
        self.assertGreaterEqual(stats.unique_colors, 16)
        self.assertGreater(stats.luminance_stddev, 1.0)
        self.assertTrue(stats.passes_content_floor())

    def test_transparent_frame_fails_the_opacity_floor(self) -> None:
        pixels = bytearray()
        for index in range(32 * 32):
            pixels += bytes((index % 256, (index * 7) % 256, (index * 13) % 256, 0))
        self.assertFalse(differ.content_stats(RgbaImage(32, 32, bytes(pixels))).passes_content_floor())

    def test_undecodable_png_is_invalid(self) -> None:
        stats = differ.content_stats(b"nope")
        self.assertFalse(stats.valid)
        self.assertFalse(stats.passes_content_floor())


class SemanticComparisonTests(unittest.TestCase):
    def test_numbers_within_tolerance_match(self) -> None:
        self.assertEqual(differ.compare({"a": 1.0}, {"a": 1.0005}), [])
        found = differ.compare({"a": 1.0}, {"a": 1.01})
        self.assertEqual([d.path for d in found], ["$.a"])
        self.assertIn("numeric delta", found[0].message)

    def test_structure_differences_are_named_by_path(self) -> None:
        found = differ.compare({"keep": 1, "gone": 2, "items": [1, 2]},
                               {"keep": 1, "new": 3, "items": [1]})
        self.assertEqual(
            sorted((d.path, d.message) for d in found),
            [("$.gone", "missing key"), ("$.items", "array length mismatch"), ("$.new", "unexpected key")],
        )

    def test_booleans_are_not_numbers(self) -> None:
        self.assertEqual(len(differ.compare({"on": True}, {"on": 1})), 1)
        self.assertEqual(differ.compare({"on": True}, {"on": True}), [])

    def test_tolerance_comes_from_the_fixture(self) -> None:
        self.assertEqual(differ.tolerance_from_fixture({"tolerance": {"semantic": 0.5}}), 0.5)
        self.assertEqual(differ.tolerance_from_fixture({"tolerance": {"rect": 2}}), 2.0)
        self.assertEqual(differ.tolerance_from_fixture({"tolerance": {"semantic": "x"}}), 0.001)
        self.assertEqual(differ.tolerance_from_fixture(None), 0.001)

    def test_format_differences_truncates(self) -> None:
        found = differ.compare(list(range(5)), list(range(5, 10)))
        text = differ.format_differences(found, limit=2)
        self.assertEqual(len(text.splitlines()), 3)
        self.assertIn("3 more difference(s)", text)
        self.assertEqual(differ.format_differences([]), "")

    def test_byte_difference_reports_offset_and_length(self) -> None:
        self.assertEqual(differ.format_byte_difference(b"abc", b"abc"), "")
        self.assertIn("offset 1", differ.format_byte_difference(b"abc", b"aXc"))
        self.assertIn("length mismatch", differ.format_byte_difference(b"ab", b"abc"))


if __name__ == "__main__":
    unittest.main()
