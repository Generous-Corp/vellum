"""Event validation and effective-state projection for the observatory."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

if __package__:
    from .observatory_common import (
        CLASSES,
        DISPOSITIONS,
        EVENTS_PATH,
        EVENT_ID_RE,
        OBSERVATION_FIELDS,
        RESOLUTION_FIELDS,
        SHA_RE,
        ObservatoryError,
        load_json,
        parse_utc,
    )
else:
    from observatory_common import (
        CLASSES,
        DISPOSITIONS,
        EVENTS_PATH,
        EVENT_ID_RE,
        OBSERVATION_FIELDS,
        RESOLUTION_FIELDS,
        SHA_RE,
        ObservatoryError,
        load_json,
        parse_utc,
    )


def validate_string_list(
    value: object, field: str, *, sorted_unique: bool = True
) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ObservatoryError(f"{field} must be a string array")
    if sorted_unique and value != sorted(set(value)):
        raise ObservatoryError(f"{field} must be sorted and unique")
    return value


def validate_event(event: object, path: Path) -> dict[str, object]:
    if not isinstance(event, dict):
        raise ObservatoryError(f"event must be an object: {path}")
    kind = event.get("kind")
    expected = (
        OBSERVATION_FIELDS
        if kind == "observation"
        else RESOLUTION_FIELDS
        if kind == "resolution"
        else set()
    )
    if not expected or set(event) != expected:
        raise ObservatoryError(
            f"event fields differ for {path}: "
            f"missing={sorted(expected - set(event))} "
            f"unknown={sorted(set(event) - expected)}"
        )
    if event.get("schema_version") != 1:
        raise ObservatoryError(f"event schema_version must be 1: {path}")
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id):
        raise ObservatoryError(f"invalid event_id: {path}")
    if path.stem != event_id:
        raise ObservatoryError(f"event filename must equal event_id: {path}")
    disposition = event.get("disposition")
    if disposition not in DISPOSITIONS:
        raise ObservatoryError(f"invalid disposition for {event_id}")
    if kind == "resolution" and disposition == "pending":
        raise ObservatoryError(f"resolution cannot remain pending: {event_id}")
    timestamp_field = "discovered_at" if kind == "observation" else "created_at"
    parse_utc(event.get(timestamp_field), f"{event_id}.{timestamp_field}")
    if not isinstance(event.get("owner"), str) or not event["owner"]:
        raise ObservatoryError(f"event owner is required: {event_id}")
    if not isinstance(event.get("rationale"), str) or not event["rationale"].strip():
        raise ObservatoryError(f"event rationale is required: {event_id}")
    if not isinstance(event.get("shared_contract_release_blocker"), bool):
        raise ObservatoryError(
            f"shared_contract_release_blocker must be boolean: {event_id}"
        )
    effort = event.get("effort_minutes")
    if not isinstance(effort, int) or effort < 0:
        raise ObservatoryError(
            f"effort_minutes must be a non-negative integer: {event_id}"
        )
    for field in ("linked_commits", "linked_pull_requests", "contract_tests"):
        validate_string_list(event.get(field), f"{event_id}.{field}")
    if kind == "resolution":
        if not isinstance(event.get("resolves"), str) or not EVENT_ID_RE.fullmatch(
            str(event["resolves"])
        ):
            raise ObservatoryError(f"resolution target is invalid: {event_id}")
        return event
    repository = event.get("source_repository")
    if repository not in {"Generous-Corp/pulp", "Generous-Corp/vellum"}:
        raise ObservatoryError(f"unexpected source_repository: {event_id}")
    commit = event.get("source_commit")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise ObservatoryError(f"source_commit must be exact: {event_id}")
    expected_direction = (
        "Pulp-to-framework"
        if repository.endswith("/pulp")
        else "framework-to-Pulp"
    )
    if event.get("direction") != expected_direction:
        raise ObservatoryError(f"direction does not match source: {event_id}")
    cursor = event.get("scan_cursor")
    if not isinstance(cursor, dict) or set(cursor) != {
        "from_commit",
        "to_commit",
    }:
        raise ObservatoryError(f"scan_cursor fields differ: {event_id}")
    for field in ("from_commit", "to_commit"):
        if not isinstance(cursor.get(field), str) or not SHA_RE.fullmatch(
            str(cursor[field])
        ):
            raise ObservatoryError(
                f"scan_cursor.{field} must be exact: {event_id}"
            )
    for field in (
        "mapped_contracts",
        "mapped_paths",
        "transitive_paths",
        "include_dependency_deltas",
        "schema_api_deltas",
        "contract_keys",
    ):
        validate_string_list(event.get(field), f"{event_id}.{field}")
    if event.get("class") not in CLASSES:
        raise ObservatoryError(f"invalid event class: {event_id}")
    severity = event.get("severity")
    if severity not in {None, "P0", "P1", "P2", "P3"}:
        raise ObservatoryError(f"invalid severity: {event_id}")
    patch = event.get("patch_id")
    if patch is not None and (
        not isinstance(patch, str) or not SHA_RE.fullmatch(patch)
    ):
        raise ObservatoryError(f"patch_id must be a Git patch ID or null: {event_id}")
    renames = event.get("rename_candidates")
    if not isinstance(renames, list):
        raise ObservatoryError(f"rename_candidates must be an array: {event_id}")
    for rename in renames:
        if not isinstance(rename, dict) or set(rename) != {
            "old_path",
            "new_path",
            "similarity_percent",
        }:
            raise ObservatoryError(f"invalid rename candidate: {event_id}")
    return event


def load_events(root: Path) -> list[tuple[Path, dict[str, object]]]:
    directory = root / EVENTS_PATH
    if not directory.exists():
        return []
    events: list[tuple[Path, dict[str, object]]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        event = validate_event(load_json(path), path)
        event_id = str(event["event_id"])
        if event_id in seen:
            raise ObservatoryError(f"duplicate event_id: {event_id}")
        seen.add(event_id)
        events.append((path, event))
    observations = {
        str(event["event_id"]): event
        for _, event in events
        if event["kind"] == "observation"
    }
    resolutions: set[str] = set()
    for _, event in events:
        if event["kind"] != "resolution":
            continue
        target = str(event["resolves"])
        if target not in observations:
            raise ObservatoryError(
                f"resolution references absent observation: {event['event_id']}"
            )
        if target in resolutions:
            raise ObservatoryError(
                f"observation has more than one resolution: {target}"
            )
        if parse_utc(
            event["created_at"], "resolution.created_at"
        ) < parse_utc(
            observations[target]["discovered_at"], "observation.discovered_at"
        ):
            raise ObservatoryError(
                f"resolution predates observation: {event['event_id']}"
            )
        resolutions.add(target)
    return events


def effective_observations(
    events: list[tuple[Path, dict[str, object]]],
) -> list[dict[str, object]]:
    observations = {
        str(event["event_id"]): dict(event)
        for _, event in events
        if event["kind"] == "observation"
    }
    for _, resolution in events:
        if resolution["kind"] != "resolution":
            continue
        target = observations[str(resolution["resolves"])]
        for field in (
            "disposition",
            "rationale",
            "owner",
            "linked_commits",
            "linked_pull_requests",
            "contract_tests",
            "shared_contract_release_blocker",
            "effort_minutes",
        ):
            target[field] = resolution[field]
        target["resolution_event"] = resolution["event_id"]
        target["resolved_at"] = resolution["created_at"]
    return sorted(
        observations.values(),
        key=lambda item: (str(item["discovered_at"]), str(item["event_id"])),
    )


def add_business_days(start: dt.datetime, days: int) -> dt.datetime:
    value = start
    remaining = days
    while remaining:
        value += dt.timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value


def deadline(
    event: dict[str, object], budgets: dict[str, int]
) -> dt.datetime:
    start = parse_utc(event["discovered_at"], "event.discovered_at")
    if event["class"] == "security" or event.get("severity") == "P0":
        return start + dt.timedelta(
            hours=budgets["security_or_p0_classification_hours"]
        )
    if event["class"] in {
        "correctness",
        "schema",
        "importer",
        "rendering",
        "platform",
    }:
        return add_business_days(
            start, budgets["ordinary_framework_classification_business_days"]
        )
    return start + dt.timedelta(days=budgets["other_classification_days"])
