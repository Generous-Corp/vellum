#!/usr/bin/env python3
"""Evaluate Vellum receiver health independently from its runner fleet."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys


class WatchdogError(RuntimeError):
    pass


def parse_time(value: object, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise WatchdogError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WatchdogError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise WatchdogError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def load_policy(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WatchdogError(f"cannot read watchdog policy: {error}") from error
    expected = {
        "schema_version",
        "named_response_owner",
        "cursor_lag_sla_minutes",
        "runner_acquisition_sla_minutes",
        "receiver_execution_sla_minutes",
        "successful_receiver_max_age_minutes",
        "pending_warning",
        "pending_limit",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WatchdogError("watchdog policy fields differ")
    if value["schema_version"] != 1:
        raise WatchdogError("watchdog policy schema_version must be 1")
    if not isinstance(value["named_response_owner"], str) or not value["named_response_owner"]:
        raise WatchdogError("watchdog policy must name a response owner")
    for field in expected - {"schema_version", "named_response_owner"}:
        if not isinstance(value[field], int) or value[field] <= 0:
            raise WatchdogError(f"watchdog policy {field} must be positive")
    if value["pending_warning"] >= value["pending_limit"]:
        raise WatchdogError("pending warning must remain below the limit")
    return value


def evaluate(
    *, policy: dict[str, object], now: dt.datetime, cursor_covers_latest: bool,
    latest_event_created_at: dt.datetime, pending_count: int,
    evidence_pr_open: bool, runs: object,
) -> dict[str, object]:
    if not isinstance(runs, list):
        raise WatchdogError("receiver runs must be an array")
    violations: list[str] = []
    if pending_count >= int(policy["pending_limit"]):
        violations.append("pending-limit-reached")
    elif pending_count >= int(policy["pending_warning"]):
        violations.append("pending-near-budget")
    if not cursor_covers_latest:
        violations.append("cursor-lag")
        lag_minutes = (now - latest_event_created_at).total_seconds() / 60
        if lag_minutes > int(policy["cursor_lag_sla_minutes"]):
            violations.append("cursor-lag-sla-breached")
    latest = runs[0] if runs else None
    receiver_active = False
    proof_recovery_required = False
    if latest is None:
        violations.append("missing-dispatch")
        proof_recovery_required = True
    elif not isinstance(latest, dict):
        raise WatchdogError("receiver run entry must be an object")
    else:
        status = latest.get("status")
        receiver_active = status in {"queued", "waiting", "pending", "in_progress"}
        conclusion = latest.get("conclusion")
        created = parse_time(latest.get("created_at"), "run.created_at")
        age_minutes = (now - created).total_seconds() / 60
        if status in {"queued", "waiting", "pending"}:
            if age_minutes > int(policy["runner_acquisition_sla_minutes"]):
                violations.append("runner-not-acquired")
        elif status == "in_progress":
            if age_minutes > int(policy["receiver_execution_sla_minutes"]):
                violations.append("receiver-run-stuck")
        elif status != "completed":
            violations.append("receiver-run-incoherent")
        elif conclusion != "success":
            violations.append("receiver-run-failed")
            proof_recovery_required = True
        elif age_minutes > int(policy["successful_receiver_max_age_minutes"]):
            violations.append("receiver-proof-stale")
            proof_recovery_required = True
        elif age_minutes >= int(policy["successful_receiver_max_age_minutes"]) * 0.9:
            proof_recovery_required = True
    retry_required = (
        not cursor_covers_latest
        and not receiver_active
        and not evidence_pr_open
    )
    heartbeat_required = (
        cursor_covers_latest
        and proof_recovery_required
        and not receiver_active
        and not evidence_pr_open
    )
    return {
        "schema_version": 1,
        "status": "pass" if not violations else "fail",
        "owner": policy["named_response_owner"],
        "cursor_covers_latest": cursor_covers_latest,
        "pending_count": pending_count,
        "evidence_pr_open": evidence_pr_open,
        "retry_required": retry_required,
        "heartbeat_required": heartbeat_required,
        "dispatch_required": retry_required or heartbeat_required,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise WatchdogError("watchdog state must be an object")
        result = evaluate(
            policy=load_policy(args.policy),
            now=parse_time(args.now, "--now"),
            cursor_covers_latest=state.get("cursor_covers_latest") is True,
            latest_event_created_at=parse_time(
                state.get("latest_event_created_at"), "state.latest_event_created_at"
            ),
            pending_count=int(state.get("pending_count", -1)),
            evidence_pr_open=state.get("evidence_pr_open") is True,
            runs=state.get("runs"),
        )
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0 if result["status"] == "pass" else 1
    except (WatchdogError, OSError, json.JSONDecodeError, ValueError) as error:
        print(f"receiver-watchdog: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
