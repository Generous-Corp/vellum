#!/usr/bin/env python3
"""Tests for reference_diff.py: helper scoring, the CLI flow, and a synthetic
parity set of real PNG pairs run through the command."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("reference_diff.py")
SPEC = importlib.util.spec_from_file_location("reference_diff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
diff = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diff
SPEC.loader.exec_module(diff)

sys.path.insert(0, str(SCRIPT.parent))
from fidelity_image import Image, open_image  # noqa: E402
sys.path.insert(0, str(SCRIPT.parents[2] / "cli"))
from vellum_png import write_png  # noqa: E402


class FakeImage:
    def __init__(
        self,
        pixels: list[tuple[int, int, int]],
        size: tuple[int, int] | None = None,
        histogram: list[int] | None = None,
    ) -> None:
        self._pixels = pixels
        self.size = size or (len(pixels), 1)
        self._histogram = histogram
        self.resized_to: tuple[int, int] | None = None
        self.converted_to: str | None = None

    def getdata(self) -> list[tuple[int, int, int]]:
        return self._pixels

    def histogram(self) -> list[int]:
        if self._histogram is not None:
            return self._histogram
        hist = [0] * 768
        for r, g, b in self._pixels:
            hist[r] += 1
            hist[256 + g] += 1
            hist[512 + b] += 1
        return hist

    def convert(self, mode: str) -> "FakeImage":
        self.converted_to = mode
        return self

    def resize(self, target: tuple[int, int], _resampling: object = None) -> "FakeImage":
        self.resized_to = target
        self.size = target
        return self


class FakeRGBAImage:
    def __init__(self, pixels: list[tuple[int, int, int, int]], size: tuple[int, int]) -> None:
        self._pixels = pixels
        self.size = size

    def getdata(self) -> list[tuple[int, int, int, int]]:
        return self._pixels


class HelperScoringTests(unittest.TestCase):
    @staticmethod
    def _panel_pixels(*, shift: int = 0, fill: tuple[int, int, int] = (220, 220, 220),
                      border_width: int = 2, rounded: bool = False) -> FakeImage:
        """Build a deterministic panel for strict planted-negative controls."""
        width, height = 64, 48
        background = (24, 28, 36)
        pixels = [background] * (width * height)
        left, top, right, bottom = 14 + shift, 10, 49 + shift, 37
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                corner = rounded and (
                    (x - left < 4 and y - top < 4)
                    or (right - x < 4 and y - top < 4)
                    or (x - left < 4 and bottom - y < 4)
                    or (right - x < 4 and bottom - y < 4)
                )
                if corner:
                    continue
                edge = (x - left < border_width or right - x < border_width
                        or y - top < border_width or bottom - y < border_width)
                pixels[y * width + x] = (120, 130, 145) if edge else fill
        return FakeImage(pixels, size=(width, height))

    def test_histogram_similarity_handles_identity_and_empty_norm(self) -> None:
        a = FakeImage([(0, 0, 0)], histogram=[1, 2, 3])
        self.assertAlmostEqual(diff.histogram_similarity(a, a), 1.0)
        self.assertEqual(diff.histogram_similarity(FakeImage([], histogram=[]), a), 0.0)

    def test_strict_floor_controls_keep_healthy_edge_variance_advisory(self) -> None:
        reference = self._panel_pixels()
        healthy = list(reference._pixels)
        # A small edge-only tonal change models a renderer AA disagreement. It
        # must remain inside the strict floor while still being visible to the
        # residual profiler; it never relaxes the production threshold.
        for x in range(14, 50):
            for y in (10, 37):
                index = y * 64 + x
                red, green, blue = healthy[index]
                healthy[index] = (min(255, red + 12), min(255, green + 12), min(255, blue + 12))
        healthy_image = FakeImage(healthy, size=(64, 48))
        self.assertGreaterEqual(diff.similarity_score(reference, healthy_image), 0.995)
        self.assertGreater(diff.residual_profile(reference, healthy_image)["differing_pixels"], 0)

    def test_all_gate_planted_negatives_fail_strict_floor(self) -> None:
        reference = self._panel_pixels()
        controls = {
            "one-pixel-shift": self._panel_pixels(shift=1),
            "color-change": self._panel_pixels(fill=(90, 220, 160)),
            "missing-border": self._panel_pixels(border_width=0),
            "wrong-weight": self._panel_pixels(border_width=4),
            "corner-radius": self._panel_pixels(rounded=True),
        }
        for name, candidate in controls.items():
            with self.subTest(control=name):
                self.assertLess(diff.similarity_score(reference, candidate), 0.995)
                profile = diff.residual_profile(reference, candidate)
                self.assertGreater(profile["differing_pixels"], 0)

    def test_mean_pixel_distance_scores_identical_different_and_mismatched_lengths(self) -> None:
        white = FakeImage([(255, 255, 255)])
        black = FakeImage([(0, 0, 0)])
        self.assertEqual(diff.mean_pixel_distance(white, white), 1.0)
        self.assertLess(diff.mean_pixel_distance(white, black), 0.001)
        self.assertEqual(diff.mean_pixel_distance(white, FakeImage([])), 0.0)
        self.assertGreater(diff.mean_pixel_distance(white, FakeImage([(128, 128, 128)])), 0.49)
        self.assertLess(diff.mean_pixel_distance(white, FakeImage([(128, 128, 128)])), 0.51)

    def test_blank_detection_uses_near_black_ratio(self) -> None:
        self.assertTrue(diff.is_blank(FakeImage([(0, 0, 0)] * 96 + [(255, 255, 255)] * 4)))
        self.assertFalse(diff.is_blank(FakeImage([(0, 0, 0)] * 94 + [(255, 255, 255)] * 6)))

    def test_dominant_colors_are_quantized_and_sorted(self) -> None:
        img = FakeImage([(33, 63, 95), (34, 64, 96), (250, 250, 250)])
        colors = diff.dominant_colors(img, k=2)
        self.assertEqual(colors, [(32, 32, 64), (32, 64, 96)])
        self.assertEqual(len(colors), 2)
        self.assertNotIn((224, 224, 224), colors)

    def test_residual_profile_detects_alpha_only_changes(self) -> None:
        reference = FakeRGBAImage([(30, 40, 50, 255)] * 4, (2, 2))
        candidate = FakeRGBAImage([(30, 40, 50, 128)] * 4, (2, 2))
        result = diff.residual_profile(reference, candidate)
        self.assertEqual(result["classification"], "alpha-only")
        self.assertEqual(result["differing_pixels"], 4)
        self.assertEqual(result["alpha_only_pixels"], 4)

    def test_residual_profile_detects_small_translation(self) -> None:
        white = (255, 255, 255, 255)
        black = (0, 0, 0, 255)
        reference = [white] * 32
        candidate = [white] * 32
        for y in range(4):
            reference[y * 8 + 2] = black
            candidate[y * 8 + 3] = black
        result = diff.residual_profile(
            FakeRGBAImage(reference, (8, 4)), FakeRGBAImage(candidate, (8, 4)), shift_radius=2)
        self.assertEqual(result["classification"], "placement-drift")
        self.assertEqual(result["shift_probe"]["best_shift"]["dx"], 1)
        self.assertGreater(result["shift_probe"]["improvement"], 0.5)

    def test_residual_profile_identity_and_rgb_inputs_are_supported(self) -> None:
        image = FakeRGBAImage([(30, 40, 50, 255)] * 4, (2, 2))
        result = diff.residual_profile(image, image)
        self.assertEqual(result["classification"], "identical")
        self.assertEqual(result["differing_fraction"], 0.0)

    def test_residual_profile_identifies_edge_concentrated_raster_difference(self) -> None:
        white = (255, 255, 255, 255)
        reference = [white] * 32
        candidate = [white] * 32
        for y in range(4):
            reference[y * 8 + 2] = (0, 0, 0, 255)
            candidate[y * 8 + 2] = (96, 96, 96, 255)
        result = diff.residual_profile(
            FakeRGBAImage(reference, (8, 4)), FakeRGBAImage(candidate, (8, 4)))
        self.assertEqual(result["classification"], "text-edge/rasterization")
        self.assertEqual(result["edge_band_pixels"], result["differing_pixels"])

    def test_load_normalized_resizes_only_when_the_size_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "image.png"
            write_png(path, Image(2, 2, [(1, 2, 3)] * 4, "RGB").to_rgba())
            same = diff.load_normalized(path, (2, 2))
            self.assertEqual(same.size, (2, 2))
            self.assertEqual(same.getdata()[0], (1, 2, 3))
            scaled = diff.load_normalized(path, (4, 6))
            self.assertEqual(scaled.size, (4, 6))
            # Lanczos weights sum to one, so a flat image stays flat.
            self.assertEqual(set(scaled.getdata()), {(1, 2, 3)})
            self.assertEqual(diff.load_normalized(path, (2, 2), mode="RGBA").getdata()[0], (1, 2, 3, 255))


class MainFlowTests(unittest.TestCase):
    def _run_main(
        self,
        argv: list[str],
        *,
        ref: FakeImage | None = None,
        cand: FakeImage | None = None,
    ) -> tuple[int, str, str]:
        ref_img = ref or FakeImage([(255, 255, 255)])
        cand_img = cand or FakeImage([(255, 255, 255)])
        stdout = io.StringIO()
        stderr = io.StringIO()
        written = None
        with tempfile.TemporaryDirectory() as tdir:
            root = Path(tdir)
            ref_path = root / "ref.png"
            cand_path = root / "cand.png"
            ref_path.write_text("ref", encoding="utf-8")
            cand_path.write_text("cand", encoding="utf-8")
            args = [str(ref_path), str(cand_path), *argv]
            with mock.patch.object(sys, "argv", [str(SCRIPT), *args]), \
                 mock.patch.object(diff, "load_normalized", side_effect=[ref_img, cand_img]), \
                 contextlib.redirect_stdout(stdout), \
                 contextlib.redirect_stderr(stderr):
                rc = diff.main()
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_main_prints_pass_verdict_for_matching_images(self) -> None:
        rc, stdout, stderr = self._run_main(["--threshold", "0.5"])
        self.assertEqual(rc, 0)
        self.assertIn("PASS", stdout)
        self.assertEqual(stderr, "")

    def test_main_fails_blank_candidate_even_when_pixels_match(self) -> None:
        blank = FakeImage([(0, 0, 0)] * 100)
        rc, stdout, _stderr = self._run_main([], ref=blank, cand=blank)
        self.assertEqual(rc, 1)
        self.assertIn("tier=blank", stdout)
        self.assertIn("candidate is essentially blank", stdout)
        self.assertIn("FAIL", stdout)
        self.assertIn("ref dominant colors", stdout)

    def test_main_json_output_contains_metrics_and_diagnosis(self) -> None:
        rc, stdout, _stderr = self._run_main(["--json", "--threshold", "0.99"])
        payload = json.loads(stdout)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["tier"], "pass")
        self.assertEqual(payload["score"], 1.0)
        self.assertEqual(payload["threshold"], 0.99)
        self.assertFalse(payload["blank_candidate"])

    def test_main_writes_residual_profile_without_changing_score(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        score_ref = FakeImage([(255, 255, 255)])
        score_cand = FakeImage([(255, 255, 255)])
        residual_ref = FakeRGBAImage([(30, 40, 50, 255)] * 4, (2, 2))
        residual_cand = FakeRGBAImage([(30, 40, 50, 128)] * 4, (2, 2))
        with tempfile.TemporaryDirectory() as tdir:
            root = Path(tdir)
            ref_path = root / "ref.png"
            cand_path = root / "cand.png"
            residual_path = root / "residual.json"
            ref_path.write_text("ref", encoding="utf-8")
            cand_path.write_text("cand", encoding="utf-8")
            with mock.patch.object(
                sys,
                "argv",
                [str(SCRIPT), str(ref_path), str(cand_path), "--json", "--residual-json", str(residual_path)],
            ), mock.patch.object(
                diff,
                "load_normalized",
                side_effect=[score_ref, score_cand, residual_ref, residual_cand],
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = diff.main()
            written = json.loads(residual_path.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["score"], 1.0)
        self.assertEqual(payload["residual_profile"]["classification"], "alpha-only")
        assert written is not None
        self.assertEqual(written["alpha_only_pixels"], 4)

    def test_main_reports_missing_file_before_loading_images(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", [str(SCRIPT), "missing-ref.png", "missing-cand.png"]), \
             mock.patch.object(diff, "load_normalized") as load_normalized, \
             contextlib.redirect_stdout(stdout), \
             contextlib.redirect_stderr(stderr):
            rc = diff.main()
        self.assertEqual(rc, 2)
        self.assertIn("file not found", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        load_normalized.assert_not_called()

    def test_main_rejects_malformed_size(self) -> None:
        rc, _stdout, stderr = self._run_main(["--size", "12-by-8"])
        self.assertEqual(rc, 2)
        self.assertIn("malformed --size", stderr)

    def test_main_rejects_threshold_outside_normalized_range(self) -> None:
        for threshold in ("-0.1", "1.1", "nan"):
            with self.subTest(threshold=threshold):
                rc, stdout, stderr = self._run_main(["--threshold", threshold])
                self.assertEqual(rc, 2)
                self.assertEqual(stdout, "")
                self.assertIn("--threshold", stderr)

    def test_main_reports_load_errors_as_compare_errors(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tdir:
            root = Path(tdir)
            ref_path = root / "ref.png"
            cand_path = root / "cand.png"
            ref_path.write_text("ref", encoding="utf-8")
            cand_path.write_text("cand", encoding="utf-8")
            with mock.patch.object(sys, "argv", [str(SCRIPT), str(ref_path), str(cand_path)]), \
                 mock.patch.object(diff, "load_normalized", side_effect=RuntimeError("bad image")), \
                 contextlib.redirect_stdout(stdout), \
                 contextlib.redirect_stderr(stderr):
                rc = diff.main()
        self.assertEqual(rc, 2)
        self.assertIn("failed to load images", stderr.getvalue())


class SyntheticParityTests(unittest.TestCase):
    """Real PNG pairs through the command: each planted defect lands in its tier."""

    WIDTH, HEIGHT = 48, 32

    def panel(self, *, fill=(220, 220, 220), swap=False, blank=False) -> Image:
        background = (0, 0, 0) if blank else (24, 28, 36)
        pixels = []
        for y in range(self.HEIGHT):
            for x in range(self.WIDTH):
                inside = 8 <= x < 40 and 6 <= y < 26
                if swap:
                    inside = not inside
                pixels.append(fill if inside and not blank else background)
        return Image(self.WIDTH, self.HEIGHT, pixels, "RGB")

    def run_tool(self, reference: Image, candidate: Image, *extra: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_png(root / "reference.png", reference.to_rgba())
            write_png(root / "candidate.png", candidate.to_rgba())
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(root / "reference.png"),
                 str(root / "candidate.png"), "--json", "--size",
                 f"{self.WIDTH}x{self.HEIGHT}", *extra],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(completed.stderr, "")
        return completed.returncode, json.loads(completed.stdout)

    def test_identical_render_passes(self) -> None:
        code, report = self.run_tool(self.panel(), self.panel())
        self.assertEqual(code, 0)
        self.assertEqual((report["tier"], report["score"]), ("pass", 1.0))

    def test_blank_render_fails_as_blank(self) -> None:
        code, report = self.run_tool(self.panel(), self.panel(blank=True))
        self.assertEqual(code, 1)
        self.assertEqual(report["tier"], "blank")
        self.assertTrue(report["blank_candidate"])

    def test_recolored_render_fails(self) -> None:
        code, report = self.run_tool(self.panel(), self.panel(fill=(40, 200, 90)))
        self.assertEqual(code, 1)
        self.assertLess(report["score"], 0.85)

    def test_the_histogram_half_is_position_blind(self) -> None:
        # A one-pixel translation keeps the histogram exactly; only the pixel
        # half of the score sees the move.
        shifted = Image(self.WIDTH, self.HEIGHT,
                        self.panel().getdata()[1:] + self.panel().getdata()[:1], "RGB")
        _code, report = self.run_tool(self.panel(), shifted)
        self.assertEqual(report["histogram_similarity"], 1.0)
        self.assertLess(report["pixel_similarity"], 1.0)

    def test_a_smaller_candidate_is_compared_at_the_reference_size(self) -> None:
        small = Image(self.WIDTH // 2, self.HEIGHT // 2,
                      [(24, 28, 36)] * (self.WIDTH // 2 * self.HEIGHT // 2), "RGB")
        code, report = self.run_tool(self.panel(), small)
        self.assertEqual(code, 1)
        self.assertLess(report["score"], 1.0)

    def test_unreadable_candidate_cannot_be_compared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_png(root / "reference.png", self.panel().to_rgba())
            (root / "candidate.png").write_bytes(b"not a png")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(root / "reference.png"), str(root / "candidate.png")],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("failed to load images", completed.stderr)



PILLOW_5X3 = [
    (0, 0, 4), (11, 0, 0), (144, 40, 36), (254, 210, 187), (255, 255, 255),
    (128, 0, 6), (100, 56, 0), (64, 160, 16), (113, 155, 178), (132, 128, 255),
    (255, 0, 8), (189, 124, 0), (0, 255, 0), (0, 99, 169), (0, 0, 255),
]


# Pillow 13.0.0 output for a 4x9 pattern resized to 5x3. The height shrinks by
# more than twice the width, so Pillow runs the vertical pass first.
TALL_PATTERN = [((x * 60 + y * 7) % 256, (x * 13 + y * 40) % 256, (x * 90 + y * 25) % 256)
                for y in range(9) for x in range(4)]
PILLOW_TALL_LANCZOS = [
    (5, 36, 21), (44, 43, 67), (97, 60, 206), (150, 77, 174), (189, 84, 25),
    (26, 171, 92), (65, 183, 180), (118, 176, 171), (171, 169, 100), (210, 181, 116),
    (47, 113, 180), (86, 131, 129), (139, 98, 64), (192, 64, 115), (231, 82, 197),
]
PILLOW_TALL_BICUBIC = [
    (4, 42, 23), (46, 51, 85), (97, 64, 184), (148, 77, 175), (190, 86, 33),
    (25, 165, 96), (67, 176, 175), (118, 172, 159), (169, 167, 105), (211, 178, 114),
    (46, 112, 176), (88, 126, 121), (139, 97, 82), (190, 68, 114), (232, 82, 191),
]


class PillowParityTests(unittest.TestCase):
    """At the default 1320x860 the score matches the Pillow-based scorer."""

    @staticmethod
    def panel(shift: int, fill: tuple[int, int, int]) -> Image:
        width, height = 64, 48
        pixels = []
        for y in range(height):
            for x in range(width):
                inside = 14 + shift <= x <= 49 + shift and 10 <= y <= 37
                edge = inside and (x - 14 - shift < 2 or 49 + shift - x < 2
                                   or y - 10 < 2 or 37 - y < 2)
                pixels.append((120, 130, 145) if edge else fill if inside else (24, 28, 36))
        return Image(width, height, pixels, "RGB")

    def test_default_size_score_matches_the_pillow_scorer(self) -> None:
        # Recorded from Pulp's diff_against_reference.py (Pillow 13.0.0) on
        # this pair: a 64x48 panel against one shifted 3 px and recolored.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_png(root / "reference.png", self.panel(0, (220, 220, 220)).to_rgba())
            write_png(root / "candidate.png", self.panel(3, (200, 180, 120)).to_rgba())
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(root / "reference.png"),
                 str(root / "candidate.png"), "--json"],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["histogram_similarity"], 0.9109)
        self.assertEqual(report["pixel_similarity"], 0.907)
        self.assertEqual(report["score"], 0.9085)
        self.assertEqual(report["tier"], "pass")
        self.assertEqual(report["dominant_colors_cand"],
                         [[0, 0, 32], [192, 160, 96], [96, 128, 128], [0, 0, 0], [128, 128, 128]])

    def test_lanczos_resize_matches_recorded_pillow_pixels(self) -> None:
        # Pillow 13.0.0 LANCZOS output for a 3x2 gradient resized to 5x3.
        source = Image(3, 2, [(0, 0, 0), (128, 64, 32), (255, 255, 255),
                              (255, 0, 0), (0, 255, 0), (0, 0, 255)], "RGB")
        self.assertEqual(source.resize_lanczos((5, 3)).getdata(), PILLOW_5X3)

    def test_pass_order_matches_pillow_when_the_height_shrinks_most(self) -> None:
        tall = Image(4, 9, TALL_PATTERN, "RGB")
        self.assertEqual(tall.resize_lanczos((5, 3)).getdata(), PILLOW_TALL_LANCZOS)
        self.assertEqual(tall.resize_bicubic((5, 3)).getdata(), PILLOW_TALL_BICUBIC)


if __name__ == "__main__":
    unittest.main()
