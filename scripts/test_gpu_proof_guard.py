#!/usr/bin/env python3
"""Tests for the cheap lane's one retry and its three alarms."""

from __future__ import annotations

import glob
import importlib.util
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # the isolated lane has no PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gpu_proof_guard", Path(__file__).with_name("gpu_proof_guard.py"))
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

REPO = "Generous-Corp/vellum"
FAILED_STEP = {"steps": [{"name": "Run native, capture, and sterile-consumer tests", "conclusion": "failure"}]}
NETWORK = "pulp-view-audio-archive: cannot download SHA256SUMS: <urlopen error timed out>\n##[error]Process completed with exit code 1.\n"
REAL = "FAIL: test_x (m.T)\nAssertionError: 1 != 2\n##[error]Process completed with exit code 1.\n"


def run(**overrides):
    base = {"path": guard.WORKFLOW_PATH, "event": "merge_group", "conclusion": "failure", "run_attempt": 1,
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}}
    return {**base, **overrides}


class RetryTests(unittest.TestCase):
    def attempt(self, run_json, jobs, logs):
        posted: list[str] = []

        def api(path):
            return {"jobs": jobs} if path.endswith("/jobs?per_page=100") else run_json

        outcome = guard.retry(repository=REPO, run_id=9, api=api, text=lambda path: logs[path.split("/")[-2]],
                              post=lambda path, payload: posted.append(path))
        return outcome, posted

    def test_a_run_whose_jobs_all_failed_on_infrastructure_is_retried_once(self) -> None:
        jobs = [{"id": 1, "name": "gpu-macos-arm64", "conclusion": "failure", **FAILED_STEP}]
        outcome, posted = self.attempt(run(), jobs, {"1": NETWORK})
        self.assertTrue(outcome.startswith("retried"), outcome)
        self.assertEqual(posted, [f"repos/{REPO}/actions/runs/9/rerun-failed-jobs"])

    def test_a_job_that_lost_its_runner_is_infrastructure(self) -> None:
        jobs = [{"id": 1, "name": "gpu-macos-arm64", "conclusion": "failure", "steps": [{"conclusion": "success"}]}]
        self.assertTrue(self.attempt(run(), jobs, {"1": ""})[0].startswith("retried"))

    def test_nothing_else_is_retried(self) -> None:
        good = [{"id": 1, "name": "gpu-macos-arm64", "conclusion": "failure", **FAILED_STEP}]
        for label, run_json, jobs, logs in (
            ("a real failure", run(), good, {"1": REAL}),
            ("one real failure among infrastructure failures", run(),
             [*good, {"id": 2, "name": "sterile-consumer", "conclusion": "failure", **FAILED_STEP}],
             {"1": NETWORK, "2": REAL}),
            ("a real defect that also mentions the network", run(), good, {"1": NETWORK.replace("##[error]", REAL)}),
            ("a second attempt", run(run_attempt=2), good, {"1": NETWORK}),
            ("a push", run(event="push"), good, {"1": NETWORK}),
            ("a fork", run(head_repository={"full_name": "x/y"}), good, {"1": NETWORK}),
            ("another workflow", run(path=".github/workflows/other.yml"), good, {"1": NETWORK}),
            ("a cancelled run", run(conclusion="cancelled"), good, {"1": NETWORK}),
            ("no failed job", run(), [], {}),
        ):
            with self.subTest(case=label):
                outcome, posted = self.attempt(run_json, jobs, logs)
                self.assertTrue(outcome.startswith("declined"), outcome)
                self.assertEqual(posted, [])

    def test_an_unreadable_log_is_not_evidence(self) -> None:
        jobs = [{"id": 1, "name": "gpu-macos-arm64", "conclusion": "failure", **FAILED_STEP}]
        outcome, posted = self.attempt(run(), jobs, {})
        self.assertTrue(outcome.startswith("declined"), outcome)
        self.assertEqual(posted, [])

    def test_the_week_of_real_failures_has_three_infrastructure_logs_and_no_false_positive(self) -> None:
        # Logs of the 28 failed pull-request proofs (11-08 to 11-15) are kept outside the
        # repository; when present, exactly the three network failures must classify as infrastructure.
        logs = sorted(glob.glob(os.environ.get("VELLUM_PRGPU_LOGS", "") + "/*.log"))
        if not logs:
            self.skipTest("set VELLUM_PRGPU_LOGS to the directory of failed-proof logs")
        found = {os.path.basename(path)[:-4] for path in logs
                 if guard.is_infrastructure_failure(
                     FAILED_STEP, Path(path).read_text(encoding="utf-8", errors="replace"))}
        self.assertEqual(found, {"11120fb", "3abbba4", "e8babd7"})


def queue_run(number, conclusion):
    return {"id": number, "conclusion": conclusion, "path": guard.WORKFLOW_PATH}


class AlarmTests(unittest.TestCase):
    def evaluate(self, queue, main=(), jobs=None):
        return guard.evaluate(queue, list(main), lambda run_id: (jobs or {}).get(run_id, []))

    def test_a_quiet_day_raises_nothing(self) -> None:
        queue = [queue_run(n, "success") for n in range(1, 12)] + [queue_run(20, "cancelled")]
        self.assertEqual(self.evaluate(queue)["alarms"], [])

    def test_more_than_one_ejection_a_day_alarms(self) -> None:
        queue = [queue_run(n, "success") for n in range(1, 40)]
        self.assertEqual(self.evaluate(queue + [queue_run(50, "failure")])["alarms"], [])
        self.assertTrue(self.evaluate(queue + [queue_run(50, "failure"), queue_run(51, "failure")])["alarms"])

    def test_one_failure_in_ten_is_the_limit(self) -> None:
        nine = [queue_run(n, "success") for n in range(1, 10)]
        self.assertEqual(self.evaluate(nine + [queue_run(50, "failure")])["alarms"], [])
        report = self.evaluate(nine[:8] + [queue_run(50, "failure")])
        self.assertTrue(any("1 in 10" in line for line in report["alarms"]), report)
        # Too few runs to say anything about a rate.
        self.assertEqual(self.evaluate([queue_run(1, "success"), queue_run(2, "failure")])["alarms"],
                         [])

    def test_any_gpu_only_failure_alarms_on_its_own(self) -> None:
        queue = [queue_run(n, "success") for n in range(1, 40)] + [queue_run(50, "failure")]
        gpu = {50: [{"name": "gpu-macos-arm64", "conclusion": "failure", **FAILED_STEP}]}
        report = self.evaluate(queue, jobs=gpu)
        self.assertEqual(len(report["gpu_only_escapes"]), 1)
        self.assertTrue(report["alarms"])
        cheap = {50: [{"name": "gpu-macos-arm64", "conclusion": "failure",
                       "steps": [{"name": "Run the pr-fast tests", "conclusion": "failure"}]}]}
        self.assertEqual(self.evaluate(queue, jobs=cheap)["alarms"], [])
        sterile = {50: [{"name": "sterile-consumer", "conclusion": "failure", "steps": []}]}
        self.assertTrue(self.evaluate(queue, jobs=sterile)["gpu_only_escapes"])
        # A failed push to main is an escape too.
        main = [queue_run(60, "failure")]
        self.assertTrue(self.evaluate(queue[:-1], main=main, jobs={60: gpu[50]})["gpu_only_escapes"])

    @unittest.skipIf(yaml is None, "PyYAML is not installed")
    def test_the_gpu_only_steps_exist_in_the_workflow(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/gpu-macos.yml").read_text(encoding="utf-8"))
        names = {step["name"] for job in workflow["jobs"].values() for step in job["steps"]}
        self.assertEqual(sorted(guard.GPU_ONLY_STEPS - names), [])
        self.assertTrue(guard.GPU_ONLY_JOBS <= set(workflow["jobs"]))
        # None of them is a step the cheap lane runs.
        cheap = {"Verify the exact release source contract", "Build the cheap pull-request slice",
                 "Run the pr-fast tests"}
        self.assertEqual(sorted(guard.GPU_ONLY_STEPS & cheap), [])

    def test_the_tracking_issue_opens_updates_and_closes(self) -> None:
        posts: list[tuple[str, dict | None]] = []
        report = {"alarms": ["2 queue entries were ejected"], "ejected": 2}
        record = lambda path, payload: posts.append((path, payload))
        self.assertEqual(guard.sync_issue(REPO, report, lambda p: [], record), "opened an issue")
        self.assertEqual(posts[-1][0], f"repos/{REPO}/issues")
        existing = [{"number": 7, "title": guard.ISSUE_TITLE}, {"number": 8, "title": "other"},
                    {"number": 9, "title": guard.ISSUE_TITLE, "pull_request": {"url": "x"}}]
        self.assertEqual(guard.sync_issue(REPO, report, lambda p: existing, record), "updated issue 7")
        self.assertTrue(posts[-1][0].endswith("/issues/7/comments"))
        self.assertIn("no alarms", guard.sync_issue(REPO, {"alarms": []}, lambda p: existing, record))
        self.assertEqual(posts[-1], (f"repos/{REPO}/issues/7", {"state": "closed", "state_reason": "completed"}))

    def test_the_window_reads_only_recent_runs_of_this_workflow(self) -> None:
        seen: list[str] = []

        def api(path):
            seen.append(path)
            return {"workflow_runs": [queue_run(1, "success"), {**queue_run(2, "failure"), "path": "other"}]}

        report = guard.alarms(repository=REPO, hours=24, api=api,
                              now=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(report["queue_runs"], 1)
        self.assertTrue(any("created=%3E%3D2026-11-15T12:00:00Z" in path for path in seen), seen)


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class GuardWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load((ROOT / ".github/workflows/gpu-proof-guard.yml").read_text(encoding="utf-8"))

    def test_the_retry_job_is_gated_to_a_first_failed_attempt_of_this_repository(self) -> None:
        condition = self.workflow["jobs"]["retry-infrastructure-failure"]["if"]
        for fragment in ("conclusion == 'failure'", "run_attempt == 1", "'pull_request'", "'merge_group'",
                         "head_repository.full_name == github.repository"):
            self.assertIn(fragment, condition)
        self.assertEqual(self.workflow["jobs"]["retry-infrastructure-failure"]["permissions"],
                         {"actions": "write", "contents": "read"})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_the_alarm_job_cannot_rerun_or_cancel_anything(self) -> None:
        permissions = self.workflow["jobs"]["alarms"]["permissions"]
        self.assertEqual(permissions, {"actions": "read", "issues": "write", "contents": "read"})
        triggers = self.workflow.get("on") or self.workflow.get(True)
        self.assertIn("schedule", triggers)
        self.assertEqual(triggers["workflow_run"]["workflows"], ["Locked macOS GPU proof"])


if __name__ == "__main__":
    unittest.main()
