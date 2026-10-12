#!/usr/bin/env python3
"""Unit tests for the browser-oracle, reimport and expected-rejection lanes.

They need no browser and no importer: each comparator is driven with small
hand-built observation directories, the outcome recorder with real child
processes (including one that dies by signal), and the rejection lane end to
end through ``calibrate`` with a fake implementation. A second fake refuses
everything with the expected code; calibration must notice that the
one-side-accepts control no longer bites and refuse to report CALIBRATED.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parity_harness as ph  # noqa: E402

lanes = ph.load_lane_module(HERE / "lanes" / "browser_lanes.py")

from vellum_png import RgbaImage, decode_png_bytes, encode_png  # noqa: E402

RECORDER = HERE / "lanes" / "outcome_recorder.py"


def png(width: int, height: int, fill=(16, 16, 16, 255), box=None, box_fill=(200, 80, 40, 255)) -> bytes:
    pixels = bytearray(bytes(fill) * (width * height))
    if box:
        x0, y0, x1, y1 = box
        for y in range(y0, y1):
            for x in range(x0, x1):
                o = (y * width + x) * 4
                pixels[o:o + 4] = bytes(box_fill)
    return encode_png(RgbaImage(width, height, bytes(pixels)))


def oracle_case(**oracle) -> dict:
    return {"case_id": "c", "source": "page.html", "width": 20, "height": 10, "scale": 2,
            "lanes": ["oracle"], "params": {"oracle_window": "40,20", "oracle_clip_x": 0, "oracle_clip_y": 0},
            "oracle": {"max_mismatch_fraction": 0.05, "justification": "test", "channel_threshold": 8, **oracle}}


def write_oracle_side(directory: Path, *, native: bytes, ir: dict | None = None, version: str = "155.0.1") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # The browser drew a box; the native render is near it but never identical.
    (directory / "oracle.png").write_bytes(png(40, 20, box=(4, 4, 30, 16)))
    (directory / "oracle-version.txt").write_text(f"Google Chrome {version}\n", encoding="utf-8")
    (directory / "oracle-request.json").write_text(json.dumps({"window": "40,20", "scale": "2", "clip": [0, 0]}),
                                                   encoding="utf-8")
    (directory / "render.png").write_bytes(native)
    (directory / "design.ir.json").write_text(json.dumps(ir or {"root": {"stable_anchor_id": "r", "children": [
        {"stable_anchor_id": "box", "type": "frame"}]}}), encoding="utf-8")
    (directory / "capture.json").write_text(json.dumps({"provenance": {"browser": {"version": version}}}),
                                            encoding="utf-8")


NATIVE = png(40, 20, box=(4, 4, 30, 15))  # one row short of the browser's box


class OracleLane(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.ref, self.cand = self.root / "reference", self.root / "candidate"

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def run_lane(self, case=None) -> dict:
        return lanes.compare_oracle(self.ref, self.cand, case or oracle_case())

    def test_native_near_the_capture_passes(self) -> None:
        for side in (self.ref, self.cand):
            write_oracle_side(side, native=NATIVE)
        result = self.run_lane()
        self.assertEqual(result["status"], "pass", result.get("reasons"))
        self.assertGreater(result["sides"]["reference"]["mismatch_pixels"], 0)

    def test_screenshot_embedded_as_native_fails(self) -> None:
        write_oracle_side(self.ref, native=NATIVE)
        write_oracle_side(self.cand, native=NATIVE, ir={"root": {"children": [
            {"stable_anchor_id": "browser:capture", "render_mode": "faithful_capture"}]}})
        result = self.run_lane()
        self.assertEqual(result["status"], "fail")
        self.assertIn("paints browser pixels", " ".join(result["reasons"]))

    def test_pixels_identical_to_the_capture_fail(self) -> None:
        write_oracle_side(self.ref, native=NATIVE)
        write_oracle_side(self.cand, native=png(40, 20, box=(4, 4, 30, 16)))
        result = self.run_lane()
        self.assertEqual(result["status"], "fail")
        self.assertIn("pixel-identical", " ".join(result["reasons"]))

    def test_unreproducible_oracle_is_blocked(self) -> None:
        for side in (self.ref, self.cand):
            write_oracle_side(side, native=NATIVE)
        (self.cand / "oracle.png").write_bytes(png(40, 20, box=(4, 4, 31, 16)))
        self.assertEqual(self.run_lane()["status"], "blocked")

    def test_unrecorded_oracle_version_is_blocked(self) -> None:
        for side in (self.ref, self.cand):
            write_oracle_side(side, native=NATIVE)
            (side / "oracle-version.txt").write_text("", encoding="utf-8")
        self.assertEqual(self.run_lane()["status"], "blocked")

    def test_capture_from_another_browser_fails(self) -> None:
        write_oracle_side(self.ref, native=NATIVE)
        write_oracle_side(self.cand, native=NATIVE)
        (self.cand / "capture.json").write_text(json.dumps({"provenance": {"browser": {"version": "1.0"}}}),
                                                encoding="utf-8")
        self.assertIn("captured with browser", " ".join(self.run_lane()["reasons"]))

    def test_candidate_further_from_the_browser_fails(self) -> None:
        write_oracle_side(self.ref, native=NATIVE)
        write_oracle_side(self.cand, native=png(40, 20, box=(4, 4, 30, 13)))
        self.assertIn("further from Chromium", " ".join(self.run_lane()["reasons"]))

    def test_ceiling_is_bounded_and_bites(self) -> None:
        for side in (self.ref, self.cand):
            write_oracle_side(side, native=NATIVE)
        self.assertIn("ceiling", " ".join(self.run_lane(oracle_case(max_mismatch_fraction=0.0))["reasons"]))
        self.assertIn("corpus:", " ".join(self.run_lane(oracle_case(max_mismatch_fraction=0.5))["reasons"]))
        self.assertIn("corpus:", " ".join(self.run_lane(oracle_case(justification=""))["reasons"]))


def write_reimport_side(directory: Path, *, first: dict, second: dict, revision: dict,
                        source: bytes = b"<p>A</p>", revised: bytes = b"<p>B</p>", stale: bool = False) -> None:
    for sub, ir, data in (("", first, source), ("second", second, source), ("revision", revision, revised)):
        target = directory / sub if sub else directory
        target.mkdir(parents=True, exist_ok=True)
        (target / "page.html").write_bytes(data)
        bound = ph.sha256_bytes(source if stale else data)
        (target / "capture.json").write_text(json.dumps({"provenance": {"source": {"sha256": bound}}}),
                                             encoding="utf-8")
        (target / "design.ir.json").write_text(json.dumps(ir), encoding="utf-8")


def tree(text: str, stamp: str = "t0") -> dict:
    return {"imported_at": stamp, "root": {"stable_anchor_id": "root", "children": [
        {"stable_anchor_id": "title", "text": text}, {"stable_anchor_id": "body", "text": "same"}]}}


REIMPORT_CASE = {"case_id": "c", "source": "page.html", "lanes": ["reimport"], "volatile": {"structure": ["/imported_at"]}}


class ReimportLane(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.ref, self.cand = self.root / "reference", self.root / "candidate"

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def both(self, **kwargs) -> None:
        for side in (self.ref, self.cand):
            write_reimport_side(side, **kwargs)

    def test_clean_reimport_passes(self) -> None:
        self.both(first=tree("A"), second=tree("A", "t1"), revision=tree("B", "t2"))
        result = lanes.compare_reimport(self.ref, self.cand, REIMPORT_CASE)
        self.assertEqual(result["status"], "pass", result.get("reasons"))
        self.assertEqual(result["detected"]["changed"], ["title"])

    def test_stale_output_is_refused(self) -> None:
        write_reimport_side(self.ref, first=tree("A"), second=tree("A"), revision=tree("B"))
        write_reimport_side(self.cand, first=tree("A"), second=tree("A"), revision=tree("B"), stale=True)
        self.assertIn("stale output", " ".join(lanes.compare_reimport(self.ref, self.cand, REIMPORT_CASE)["reasons"]))

    def test_undetected_revision_fails(self) -> None:
        self.both(first=tree("A"), second=tree("A"), revision=tree("A"))
        self.assertIn("not detected", " ".join(lanes.compare_reimport(self.ref, self.cand, REIMPORT_CASE)["reasons"]))

    def test_nondeterministic_reimport_fails(self) -> None:
        self.both(first=tree("A"), second=tree("A*"), revision=tree("B"))
        self.assertIn("reimported differently",
                      " ".join(lanes.compare_reimport(self.ref, self.cand, REIMPORT_CASE)["reasons"]))

    def test_sides_detecting_different_changes_fail(self) -> None:
        write_reimport_side(self.ref, first=tree("A"), second=tree("A"), revision=tree("B"))
        churned = tree("B")
        churned["root"]["children"][1]["stable_anchor_id"] = "body2"
        write_reimport_side(self.cand, first=tree("A"), second=tree("A"), revision=churned)
        self.assertIn("different changes", " ".join(lanes.compare_reimport(self.ref, self.cand, REIMPORT_CASE)["reasons"]))


class OutcomeRecorder(unittest.TestCase):
    DIAGNOSTICS = json.dumps([{"code": "bad-input", "pattern": "refusing"}, {"code": "soft", "pattern": "careful"}])

    def record(self, *commands: list[str]) -> dict:
        with tempfile.TemporaryDirectory() as work:
            argv = [sys.executable, str(RECORDER), "--out", "outcome.json", "--diagnostics", self.DIAGNOSTICS, "--"]
            for index, command in enumerate(commands):
                argv += (["--then"] if index else []) + command
            result = subprocess.run(argv, cwd=work, capture_output=True, text=True, encoding="utf-8")
            if result.returncode != 0:
                return {"recorder_exit": result.returncode}
            return json.loads((Path(work) / "outcome.json").read_text(encoding="utf-8"))

    def py(self, code: str) -> list[str]:
        return [sys.executable, "-c", code]

    def test_classifies_every_outcome(self) -> None:
        self.assertEqual(self.record(self.py("pass"))["outcome"], "accept")
        warn = self.record(self.py("import sys; print('careful', file=sys.stderr)"))
        self.assertEqual((warn["outcome"], warn["codes"]), ("warn", ["soft"]))
        reject = self.record(self.py("import sys; print('refusing', file=sys.stderr); sys.exit(1)"),
                             self.py("raise SystemExit('never runs')"))
        self.assertEqual((reject["outcome"], reject["codes"], len(reject["commands"])), ("reject", ["bad-input"], 1))
        crash = self.record(self.py("pass"), self.py("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"))
        self.assertEqual((crash["outcome"], crash["signal"]), ("crash", 11))

    def test_unstartable_tool_is_a_skip_not_an_outcome(self) -> None:
        self.assertEqual(self.record(["/nonexistent/importer"]), {"recorder_exit": ph.SKIP_EXIT})


FAKE_IMPORTER = textwrap.dedent(
    """
    import sys
    MODE = {mode!r}
    data = open(sys.argv[1], "rb").read()
    if MODE == "honest" and b"BAD" in data:
        print("refusing malformed input", file=sys.stderr)
        sys.exit(2)
    if MODE == "refuse-everything":
        print("refusing malformed input", file=sys.stderr)
        sys.exit(2)
    """
)


def fake_rejection_setup(root: Path, mode: str) -> tuple[Path, Path, Path, Path]:
    source_root = root / "src"
    (source_root / "inputs").mkdir(parents=True)
    (source_root / "inputs" / "bad.txt").write_text("BAD input\n", encoding="utf-8")
    (source_root / "inputs" / "good.txt").write_text("good input\n", encoding="utf-8")
    importer = root / "fake_importer.py"
    importer.write_text(FAKE_IMPORTER.format(mode=mode), encoding="utf-8")
    impl = root / "impl.json"
    impl.write_text(json.dumps({
        "schema": ph.IMPLEMENTATION_SCHEMA, "name": f"fake-{mode}", "repository": "example/fake",
        "authority": "browser-source",
        "tools": {"importer": str(importer), "recorder": str(RECORDER)},
        "source_kinds": {"fake-outcome": []},
        "probes": [{"name": name, "argv": ["/bin/echo", name]} for name in ph.REQUIRED_PROBES],
        "steps": [{"id": "observe", "argv": [sys.executable, "{tool:recorder}", "--out", "outcome.json",
                                             "--diagnostics", json.dumps([{"code": "bad-input", "pattern": "refusing"}]),
                                             "--", sys.executable, "{tool:importer}", "{source}"]}],
    }), encoding="utf-8")
    corpus = root / "corpus.json"
    corpus.write_text(json.dumps({"schema": ph.CORPUS_SCHEMA, "cases": [{
        "case_id": "bad", "source": "inputs/bad.txt", "source_kind": "fake-outcome", "width": 8, "height": 8,
        "lanes": ["rejection"], "expect": {"outcome": "reject", "code": "bad-input"},
        "wellformed": {"source": "inputs/good.txt"}}]}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source_root)], check=True)
    subprocess.run(["git", "-C", str(source_root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source_root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
                   check=True)
    return source_root, impl, corpus, importer


class RejectionCalibration(unittest.TestCase):
    def calibrate(self, mode: str) -> dict:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source_root, impl, corpus, _ = fake_rejection_setup(root, mode)
            return ph.calibrate(corpus, source_root, impl, root / "out", {}, None, {"checkout": source_root})

    def outcome(self, report: dict, name: str) -> str:
        return next(c["outcome"] for c in report["controls"] if c["control"] == name)

    def test_honest_fake_calibrates(self) -> None:
        report = self.calibrate("honest")
        self.assertTrue(report["calibrated"], report["control_failures"])
        for name in ("rejection-code-differs", "rejection-accepted-both", "rejection-crash",
                     "rejection-one-side-accepts", "planted-skip"):
            self.assertEqual(self.outcome(report, name), "REJECTED as required", name)
        self.assertEqual(self.outcome(report, "oracle-unstable"), "OUT OF SCOPE (no case selects this lane)")

    def test_fake_that_refuses_everything_is_not_calibrated(self) -> None:
        report = self.calibrate("refuse-everything")
        self.assertTrue(report["positive"]["verdict"]["accepting"])
        self.assertFalse(report["calibrated"])
        self.assertIn("rejection-one-side-accepts", report["control_failures"])

    def test_fake_that_accepts_everything_is_not_calibrated(self) -> None:
        report = self.calibrate("accept-everything")
        self.assertFalse(report["positive"]["verdict"]["accepting"])
        self.assertFalse(report["calibrated"])


class Fixtures(unittest.TestCase):
    def test_malformed_fixtures_are_reproducible(self) -> None:
        generator = HERE / "corpus" / "fixtures" / "malformed" / "generate.py"
        self.assertEqual(subprocess.run([sys.executable, str(generator), "--check"]).returncode, 0)

    def test_corpora_validate(self) -> None:
        for name in ("browser-calibration", "browser-fidelity-gaps", "rejection-calibration", "rejection-contract-gaps"):
            path = HERE / "corpus" / f"{name}.json"
            self.assertTrue(ph.validate_corpus(ph.load_json(path), path.parent))

    def test_browser_pixel_markers(self) -> None:
        self.assertEqual(lanes.browser_pixel_nodes({"root": {"stable_anchor_id": "r", "attributes": {
            "browser_reference_asset": "reference:browser"}, "children": [
            {"stable_anchor_id": "a", "attributes": {"asset_ref": "reference:browser-static"}},
            {"stable_anchor_id": "b", "attributes": {"materialized_role": "captured-paint-authority"}},
            {"stable_anchor_id": "c", "attributes": {"asset_ref": "swatch"}}]}}), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
