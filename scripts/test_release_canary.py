#!/usr/bin/env python3
"""release_canary.py computes the next patch version, names the failing check and restores the checkout."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_canary as rc  # noqa: E402
import version_at_land  # noqa: E402

CMAKE = "cmake_minimum_required(VERSION 3.24)\nproject(Vellum\n    VERSION {version}\n    LANGUAGES CXX)\n"


class NextPatch(unittest.TestCase):
    def test_the_patch_number_advances(self) -> None:
        self.assertEqual(rc.next_patch(CMAKE.format(version="0.2.0")), "0.2.1")
        self.assertEqual(rc.next_patch(CMAKE.format(version="1.9.9")), "1.9.10")

    def test_a_commented_version_is_ignored(self) -> None:
        text = "# VERSION 9.9.9\n" + CMAKE.format(version="0.3.4")
        self.assertEqual(rc.next_patch(text), "0.3.5")

    def test_a_missing_or_malformed_version_refuses(self) -> None:
        with self.assertRaises(version_at_land.VersionError):
            rc.next_patch("project(Vellum LANGUAGES CXX)\n")
        with self.assertRaises(version_at_land.VersionError):
            rc.next_patch(CMAKE.format(version="0.2"))


class FailingCheck(unittest.TestCase):
    def test_the_failing_step_is_named(self) -> None:
        output = ("run   a.yml / one\npass  a.yml / one\nrun   a.yml / two\n"
                  "FAIL  a.yml / two (exit 1)\nv0.2.1 would fail a.yml / two on abc; refusing it\n")
        self.assertEqual(rc.failing_check(output), "FAIL  a.yml / two (exit 1)")

    def test_a_refusal_before_any_step_is_named(self) -> None:
        self.assertEqual(rc.failing_check("pretag checks refused: the tag needs a record\n"),
                         "pretag checks refused: the tag needs a record")

    def test_an_unexplained_failure_says_so(self) -> None:
        self.assertIn("without naming", rc.failing_check("nothing useful\n"))


class Inconclusive(unittest.TestCase):
    def test_pulp_moving_under_the_audit_is_not_a_refusal(self) -> None:
        moved = "::error::the open PRs changed while their snapshot was collected, on attempt 3 of 3; Re-run this job.\n"
        self.assertTrue(rc.inconclusive(moved + "FAIL  provenance.yml / x (exit 1)\n"))

    def test_a_content_refusal_is_conclusive(self) -> None:
        self.assertFalse(rc.inconclusive("pretag checks refused: v0.2.1 needs a parity completion record\n"))


class Restores(unittest.TestCase):
    def test_a_dirty_checkout_is_refused_and_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for command in (["init", "-q"], ["config", "user.email", "t@invalid"], ["config", "user.name", "t"]):
                subprocess.run(["git", "-C", str(root), *command], check=True)
            (root / "f").write_text("a")
            subprocess.run(["git", "-C", str(root), "add", "f"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "x"], check=True)
            (root / "f").write_text("b")
            with self.assertRaises(rc.CanaryError):
                rc.run(root, "0.0.1")
            self.assertEqual((root / "f").read_text(), "b")


if __name__ == "__main__":
    unittest.main()
