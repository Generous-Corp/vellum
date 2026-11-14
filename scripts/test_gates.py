#!/usr/bin/env python3
"""gates.sh fails on a planted prose violation, passes a clean change, and refuses a dirty tree."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
EM_DASH = "—"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class GatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "scripts").mkdir()
        for name in ("gates.sh", "prose_lint.py", "crisp_writing_check.py"):
            shutil.copy(SCRIPTS / name, self.repo / "scripts" / name)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "tag", "base")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def commit(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(self.repo, "add", relative)
        git(self.repo, "commit", "-q", "-m", "change")

    def gates(self) -> subprocess.CompletedProcess[str]:
        worktrees = self.repo / ".worktrees"
        env = {**os.environ, "GATES_ONLY": "prose-lint", "PULP_WORKTREES_ROOT": str(worktrees),
               "VELLUM_GATES_CACHE": str(worktrees / ".cache-unused")}
        return subprocess.run(["bash", "scripts/gates.sh", "base"], cwd=self.repo, env=env,
                              capture_output=True, text=True, check=False)

    def test_default_cache_uses_configured_worktree_root(self) -> None:
        self.commit("docs/new.md", "A plain sentence with nothing to flag.\n")
        worktrees = self.repo / ".worktrees"
        env = {**os.environ, "GATES_ONLY": "prose-lint", "PULP_WORKTREES_ROOT": str(worktrees)}
        result = subprocess.run(["bash", "scripts/gates.sh", "base"], cwd=self.repo, env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((worktrees / "vellum-gates-cache").is_dir())

    def test_planted_prose_violation_fails(self) -> None:
        self.commit("docs/new.md", f"A sentence {EM_DASH} with an em dash.\n")
        result = self.gates()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAIL prose-lint", result.stdout)

    def test_clean_change_passes(self) -> None:
        self.commit("docs/new.md", "A plain sentence with nothing to flag.\n")
        result = self.gates()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS prose-lint", result.stdout)

    def test_uncommitted_tracked_change_fails(self) -> None:
        self.commit("docs/new.md", "A plain sentence with nothing to flag.\n")
        (self.repo / "docs/new.md").write_text(f"Edited {EM_DASH} but not committed.\n", encoding="utf-8")
        result = self.gates()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("uncommitted", result.stderr)


if __name__ == "__main__":
    unittest.main()
