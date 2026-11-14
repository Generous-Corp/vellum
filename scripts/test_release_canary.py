#!/usr/bin/env python3
"""release_canary.py computes the next patch version, names the failing check and restores the checkout."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        moved = ("::error::the open PRs changed while their snapshot was collected, on attempt 3 of 3; the "
                 "authority evidence was never judged against a Pulp main and open PRs that held still. Re-run this job.\n")
        self.assertTrue(rc.inconclusive(moved + "FAIL  provenance.yml / x (exit 1)\n"))

    def test_an_earlier_attempt_or_a_bare_phrase_is_not_enough(self) -> None:
        self.assertFalse(rc.inconclusive("retrying (attempt 1 of 3); Re-run this job\n"))

    def test_a_content_refusal_is_conclusive(self) -> None:
        self.assertFalse(rc.inconclusive("pretag checks refused: v0.2.1 needs a parity completion record\n"))


def make_repo(root: Path, cmake: str = CMAKE.format(version="0.2.0")) -> None:
    for command in (["init", "-q"], ["config", "user.email", "t@invalid"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(root), *command], check=True)
    (root / "CMakeLists.txt").write_text(cmake)
    (root / "provenance/pulp-observatory").mkdir(parents=True)
    (root / rc.CURSOR).write_text('{"pulp": {"last_scanned_commit": "' + "a" * 40 + '"}}')
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "x"], check=True)


class Run(unittest.TestCase):
    def staged(self, root: Path, pretag: tuple[int, str]) -> int:
        def bump(path: Path, new: str) -> list[str]:
            (path / "CMakeLists.txt").write_text(CMAKE.format(version=new))
            return ["CMakeLists.txt"]
        with mock.patch.object(rc, "ensure_pulp", return_value=False), \
             mock.patch.object(rc.version_at_land, "apply", side_effect=bump), \
             mock.patch.object(rc, "run_pretag", return_value=pretag), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return rc.run(root, "0.2.1")

    def test_a_dirty_checkout_is_refused_and_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root)
            (root / "CMakeLists.txt").write_text("changed")
            with self.assertRaises(rc.CanaryError):
                rc.run(root, "0.0.1")
            self.assertEqual((root / "CMakeLists.txt").read_text(), "changed")

    def test_a_preexisting_tag_survives_a_refused_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root)
            subprocess.run(["git", "-C", str(root), "tag", "v0.2.1"], check=True)
            self.assertEqual(self.staged(root, (1, "pretag checks refused: v0.2.1 already exists\n")), 1)
            self.assertEqual(rc.git(root, "tag", "--list", "v0.2.1"), "v0.2.1")

    def test_the_checkout_is_restored_after_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root)
            before = rc.git(root, "rev-parse", "HEAD")
            self.assertEqual(self.staged(root, (0, "")), 0)
            self.assertEqual(rc.git(root, "rev-parse", "HEAD"), before)
            self.assertIn("0.2.0", (root / "CMakeLists.txt").read_text())

    def test_only_the_moving_audit_exits_two(self) -> None:
        moved = ("on attempt 3 of 3; the authority evidence was never judged against a Pulp main and open PRs "
                 "that held still. Re-run this job.\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root)
            self.assertEqual(self.staged(root, (1, moved)), 2)
            self.assertEqual(self.staged(root, (1, "pretag checks refused: parity record\n")), 1)


class Failures(unittest.TestCase):
    def main_in(self, root: Path, argv: list[str], **patches: object) -> int:
        with mock.patch.object(rc, "ROOT", root), contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            return rc.main(argv)

    def test_a_clone_failure_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root)
            error = subprocess.CalledProcessError(128, ["git", "clone"])
            with mock.patch.object(rc, "ensure_pulp", side_effect=error):
                self.assertEqual(self.main_in(root, ["0.2.1"]), 1)

    def test_a_malformed_cmake_version_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root, "project(Vellum LANGUAGES CXX)\n")
            self.assertEqual(self.main_in(root, []), 1)

    def test_missing_metadata_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_repo(root)
            (root / rc.CURSOR).write_text("{}")
            subprocess.run(["git", "-C", str(root), "commit", "-qam", "bad"], check=True)
            self.assertEqual(self.main_in(root, ["0.2.1"]), 1)


if __name__ == "__main__":
    unittest.main()
