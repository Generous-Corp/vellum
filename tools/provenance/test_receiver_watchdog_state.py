#!/usr/bin/env python3
"""Behavioural tests for the watchdog state builder (lag clock, coverage)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    from .git_test_environment import disable_auto_maintenance
except ImportError:  # Direct script execution.
    from git_test_environment import disable_auto_maintenance

disable_auto_maintenance()


SCRIPT = Path(__file__).with_name("receiver_watchdog_state.py")
SPEC = importlib.util.spec_from_file_location("receiver_watchdog_state", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
state_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state_module)


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def commit(repo: Path, message: str, when: str) -> str:
    import os
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message, env=env)
    return git(repo, "rev-parse", "HEAD")


class WatchdogStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        top = Path(self.temporary.name)
        self.pulp = top / "pulp"
        self.vellum = top / "vellum"
        for repo in (self.pulp, self.vellum):
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Watchdog Test")
            git(repo, "config", "user.email", "watchdog@example.invalid")
        (self.pulp / "README.md").write_text("base\n", encoding="utf-8")
        self.pulp_base = commit(self.pulp, "pulp base", "2026-08-09T10:00:00Z")
        (self.vellum / "graphics").mkdir()
        (self.vellum / "graphics/render.cpp").write_text("int v = 1;\n", encoding="utf-8")
        report = self.vellum / "provenance/pulp-observatory/reports/current.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({"pending": 2}) + "\n", encoding="utf-8")
        self.vellum_base = None
        self.write_cursor("0" * 40)
        self.vellum_base = commit(self.vellum, "vellum base", "2026-08-09T10:00:00Z")
        self.write_cursor(self.vellum_base)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_cursor(self, vellum_commit: str) -> None:
        path = self.vellum / "provenance/pulp-observatory/cursor.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "pulp": {"last_scanned_commit": self.pulp_base},
            "vellum": {"last_scanned_commit": vellum_commit},
        }) + "\n", encoding="utf-8")

    def test_covered_main_has_no_lag(self) -> None:
        state = state_module.build_state(root=self.vellum, pulp_repo=self.pulp)
        self.assertTrue(state["vellum_cursor_covers_main"])
        self.assertIsNone(state["vellum_lag_started_at"])
        self.assertEqual(state["pending_count"], 2)

    def test_lag_clock_starts_at_the_first_uncovered_landing_not_the_tip(self) -> None:
        # Steady traffic: three landings after the cursor, minutes apart. The
        # clock must read the OLDEST one, or the SLA can never be breached.
        (self.vellum / "graphics/render.cpp").write_text("int v = 2;\n", encoding="utf-8")
        commit(self.vellum, "landing one", "2026-08-09T11:00:00Z")
        (self.vellum / "graphics/render.cpp").write_text("int v = 3;\n", encoding="utf-8")
        commit(self.vellum, "landing two", "2026-08-09T11:20:00Z")
        (self.vellum / "graphics/render.cpp").write_text("int v = 4;\n", encoding="utf-8")
        commit(self.vellum, "landing three", "2026-08-09T11:40:00Z")
        state = state_module.build_state(root=self.vellum, pulp_repo=self.pulp)
        self.assertFalse(state["vellum_cursor_covers_main"])
        self.assertTrue(state["vellum_lag_started_at"].startswith("2026-08-09T11:00:00"))

    def test_lag_clock_follows_first_parent_landings_only(self) -> None:
        # A merged branch's own (author-dated) commits must not move the clock;
        # the landing merge commit's committer time does.
        git(self.vellum, "checkout", "-q", "-b", "feature")
        (self.vellum / "graphics/render.cpp").write_text("int v = 2;\n", encoding="utf-8")
        commit(self.vellum, "old feature commit", "2026-08-01T09:00:00Z")
        git(self.vellum, "checkout", "-q", "main")
        import os
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-08-09T11:30:00Z",
                   GIT_COMMITTER_DATE="2026-08-09T11:30:00Z")
        subprocess.run(["git", "merge", "-q", "--no-ff", "feature", "-m", "land feature"],
                       cwd=self.vellum, check=True, env=env, capture_output=True)
        state = state_module.build_state(root=self.vellum, pulp_repo=self.pulp)
        self.assertFalse(state["vellum_cursor_covers_main"])
        self.assertTrue(state["vellum_lag_started_at"].startswith("2026-08-09T11:30:00"))

    def test_cli_writes_the_state_file(self) -> None:
        out = Path(self.temporary.name) / "state.json"
        rc = state_module.main([
            "--root", str(self.vellum), "--pulp-repo", str(self.pulp), "--output", str(out)
        ])
        self.assertEqual(rc, 0)
        state = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("vellum_cursor_covers_main", state)
        self.assertIn("vellum_lag_started_at", state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
