#!/usr/bin/env python3
"""Fixture tests for the independent receiver watchdog."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("receiver_watchdog.py")
SPEC = importlib.util.spec_from_file_location("receiver_watchdog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


NOW = dt.datetime(2026, 8, 9, 12, 0, tzinfo=dt.timezone.utc)
POLICY = {
    "schema_version": 1,
    "named_response_owner": "@danielraffel",
    "cursor_lag_sla_minutes": 30,
    "runner_acquisition_sla_minutes": 10,
    "successful_receiver_max_age_minutes": 1440,
    "pending_warning": 15,
    "pending_limit": 20,
}


def run(*, covered: bool = True, pending: int = 0, evidence_pr_open: bool = False, runs=None):
    if runs is None:
        runs = [{
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-08-09T11:55:00Z",
        }]
    return watchdog.evaluate(
        policy=POLICY,
        now=NOW,
        cursor_covers_latest=covered,
        latest_event_created_at=dt.datetime(
            2026, 8, 9, 11, 55, tzinfo=dt.timezone.utc
        ),
        pending_count=pending,
        evidence_pr_open=evidence_pr_open,
        runs=runs,
    )


class WatchdogTests(unittest.TestCase):
    def test_healthy_fixture_passes(self) -> None:
        self.assertEqual(run()["status"], "pass")

    def test_stale_cursor_fails(self) -> None:
        self.assertIn("cursor-lag", run(covered=False)["violations"])

    def test_stale_cursor_breaches_named_sla(self) -> None:
        result = watchdog.evaluate(
            policy=POLICY,
            now=NOW,
            cursor_covers_latest=False,
            latest_event_created_at=dt.datetime(
                2026, 8, 9, 11, 0, tzinfo=dt.timezone.utc
            ),
            pending_count=0,
            evidence_pr_open=False,
            runs=[{
                "status": "completed", "conclusion": "success",
                "created_at": "2026-08-09T11:55:00Z",
            }],
        )
        self.assertIn("cursor-lag-sla-breached", result["violations"])

    def test_queued_no_runner_fails_after_sla(self) -> None:
        result = run(runs=[{
            "status": "queued", "conclusion": None,
            "created_at": "2026-08-09T11:40:00Z",
        }])
        self.assertIn("runner-not-acquired", result["violations"])
        self.assertFalse(result["retry_required"])

    def test_failed_receiver_fails(self) -> None:
        result = run(runs=[{
            "status": "completed", "conclusion": "failure",
            "created_at": "2026-08-09T11:55:00Z",
        }])
        self.assertIn("receiver-run-failed", result["violations"])

    def test_missing_dispatch_fails(self) -> None:
        self.assertIn("missing-dispatch", run(runs=[])["violations"])

    def test_near_budget_fails_before_limit(self) -> None:
        result = run(pending=15)
        self.assertIn("pending-near-budget", result["violations"])

    def test_stale_success_proof_fails(self) -> None:
        result = run(runs=[{
            "status": "completed", "conclusion": "success",
            "created_at": "2026-08-08T11:00:00Z",
        }])
        self.assertIn("receiver-proof-stale", result["violations"])

    def test_successful_retry_supersedes_failed_run(self) -> None:
        result = run(runs=[
            {"status": "completed", "conclusion": "success", "created_at": "2026-08-09T11:55:00Z"},
            {"status": "completed", "conclusion": "failure", "created_at": "2026-08-09T11:50:00Z"},
        ])
        self.assertEqual(result["status"], "pass")

    def test_lag_after_failure_requests_retry(self) -> None:
        result = run(covered=False, runs=[{
            "status": "completed", "conclusion": "failure",
            "created_at": "2026-08-09T11:55:00Z",
        }])
        self.assertTrue(result["retry_required"])

    def test_open_evidence_pr_suppresses_replay_churn(self) -> None:
        result = run(covered=False, evidence_pr_open=True)
        self.assertFalse(result["retry_required"])
        self.assertIn("cursor-lag", result["violations"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
