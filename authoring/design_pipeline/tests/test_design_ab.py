"""Exercise vellum-design-ab on generated PNG pairs."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "cli"))
from vellum_png import RgbaImage, write_png  # noqa: E402

TOOL: Path | None = None


def solid(width: int, height: int, rgba: tuple[int, int, int, int]) -> RgbaImage:
    return RgbaImage(width, height, bytes(rgba) * (width * height))


def left_half(width: int, height: int, left: tuple[int, int, int, int],
              right: tuple[int, int, int, int]) -> RgbaImage:
    row = bytes(left) * (width // 2) + bytes(right) * (width - width // 2)
    return RgbaImage(width, height, row * height)


class DesignAbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def png(self, name: str, image: RgbaImage) -> str:
        path = self.root / name
        write_png(path, image)
        return str(path)

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(TOOL), *arguments], text=True,
                              capture_output=True, check=False)

    def test_identical_images_pass_the_gate(self) -> None:
        reference = self.png("reference.png", solid(8, 8, (10, 20, 30, 255)))
        ours = self.png("ours.png", solid(8, 8, (10, 20, 30, 255)))
        completed = self.run_tool(reference, ours, "--fail-below", "0.85")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("similarity 100.0%  (0/64 pixels differ", completed.stdout)
        self.assertIn("PASS (gate 85%)", completed.stdout)

    def test_half_changed_image_needs_review_below_the_gate(self) -> None:
        reference = self.png("reference.png", solid(8, 8, (0, 0, 0, 255)))
        ours = self.png("ours.png", left_half(8, 8, (0, 0, 0, 255), (255, 255, 255, 255)))
        completed = self.run_tool(reference, ours, "--fail-below", "0.85")
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("similarity 50.0%  (32/64 pixels differ", completed.stdout)
        self.assertIn("NEEDS REVIEW (gate 85%)", completed.stdout)
        passing = self.run_tool(reference, ours, "--fail-below", "0.5")
        self.assertEqual(passing.returncode, 0, passing.stdout)
        self.assertIn("PASS (gate 50%)", passing.stdout)

    def test_without_a_gate_the_tool_only_reports(self) -> None:
        reference = self.png("reference.png", solid(8, 8, (0, 0, 0, 255)))
        ours = self.png("ours.png", solid(8, 8, (255, 255, 255, 255)))
        completed = self.run_tool(reference, ours)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("similarity 0.0%  (64/64 pixels differ", completed.stdout)
        self.assertNotIn("gate", completed.stdout)

    def test_changes_within_tolerance_still_match(self) -> None:
        reference = self.png("reference.png", solid(4, 4, (100, 100, 100, 255)))
        ours = self.png("ours.png", solid(4, 4, (120, 100, 100, 255)))
        completed = self.run_tool(reference, ours, "--fail-below", "1")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("(0/16 pixels differ", completed.stdout)

    def test_size_mismatch_is_penalised(self) -> None:
        reference = self.png("reference.png", solid(8, 8, (0, 0, 0, 255)))
        ours = self.png("ours.png", solid(4, 8, (0, 0, 0, 255)))
        completed = self.run_tool(reference, ours, "--fail-below", "0.85")
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("similarity 50.0%", completed.stdout)

    def test_unreadable_inputs_and_missing_arguments_exit_two(self) -> None:
        reference = self.png("reference.png", solid(2, 2, (0, 0, 0, 255)))
        missing = self.run_tool(reference, str(self.root / "missing.png"))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("comparison failed", missing.stderr)
        usage = self.run_tool(reference)
        self.assertEqual(usage.returncode, 2)
        self.assertIn("usage: vellum-design-ab", usage.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: test_design_ab.py <vellum-design-ab>")
    TOOL = Path(sys.argv.pop(1))
    unittest.main()
