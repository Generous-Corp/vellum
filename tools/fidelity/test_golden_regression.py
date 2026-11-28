#!/usr/bin/env python3
"""Tests for golden_regression.py on synthetic baselines, in both input modes."""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "golden_regression.py"
SPEC = importlib.util.spec_from_file_location("golden_regression", SCRIPT)
golden = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(golden)

sys.path.insert(0, str(HERE))
from fidelity_image import Image, open_image  # noqa: E402
sys.path.insert(0, str(HERE.parents[1] / "cli"))
from vellum_png import write_png  # noqa: E402


def panel(width: int = 60, height: int = 40, *, shift: int = 0, lift: int = 0,
          marker: bool = False) -> Image:
    """A dark frame with a light card; `lift` brightens every pixel evenly."""
    pixels = []
    for y in range(height):
        for x in range(width):
            color = (20, 22, 30)
            if 10 + shift <= x < width - 20 + shift and 8 <= y < height - 10:
                color = (200, 200, 210)
            if marker and 4 <= x < 12 and 2 <= y < 6:
                color = (250, 40, 40)
            pixels.append(tuple(min(255, c + lift) for c in color))
    return Image(width, height, pixels, "RGB")


# Recorded from Pulp's golden_regression.py compare() (Pillow 13.0.0, numpy
# 2.5.5) on these same synthetic pairs.
PULP_LINES = {
    "shift": "golden: pixels>24: 9.167% (max 1.00%) | edge-agree: 0.5728 (min 0.850) | REGRESSION",
    "marker": "golden: pixels>24: 1.333% (max 1.00%) | edge-agree: 0.8333 (min 0.850) | REGRESSION",
    "resize": "golden: pixels>24: 22.000% (max 1.00%) | edge-agree: 0.1148 (min 0.850) | REGRESSION",
    "lift": "golden: pixels>24: 0.000% (max 1.00%) | edge-agree: 1.0000 (min 0.850) | PASS",
}


class GoldenRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.baseline = self.png("baseline.png", panel())

    def tearDown(self) -> None:
        self.directory.cleanup()

    def png(self, name: str, image: Image) -> Path:
        path = self.root / name
        write_png(path, image.to_rgba())
        return path

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = golden.main(list(argv))
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def check(self, candidate: Image) -> tuple[int, str]:
        code, out, _err = self.run_main("--baseline", str(self.baseline),
                                        "--candidate", str(self.png("current.png", candidate)))
        return code, out.strip()

    def test_selftest_detects_edges_in_both_directions(self) -> None:
        code, out, _err = self.run_main("--selftest")
        self.assertEqual(code, 0)
        self.assertIn("selftest passed", out)
        self.assertTrue(golden.edges([[255, 255, 0, 0]])[0][2])
        self.assertTrue(golden.edges([[0, 0, 255, 255]])[0][2])
        self.assertFalse(golden.edges([[10, 10, 30, 30]])[0][2])

    def test_identical_render_passes(self) -> None:
        self.assertEqual(self.check(panel()),
                         (0, "golden: pixels>24: 0.000% (max 1.00%) | edge-agree: 1.0000 "
                             "(min 0.850) | PASS"))

    def test_scores_match_the_numpy_implementation(self) -> None:
        for name, candidate in (("shift", panel(shift=5)), ("marker", panel(marker=True)),
                                ("resize", panel(90, 60)), ("lift", panel(lift=10))):
            with self.subTest(case=name):
                code, line = self.check(candidate)
                self.assertEqual(line, PULP_LINES[name])
                self.assertEqual(code, 0 if line.endswith("PASS") else 3)

    def test_dilation_grows_four_neighbour_by_radius(self) -> None:
        mask = [[False] * 5 for _ in range(5)]
        mask[2][2] = True
        grown = golden._dilate(mask, 1)
        self.assertEqual(sum(map(sum, grown)), 5)
        self.assertEqual(sum(map(sum, golden._dilate(mask, 2))), 13)

    def test_render_command_writes_to_the_output_placeholder(self) -> None:
        candidate = self.png("rendered.png", panel())
        renderer = self.root / "render.py"
        renderer.write_text(
            "import shutil, sys\n"
            "shutil.copyfile(sys.argv[1], sys.argv[2])\n", encoding="utf-8")
        command = f"{sys.executable} {renderer} {candidate} {{output}}"
        code, out, err = self.run_main("--baseline", str(self.baseline), "--render-cmd", command)
        self.assertEqual(code, 0, err)
        self.assertIn("| PASS", out)

    def test_render_command_gets_the_output_appended_without_a_placeholder(self) -> None:
        candidate = self.png("rendered.png", panel(shift=5))
        renderer = self.root / "render.py"
        renderer.write_text(
            "import shutil, sys\n"
            "shutil.copyfile(sys.argv[1], sys.argv[-1])\n", encoding="utf-8")
        code, out, _err = self.run_main("--baseline", str(self.baseline), "--render-cmd",
                                        f"{sys.executable} {renderer} {candidate}")
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), PULP_LINES["shift"])

    def test_a_failing_or_silent_render_command_is_a_setup_error(self) -> None:
        failing = f"{sys.executable} -c \"import sys; sys.exit(4)\""
        code, _out, err = self.run_main("--baseline", str(self.baseline), "--render-cmd", failing)
        self.assertEqual(code, 2)
        self.assertIn("render command failed (exit 4)", err)
        silent = f"{sys.executable} -c pass {{output}}"
        code, _out, err = self.run_main("--baseline", str(self.baseline), "--render-cmd", silent)
        self.assertEqual(code, 2)
        self.assertIn("render command wrote no PNG", err)
        code, _out, err = self.run_main("--baseline", str(self.baseline),
                                        "--render-cmd", str(self.root / "missing-renderer"))
        self.assertEqual(code, 2)
        self.assertIn("could not start", err)

    def test_inputs_are_validated(self) -> None:
        code, _out, err = self.run_main("--baseline", str(self.root / "missing.png"),
                                        "--candidate", str(self.baseline))
        self.assertEqual(code, 2)
        self.assertIn("baseline missing", err)
        code, _out, err = self.run_main("--baseline", str(self.baseline),
                                        "--candidate", str(self.root / "missing.png"))
        self.assertEqual(code, 2)
        self.assertIn("candidate missing", err)
        unreadable = self.root / "bad.png"
        unreadable.write_bytes(b"not a png")
        code, _out, err = self.run_main("--baseline", str(self.baseline),
                                        "--candidate", str(unreadable))
        self.assertEqual(code, 2)
        self.assertIn("cannot compare", err)
        self.assertEqual(self.run_main("--baseline", str(self.baseline))[0], 2)

    def test_comparison_image_stacks_baseline_current_and_diff(self) -> None:
        out_compare = self.root / "compare.png"
        code, out, _err = self.run_main("--baseline", str(self.baseline), "--candidate",
                                        str(self.png("current.png", panel(shift=5))),
                                        "--out-compare", str(out_compare))
        self.assertEqual(code, 3)
        self.assertIn("wrote comparison", out)
        image = open_image(out_compare)
        # Three 760-wide panels of 506 px (60x40 scaled), each under a 30 px label.
        self.assertEqual(image.size, (760 + 32, (506 + 30) * 3 + 16 * 4))

    def test_command_line_entry_point(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--baseline", str(self.baseline),
             "--candidate", str(self.baseline)],
            text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("| PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
