"""Shared primitives and schema constants for the Pulp/Vellum observatory."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


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
        raise ObservatoryError(
            f"observatory budget keys differ: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
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
