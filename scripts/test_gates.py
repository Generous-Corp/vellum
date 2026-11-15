#!/usr/bin/env python3
"""gates.sh fails on a planted prose violation, passes a clean change, and refuses a dirty tree."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
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

    def test_nothing_is_created_under_the_temp_dir_or_home_cache(self) -> None:
        self.commit("docs/new.md", "A plain sentence with nothing to flag.\n")
        fake = self.repo / ".fake"
        for name in ("tmp", "home"):
            (fake / name).mkdir(parents=True)
        shim = fake / "bin"
        shim.mkdir()
        real = shutil.which("mktemp")
        (shim / "mktemp").write_text(f'#!/bin/bash\necho "$*" >> "{fake}/mktemp.log"\nexec {real} "$@"\n')
        (shim / "mktemp").chmod(0o755)
        env = {**os.environ, "GATES_ONLY": "prose-lint", "PULP_WORKTREES_ROOT": str(self.repo / ".worktrees"),
               "TMPDIR": str(fake / "tmp"), "HOME": str(fake / "home"), "PATH": f"{shim}:{os.environ['PATH']}"}
        env.pop("VELLUM_GATES_CACHE", None)
        result = subprocess.run(["bash", "scripts/gates.sh", "base"], cwd=self.repo, env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = (fake / "mktemp.log").read_text().splitlines()
        self.assertTrue(lines)
        root = str(self.repo / ".worktrees")
        for line in lines:
            paths = [word for word in line.split() if not word.startswith("-")]
            self.assertTrue(paths and all(path.startswith(root) for path in paths), line)
        self.assertEqual(list((fake / "tmp").iterdir()), [])
        self.assertFalse((fake / "home" / ".cache").exists())

    def rehearsal(self, stub: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        """Run only the release rehearsal on a release-path change, against a
        stand-in for the planning repository's tool."""
        planning = Path(self.tmp.name).parent / f"{Path(self.tmp.name).name}-planning"
        tool = planning / "vellum-move" / "tools" / "rehearse_tag_pipeline.py"
        tool.parent.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, planning, ignore_errors=True)
        tool.write_text(stub, encoding="utf-8")
        git(self.repo, "tag", "v0.4.1")
        self.commit("scripts/release_request.py", "print('release')\n")
        worktrees = self.repo / ".worktrees"
        env = {**os.environ, "GATES_ONLY": "release-rehearsal", "PULP_WORKTREES_ROOT": str(worktrees),
               "VELLUM_GATES_CACHE": str(worktrees / ".cache-unused"), "PULP_PLANNING_DIR": str(planning),
               "REHEARSAL_ARGV": str(planning / "argv.json")}
        return subprocess.run(["bash", "scripts/gates.sh", "base"], cwd=self.repo, env=env,
                              capture_output=True, text=True, check=False), planning / "argv.json"

    # The planning tool's interface, as far as this script uses it: --change
    # is required, as it is in the real tool.
    REHEARSAL_STUB = (
        "import argparse, json, os, sys\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--change', required=True)\n"
        "parser.add_argument('--tag', required=True)\n"
        "parser.add_argument('--simulate', action='store_true')\n"
        "args = parser.parse_args()\n"
        "json.dump(sys.argv[1:], open(os.environ['REHEARSAL_ARGV'], 'w'))\n"
    )

    def test_the_release_rehearsal_names_this_checkout_as_the_change(self) -> None:
        result, recorded = self.rehearsal(self.REHEARSAL_STUB)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gates: PASS release-rehearsal", result.stdout)
        arguments = json.loads(recorded.read_text())
        self.assertIn("--simulate", arguments)
        self.assertEqual(arguments[arguments.index("--tag") + 1], "v0.4.2")
        change = Path(arguments[arguments.index("--change") + 1])
        self.assertEqual(change.resolve(), self.repo.resolve())

    def test_a_failing_release_rehearsal_fails_the_gates(self) -> None:
        result, _ = self.rehearsal("raise SystemExit(3)\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("gates: FAIL release-rehearsal", result.stdout)

    # The observatory-events check runs the real gates.sh against a stand-in
    # observatory.py that exits as the real verify would.
    GAP_STUB = (
        "import sys\n"
        "sys.stderr.write('observatory: observatory health budgets failed: cursor-event-coverage-gap\\n')\n"
        "raise SystemExit(1)\n"
    )

    def observatory_gates(self, stub: str, change_path: str = "graphics/mapped.cpp") -> subprocess.CompletedProcess[str]:
        pulp = Path(self.tmp.name).parent / f"{Path(self.tmp.name).name}-pulp"
        self.addCleanup(shutil.rmtree, pulp, ignore_errors=True)
        pulp.mkdir()
        git(pulp, "init", "-q", "-b", "main")
        git(pulp, "config", "user.email", "test@example.com")
        git(pulp, "config", "user.name", "Test")
        (pulp / "a.txt").write_text("a\n")
        git(pulp, "add", "-A")
        git(pulp, "commit", "-q", "-m", "pulp")
        pulp_sha = subprocess.run(["git", "-C", str(pulp), "rev-parse", "HEAD"], capture_output=True,
                                  text=True, check=True).stdout.strip()
        (self.repo / "tools" / "provenance").mkdir(parents=True)
        (self.repo / "tools" / "provenance" / "observatory.py").write_text(stub, encoding="utf-8")
        cursor = self.repo / "provenance" / "pulp-observatory" / "cursor.json"
        cursor.parent.mkdir(parents=True)
        cursor.write_text(json.dumps({"pulp": {"last_scanned_commit": pulp_sha}}), encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "observatory fixture")
        git(self.repo, "tag", "-f", "base")
        self.commit(change_path, "int x = 1;\n")
        worktrees = self.repo / ".worktrees"
        env = {**os.environ, "GATES_ONLY": "observatory-events", "PULP_WORKTREES_ROOT": str(worktrees),
               "VELLUM_GATES_UPSTREAM_URL": str(pulp)}
        env.pop("VELLUM_GATES_CACHE", None)
        env.pop("VELLUM_GATES_UPSTREAM_CLONE", None)
        return subprocess.run(["bash", "scripts/gates.sh", "base"], cwd=self.repo, env=env,
                              capture_output=True, text=True, check=False)

    def test_a_missing_observation_event_fails_and_prints_the_reconcile_command(self) -> None:
        result = self.observatory_gates(self.GAP_STUB)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("gates: FAIL observatory-events", result.stdout)
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        self.assertIn("observatory.py reconcile", result.stdout)
        self.assertIn(f"--vellum-target {head}", result.stdout)
        self.assertIn("--write --events-only", result.stdout)
        self.assertRegex(result.stdout, r"--pulp-target [0-9a-f]{40}")
        self.assertIn("LAST change before the final push", result.stdout)
        self.assertIn("merge origin/main first", result.stdout)
        # Read-only: the check wrote no event and left the tree clean.
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain", "--untracked-files=no"],
                                        capture_output=True, text=True, check=True).stdout.strip(), "")

    def test_a_change_with_its_observation_event_passes(self) -> None:
        result = self.observatory_gates("raise SystemExit(0)\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gates: PASS observatory-events", result.stdout)
        self.assertNotIn("reconcile", result.stdout)

    def test_a_non_mapped_change_passes_without_a_reconcile_hint(self) -> None:
        # The real verify passes a change that touches no mapped path; the stub stands in for that.
        result = self.observatory_gates("raise SystemExit(0)\n", change_path="docs/unmapped.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("reconcile", result.stdout)

    def test_another_observatory_failure_fails_without_a_reconcile_hint(self) -> None:
        stub = "import sys\nsys.stderr.write('observatory: events are append-only\\n')\nraise SystemExit(1)\n"
        result = self.observatory_gates(stub)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("gates: FAIL observatory-events", result.stdout)
        self.assertNotIn("observatory.py reconcile", result.stdout)

    def test_an_unchanged_tree_skips_the_observatory_check(self) -> None:
        git(self.repo, "tag", "-f", "base")
        (self.repo / "tools" / "provenance").mkdir(parents=True)
        (self.repo / "tools" / "provenance" / "observatory.py").write_text(self.GAP_STUB, encoding="utf-8")
        (self.repo / "provenance" / "pulp-observatory").mkdir(parents=True)
        (self.repo / "provenance" / "pulp-observatory" / "cursor.json").write_text("{}", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fixture")
        git(self.repo, "tag", "-f", "base2")
        env = {**os.environ, "GATES_ONLY": "observatory-events", "PULP_WORKTREES_ROOT": str(self.repo / ".worktrees")}
        result = subprocess.run(["bash", "scripts/gates.sh", "base2"], cwd=self.repo, env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SKIP observatory-events", result.stdout)


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

    def test_upstream_refresh_failure_is_not_ignored(self) -> None:
        source = (SCRIPTS / "gates.sh").read_text(encoding="utf-8")
        refresh = 'git -C "$UPSTREAM_SRC" fetch -q --no-tags origin main >&2'
        self.assertIn(refresh, source)
        self.assertRegex(
            source,
            re.escape(refresh)
            + r'; then\n\s+echo "gates: cannot refresh upstream main; refusing to use a stale clone"',
        )
        self.assertNotIn(refresh + " || true", source)

    def test_failed_upstream_clone_does_not_print_credentials(self) -> None:
        secret_url = "https://x-access-token:fake-secret@example.invalid/pulp.git"
        text = (SCRIPTS / "gates.sh").read_text(encoding="utf-8")
        match = re.search(r"^upstream_at\(\) \{\n.*?^\}\n", text, re.S | re.M)
        self.assertIsNotNone(match)
        src = Path(self.tmp.name) / "src"
        script = f'CACHE={self.tmp.name}/c; UPSTREAM_SRC={src}; UPSTREAM_URL={secret_url}\n' + match.group(0) + "\nupstream_at x 0000\n"
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("fake-secret", result.stdout + result.stderr)
        self.assertNotIn(secret_url, result.stdout + result.stderr)
        self.assertIn("cannot clone", result.stderr)


if __name__ == "__main__":
    unittest.main()
