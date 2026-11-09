#!/usr/bin/env python3
"""Tests for pulp_main_drift_annotations.py."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parent))
import pulp_main_drift_annotations as annotate  # noqa: E402

LIVE = "a" * 40


class AnnotationTests(unittest.TestCase):
    def report(self, payload: object) -> Path:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = directory / "report.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_each_drift_error_is_one_warning(self) -> None:
        path = self.report({"errors": [
            "authoritative Pulp main: routing projection differs from accepted blob",
            "authoritative Pulp main: router differs from accepted blob",
            "open PR snapshot: unrelated",
        ]})
        lines = annotate.annotations(path, LIVE)
        warnings = [line for line in lines if line.startswith("::warning")]
        self.assertEqual(len(warnings), 3)
        self.assertIn("routing projection differs", warnings[0])
        self.assertIn(LIVE[:12], warnings[0])
        self.assertIn("router differs", warnings[1])
        self.assertIn("next push to Vellum main", warnings[2])
        self.assertFalse(any("unrelated" in line for line in lines))

    def test_a_clean_report_is_a_plain_line(self) -> None:
        lines = annotate.annotations(self.report({"errors": []}), LIVE)
        self.assertEqual(lines, [f"Pulp main {LIVE} matches the accepted boundary evidence"])

    def test_other_errors_are_inconclusive_not_a_match(self) -> None:
        path = self.report({"errors": [
            "--pulp-main-ref 'x' does not name a commit in the Pulp checkout",
            "something else",
        ]})
        lines = annotate.annotations(path, LIVE)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("::warning"))
        self.assertIn("comparison inconclusive: --pulp-main-ref 'x' does not name", lines[0])
        self.assertNotIn("matches", lines[0])

    def test_unreadable_or_malformed_reports_warn(self) -> None:
        missing = annotate.annotations(Path("/nonexistent/report.json"), LIVE)
        self.assertTrue(missing[0].startswith("::warning") and "no verifier report" in missing[0])
        malformed = annotate.annotations(self.report(["not", "a", "report"]), LIVE)
        self.assertIn("no error list", malformed[0])

    def test_messages_cannot_break_out_of_one_annotation(self) -> None:
        path = self.report({"errors": ["authoritative Pulp main: a\n::error::injected %"]})
        warning = annotate.annotations(path, LIVE)[0]
        self.assertNotIn("\n", warning)
        self.assertIn("%0A::error::injected %25", warning)

    def test_cli_always_exits_zero(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(annotate.main(["/nonexistent/report.json", LIVE]), 0)
            path = self.report({"errors": ["authoritative Pulp main: x"]})
            self.assertEqual(annotate.main([str(path), LIVE]), 0)


if __name__ == "__main__":
    unittest.main()
