from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vellum_image_compare import Crop, compare_images  # noqa: E402
from vellum_png import PngError, RgbaImage  # noqa: E402


class ImageComparisonTests(unittest.TestCase):
    def image(self, pixels: bytes, width: int = 2, height: int = 1) -> RgbaImage:
        return RgbaImage(width, height, pixels)

    def test_exact_match_is_zero_error(self) -> None:
        image = self.image(bytes((10, 20, 30, 255, 40, 50, 60, 255)))
        report, diff = compare_images(image, image)
        self.assertTrue(report["passed"])
        self.assertEqual(report["differing_pixels"], 0)
        self.assertEqual(report["mean_absolute_error"], 0.0)
        self.assertEqual(diff.pixels, bytes((0, 0, 0, 255)) * 2)

    def test_difference_reports_metrics_and_diff_pixel(self) -> None:
        reference = self.image(bytes((10, 20, 30, 255, 40, 50, 60, 255)))
        actual = self.image(bytes((15, 20, 40, 255, 40, 50, 60, 255)))
        report, diff = compare_images(reference, actual)
        self.assertFalse(report["passed"])
        self.assertEqual(report["differing_pixels"], 1)
        self.assertEqual(report["max_channel_error"], 10)
        self.assertEqual(diff.pixels[:4], bytes((5, 0, 10, 255)))
        self.assertEqual(diff.pixels[4:], bytes((0, 0, 0, 255)))

    def test_threshold_and_crop_are_explicit(self) -> None:
        reference = self.image(bytes((10, 20, 30, 255, 40, 50, 60, 255)))
        actual = self.image(bytes((15, 20, 30, 255, 40, 50, 60, 255)))
        report, _ = compare_images(reference, actual, threshold=5, crop=Crop(1, 0, 1, 1))
        self.assertTrue(report["passed"])
        self.assertEqual(report["compared_pixels"], 1)

    def test_alpha_only_difference_is_visible_in_diff(self) -> None:
        reference = self.image(bytes((20, 30, 40, 255)), width=1)
        actual = self.image(bytes((20, 30, 40, 200)), width=1)
        report, diff = compare_images(reference, actual)
        self.assertFalse(report["passed"])
        self.assertEqual(report["differing_pixels"], 1)
        self.assertEqual(diff.pixels, bytes((55, 55, 55, 255)))

    def test_crop_must_fit_both_images(self) -> None:
        image = self.image(bytes((0, 0, 0, 255)) * 2)
        with self.assertRaisesRegex(PngError, "exceeds image"):
            compare_images(image, image, crop=Crop(1, 0, 2, 1))


if __name__ == "__main__":
    unittest.main()
