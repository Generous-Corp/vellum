#!/usr/bin/env python3
"""prose_lint.py flags new em dashes and breadcrumbs, and nothing it should skip."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PATH = Path(__file__).with_name("prose_lint.py")
SPEC = importlib.util.spec_from_file_location("prose_lint", PATH)
assert SPEC and SPEC.loader
prose_lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prose_lint)

EM_DASH = "—"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class ProseLintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        self.write("docs/old.md", f"Already here {EM_DASH} not this change's problem.\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "tag", "base")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def lint(self, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
        for rel, text in files.items():
            self.write(rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change")
        return subprocess.run(
            [sys.executable, "-I", str(PATH), "--base", "base"],
            cwd=self.repo, capture_output=True, text=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        )

    def assertFlags(self, files: dict[str, str], needle: str) -> None:
        result = self.lint(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stdout)

    def assertClean(self, files: dict[str, str]) -> None:
        result = self.lint(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_em_dash_in_docs(self) -> None:
        self.assertFlags({"docs/new.md": f"Fast {EM_DASH} and small.\n"}, "docs/new.md:1: em dash")

    def test_phase_breadcrumb_in_a_comment(self) -> None:
        self.assertFlags({"tools/run.py": "x = 1  # Phase 3 fixture\n"}, "tools/run.py:1: breadcrumb")

    def test_pull_request_number_as_explanation(self) -> None:
        self.assertFlags({"src/a.cpp": "// The judge App (vellum#275) is planned.\n"}, "src/a.cpp:1: breadcrumb")

    def test_dated_owner_decision(self) -> None:
        self.assertFlags({"scripts/r.py": "# owner decision 2026-10-31 allows it\n"}, "scripts/r.py:1: breadcrumb")

    def test_dated_team_lead_ruling(self) -> None:
        self.assertFlags({"tools/m.py": "# TEAM-LEAD RULING 2026-10-11 defers this\n"}, "tools/m.py:1: breadcrumb")

    def test_identifier_in_code_is_not_prose(self) -> None:
        self.assertClean({"tools/run.py": 'FIXTURE = "fixtures/authoring-phase3"\n'})

    def test_only_added_lines_are_checked(self) -> None:
        self.assertClean({"docs/old.md": f"Already here {EM_DASH} not this change's problem.\nNew line.\n"})

    def test_inline_skip(self) -> None:
        self.assertClean({"docs/new.md": f"Quoted upstream title {EM_DASH} kept. crisp-writing: ok\n"})

    def test_exempt_paths(self) -> None:
        line = f"Recorded {EM_DASH} in Phase 4.\n"
        self.assertClean({
            "mirror/core/a.md": line,
            "framework/core/a.md": line,
            "provenance/notes.md": line,
            "decisions/0099-x.md": line,
            "NOTICE.md": line,
            "fixtures/data.json": f'{{"note": "{EM_DASH}"}}\n',
            "rmlui/cmake/patches/fix.patch": line,
        })

    def test_unreadable_base_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(PATH), "--base", "no-such-ref"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot diff against no-such-ref", result.stderr)


class VendoredCheckerTest(unittest.TestCase):
    def test_vendored_checker_matches_its_recorded_hash(self) -> None:
        digest = hashlib.sha256(prose_lint.CHECKER.read_bytes()).hexdigest()
        self.assertEqual(digest, prose_lint.CHECKER_SHA256,
                         "crisp_writing_check.py changed; copy the skill's check.py and update CHECKER_SHA256")


if __name__ == "__main__":
    unittest.main()
