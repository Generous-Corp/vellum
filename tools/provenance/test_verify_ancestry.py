#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    from verify_ancestry import AncestryError, verify
except ModuleNotFoundError:
    from tools.provenance.verify_ancestry import AncestryError, verify


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", content],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return run(repo, "rev-parse", "HEAD")


class AncestryTests(unittest.TestCase):
    def test_branch_before_main_merge_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            old_main = commit(repo, "state", "old main")
            subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "pr", old_main], check=True)
            branch_tip = commit(repo, "branch", "PR forked before main merge")
            subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
            commit(repo, "main", "main moved")
            subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", "pr", "-m", "merge PR"], check=True, stdout=subprocess.DEVNULL)
            current_main = run(repo, "rev-parse", "HEAD")
            with self.assertRaisesRegex(AncestryError, "does not contain base"):
                verify(repo, current_main, branch_tip)
            verify(repo, old_main, branch_tip)

    def test_merge_group_synthetic_target_contains_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            base = commit(repo, "state", "main")
            target = commit(repo, "feature", "synthetic merge target")
            verify(repo, base, target)


if __name__ == "__main__":
    unittest.main()
