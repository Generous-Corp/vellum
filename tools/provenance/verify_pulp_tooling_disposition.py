#!/usr/bin/env python3
"""Verify Vellum's immutable observation of Pulp developer tooling.

This gate protects a future Pulp-consumer migration from silently forgetting
Pulp-owned or candidate tooling. It deliberately does not claim that Vellum
owns, implements, or has adopted any recorded surface.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_PULP_REPOSITORY = "https://github.com/Generous-Corp/pulp.git"
EXPECTED_PULP_COMMIT = "b63008422a7a0657e428a3d9deb947698855b7b3"
EXPECTED_MAP_PATH = "docs/status/pulp-tooling-disposition.json"
EXPECTED_SNAPSHOT_PATH = (
    "provenance/pulp-tooling-disposition/pulp-tooling-disposition.v1.json"
)
EXPECTED_LOCK_PATH = "provenance/pulp-tooling-disposition/source-lock.v1.json"
EXPECTED_MAP_GIT_BLOB_SHA1 = "7b8e603d9569a463cbe16c68f7d2472b52774305"
EXPECTED_MAP_SHA256 = "421100ebd9d15890a60fb9871a4eb96329e9fc374c9f41b7ca15208e68704446"
EXPECTED_SOURCE_BLOBS_SHA256 = (
    "32fc04c980cb13164b628b5cf0030bb1fc83dd707583489b524d192a2a8ed7b0"
)
EXPECTED_SOURCE_BLOB_COUNT = 90
EXPECTED_COUNTS = {
    "cli": 54,
    "claude_commands": 28,
    "agent_skills": 55,
    "mcp_tools": 65,
    "plugin_registrations": 3,
}
EXPECTED_CATEGORIES = set(EXPECTED_COUNTS)
EXPECTED_DISPOSITIONS = {"pulp-owned", "candidate-shared-later", "excluded"}
EXPECTED_STATUS = {
    "authority_transfer": False,
    "pulp_adoption": False,
    "pulp_remains_owner": True,
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_MODES = {"100644", "100755", "120000"}


class DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
        raise ValueError(f"{path}: cannot load strict JSON: {exc}") from exc


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git identity


def _exact_keys(
    value: Any,
    expected: set[str],
    location: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location}: expected object")
        return False
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{location}: keys differ; missing={sorted(expected - actual)} "
            f"unexpected={sorted(actual - expected)}"
        )
        return False
    return True


def _nonempty_string(value: Any, location: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location}: expected non-empty string")
        return False
    return True


def _validate_argument(value: Any, location: str, errors: list[str]) -> None:
    allowed = {"name", "kind", "required"}
    if not isinstance(value, dict):
        errors.append(f"{location}: expected object")
        return
    unexpected = set(value) - allowed
    if unexpected:
        errors.append(f"{location}: unexpected keys {sorted(unexpected)}")
    _nonempty_string(value.get("name"), f"{location}.name", errors)
    if "kind" in value and value["kind"] not in {
        "environment",
        "flag",
        "option",
        "passthrough",
        "positional",
        "value",
    }:
        errors.append(f"{location}.kind: invalid value {value['kind']!r}")
    if "required" in value and not isinstance(value["required"], bool):
        errors.append(f"{location}.required: expected boolean")


def _validate_cli_node(
    value: Any,
    location: str,
    errors: list[str],
    *,
    top_level: bool,
) -> None:
    expected = {"name", "arguments", "subcommands"}
    if top_level:
        expected.add("disposition")
    if not _exact_keys(value, expected, location, errors):
        return
    _nonempty_string(value["name"], f"{location}.name", errors)
    if top_level and value["disposition"] not in EXPECTED_DISPOSITIONS:
        errors.append(f"{location}.disposition: invalid value {value['disposition']!r}")

    arguments = value["arguments"]
    if not isinstance(arguments, list):
        errors.append(f"{location}.arguments: expected array")
    else:
        names: set[str] = set()
        for index, argument in enumerate(arguments):
            _validate_argument(argument, f"{location}.arguments[{index}]", errors)
            if isinstance(argument, dict) and isinstance(argument.get("name"), str):
                if argument["name"] in names:
                    errors.append(
                        f"{location}.arguments: duplicate name {argument['name']!r}"
                    )
                names.add(argument["name"])

    subcommands = value["subcommands"]
    if not isinstance(subcommands, list):
        errors.append(f"{location}.subcommands: expected array")
    else:
        names = set()
        for index, subcommand in enumerate(subcommands):
            child = f"{location}.subcommands[{index}]"
            _validate_cli_node(subcommand, child, errors, top_level=False)
            if isinstance(subcommand, dict) and isinstance(subcommand.get("name"), str):
                if subcommand["name"] in names:
                    errors.append(
                        f"{location}.subcommands: duplicate name "
                        f"{subcommand['name']!r}"
                    )
                names.add(subcommand["name"])


def validate_inventory(data: Any) -> list[str]:
    errors: list[str] = []
    top_keys = {
        "schema_version",
        "purpose",
        "inheritance",
        "cli_argument_provenance",
        "classification_meanings",
        "source_files",
        "entries",
    }
    if not _exact_keys(data, top_keys, "map", errors):
        return errors
    if data["schema_version"] != 1:
        errors.append("map.schema_version: expected 1")
    _nonempty_string(data["purpose"], "map.purpose", errors)
    _nonempty_string(data["inheritance"], "map.inheritance", errors)

    provenance_keys = {"inventory_source", "parser_guard", "limitation"}
    if _exact_keys(
        data["cli_argument_provenance"],
        provenance_keys,
        "map.cli_argument_provenance",
        errors,
    ):
        for key in sorted(provenance_keys):
            _nonempty_string(
                data["cli_argument_provenance"][key],
                f"map.cli_argument_provenance.{key}",
                errors,
            )

    meanings = data["classification_meanings"]
    if _exact_keys(
        meanings,
        EXPECTED_DISPOSITIONS,
        "map.classification_meanings",
        errors,
    ):
        for key in sorted(EXPECTED_DISPOSITIONS):
            _nonempty_string(meanings[key], f"map.classification_meanings.{key}", errors)

    sources = data["source_files"]
    if _exact_keys(sources, EXPECTED_CATEGORIES, "map.source_files", errors):
        for category in sorted(EXPECTED_CATEGORIES):
            patterns = sources[category]
            if not isinstance(patterns, list) or not patterns:
                errors.append(f"map.source_files.{category}: expected non-empty array")
                continue
            if len(patterns) != len(set(patterns)):
                errors.append(f"map.source_files.{category}: duplicate pattern")
            for index, pattern in enumerate(patterns):
                _nonempty_string(
                    pattern,
                    f"map.source_files.{category}[{index}]",
                    errors,
                )

    entries = data["entries"]
    if not _exact_keys(entries, EXPECTED_CATEGORIES, "map.entries", errors):
        return errors
    for category in sorted(EXPECTED_CATEGORIES):
        rows = entries[category]
        if not isinstance(rows, list):
            errors.append(f"map.entries.{category}: expected array")
            continue
        if len(rows) != EXPECTED_COUNTS[category]:
            errors.append(
                f"map.entries.{category}: expected {EXPECTED_COUNTS[category]} "
                f"entries, got {len(rows)}"
            )
        names: set[str] = set()
        for index, row in enumerate(rows):
            location = f"map.entries.{category}[{index}]"
            if category == "cli":
                _validate_cli_node(row, location, errors, top_level=True)
            elif category == "plugin_registrations":
                if not isinstance(row, dict):
                    errors.append(f"{location}: expected object")
                    continue
                if not {"name", "disposition"}.issubset(row):
                    errors.append(f"{location}: missing name or disposition")
                _nonempty_string(row.get("name"), f"{location}.name", errors)
                if row.get("disposition") not in EXPECTED_DISPOSITIONS:
                    errors.append(
                        f"{location}.disposition: invalid value "
                        f"{row.get('disposition')!r}"
                    )
                for list_key in ("registration_keys", "environment_keys", "header_keys"):
                    if list_key in row:
                        values = row[list_key]
                        if (
                            not isinstance(values, list)
                            or not all(isinstance(item, str) and item for item in values)
                            or len(values) != len(set(values))
                        ):
                            errors.append(
                                f"{location}.{list_key}: expected unique string array"
                            )
            else:
                if _exact_keys(row, {"name", "disposition"}, location, errors):
                    _nonempty_string(row["name"], f"{location}.name", errors)
                    if row["disposition"] not in EXPECTED_DISPOSITIONS:
                        errors.append(
                            f"{location}.disposition: invalid value "
                            f"{row['disposition']!r}"
                        )
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                if row["name"] in names:
                    errors.append(
                        f"map.entries.{category}: duplicate name {row['name']!r}"
                    )
                names.add(row["name"])
    return errors


def _canonical_source_rows(
    concrete: dict[str, Any],
    category_order: list[str],
) -> bytes:
    rows: list[dict[str, str]] = []
    for category in category_order:
        for row in concrete[category]:
            rows.append({"category": category, **row})
    return json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_lock(lock: Any, inventory: Any) -> list[str]:
    errors: list[str] = []
    top_keys = {
        "schema_version",
        "record_kind",
        "purpose",
        "status",
        "pulp_source",
        "inventory_counts",
        "required_inventory_categories",
        "required_source_categories",
        "allowed_dispositions",
        "verification",
    }
    if not _exact_keys(lock, top_keys, "lock", errors):
        return errors
    if lock["schema_version"] != 1:
        errors.append("lock.schema_version: expected 1")
    if lock["record_kind"] != "pulp-tooling-disposition-observation":
        errors.append("lock.record_kind: unexpected value")
    _nonempty_string(lock["purpose"], "lock.purpose", errors)

    status = lock["status"]
    status_keys = {*EXPECTED_STATUS, "note"}
    if _exact_keys(status, status_keys, "lock.status", errors):
        for key, expected in EXPECTED_STATUS.items():
            if status[key] is not expected:
                errors.append(f"lock.status.{key}: expected {expected!r}")
        _nonempty_string(status["note"], "lock.status.note", errors)

    source_keys = {
        "repository",
        "commit",
        "authoritative_map",
        "concrete_source_blobs",
        "concrete_source_blob_count",
        "concrete_source_blobs_sha256",
    }
    source = lock["pulp_source"]
    if not _exact_keys(source, source_keys, "lock.pulp_source", errors):
        return errors
    if source["repository"] != EXPECTED_PULP_REPOSITORY:
        errors.append("lock.pulp_source.repository: differs from pinned baseline")
    if source["commit"] != EXPECTED_PULP_COMMIT or not HEX40.fullmatch(
        str(source["commit"])
    ):
        errors.append("lock.pulp_source.commit: differs from pinned Pulp commit")

    map_record = source["authoritative_map"]
    map_keys = {"path", "git_blob_sha1", "sha256", "vellum_snapshot_path"}
    if _exact_keys(
        map_record,
        map_keys,
        "lock.pulp_source.authoritative_map",
        errors,
    ):
        expected = {
            "path": EXPECTED_MAP_PATH,
            "git_blob_sha1": EXPECTED_MAP_GIT_BLOB_SHA1,
            "sha256": EXPECTED_MAP_SHA256,
            "vellum_snapshot_path": EXPECTED_SNAPSHOT_PATH,
        }
        for key, value in expected.items():
            if map_record[key] != value:
                errors.append(
                    f"lock.pulp_source.authoritative_map.{key}: "
                    "differs from pinned baseline"
                )

    if lock["inventory_counts"] != EXPECTED_COUNTS:
        errors.append("lock.inventory_counts: differs from pinned baseline")
    expected_categories = sorted(EXPECTED_CATEGORIES)
    if lock["required_inventory_categories"] != expected_categories:
        errors.append("lock.required_inventory_categories: incomplete or reordered")
    if lock["required_source_categories"] != expected_categories:
        errors.append("lock.required_source_categories: incomplete or reordered")
    if lock["allowed_dispositions"] != sorted(EXPECTED_DISPOSITIONS):
        errors.append("lock.allowed_dispositions: differs from required vocabulary")

    concrete = source["concrete_source_blobs"]
    if not isinstance(concrete, dict) or set(concrete) != EXPECTED_CATEGORIES:
        errors.append("lock.pulp_source.concrete_source_blobs: category set differs")
    else:
        global_paths: set[str] = set()
        for category in inventory["source_files"]:
            rows = concrete.get(category)
            if not isinstance(rows, list) or not rows:
                errors.append(
                    f"lock.pulp_source.concrete_source_blobs.{category}: "
                    "expected non-empty array"
                )
                continue
            prior_path = ""
            matched_patterns = {pattern: 0 for pattern in inventory["source_files"][category]}
            for index, row in enumerate(rows):
                location = (
                    f"lock.pulp_source.concrete_source_blobs.{category}[{index}]"
                )
                if not _exact_keys(
                    row,
                    {"path", "git_mode", "git_blob_sha1"},
                    location,
                    errors,
                ):
                    continue
                path = row["path"]
                if not _nonempty_string(path, f"{location}.path", errors):
                    continue
                if path.startswith("/") or ".." in Path(path).parts:
                    errors.append(f"{location}.path: must be repository-relative")
                if path <= prior_path:
                    errors.append(
                        f"lock.pulp_source.concrete_source_blobs.{category}: "
                        "paths must be sorted and unique"
                    )
                prior_path = path
                if path in global_paths:
                    errors.append(f"{location}.path: duplicate across categories")
                global_paths.add(path)
                if row["git_mode"] not in GIT_MODES:
                    errors.append(f"{location}.git_mode: invalid Git mode")
                if not HEX40.fullmatch(str(row["git_blob_sha1"])):
                    errors.append(f"{location}.git_blob_sha1: expected 40 lowercase hex")
                for pattern in matched_patterns:
                    if fnmatch.fnmatchcase(path, pattern):
                        matched_patterns[pattern] += 1
            for pattern, count in matched_patterns.items():
                if count == 0:
                    errors.append(
                        f"lock.pulp_source.concrete_source_blobs.{category}: "
                        f"source pattern {pattern!r} has no concrete blob"
                    )
        if len(global_paths) != EXPECTED_SOURCE_BLOB_COUNT:
            errors.append(
                "lock.pulp_source.concrete_source_blobs: expected "
                f"{EXPECTED_SOURCE_BLOB_COUNT} unique paths, got {len(global_paths)}"
            )
        try:
            canonical = _canonical_source_rows(
                concrete,
                list(inventory["source_files"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                "lock.pulp_source.concrete_source_blobs: cannot canonicalize "
                f"malformed rows: {exc}"
            )
        else:
            actual_digest = sha256_bytes(canonical)
            if source["concrete_source_blobs_sha256"] != actual_digest:
                errors.append(
                    "lock.pulp_source.concrete_source_blobs_sha256: "
                    "does not match concrete rows"
                )
            if actual_digest != EXPECTED_SOURCE_BLOBS_SHA256:
                errors.append(
                    "lock.pulp_source.concrete_source_blobs: differs from pinned baseline"
                )

    if source["concrete_source_blob_count"] != EXPECTED_SOURCE_BLOB_COUNT:
        errors.append("lock.pulp_source.concrete_source_blob_count: differs from baseline")
    if not HEX64.fullmatch(str(source["concrete_source_blobs_sha256"])):
        errors.append(
            "lock.pulp_source.concrete_source_blobs_sha256: expected lowercase sha256"
        )

    verification = lock["verification"]
    if _exact_keys(verification, {"command", "policy"}, "lock.verification", errors):
        if verification["command"] != (
            "python3 tools/provenance/verify_pulp_tooling_disposition.py"
        ):
            errors.append("lock.verification.command: unexpected command")
        _nonempty_string(verification["policy"], "lock.verification.policy", errors)
    return errors


def verify(root: Path) -> dict[str, Any]:
    snapshot = root / EXPECTED_SNAPSHOT_PATH
    lock_path = root / EXPECTED_LOCK_PATH
    errors: list[str] = []
    checks: dict[str, Any] = {}

    try:
        snapshot_bytes = snapshot.read_bytes()
    except OSError as exc:
        return {
            "schema_version": 1,
            "status": "fail",
            "errors": [f"{snapshot}: cannot read snapshot: {exc}"],
            "checks": checks,
        }

    map_sha256 = sha256_bytes(snapshot_bytes)
    map_git_blob = git_blob_sha1(snapshot_bytes)
    checks["map_sha256"] = map_sha256
    checks["map_git_blob_sha1"] = map_git_blob
    if map_sha256 != EXPECTED_MAP_SHA256:
        errors.append("tooling map snapshot sha256 differs from pinned baseline")
    if map_git_blob != EXPECTED_MAP_GIT_BLOB_SHA1:
        errors.append("tooling map snapshot Git blob differs from pinned baseline")

    try:
        inventory = load_json(snapshot)
    except ValueError as exc:
        errors.append(str(exc))
        inventory = None
    inventory_valid = False
    if inventory is not None:
        inventory_errors = validate_inventory(inventory)
        errors.extend(inventory_errors)
        inventory_valid = not inventory_errors
        checks["inventory_counts"] = {
            key: len(inventory.get("entries", {}).get(key, []))
            if isinstance(inventory.get("entries", {}).get(key), list)
            else None
            for key in sorted(EXPECTED_CATEGORIES)
        }

    try:
        lock = load_json(lock_path)
    except ValueError as exc:
        errors.append(str(exc))
        lock = None
    if lock is not None and inventory_valid:
        errors.extend(validate_lock(lock, inventory))
        checks["pulp_commit"] = lock.get("pulp_source", {}).get("commit")
        checks["source_blob_count"] = lock.get("pulp_source", {}).get(
            "concrete_source_blob_count"
        )
        checks["authority_transfer"] = lock.get("status", {}).get(
            "authority_transfer"
        )
        checks["pulp_adoption"] = lock.get("status", {}).get("pulp_adoption")

    return {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the pinned Pulp tooling-disposition observation"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Vellum repository root",
    )
    parser.add_argument("--output", type=Path, help="write JSON evidence")
    args = parser.parse_args(argv)

    report = verify(args.root.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if report["status"] == "pass":
        counts = report["checks"]["inventory_counts"]
        rendered = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
        print(
            "pulp-tooling-disposition: OK "
            f"pulp_commit={report['checks']['pulp_commit']} {rendered}"
        )
        return 0

    for error in report["errors"]:
        print(f"pulp-tooling-disposition: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
