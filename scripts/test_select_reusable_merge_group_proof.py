#!/usr/bin/env python3
"""Tests for the push-to-main reuse of the merge-queue GPU proof."""

from __future__ import annotations

import copy
import importlib.util
import io
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gpu-macos.yml"
SCRIPT = Path(__file__).with_name("select_reusable_merge_group_proof.py")
SPEC = importlib.util.spec_from_file_location("select_reusable_merge_group_proof", SCRIPT)
assert SPEC and SPEC.loader
selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selector)

REPO = "Generous-Corp/vellum"
SHA = "a" * 40
RUN_ID = 41000
ATTEMPT = 1
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)


def steps(*names_and_conclusions):
    return [{"name": name, "conclusion": conclusion} for name, conclusion in names_and_conclusions]


BUILT = steps(
    ("Set up job", "success"), (selector.BUILT_STEP, "success"),
    (selector.REUSED_STEP, "skipped"), (selector.CLASSIFIER_SKIP_STEP, "skipped"),
)
REUSED = steps(
    (selector.BUILT_STEP, "skipped"), (selector.REUSED_STEP, "success"),
    (selector.CLASSIFIER_SKIP_STEP, "skipped"),
)
CLASSIFIER_SKIP = steps(
    (selector.BUILT_STEP, "skipped"), (selector.REUSED_STEP, "skipped"),
    (selector.CLASSIFIER_SKIP_STEP, "success"),
)


class FakeGitHub:
    def __init__(self) -> None:
        self.runs = [{
            "id": RUN_ID, "run_attempt": ATTEMPT, "event": "merge_group",
            "path": selector.WORKFLOW_PATH, "head_sha": SHA,
            "head_branch": "gh-readonly-queue/main/pr-9-" + "b" * 40,
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "status": "completed", "conclusion": "success",
            "run_started_at": "2026-11-14T11:00:00Z",
        }]
        self.jobs = {RUN_ID: [
            {"name": "gpu-macos-arm64", "conclusion": "success", "run_attempt": ATTEMPT,
             "head_sha": SHA, "steps": copy.deepcopy(BUILT)},
            {"name": "sterile-consumer", "conclusion": "success", "run_attempt": ATTEMPT,
             "head_sha": SHA, "steps": []},
        ]}
        self.queries: list[str] = []

    def api(self, path: str):
        self.queries.append(path)
        if "/actions/workflows/gpu-macos.yml/runs?" in path:
            assert "event=merge_group" in path and "status=success" in path
            assert f"head_sha={SHA}" in path
            return {"workflow_runs": self.runs}
        match = re.search(r"/actions/runs/(\d+)/jobs", path)
        if match:
            return {"jobs": self.jobs[int(match.group(1))]}
        raise AssertionError(path)

    def select(self, **options):
        return selector.select(repository=REPO, sha=SHA, api=self.api, now=lambda: NOW, **options)


class SelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github = FakeGitHub()

    def test_reuses_a_successful_queue_proof_of_this_exact_commit(self) -> None:
        self.assertEqual(self.github.select(), (RUN_ID, "built"))

    def test_a_queue_run_that_itself_reused_a_pull_request_proof_counts(self) -> None:
        self.github.jobs[RUN_ID][0]["steps"] = copy.deepcopy(REUSED)
        self.assertEqual(self.github.select(), (RUN_ID, "reused"))

    def test_a_classifier_skip_is_refused_unless_explicitly_allowed(self) -> None:
        self.github.jobs[RUN_ID][0]["steps"] = copy.deepcopy(CLASSIFIER_SKIP)
        with self.assertRaises(selector.Decline):
            self.github.select()
        self.assertEqual(self.github.select(allow_classifier_skip=True), (RUN_ID, "classifier-skip"))

    def test_a_proof_job_that_shows_no_proof_is_refused(self) -> None:
        for shape in ([], steps(("Set up job", "success")),
                      steps((selector.BUILT_STEP, "failure")),
                      steps((selector.BUILT_STEP, "success"), (selector.REUSED_STEP, "success")),
                      steps((selector.BUILT_STEP, "success"), (selector.CLASSIFIER_SKIP_STEP, "success"))):
            with self.subTest(shape=shape):
                self.github.jobs[RUN_ID][0]["steps"] = shape
                with self.assertRaises(selector.Decline):
                    self.github.select(allow_classifier_skip=True)

    def test_every_disqualifying_run_field_is_refused(self) -> None:
        changes = {
            "event": "pull_request", "path": ".github/workflows/other.yml",
            "head_sha": "c" * 40, "head_branch": "main",
            "status": "in_progress", "conclusion": "failure",
            "run_started_at": "2026-11-13T11:00:00Z",
            "repository": {"full_name": "someone/vellum"},
            "head_repository": {"full_name": "someone/vellum"},
            "run_attempt": True, "id": "41000",
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                github = FakeGitHub()
                github.runs[0][key] = value
                with self.assertRaises(selector.Decline):
                    github.select()

    def test_a_future_or_missing_start_time_is_refused(self) -> None:
        for value in ("2026-11-14T13:00:00Z", None, "yesterday"):
            with self.subTest(value=value):
                github = FakeGitHub()
                github.runs[0]["run_started_at"] = value
                with self.assertRaises(selector.Decline):
                    github.select()

    def test_exactly_the_age_bound_is_still_reused(self) -> None:
        start = NOW - selector.MAX_AGE
        self.github.runs[0]["run_started_at"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self.github.select(), (RUN_ID, "built"))

    def test_both_proof_jobs_must_succeed_in_the_latest_attempt_on_this_commit(self) -> None:
        mutations = [
            lambda jobs: jobs[0].update(conclusion="skipped"),
            lambda jobs: jobs[1].update(conclusion="failure"),
            lambda jobs: jobs[1].update(run_attempt=ATTEMPT + 1),
            lambda jobs: jobs[0].update(head_sha="c" * 40),
            lambda jobs: jobs.pop(1),
            lambda jobs: jobs.append(copy.deepcopy(jobs[1])),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                github = FakeGitHub()
                mutate(github.jobs[RUN_ID])
                with self.assertRaises(selector.Decline):
                    github.select()

    def test_the_newest_qualifying_run_wins_and_older_failures_do_not_block(self) -> None:
        newer = copy.deepcopy(self.github.runs[0])
        newer["id"] = RUN_ID + 1
        newer["event"] = "pull_request"
        self.github.runs.append(newer)
        self.github.jobs[RUN_ID + 1] = self.github.jobs[RUN_ID]
        self.assertEqual(self.github.select(), (RUN_ID, "built"))

    def test_no_runs_declines(self) -> None:
        self.github.runs = []
        with self.assertRaises(selector.Decline):
            self.github.select()

    def test_a_api_error_propagates_so_main_runs_the_full_proof(self) -> None:
        def broken(path: str):
            raise RuntimeError("API down")
        with self.assertRaises(RuntimeError):
            selector.select(repository=REPO, sha=SHA, api=broken, now=lambda: NOW)


class MainTests(unittest.TestCase):
    def repo(self) -> tuple[tempfile.TemporaryDirectory, str]:
        directory = tempfile.TemporaryDirectory()
        subprocess.run(["git", "init", "-q", directory.name], check=True)
        subprocess.run(
            ["git", "-C", directory.name, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        head = subprocess.run(["git", "-C", directory.name, "rev-parse", "HEAD"],
                              check=True, text=True, capture_output=True).stdout.strip()
        return directory, head

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = selector.main(argv)
        return status, out.getvalue()

    def test_a_head_that_is_not_the_pushed_commit_declines_with_exit_3(self) -> None:
        directory, _ = self.repo()
        with directory:
            status, stdout = self.run_main(
                ["--repository", REPO, "--sha", "d" * 40, "--root", directory.name])
        self.assertEqual((status, stdout), (selector.NO_REUSE, ""))

    def test_a_malformed_sha_declines(self) -> None:
        directory, _ = self.repo()
        with directory:
            status, stdout = self.run_main(
                ["--repository", REPO, "--sha", "HEAD", "--root", directory.name])
        self.assertEqual((status, stdout), (selector.NO_REUSE, ""))

    def test_an_unreachable_api_declines_with_exit_3(self) -> None:
        directory, head = self.repo()
        original = selector.gh_api
        selector.gh_api = lambda path: (_ for _ in ()).throw(OSError("offline"))
        try:
            with directory:
                status, stdout = self.run_main(
                    ["--repository", REPO, "--sha", head, "--root", directory.name])
        finally:
            selector.gh_api = original
        self.assertEqual((status, stdout), (selector.NO_REUSE, ""))


class WorkflowTests(unittest.TestCase):
    def test_push_reuse_is_limited_to_main_and_release_refs_are_never_offered_it(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        block = text.split("      - name: Reuse an identical-tree proof\n", 1)[1].split("\n\n", 1)[0]
        condition = re.search(r"^        if: (.*)$", block, re.M).group(1)
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", condition)
        self.assertNotIn("refs/tags", condition)
        self.assertIn("select_reusable_merge_group_proof.py", block)
        # The workflow does not opt in to trusting classifier skips on push.
        self.assertNotIn("--allow-classifier-skip", text)

    def test_the_workflow_token_can_read_actions(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^permissions:\n  actions: read\n")

    def test_the_proof_jobs_still_report_on_a_push(self) -> None:
        # release-tag.yml reads the newest check run of each required context
        # on the landed commit; a job skipped by its own condition would read
        # as unmet there. Neither proof job may gain a push-skipping if:.
        text = WORKFLOW.read_text(encoding="utf-8")
        for job in ("gpu-macos-arm64", "sterile-consumer"):
            header = re.search(rf"^  {job}:\n(.*?)\n    runs-on:", text, re.M | re.S).group(1)
            self.assertNotIn("'push'", header, job)


if __name__ == "__main__":
    unittest.main()
