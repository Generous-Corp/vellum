#!/usr/bin/env python3
"""Reconcile and verify the append-only Pulp/Vellum change observatory.

The observatory discovers relevant changes and records review work.  It never
copies or applies patches.  JSON is used inside ``*.yaml`` files because JSON
is a strict YAML subset and keeps this trust-boundary tool dependency-free.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
OBSERVATORY = Path("provenance/pulp-observatory")
LOCK_PATH = OBSERVATORY / "provenance.lock"
MAP_PATH = OBSERVATORY / "legacy-path-map.yaml"
CURSOR_PATH = OBSERVATORY / "cursor.json"
EVENTS_PATH = OBSERVATORY / "events"
REPORT_JSON_PATH = OBSERVATORY / "reports/current.json"
REPORT_MD_PATH = OBSERVATORY / "reports/current.md"
BUDGETS_PATH = Path("product/budgets.yaml")
SHA_RE = re.compile(r"[0-9a-f]{40}")
EVENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,160}")
CLASSES = {
    "security",
    "correctness",
    "schema",
    "importer",
    "rendering",
    "platform",
    "test",
    "build",
    "documentation",
    "pulp-specific",
}
DISPOSITIONS = {
    "pending",
    "not-applicable",
    "port-required",
    "ported",
    "superseded",
    "Pulp-only",
    "framework-only",
}
OBSERVATION_FIELDS = {
    "schema_version",
    "event_id",
    "kind",
    "source_repository",
    "source_commit",
    "discovered_at",
    "scan_cursor",
    "direction",
    "mapped_contracts",
    "mapped_paths",
    "transitive_paths",
    "rename_candidates",
    "patch_id",
    "include_dependency_deltas",
    "schema_api_deltas",
    "class",
    "severity",
    "disposition",
    "rationale",
    "owner",
    "linked_commits",
    "linked_pull_requests",
    "contract_tests",
    "contract_keys",
    "shared_contract_release_blocker",
    "effort_minutes",
}
RESOLUTION_FIELDS = {
    "schema_version",
    "event_id",
    "kind",
    "created_at",
    "resolves",
    "disposition",
    "rationale",
    "owner",
    "linked_commits",
    "linked_pull_requests",
    "contract_tests",
    "shared_contract_release_blocker",
    "effort_minutes",
}
REQUIRED_BUDGETS = {
    "security_or_p0_classification_hours",
    "ordinary_framework_classification_business_days",
    "other_classification_days",
    "maximum_pending_events",
    "maximum_overdue_events",
    "maximum_pending_event_age_days",
    "maximum_repeated_generic_fixes",
    "maximum_framework_effort_percent",
}


class ObservatoryError(RuntimeError):
    pass


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ObservatoryError(f"cannot read JSON/YAML-subset file {path}: {error}") from error


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def parse_utc(value: object, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ObservatoryError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ObservatoryError(f"{field} is not a valid timestamp") from error
    if parsed.utcoffset() != dt.timedelta(0):
        raise ObservatoryError(f"{field} must be UTC")
    return parsed


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_budgets(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    in_observatory = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "observatory:":
            in_observatory = True
            continue
        if in_observatory and line and not line.startswith("  "):
            break
        if not in_observatory or not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"  ([a-z0-9_]+): ([0-9]+)", line)
        if not match:
            raise ObservatoryError(f"unsupported observatory budget syntax: {line!r}")
        values[match.group(1)] = int(match.group(2))
    missing = REQUIRED_BUDGETS - set(values)
    unknown = set(values) - REQUIRED_BUDGETS
    if missing or unknown:
        raise ObservatoryError(f"observatory budget keys differ: missing={sorted(missing)} unknown={sorted(unknown)}")
    return values


def git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=repo, input=input_bytes, capture_output=True, check=True
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ObservatoryError(f"git {' '.join(args)} failed in {repo}: {detail}") from error
    return completed.stdout.decode("utf-8", errors="strict").strip()


def require_commit(repo: Path, commit: str, field: str) -> None:
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise ObservatoryError(f"{field} must be an exact 40-character commit SHA")
    resolved = git(repo, "rev-parse", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ObservatoryError(f"{field} did not resolve exactly: {commit}")


def require_ancestor(repo: Path, ancestor: str, descendant: str, field: str) -> None:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, capture_output=True
    )
    if completed.returncode != 0:
        raise ObservatoryError(f"{field}: {ancestor} is not an ancestor of {descendant}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
    )
    if completed.returncode not in (0, 1):
        raise ObservatoryError(
            f"cannot compare Git ancestry: {ancestor} -> {descendant}"
        )
    return completed.returncode == 0


def tree_identical_scanned_parent(
    repo: Path, commit: str, cursor_from: str
) -> str | None:
    """Return a scanned parent when a merge adds no tree beyond that parent."""
    row = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
    if len(row) < 3:
        return None
    commit_tree = git(repo, "rev-parse", f"{commit}^{{tree}}")
    for parent in row[1:]:
        if (
            is_ancestor(repo, cursor_from, parent)
            and git(repo, "rev-parse", f"{parent}^{{tree}}") == commit_tree
        ):
            return parent
    return None


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    return any(path.startswith(pattern) for pattern in patterns)


def transitive_match(path: str, rules: Iterable[str]) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(path, rule) for rule in rules)


def parse_diff_entries(repo: Path, commit: str) -> list[dict[str, object]]:
    parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
    arguments = ["diff-tree", "--no-commit-id", "--name-status", "-r", "-M"]
    if len(parents) == 1:
        arguments.append("--root")
        arguments.append(commit)
    else:
        arguments.extend([parents[1], commit])
    output = git(repo, *arguments)
    entries: list[dict[str, object]] = []
    for line in output.splitlines():
        pieces = line.split("\t")
        status = pieces[0]
        if status.startswith(("R", "C")):
            if len(pieces) != 3:
                raise ObservatoryError(f"malformed rename/copy entry for {commit}: {line}")
            entries.append({"status": status, "old_path": pieces[1], "path": pieces[2]})
        else:
            if len(pieces) != 2:
                raise ObservatoryError(f"malformed diff entry for {commit}: {line}")
            entries.append({"status": status, "path": pieces[1]})
    return entries


def patch_id(repo: Path, commit: str) -> str | None:
    patch = subprocess.run(
        ["git", "show", "--pretty=format:", "--no-ext-diff", "--binary", commit],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    if not patch.strip():
        return None
    completed = subprocess.run(
        ["git", "patch-id", "--stable"], cwd=repo, input=patch, capture_output=True, check=True
    )
    text = completed.stdout.decode().strip()
    return text.split()[0] if text else None


def mapped_change(
    entries: list[dict[str, object]], mappings: list[dict[str, object]], source: str, transitive_rules: list[str]
) -> dict[str, object] | None:
    path_key = "pulp_paths" if source == "pulp" else "vellum_paths"
    all_paths = sorted({str(entry[side]) for entry in entries for side in ("old_path", "path") if side in entry})
    matched: list[dict[str, object]] = []
    for mapping in mappings:
        patterns = mapping.get(path_key)
        if not isinstance(patterns, list):
            raise ObservatoryError(f"mapping {mapping.get('id')} lacks {path_key}")
        direct = [path for path in all_paths if path_matches(path, patterns)]
        if direct:
            matched.append(mapping)
    if not matched:
        return None
    direct_patterns = [pattern for mapping in matched for pattern in mapping[path_key]]
    mapped_paths = sorted(path for path in all_paths if path_matches(path, direct_patterns))
    roots = {PurePosixPath(pattern).parts[0] for pattern in direct_patterns if PurePosixPath(pattern).parts}
    transitive = sorted(
        path for path in all_paths
        if path not in mapped_paths
        and PurePosixPath(path).parts
        and (
            PurePosixPath(path).parts[0] in roots
            or (source == "pulp" and path == "CMakeLists.txt")
        )
        and transitive_match(path, transitive_rules)
    )
    renames = []
    for entry in entries:
        if str(entry["status"]).startswith(("R", "C")):
            old = str(entry["old_path"])
            new = str(entry["path"])
            if old in mapped_paths or new in mapped_paths or old in transitive or new in transitive:
                score = int(str(entry["status"])[1:] or "0")
                renames.append({"old_path": old, "new_path": new, "similarity_percent": score})
    tests = sorted({str(test) for mapping in matched for test in mapping.get("contract_tests", [])})
    contracts = sorted(str(mapping["id"]) for mapping in matched)
    return {
        "mapped_contracts": contracts,
        "mapped_paths": mapped_paths,
        "transitive_paths": transitive,
        "rename_candidates": renames,
        "contract_tests": tests,
        "contract_keys": contracts,
    }


def classify_paths(paths: list[str]) -> str:
    lowered = "\n".join(paths).lower()
    if any(token in lowered for token in ("security", "crypto", "license", "provenance")):
        return "security"
    if any(token in lowered for token in ("schema", "design_ir", "design-ir", ".d.ts")):
        return "schema"
    if any(token in lowered for token in ("import", "figma", "reimport")):
        return "importer"
    if any(token in lowered for token in ("render", "graphics", "canvas", "skia", "dawn")):
        return "rendering"
    if any(token in lowered for token in ("platform", "window_host", "apps/", "macos")):
        return "platform"
    if any(token in lowered for token in ("test", "fixture", "scenario")):
        return "test"
    if any(token in lowered for token in ("cmakelists", ".cmake", "package.json", "package-lock")):
        return "build"
    if any(token in lowered for token in ("readme", "docs/", ".md")):
        return "documentation"
    return "correctness"


def delta_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    includes = sorted(path for path in paths if any(token in path.lower() for token in (
        "cmakelists", ".cmake", "/include/", ".hpp", ".h", "package.json", "package-lock"
    )))
    schemas = sorted(path for path in paths if any(token in path.lower() for token in (
        "schema", "design_ir", "design-ir", ".d.ts", "contract"
    )))
    return includes, schemas


def commits_between(repo: Path, start: str, target: str) -> list[str]:
    require_commit(repo, start, "scan start")
    require_commit(repo, target, "scan target")
    require_ancestor(repo, start, target, "scan cursor")
    output = git(repo, "rev-list", "--reverse", "--topo-order", f"{start}..{target}")
    return output.splitlines() if output else []


def observation_for_commit(
    *, source: str, repository: str, repo: Path, commit: str, cursor_from: str,
    cursor_to: str, discovered_at: str, mappings: list[dict[str, object]], transitive_rules: list[str]
) -> dict[str, object] | None:
    if (
        source == "vellum"
        and tree_identical_scanned_parent(repo, commit, cursor_from) is not None
    ):
        return None
    entries = parse_diff_entries(repo, commit)
    mapped = mapped_change(entries, mappings, source, transitive_rules)
    if mapped is None:
        return None
    paths = sorted(set(mapped["mapped_paths"]) | set(mapped["transitive_paths"]))
    include_deltas, schema_deltas = delta_paths(paths)
    direction = "Pulp-to-framework" if source == "pulp" else "framework-to-Pulp"
    prefix = "pulp" if source == "pulp" else "vellum"
    return {
        "schema_version": 1,
        "event_id": f"{prefix}-{commit}",
        "kind": "observation",
        "source_repository": repository,
        "source_commit": commit,
        "discovered_at": discovered_at,
        "scan_cursor": {"from_commit": cursor_from, "to_commit": cursor_to},
        "direction": direction,
        **mapped,
        "patch_id": patch_id(repo, commit),
        "include_dependency_deltas": include_deltas,
        "schema_api_deltas": schema_deltas,
        "class": classify_paths(paths),
        "severity": None,
        "disposition": "pending",
        "rationale": "Human classification required; discovery is not a port decision.",
        "owner": "@danielraffel",
        "linked_commits": [],
        "linked_pull_requests": [],
        "shared_contract_release_blocker": False,
        "effort_minutes": 0,
    }


def validate_string_list(value: object, field: str, *, sorted_unique: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ObservatoryError(f"{field} must be a string array")
    if sorted_unique and value != sorted(set(value)):
        raise ObservatoryError(f"{field} must be sorted and unique")
    return value


def validate_event(event: object, path: Path) -> dict[str, object]:
    if not isinstance(event, dict):
        raise ObservatoryError(f"event must be an object: {path}")
    kind = event.get("kind")
    expected = OBSERVATION_FIELDS if kind == "observation" else RESOLUTION_FIELDS if kind == "resolution" else set()
    if not expected or set(event) != expected:
        raise ObservatoryError(f"event fields differ for {path}: missing={sorted(expected - set(event))} unknown={sorted(set(event) - expected)}")
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
        raise ObservatoryError(f"shared_contract_release_blocker must be boolean: {event_id}")
    effort = event.get("effort_minutes")
    if not isinstance(effort, int) or effort < 0:
        raise ObservatoryError(f"effort_minutes must be a non-negative integer: {event_id}")
    for field in ("linked_commits", "linked_pull_requests", "contract_tests"):
        validate_string_list(event.get(field), f"{event_id}.{field}")
    if kind == "resolution":
        if not isinstance(event.get("resolves"), str) or not EVENT_ID_RE.fullmatch(str(event["resolves"])):
            raise ObservatoryError(f"resolution target is invalid: {event_id}")
        return event
    repository = event.get("source_repository")
    if repository not in {"Generous-Corp/pulp", "Generous-Corp/vellum"}:
        raise ObservatoryError(f"unexpected source_repository: {event_id}")
    commit = event.get("source_commit")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise ObservatoryError(f"source_commit must be exact: {event_id}")
    expected_direction = "Pulp-to-framework" if repository.endswith("/pulp") else "framework-to-Pulp"
    if event.get("direction") != expected_direction:
        raise ObservatoryError(f"direction does not match source: {event_id}")
    cursor = event.get("scan_cursor")
    if not isinstance(cursor, dict) or set(cursor) != {"from_commit", "to_commit"}:
        raise ObservatoryError(f"scan_cursor fields differ: {event_id}")
    for field in ("from_commit", "to_commit"):
        if not isinstance(cursor.get(field), str) or not SHA_RE.fullmatch(str(cursor[field])):
            raise ObservatoryError(f"scan_cursor.{field} must be exact: {event_id}")
    for field in (
        "mapped_contracts", "mapped_paths", "transitive_paths", "include_dependency_deltas",
        "schema_api_deltas", "contract_keys"
    ):
        validate_string_list(event.get(field), f"{event_id}.{field}")
    if event.get("class") not in CLASSES:
        raise ObservatoryError(f"invalid event class: {event_id}")
    severity = event.get("severity")
    if severity not in {None, "P0", "P1", "P2", "P3"}:
        raise ObservatoryError(f"invalid severity: {event_id}")
    patch = event.get("patch_id")
    if patch is not None and (not isinstance(patch, str) or not SHA_RE.fullmatch(patch)):
        raise ObservatoryError(f"patch_id must be a Git patch ID or null: {event_id}")
    renames = event.get("rename_candidates")
    if not isinstance(renames, list):
        raise ObservatoryError(f"rename_candidates must be an array: {event_id}")
    for rename in renames:
        if not isinstance(rename, dict) or set(rename) != {"old_path", "new_path", "similarity_percent"}:
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
    observations = {str(event["event_id"]): event for _, event in events if event["kind"] == "observation"}
    resolutions: set[str] = set()
    for _, event in events:
        if event["kind"] != "resolution":
            continue
        target = str(event["resolves"])
        if target not in observations:
            raise ObservatoryError(f"resolution references absent observation: {event['event_id']}")
        if target in resolutions:
            raise ObservatoryError(f"observation has more than one resolution: {target}")
        if parse_utc(event["created_at"], "resolution.created_at") < parse_utc(observations[target]["discovered_at"], "observation.discovered_at"):
            raise ObservatoryError(f"resolution predates observation: {event['event_id']}")
        resolutions.add(target)
    return events


def effective_observations(events: list[tuple[Path, dict[str, object]]]) -> list[dict[str, object]]:
    observations = {str(event["event_id"]): dict(event) for _, event in events if event["kind"] == "observation"}
    for _, resolution in events:
        if resolution["kind"] != "resolution":
            continue
        target = observations[str(resolution["resolves"])]
        for field in (
            "disposition", "rationale", "owner", "linked_commits", "linked_pull_requests",
            "contract_tests", "shared_contract_release_blocker", "effort_minutes"
        ):
            target[field] = resolution[field]
        target["resolution_event"] = resolution["event_id"]
        target["resolved_at"] = resolution["created_at"]
    return sorted(observations.values(), key=lambda item: (str(item["discovered_at"]), str(item["event_id"])))


def add_business_days(start: dt.datetime, days: int) -> dt.datetime:
    value = start
    remaining = days
    while remaining:
        value += dt.timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value


def deadline(event: dict[str, object], budgets: dict[str, int]) -> dt.datetime:
    start = parse_utc(event["discovered_at"], "event.discovered_at")
    if event["class"] == "security" or event.get("severity") == "P0":
        return start + dt.timedelta(hours=budgets["security_or_p0_classification_hours"])
    if event["class"] in {"correctness", "schema", "importer", "rendering", "platform"}:
        return add_business_days(start, budgets["ordinary_framework_classification_business_days"])
    return start + dt.timedelta(days=budgets["other_classification_days"])


def activation_blockers(root: Path, lock: dict[str, object]) -> list[str]:
    blockers: list[str] = []
    if lock.get("state") != "active":
        blockers.append("authority-not-transferred")
    policy = load_json(root / "provenance/authority/trust-policy.v1.json")
    if policy.get("state") != "enabled":
        blockers.append("dedicated-app-trust-policy-not-enabled")
    repositories = policy.get("repositories", {}) if isinstance(policy, dict) else {}
    for name in ("pulp", "vellum"):
        repository = repositories.get(name, {}) if isinstance(repositories, dict) else {}
        if not isinstance(repository, dict) or repository.get("repository_id") is None or repository.get("reader_app_id") is None:
            blockers.append(f"{name}-repository-or-reader-app-id-unpinned")
        if name == "vellum" and (
            not isinstance(repository, dict) or repository.get("dispatcher_app_id") is None
        ):
            blockers.append("vellum-dispatcher-app-id-unpinned")
        checks = repository.get("required_check_app_ids", {}) if isinstance(repository, dict) else {}
        if not isinstance(checks, dict) or not checks or any(value is None for value in checks.values()):
            blockers.append(f"{name}-required-check-producers-unpinned")
    if lock.get("vellum_authority_start_commit") is None:
        blockers.append("immutable-vellum-authority-start-not-recorded")
    if lock.get("pulp_activation_commit") is None:
        blockers.append("landed-pulp-freeze-evidence-not-recorded")
    return sorted(set(blockers))


def build_report(
    *, root: Path, lock: dict[str, object], cursor: dict[str, object], events: list[tuple[Path, dict[str, object]]],
    budgets: dict[str, int], now: dt.datetime | None, coverage_gaps: list[dict[str, str]]
) -> dict[str, object]:
    effective = effective_observations(events)
    pending = [event for event in effective if event["disposition"] == "pending"]
    overdue: list[dict[str, str]] = []
    maximum_age_days = 0.0
    if now is not None:
        for event in pending:
            discovered = parse_utc(event["discovered_at"], "event.discovered_at")
            age = (now - discovered).total_seconds() / 86400
            maximum_age_days = max(maximum_age_days, age)
            due = deadline(event, budgets)
            if now > due:
                overdue.append({"event_id": str(event["event_id"]), "deadline": utc_text(due)})
    repeat_counts: dict[str, int] = {}
    for event in effective:
        if event["disposition"] not in {"port-required", "ported"}:
            continue
        for key in event["contract_keys"]:
            repeat_counts[str(key)] = repeat_counts.get(str(key), 0) + 1
    repeated = {key: count for key, count in sorted(repeat_counts.items()) if count > 1}
    effort = cursor.get("effort_window")
    if not isinstance(effort, dict):
        raise ObservatoryError("cursor.effort_window must be an object")
    framework_minutes = effort.get("framework_effort_minutes")
    observatory_minutes = effort.get("observatory_effort_minutes")
    if not isinstance(framework_minutes, int) or framework_minutes < 0 or not isinstance(observatory_minutes, int) or observatory_minutes < 0:
        raise ObservatoryError("cursor effort values must be non-negative integers")
    effort_percent = 0.0 if framework_minutes == 0 else round(observatory_minutes * 100.0 / framework_minutes, 2)
    violations: list[str] = []
    if len(pending) > budgets["maximum_pending_events"]:
        violations.append("pending-event-count-exceeded")
    if len(overdue) > budgets["maximum_overdue_events"]:
        violations.append("overdue-event-count-exceeded")
    if maximum_age_days > budgets["maximum_pending_event_age_days"]:
        violations.append("pending-event-age-exceeded")
    if repeated and max(repeated.values()) > budgets["maximum_repeated_generic_fixes"]:
        violations.append("repeated-generic-fix-threshold-exceeded")
    if effort_percent > budgets["maximum_framework_effort_percent"]:
        violations.append("observatory-effort-share-exceeded")
    if coverage_gaps:
        violations.append("cursor-event-coverage-gap")
    release_blockers = [
        str(event["event_id"]) for event in effective
        if event.get("shared_contract_release_blocker") is True
        or (event["event_id"] in {item["event_id"] for item in overdue} and event["class"] in {"security"})
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
        f"- Observatory effort: {report['observatory_effort_percent']}% of framework effort",
        "",
        "## Events",
        "",
    ]
    events = report["events"]
    if events:
        lines.extend(["| Event | Direction | Class | Disposition | Deadline |", "| --- | --- | --- | --- | --- |"])
        for event in events:
            lines.append(
                f"| `{event['event_id']}` | {event['direction']} | {event['class']} | "
                f"{event['disposition']} | {event['deadline']} |"
            )
    else:
        lines.append("No mapped change events have been recorded.")
    lines.extend(["", "## Activation blockers", ""])
    blockers = report["activation_blockers"]
    lines.extend(f"- `{blocker}`" for blocker in blockers)
    if not blockers:
        lines.append("None.")
    lines.extend(["", "This report is generated. The observatory never applies source patches.", ""])
    return "\n".join(lines)


def validate_lock_map_cursor(root: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, int]]:
    lock = load_json(root / LOCK_PATH)
    mapping = load_json(root / MAP_PATH)
    cursor = load_json(root / CURSOR_PATH)
    budgets = load_budgets(root / BUDGETS_PATH)
    if not isinstance(lock, dict) or lock.get("schema_version") != 2:
        raise ObservatoryError("observatory provenance.lock schema_version must be 2")
    if lock.get("state") not in {"prepared", "active"}:
        raise ObservatoryError("observatory lock state must be prepared or active")
    policy = lock.get("policy")
    if policy != {
        "synchronized_editable_copies_allowed": False,
        "automatic_patch_application_allowed": False,
        "one_active_authority_required": True,
    }:
        raise ObservatoryError("observatory anti-synchronization policy drifted")
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 2:
        raise ObservatoryError("legacy path map schema_version must be 2")
    mappings = mapping.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ObservatoryError("legacy path map needs mappings")
    ids: set[str] = set()
    for item in mappings:
        if not isinstance(item, dict):
            raise ObservatoryError("each legacy mapping must be an object")
        required = {"id", "pulp_paths", "vellum_paths", "symbols", "targets", "schemas", "platform_hosts", "contract_tests", "authority"}
        if set(item) != required:
            raise ObservatoryError(f"legacy mapping fields differ: {item.get('id')}")
        identifier = item.get("id")
        if not isinstance(identifier, str) or identifier in ids:
            raise ObservatoryError("legacy mapping IDs must be unique strings")
        ids.add(identifier)
        for field in ("pulp_paths", "vellum_paths", "symbols", "targets", "schemas", "platform_hosts", "contract_tests"):
            validate_string_list(item.get(field), f"mapping.{identifier}.{field}", sorted_unique=False)
    if not isinstance(cursor, dict) or cursor.get("schema_version") != 2 or cursor.get("state") not in {"prepared", "active"}:
        raise ObservatoryError("cursor schema/state is invalid")
    if cursor.get("state") != lock.get("state"):
        raise ObservatoryError("cursor and provenance lock states differ")
    for source, repository in (("pulp", "Generous-Corp/pulp"), ("vellum", "Generous-Corp/vellum")):
        value = cursor.get(source)
        if not isinstance(value, dict) or set(value) != {"repository", "scan_base_commit", "last_scanned_commit", "last_dispatch_event"}:
            raise ObservatoryError(f"cursor.{source} fields differ")
        if value.get("repository") != repository:
            raise ObservatoryError(f"cursor.{source}.repository drifted")
        for field in ("scan_base_commit", "last_scanned_commit"):
            if not isinstance(value.get(field), str) or not SHA_RE.fullmatch(str(value[field])):
                raise ObservatoryError(f"cursor.{source}.{field} must be exact")
    reconciled = cursor.get("reconciled_at")
    if reconciled is not None:
        parse_utc(reconciled, "cursor.reconciled_at")
    return lock, mapping, cursor, budgets


def expected_observations(
    *, source: str, repo: Path, repository: str, start: str, target: str,
    discovered_at: str, mappings: list[dict[str, object]], transitive_rules: list[str]
) -> list[dict[str, object]]:
    result = []
    for commit in commits_between(repo, start, target):
        event = observation_for_commit(
            source=source, repository=repository, repo=repo, commit=commit,
            cursor_from=start, cursor_to=target, discovered_at=discovered_at,
            mappings=mappings, transitive_rules=transitive_rules,
        )
        if event is not None:
            result.append(event)
    return result


def verify_event_against_git(event: dict[str, object], source: str, repo: Path, mappings: list[dict[str, object]], rules: list[str]) -> None:
    expected = observation_for_commit(
        source=source,
        repository=str(event["source_repository"]),
        repo=repo,
        commit=str(event["source_commit"]),
        cursor_from=str(event["scan_cursor"]["from_commit"]),
        cursor_to=str(event["scan_cursor"]["to_commit"]),
        discovered_at=str(event["discovered_at"]),
        mappings=mappings,
        transitive_rules=rules,
    )
    if expected is None:
        raise ObservatoryError(f"event does not correspond to a mapped change: {event['event_id']}")
    derived_fields = {
        "source_repository", "source_commit", "scan_cursor", "direction", "mapped_contracts",
        "mapped_paths", "transitive_paths", "rename_candidates", "patch_id",
        "include_dependency_deltas", "schema_api_deltas", "class", "contract_tests", "contract_keys"
    }
    mismatches = [field for field in sorted(derived_fields) if event[field] != expected[field]]
    if mismatches:
        raise ObservatoryError(f"event derived fields differ from Git for {event['event_id']}: {mismatches}")


def coverage_gaps(
    *, root: Path, mapping: dict[str, object], cursor: dict[str, object], events: list[tuple[Path, dict[str, object]]],
    pulp_repo: Path | None, vellum_repo: Path | None,
    pulp_target: str | None = None, vellum_target: str | None = None
) -> list[dict[str, str]]:
    observations = {
        (str(event["source_repository"]), str(event["source_commit"])): event
        for _, event in events if event["kind"] == "observation"
    }
    gaps: list[dict[str, str]] = []
    mappings = mapping["mappings"]
    rules = mapping.get("transitive_path_rules", [])
    assert isinstance(mappings, list) and isinstance(rules, list)
    for source, repo, requested_target in (
        ("pulp", pulp_repo, pulp_target), ("vellum", vellum_repo, vellum_target)
    ):
        if repo is None:
            gaps.append({"source": source, "commit": "unverified", "reason": "source-repository-not-supplied"})
            continue
        source_cursor = cursor[source]
        repository = str(source_cursor["repository"])
        start = str(source_cursor["scan_base_commit"])
        target = str(source_cursor["last_scanned_commit"])
        require_commit(repo, start, f"cursor.{source}.scan_base_commit")
        require_commit(repo, target, f"cursor.{source}.last_scanned_commit")
        require_ancestor(repo, start, target, f"cursor.{source}")
        scanned_commit_list = commits_between(repo, start, target)
        scanned_commits = set(scanned_commit_list)
        for commit in scanned_commit_list:
            probe = observation_for_commit(
                source=source, repository=repository, repo=repo, commit=commit,
                cursor_from=start, cursor_to=target,
                discovered_at="2000-01-01T00:00:00Z", mappings=mappings, transitive_rules=rules,
            )
            if probe is None:
                continue
            event = observations.get((repository, commit))
            if event is None:
                gaps.append({"source": source, "commit": commit, "reason": "mapped-commit-has-no-event"})
            else:
                verify_event_against_git(event, source, repo, mappings, rules)
        for (event_repository, event_commit), event in observations.items():
            if event_repository != repository:
                continue
            if event_commit not in scanned_commits:
                gaps.append({
                    "source": source,
                    "commit": event_commit,
                    "reason": "observation-outside-reconciled-cursor",
                })
                continue
            verify_event_against_git(event, source, repo, mappings, rules)
        if requested_target is not None:
            require_commit(repo, requested_target, f"requested {source} target")
            require_ancestor(repo, target, requested_target, f"requested {source} target")
            if source == "vellum" and target != requested_target:
                verify_vellum_observatory_tail(repo, target, requested_target)
            for commit in commits_between(repo, target, requested_target):
                probe = observation_for_commit(
                    source=source, repository=repository, repo=repo, commit=commit,
                    cursor_from=target, cursor_to=requested_target,
                    discovered_at="2000-01-01T00:00:00Z", mappings=mappings,
                    transitive_rules=rules,
                )
                if probe is not None:
                    gaps.append({
                        "source": source,
                        "commit": commit,
                        "reason": "mapped-commit-after-reconciled-cursor",
                    })
    return gaps


def verify_vellum_observatory_tail(repo: Path, cursor: str, target: str) -> None:
    """Prove that commits after the reconciled source cursor contain evidence only."""
    require_commit(repo, cursor, "cursor.vellum.last_scanned_commit")
    require_commit(repo, target, "requested vellum target")
    require_ancestor(repo, cursor, target, "requested vellum target")
    allowed_modified = {
        CURSOR_PATH.as_posix(),
        REPORT_JSON_PATH.as_posix(),
        REPORT_MD_PATH.as_posix(),
    }
    events_prefix = EVENTS_PATH.as_posix() + "/"
    previous = cursor
    for commit in commits_between(repo, cursor, target):
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if (
            len(parents) == 3
            and previous in parents[1:]
            and git(repo, "rev-parse", f"{commit}^{{tree}}")
            == git(repo, "rev-parse", f"{previous}^{{tree}}")
        ):
            previous = commit
            continue
        if len(parents) != 2:
            raise ObservatoryError(
                f"Vellum observatory evidence tail must be linear: {commit}"
            )
        if parents[1] != previous:
            raise ObservatoryError(
                f"Vellum observatory evidence tail is discontinuous: {commit}"
            )
        for entry in parse_diff_entries(repo, commit):
            status = str(entry["status"])
            path = str(entry["path"])
            if "old_path" in entry:
                raise ObservatoryError(
                    f"Vellum observatory evidence tail forbids rename/copy at {commit}: "
                    f"{entry['old_path']} -> {path}"
                )
            if path in allowed_modified and status == "M":
                continue
            relative_event = path[len(events_prefix):] if path.startswith(events_prefix) else ""
            if (
                status == "A"
                and relative_event
                and "/" not in relative_event
                and relative_event.endswith(".yaml")
            ):
                continue
            raise ObservatoryError(
                f"Vellum observatory evidence tail contains non-evidence change at "
                f"{commit}: {status} {path}"
            )
        previous = commit


def verify_append_only(root: Path, git_base: str | None) -> None:
    if git_base is None:
        return
    require_commit(root, git_base, "git base")
    head = git(root, "rev-parse", "HEAD")
    require_ancestor(root, git_base, head, "append-only comparison")
    output = git(root, "diff", "--name-status", "-M", f"{git_base}..{head}", "--", EVENTS_PATH.as_posix())
    bad = [line for line in output.splitlines() if line and not line.startswith("A\t")]
    if bad:
        raise ObservatoryError("observatory events are append-only; modified/deleted/renamed events: " + "; ".join(bad))
    try:
        previous = json.loads(git(root, "show", f"{git_base}:{CURSOR_PATH.as_posix()}"))
    except ObservatoryError:
        return
    current = load_json(root / CURSOR_PATH)
    for source in ("pulp", "vellum"):
        before = previous[source]["last_scanned_commit"]
        after = current[source]["last_scanned_commit"]
        repo = root if source == "vellum" else None
        if repo is not None:
            require_ancestor(repo, before, after, f"cursor.{source} cannot move backward")


def verify(
    *, root: Path, pulp_repo: Path | None, vellum_repo: Path | None, git_base: str | None,
    allow_missing_source_repositories: bool = False,
    pulp_target: str | None = None, vellum_target: str | None = None
) -> dict[str, object]:
    lock, mapping, cursor, budgets = validate_lock_map_cursor(root)
    events = load_events(root)
    verify_append_only(root, git_base)
    gaps = coverage_gaps(
        root=root, mapping=mapping, cursor=cursor, events=events,
        pulp_repo=pulp_repo, vellum_repo=vellum_repo,
        pulp_target=pulp_target, vellum_target=vellum_target,
    )
    if allow_missing_source_repositories:
        gaps = [gap for gap in gaps if gap["reason"] != "source-repository-not-supplied"]
    now = parse_utc(cursor["reconciled_at"], "cursor.reconciled_at") if cursor.get("reconciled_at") else None
    report = build_report(root=root, lock=lock, cursor=cursor, events=events, budgets=budgets, now=now, coverage_gaps=gaps)
    committed_json = load_json(root / REPORT_JSON_PATH)
    if committed_json != report:
        raise ObservatoryError("reports/current.json is stale; run observatory reconcile")
    committed_md = (root / REPORT_MD_PATH).read_text(encoding="utf-8")
    if committed_md != render_markdown(report):
        raise ObservatoryError("reports/current.md is stale; run observatory reconcile")
    if report["health"] != "pass":
        raise ObservatoryError("observatory health budgets failed: " + ", ".join(report["budget_violations"]))
    return report


def reconcile(
    *, root: Path, pulp_repo: Path, vellum_repo: Path, pulp_target: str, vellum_target: str,
    now_text: str, write: bool
) -> dict[str, object]:
    now = parse_utc(now_text, "--now")
    lock, mapping, cursor, budgets = validate_lock_map_cursor(root)
    mappings = mapping["mappings"]
    rules = mapping.get("transitive_path_rules", [])
    assert isinstance(mappings, list) and isinstance(rules, list)
    existing = load_events(root)
    observations = {
        (str(event["source_repository"]), str(event["source_commit"])): event
        for _, event in existing if event["kind"] == "observation"
    }
    new_events: list[dict[str, object]] = []
    for source, repo, target in (("pulp", pulp_repo, pulp_target), ("vellum", vellum_repo, vellum_target)):
        source_cursor = cursor[source]
        start = str(source_cursor["last_scanned_commit"])
        repository = str(source_cursor["repository"])
        for event in expected_observations(
            source=source, repo=repo, repository=repository, start=start, target=target,
            discovered_at=now_text, mappings=mappings, transitive_rules=rules,
        ):
            key = (repository, str(event["source_commit"]))
            if key in observations:
                verify_event_against_git(observations[key], source, repo, mappings, rules)
            else:
                new_events.append(event)
        source_cursor["last_scanned_commit"] = target
    cursor["reconciled_at"] = now_text
    combined = existing + [(root / EVENTS_PATH / f"{event['event_id']}.yaml", event) for event in new_events]
    gaps = coverage_gaps(root=root, mapping=mapping, cursor=cursor, events=combined, pulp_repo=pulp_repo, vellum_repo=vellum_repo)
    report = build_report(root=root, lock=lock, cursor=cursor, events=combined, budgets=budgets, now=now, coverage_gaps=gaps)
    if write:
        for event in new_events:
            path = root / EVENTS_PATH / f"{event['event_id']}.yaml"
            if path.exists():
                raise ObservatoryError(f"refusing to overwrite append-only event: {path}")
            write_json_atomic(path, event)
        write_json_atomic(root / CURSOR_PATH, cursor)
        write_json_atomic(root / REPORT_JSON_PATH, report)
        write_text_atomic(root / REPORT_MD_PATH, render_markdown(report))
    return {"new_events": [event["event_id"] for event in new_events], "cursor": cursor, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--pulp-repo", type=Path)
    verify_parser.add_argument("--vellum-repo", type=Path)
    verify_parser.add_argument("--git-base")
    verify_parser.add_argument("--pulp-target")
    verify_parser.add_argument("--vellum-target")
    verify_parser.add_argument("--allow-missing-source-repositories", action="store_true")
    verify_parser.add_argument("--output", type=Path)
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--pulp-repo", type=Path, required=True)
    reconcile_parser.add_argument("--vellum-repo", type=Path, required=True)
    reconcile_parser.add_argument("--pulp-target", required=True)
    reconcile_parser.add_argument("--vellum-target", required=True)
    reconcile_parser.add_argument("--now", required=True)
    reconcile_parser.add_argument("--write", action="store_true")
    reconcile_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "verify":
            result = verify(
                root=root,
                pulp_repo=args.pulp_repo.resolve() if args.pulp_repo else None,
                vellum_repo=args.vellum_repo.resolve() if args.vellum_repo else None,
                git_base=args.git_base,
                allow_missing_source_repositories=args.allow_missing_source_repositories,
                pulp_target=args.pulp_target,
                vellum_target=args.vellum_target,
            )
        else:
            result = reconcile(
                root=root,
                pulp_repo=args.pulp_repo.resolve(),
                vellum_repo=args.vellum_repo.resolve(),
                pulp_target=args.pulp_target,
                vellum_target=args.vellum_target,
                now_text=args.now,
                write=args.write,
            )
        output = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        print(output, end="")
        return 0
    except (ObservatoryError, OSError, ValueError) as error:
        print(f"observatory: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
