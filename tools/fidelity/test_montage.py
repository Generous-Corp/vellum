#!/usr/bin/env python3
"""Tests for the labeled comparison montage, on a synthetic panel set."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import montage  # noqa: E402
from bitmap_font import GLYPHS, MISSING, glyph, text_width  # noqa: E402
from fidelity_image import Image, open_image  # noqa: E402

sys.path.insert(0, str(HERE.parents[1] / "cli"))
from vellum_png import read_png, write_png  # noqa: E402

FG = (235, 235, 240)


def solid(path: Path, width: int, height: int, color: tuple[int, int, int]) -> Path:
    write_png(path, Image(width, height, [color] * (width * height), "RGB").to_rgba())
    return path


class MontageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.reference = solid(self.root / "reference.png", 40, 20, (200, 30, 30))
        self.render = solid(self.root / "render.png", 40, 20, (30, 30, 200))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self, name: str, **options) -> Image:
        out = self.root / name
        montage.build_montage([("Reference", self.reference), ("Render", self.render)],
                              out, panel_width=40, **options)
        return open_image(out)

    def pixel(self, image: Image, x: int, y: int) -> tuple[int, ...]:
        return image.getdata()[y * image.width + x]

    def title_ink(self, image: Image, x: int, y: int, width: int, height: int) -> int:
        return sum(1 for yy in range(y, y + height) for xx in range(x, x + width)
                   if self.pixel(image, xx, yy) == FG)

    def test_stacked_layout_places_titles_above_panels(self) -> None:
        image = self.build("stacked.png")
        # Width: pad + (panel + 2 pad); height: pad + 2 * (panel + title + pad).
        self.assertEqual(image.size, (14 + 40 + 28, 14 + 2 * (20 + 36 + 14)))
        self.assertEqual(self.pixel(image, 14, 14 + 36), (200, 30, 30))
        self.assertEqual(self.pixel(image, 14, 14 + 70 + 36), (30, 30, 200))
        self.assertEqual(self.pixel(image, 14 + 39, 14 + 1), (12, 12, 14))
        self.assertGreater(self.title_ink(image, 14, 14, 41, 36), 0)
        self.assertGreater(self.title_ink(image, 14, 14 + 70, 41, 36), 0)

    def test_labels_can_be_turned_off(self) -> None:
        image = self.build("plain.png", labels=False)
        self.assertEqual(image.size, (82, 14 + 2 * (20 + 14)))
        self.assertEqual(self.title_ink(image, 0, 0, image.width, image.height), 0)
        self.assertEqual(self.pixel(image, 14, 14), (200, 30, 30))

    def test_columns_place_panels_side_by_side(self) -> None:
        image = self.build("columns.png", columns=2)
        self.assertEqual(image.size, (14 + 2 * 68, 14 + 20 + 36 + 14))
        self.assertEqual(self.pixel(image, 14 + 68, 14 + 36), (30, 30, 200))

    def test_different_labels_draw_different_ink(self) -> None:
        a = self.build("a.png")
        out = self.root / "b.png"
        montage.build_montage([("Candidate", self.reference), ("Render", self.render)],
                              out, panel_width=40)
        b = open_image(out)
        self.assertNotEqual(a.getdata()[:82 * 50], b.getdata()[:82 * 50])

    def test_output_is_deterministic(self) -> None:
        first = self.root / "first.png"
        second = self.root / "second.png"
        for out in (first, second):
            montage.build_montage([("Reference", self.reference)], out, panel_width=40)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_panels_scale_to_the_panel_width(self) -> None:
        out = self.root / "scaled.png"
        montage.build_montage([("Wide", self.reference)], out, panel_width=80)
        self.assertEqual(read_png(out).width, 14 + 80 + 28)
        self.assertEqual(read_png(out).height, 14 + 40 + 36 + 14)

    def test_panel_spec_splits_on_the_first_colon(self) -> None:
        spec = f"{self.reference}:1. Figma: source"
        self.assertEqual(montage._parse_panel(spec), ("1. Figma: source", str(self.reference)))
        self.assertEqual(montage._parse_panel(str(self.render)), ("render", str(self.render)))

    def test_cli_writes_the_montage(self) -> None:
        out = self.root / "cli.png"
        self.assertEqual(montage.main(["--out", str(out), "--columns", "2",
                                       f"{self.reference}:Reference", str(self.render)]), 0)
        self.assertEqual(read_png(out).width, 14 + 2 * (1000 + 28))

    def test_font_covers_labels_and_marks_missing_glyphs(self) -> None:
        for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:-_/()%#'+=":
            self.assertIn(character, GLYPHS)
        self.assertEqual(glyph("a"), glyph("A"))
        self.assertEqual(glyph("~"), MISSING)
        self.assertEqual(text_width("AB", 2), 22)


if __name__ == "__main__":
    unittest.main()
