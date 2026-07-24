"""Health-report calculation and rendering for the observatory."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

if __package__:
    from .observatory_common import ObservatoryError, load_json, parse_utc, utc_text
    from .observatory_events import deadline, effective_observations
else:
    from observatory_common import ObservatoryError, load_json, parse_utc, utc_text
    from observatory_events import deadline, effective_observations


def activation_blockers(root: Path, lock: dict[str, object]) -> list[str]:
    blockers: list[str] = []
    if lock.get("state") != "active":
        blockers.append("authority-not-transferred")
    policy = load_json(root / "provenance/authority/trust-policy.v1.json")
    if policy.get("state") != "enabled":
        blockers.append("dedicated-app-trust-policy-not-enabled")
    repositories = (
        policy.get("repositories", {}) if isinstance(policy, dict) else {}
    )
    for name in ("pulp", "vellum"):
        repository = (
            repositories.get(name, {}) if isinstance(repositories, dict) else {}
        )
        if (
            not isinstance(repository, dict)
            or repository.get("repository_id") is None
            or repository.get("reader_app_id") is None
        ):
            blockers.append(f"{name}-repository-or-reader-app-id-unpinned")
        if name == "vellum" and (
            not isinstance(repository, dict)
            or repository.get("dispatcher_app_id") is None
        ):
            blockers.append("vellum-dispatcher-app-id-unpinned")
        checks = (
            repository.get("required_check_app_ids", {})
            if isinstance(repository, dict)
            else {}
        )
        if (
            not isinstance(checks, dict)
            or not checks
            or any(value is None for value in checks.values())
        ):
            blockers.append(f"{name}-required-check-producers-unpinned")
    if lock.get("vellum_authority_start_commit") is None:
        blockers.append("immutable-vellum-authority-start-not-recorded")
    if lock.get("vellum_authority_record_commit") is None:
        blockers.append("immutable-vellum-authority-record-not-recorded")
    if lock.get("pulp_activation_commit") is None:
        blockers.append("landed-pulp-freeze-evidence-not-recorded")
    return sorted(set(blockers))


def build_report(
    *,
    root: Path,
    lock: dict[str, object],
    cursor: dict[str, object],
    events: list[tuple[Path, dict[str, object]]],
    budgets: dict[str, int],
    now: dt.datetime | None,
    coverage_gaps: list[dict[str, str]],
) -> dict[str, object]:
    effective = effective_observations(events)
    pending = [event for event in effective if event["disposition"] == "pending"]
    overdue: list[dict[str, str]] = []
    maximum_age_days = 0.0
    if now is not None:
        for event in pending:
            discovered = parse_utc(
                event["discovered_at"], "event.discovered_at"
            )
            age = (now - discovered).total_seconds() / 86400
            maximum_age_days = max(maximum_age_days, age)
            due = deadline(event, budgets)
            if now > due:
                overdue.append(
                    {
                        "event_id": str(event["event_id"]),
                        "deadline": utc_text(due),
                    }
                )
    repeat_counts: dict[str, int] = {}
    for event in effective:
        if event["disposition"] not in {"port-required", "ported"}:
            continue
        for key in event["contract_keys"]:
            repeat_counts[str(key)] = repeat_counts.get(str(key), 0) + 1
    repeated = {
        key: count for key, count in sorted(repeat_counts.items()) if count > 1
    }
    effort = cursor.get("effort_window")
    if not isinstance(effort, dict):
        raise ObservatoryError("cursor.effort_window must be an object")
    framework_minutes = effort.get("framework_effort_minutes")
    observatory_minutes = effort.get("observatory_effort_minutes")
    if (
        not isinstance(framework_minutes, int)
        or framework_minutes < 0
        or not isinstance(observatory_minutes, int)
        or observatory_minutes < 0
    ):
        raise ObservatoryError(
            "cursor effort values must be non-negative integers"
        )
    effort_percent = (
        0.0
        if framework_minutes == 0
        else round(observatory_minutes * 100.0 / framework_minutes, 2)
    )
    violations: list[str] = []
    if len(pending) > budgets["maximum_pending_events"]:
        violations.append("pending-event-count-exceeded")
    if len(overdue) > budgets["maximum_overdue_events"]:
        violations.append("overdue-event-count-exceeded")
    if maximum_age_days > budgets["maximum_pending_event_age_days"]:
        violations.append("pending-event-age-exceeded")
    if (
        repeated
        and max(repeated.values()) > budgets["maximum_repeated_generic_fixes"]
    ):
        violations.append("repeated-generic-fix-threshold-exceeded")
    if effort_percent > budgets["maximum_framework_effort_percent"]:
        violations.append("observatory-effort-share-exceeded")
    if coverage_gaps:
        violations.append("cursor-event-coverage-gap")
    overdue_ids = {item["event_id"] for item in overdue}
    release_blockers = [
        str(event["event_id"])
        for event in effective
        if event.get("shared_contract_release_blocker") is True
        or (
            event["event_id"] in overdue_ids
            and event["class"] in {"security"}
        )
    ]
    return {
        "schema_version": 2,
        "state": str(lock["state"]),
        "generated_at": utc_text(now) if now is not None else None,
        "health": "pass" if not violations else "fail",
        "pending": len(pending),
        "overdue": len(overdue),
        "maximum_pending_age_days": round(maximum_age_days, 3),
        "observatory_effort_percent": effort_percent,
        "repeated_generic_fixes": repeated,
        "coverage_gaps": coverage_gaps,
        "budget_violations": violations,
        "release_blockers": sorted(set(release_blockers)),
        "events": [
            {
                "event_id": event["event_id"],
                "source_repository": event["source_repository"],
                "source_commit": event["source_commit"],
                "direction": event["direction"],
                "class": event["class"],
                "disposition": event["disposition"],
                "deadline": utc_text(deadline(event, budgets)),
                "mapped_contracts": event["mapped_contracts"],
            }
            for event in effective
        ],
        "activation_blockers": activation_blockers(root, lock),
    }


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Pulp/Vellum observatory health",
        "",
        f"- State: `{report['state']}`",
        f"- Health: `{report['health']}`",
        f"- Pending events: {report['pending']}",
        f"- Overdue events: {report['overdue']}",
        (
            f"- Observatory effort: {report['observatory_effort_percent']}% "
            "of framework effort"
        ),
        "",
        "## Events",
        "",
    ]
    events = report["events"]
    if events:
        lines.extend(
            [
                "| Event | Direction | Class | Disposition | Deadline |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for event in events:
            lines.append(
                f"| `{event['event_id']}` | {event['direction']} | "
                f"{event['class']} | {event['disposition']} | "
                f"{event['deadline']} |"
            )
    else:
        lines.append("No mapped change events have been recorded.")
    lines.extend(["", "## Activation blockers", ""])
    blockers = report["activation_blockers"]
    lines.extend(f"- `{blocker}`" for blocker in blockers)
    if not blockers:
        lines.append("None.")
    lines.extend(
        [
            "",
            "This report is generated. The observatory never applies source patches.",
            "",
        ]
    )
    return "\n".join(lines)
