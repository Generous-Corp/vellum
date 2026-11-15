#!/usr/bin/env python3
"""Proof for tools/visual/runner.py: golden lifecycle, failure artifacts, report."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "visual"))
sys.path.insert(0, str(ROOT / "cli"))

import runner  # noqa: E402
from vellum_png import RgbaImage, decode_png_bytes, encode_png, read_png  # noqa: E402


def textured(seed: int = 0, size: int = 8) -> RgbaImage:
    pixels = bytearray()
    for index in range(size * size):
        pixels += bytes(((index * 5 + seed) % 256, (index * 11) % 256, (index * 17) % 256, 255))
    return RgbaImage(size, size, bytes(pixels))


def with_pixel(image: RgbaImage, x: int, y: int, color: tuple[int, int, int, int]) -> RgbaImage:
    pixels = bytearray(image.pixels)
    offset = (y * image.width + x) * 4
    pixels[offset:offset + 4] = bytes(color)
    return RgbaImage(image.width, image.height, bytes(pixels))


class Workspace:
    """A throwaway repo root with a visual root and a swappable capture file."""

    def __init__(self, tmp: str) -> None:
        self.root = Path(tmp)
        self.visual = self.root / "visual"
        self.capture = self.root / "captures" / "current.png"
        self.capture.parent.mkdir(parents=True)

    def write_capture(self, image: RgbaImage) -> None:
        self.capture.write_bytes(encode_png(image))

    def fixture(self, entry: str = "panel", surface: str = "ui", **overrides: object) -> Path:
        document: dict[str, object] = {
            "capture": {"format": "png", "driver": "file", "path": "captures/current.png"},
        }
        document.update(overrides)
        path = self.visual / "fixtures" / surface / f"{entry}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def run(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = runner.main(["--repo-root", str(self.root), "--visual-root", str(self.visual),
                                "--surface", "ui", *args])
        return code, out.getvalue(), err.getvalue()


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace(self._tmp.name)
        self.ws.fixture()
        self.ws.write_capture(textured())

    def test_generate_refuses_to_rewrite_everything_by_accident(self) -> None:
        code, _, err = self.ws.run("--generate")
        self.assertEqual(code, 2)
        self.assertIn("--all", err)
        self.assertFalse((self.ws.visual / "goldens").exists())

    def test_all_and_entry_are_exclusive(self) -> None:
        self.assertEqual(self.ws.run("--generate", "--all", "--entry", "panel")[0], 2)

    def test_generate_then_verify_passes(self) -> None:
        self.assertEqual(self.ws.run("--generate", "--all")[0], 0)
        golden = self.ws.visual / "goldens" / "ui" / "panel.png"
        self.assertEqual(golden.read_bytes(), self.ws.capture.read_bytes())
        code, out, _ = self.ws.run("--verify")
        self.assertEqual(code, 0)
        self.assertIn("ok ui/panel", out)

    def test_missing_golden_fails_and_keeps_the_actual(self) -> None:
        actuals = self.ws.root / "actuals"
        code, _, err = self.ws.run("--actuals-dir", str(actuals))
        self.assertEqual(code, 1)
        self.assertIn("missing golden", err)
        self.assertTrue((actuals / "ui" / "panel.actual.png").is_file())
        self.assertFalse((self.ws.visual / "goldens").exists())

    def test_a_changed_pixel_fails_with_actual_diff_and_montage(self) -> None:
        self.ws.run("--generate", "--all")
        baseline = textured()
        self.ws.write_capture(with_pixel(baseline, 3, 2, (255, 255, 255, 255)))
        actuals = self.ws.root / "actuals"
        report_path = self.ws.root / "report.json"
        code, _, err = self.ws.run("--actuals-dir", str(actuals), "--report", str(report_path))
        self.assertEqual(code, 1)
        self.assertIn("FAILED ui/panel", err)
        self.assertEqual(read_png(actuals / "ui" / "panel.actual.png").pixels,
                         with_pixel(baseline, 3, 2, (255, 255, 255, 255)).pixels)
        diff = read_png(actuals / "ui" / "panel.diff.png")
        self.assertEqual(diff.pixels[(2 * 8 + 3) * 4] , 255)  # red where the pixel changed
        montage = read_png(actuals / "ui" / "panel.montage.png")
        self.assertGreater(montage.width, 3 * 8)  # golden, actual and diff side by side
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "vellum.visual-report.v1")
        self.assertFalse(report["passed"])
        self.assertEqual(report["totals"], {"fixtures": 1, "passed": 0, "failed": 1})
        entry = report["surfaces"]["ui"][0]
        self.assertEqual(entry["bounds"], {"x": 3, "y": 2, "width": 1, "height": 1})
        self.assertEqual(entry["comparison"]["diff_pixels"], 1)

    def test_default_comparison_is_exact_and_the_fixture_can_relax_it(self) -> None:
        self.ws.run("--generate", "--all")
        nudged = with_pixel(textured(), 0, 0, (200, 200, 200, 255))
        self.ws.write_capture(nudged)
        self.assertEqual(self.ws.run()[0], 1)
        self.ws.fixture(tolerance={"channel": 0, "similarity": 0.98})
        self.assertEqual(self.ws.run()[0], 0)
        # Relaxing similarity does not relax the channel tolerance on the rest.
        self.ws.write_capture(textured(seed=40))
        self.assertEqual(self.ws.run()[0], 1)

    def test_channel_tolerance_ignores_small_noise(self) -> None:
        self.ws.run("--generate", "--all")
        noisy = RgbaImage(8, 8, bytes(min(255, b + 3) if i % 4 != 3 else b
                                      for i, b in enumerate(textured().pixels)))
        self.ws.write_capture(noisy)
        self.assertEqual(self.ws.run()[0], 1)
        self.ws.fixture(tolerance={"channel": 3})
        self.assertEqual(self.ws.run()[0], 0)

    def test_a_wrong_sized_capture_fails_even_with_lenient_similarity(self) -> None:
        self.ws.fixture(tolerance={"similarity": 0.01})
        self.ws.run("--generate", "--all")
        self.ws.write_capture(textured(size=16))
        code, _, err = self.ws.run()
        self.assertEqual(code, 1)
        self.assertIn("dimensions differ", err)

    def test_orphaned_golden_fails_verification(self) -> None:
        self.ws.run("--generate", "--all")
        (self.ws.visual / "fixtures" / "ui" / "panel.json").unlink()
        self.ws.fixture(entry="other")
        self.ws.run("--generate", "--entry", "other")
        code, _, err = self.ws.run()
        self.assertEqual(code, 1)
        self.assertIn("orphaned golden panel.png", err)

    def test_content_floor_refuses_an_empty_golden_and_an_empty_capture(self) -> None:
        self.ws.fixture(content_floor=True)
        self.ws.write_capture(RgbaImage(8, 8, bytes((9, 9, 9, 255)) * 64))
        code, _, err = self.ws.run("--generate", "--all")
        self.assertEqual(code, 2)
        self.assertIn("empty-looking golden", err)
        self.ws.write_capture(textured())
        self.assertEqual(self.ws.run("--generate", "--all")[0], 0)
        self.ws.write_capture(RgbaImage(8, 8, bytes((9, 9, 9, 255)) * 64))
        code, _, err = self.ws.run()
        self.assertEqual(code, 1)
        self.assertIn("stable empty frame", err)

    def test_unknown_entry_and_missing_surface_are_usage_errors(self) -> None:
        code, _, err = self.ws.run("--entry", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown visual fixture", err)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = runner.main(["--repo-root", str(self.ws.root), "--visual-root", str(self.ws.visual),
                                "--surface", "absent"])
        self.assertEqual(code, 2)

    def test_a_capture_that_is_not_a_png_is_an_error_not_a_pass(self) -> None:
        self.ws.run("--generate", "--all")
        self.ws.capture.write_bytes(b"not a png")
        code, _, err = self.ws.run()
        self.assertEqual(code, 2)
        self.assertIn("not a valid PNG", err)


class DriverAndFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace(self._tmp.name)

    def test_file_capture_cannot_escape_the_repo_root(self) -> None:
        self.ws.fixture(capture={"format": "png", "driver": "file", "path": "../outside.png"})
        code, _, err = self.ws.run()
        self.assertEqual(code, 2)
        self.assertIn("escapes the repository root", err)

    def test_command_driver_captures_stdout_and_reports_failures(self) -> None:
        image = textured()
        script = self.ws.root / "emit.py"
        script.write_text(
            "import sys; sys.stdout.buffer.write(open('captures/current.png','rb').read())\n",
            encoding="utf-8")
        self.ws.write_capture(image)
        self.ws.fixture(capture={"format": "png", "driver": "command", "argv": [sys.executable, "emit.py"]})
        self.assertEqual(self.ws.run("--generate", "--all")[0], 0)
        self.assertEqual(self.ws.run()[0], 0)
        failing = self.ws.root / "fail.py"
        failing.write_text("import sys; sys.stderr.write('boom'); sys.exit(3)\n", encoding="utf-8")
        self.ws.fixture(capture={"format": "png", "driver": "command", "argv": [sys.executable, "fail.py"]})
        code, _, err = self.ws.run()
        self.assertEqual(code, 2)
        self.assertIn("exited 3: boom", err)

    def test_json_fixtures_use_the_semantic_tolerance(self) -> None:
        source = self.ws.root / "captures" / "tree.json"
        source.write_text(json.dumps({"x": 1.0, "label": "a"}), encoding="utf-8")
        self.ws.fixture(capture={"format": "json", "driver": "file", "path": "captures/tree.json"},
                        tolerance={"semantic": 0.01})
        self.assertEqual(self.ws.run("--generate", "--all")[0], 0)
        source.write_text(json.dumps({"x": 1.005, "label": "a"}), encoding="utf-8")
        self.assertEqual(self.ws.run()[0], 0)
        source.write_text(json.dumps({"x": 1.5, "label": "b"}), encoding="utf-8")
        actuals = self.ws.root / "actuals"
        code, _, err = self.ws.run("--actuals-dir", str(actuals))
        self.assertEqual(code, 1)
        self.assertIn("$.label", err)
        self.assertTrue((actuals / "ui" / "panel.actual.json").is_file())

    def test_malformed_fixtures_are_rejected(self) -> None:
        bad = [
            {},
            {"capture": {"format": "bmp"}},
            {"capture": {"driver": "file"}},
            {"capture": {"driver": "command", "argv": []}},
            {"capture": {"driver": "carrier-pigeon"}},
            {"capture": {"driver": "file", "path": "x.png"}, "tolerance": {"channel": 999}},
            {"capture": {"driver": "file", "path": "x.png"}, "tolerance": {"similarity": 0}},
        ]
        for index, document in enumerate(bad):
            path = self.ws.visual / "fixtures" / "bad" / f"f{index}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(runner.RunnerError, msg=json.dumps(document)):
                runner.load_fixture(path)

    def test_fixture_ids_default_to_surface_slash_entry(self) -> None:
        path = self.ws.fixture(entry="knob", surface="controls")
        self.assertEqual(runner.load_fixture(path).id, "controls/knob")


class CheckedInFixtureTests(unittest.TestCase):
    def test_the_repository_fixture_verifies_through_the_cli(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "visual" / "runner.py"), "--verify",
             "--surface", "regression", "--repo-root", str(ROOT)],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ok regression/swatch", completed.stdout)
        golden = decode_png_bytes((ROOT / "tools/visual/goldens/regression/swatch.png").read_bytes())
        self.assertEqual((golden.width, golden.height), (32, 32))


if __name__ == "__main__":
    unittest.main()
