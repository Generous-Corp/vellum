#!/usr/bin/env python3
"""Validate and plan coalesced Pulp observatory receiver deliveries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any


SHA_RE = re.compile(r"[0-9a-f]{40}")
EVENT_PATH_RE = re.compile(
    r"\.github/vellum-change-events/[A-Za-z0-9][A-Za-z0-9._-]{1,160}\.json"
)
EVENT_PREFIX = ".github/vellum-change-events/"


class ReceiverError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=False, capture_output=True, text=True
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReceiverError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def require_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ReceiverError(f"{field} must be a full lowercase commit SHA")
    return value


def require_commit(repo: Path, value: str, field: str) -> str:
    require_sha(value, field)
    try:
        resolved = git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    except ReceiverError as error:
        raise ReceiverError(f"{field} is not available: {value}") from error
    if resolved != value:
        raise ReceiverError(f"{field} did not resolve exactly: {value}")
    return value


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise ReceiverError(result.stderr.strip() or "git merge-base failed")
    return result.returncode == 0


def canonical_sha256(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def canonical_line_sha256(value: object) -> str:
    data = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    return hashlib.sha256(data).hexdigest()


def json_at(repo: Path, commit: str, path: str) -> dict[str, Any]:
    try:
        value = json.loads(git(repo, "show", f"{commit}:{path}"))
    except json.JSONDecodeError as error:
        raise ReceiverError(f"durable event is not JSON: {commit}:{path}") from error
    if not isinstance(value, dict):
        raise ReceiverError(f"durable event must be an object: {commit}:{path}")
    return value


def validate_event_path(value: object) -> str:
    if not isinstance(value, str) or EVENT_PATH_RE.fullmatch(value) is None:
        raise ReceiverError("event path is not direct and safe")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ReceiverError("event path is not direct and safe")
    return value


def validate_payload(
    payload: object, *, pulp_repo: Path, source_commit: str
) -> list[str]:
    if not isinstance(payload, dict):
        raise ReceiverError("repository dispatch payload must be an object")
    required = {
        "schema_version",
        "source_repository",
        "source_base",
        "source_commit",
        "source_head",
        "direction",
        "affected_slices",
        "transferred_slices",
        "event_refs",
        "ownership_projection_sha256",
    }
    if set(payload) != required:
        raise ReceiverError(
            "dispatch payload fields differ: "
            f"missing={sorted(required - set(payload))} "
            f"unknown={sorted(set(payload) - required)}"
        )
    if payload["schema_version"] != 1:
        raise ReceiverError("dispatch payload schema_version must be 1")
    if payload["source_repository"] != "Generous-Corp/pulp":
        raise ReceiverError("dispatch source repository differs")
    if payload["direction"] != "pulp-to-framework":
        raise ReceiverError("dispatch direction differs")
    if require_sha(payload["source_commit"], "payload source_commit") != source_commit:
        raise ReceiverError("payload source_commit differs from supplied lower bound")
    source_base = require_commit(
        pulp_repo,
        require_sha(payload["source_base"], "payload source_base"),
        "payload source_base",
    )
    if not is_ancestor(pulp_repo, source_base, source_commit):
        raise ReceiverError("payload source_base is not an ancestor of source_commit")
    if require_sha(payload["source_head"], "payload source_head") != source_commit:
        raise ReceiverError("payload source_head differs from source_commit")
    for field in ("affected_slices", "transferred_slices"):
        value = payload[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ReceiverError(f"payload {field} must be a string array")
    projection_hash = payload["ownership_projection_sha256"]
    if not isinstance(projection_hash, str) or re.fullmatch(
        r"[0-9a-f]{64}", payload["ownership_projection_sha256"]
    ) is None:
        raise ReceiverError("ownership projection hash is invalid")
    projection = json_at(
        pulp_repo, source_commit, ".github/vellum-ownership.json"
    )
    if canonical_line_sha256(projection) != projection_hash:
        raise ReceiverError("ownership projection hash differs")
    refs = payload["event_refs"]
    if not isinstance(refs, list) or not refs:
        raise ReceiverError("dispatch must contain durable event coverage")
    paths: list[str] = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise ReceiverError("event ref fields differ")
        path = validate_event_path(ref["path"])
        expected = ref["sha256"]
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ReceiverError(f"event hash is invalid: {path}")
        event = json_at(pulp_repo, source_commit, path)
        if canonical_sha256(event) != expected:
            raise ReceiverError(f"durable event hash differs: {path}")
        if event.get("kind") not in {"change", "authority-transition"}:
            raise ReceiverError(f"durable event kind is invalid: {path}")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise ReceiverError("dispatch contains duplicate event paths")
    delta = git(
        pulp_repo,
        "diff",
        "--name-status",
        "--no-renames",
        source_base,
        source_commit,
        "--",
        EVENT_PREFIX,
    )
    added: list[str] = []
    for line in delta.splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or fields[0] != "A":
            raise ReceiverError("delivered durable events are not append-only")
        added.append(validate_event_path(fields[1]))
    if set(added) != set(paths):
        raise ReceiverError("event refs do not exactly cover the delivered range")
    return paths


def durable_event_range(repo: Path, cursor: str, main: str) -> tuple[str, str]:
    if not is_ancestor(repo, cursor, main):
        raise ReceiverError("fresh Pulp main is not a descendant of the committed cursor")
    commits = git(
        repo, "rev-list", "--reverse", "--topo-order", f"{cursor}..{main}"
    ).splitlines()
    paths: set[str] = set()
    event_commits: set[str] = set()
    for commit in commits:
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        for parent in parents:
            delta = git(
                repo,
                "diff",
                "--name-status",
                "--no-renames",
                parent,
                commit,
                "--",
                EVENT_PREFIX,
            )
            for line in delta.splitlines():
                if not line:
                    continue
                fields = line.split("\t")
                if len(fields) != 2 or fields[0] != "A":
                    raise ReceiverError(
                        "durable Pulp events must be append-only on every history edge"
                    )
                paths.add(validate_event_path(fields[1]))
                event_commits.add(commit)
    for path in paths:
        event = json_at(repo, main, path)
        if event.get("kind") not in {"change", "authority-transition"}:
            raise ReceiverError(f"durable event kind is invalid: {path}")
    if not paths:
        return cursor, cursor
    oldest = min(
        event_commits,
        key=lambda commit: int(git(repo, "show", "-s", "--format=%ct", commit)),
    )
    return main, oldest


def latest_durable_event_head(repo: Path, cursor: str, main: str) -> str:
    target, _ = durable_event_range(repo, cursor, main)
    return target


def load_cursor(root: Path) -> str:
    path = root / "provenance/pulp-observatory/cursor.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return require_sha(value["pulp"]["last_scanned_commit"], "committed cursor")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ReceiverError(f"cannot read committed Pulp cursor: {error}") from error


def plan(
    *, root: Path, pulp_repo: Path, pulp_main: str, lower_bound: str,
    payload: object | None,
) -> dict[str, object]:
    cursor = require_commit(pulp_repo, load_cursor(root), "committed cursor")
    main = require_commit(pulp_repo, pulp_main, "fresh Pulp main")
    lower = require_commit(pulp_repo, lower_bound, "supplied lower bound")
    if payload is None and not is_ancestor(pulp_repo, cursor, lower):
        raise ReceiverError("trusted replay target must descend from the cursor")
    if payload is not None and not (
        is_ancestor(pulp_repo, cursor, lower)
        or is_ancestor(pulp_repo, lower, cursor)
    ):
        raise ReceiverError("dispatch lower bound is divergent from the cursor")
    if not is_ancestor(pulp_repo, lower, main):
        raise ReceiverError("supplied lower bound is not landed on fresh Pulp main")
    if payload is not None:
        validate_payload(payload, pulp_repo=pulp_repo, source_commit=lower)
    latest, oldest_event = durable_event_range(pulp_repo, cursor, main)
    if not is_ancestor(pulp_repo, lower, latest):
        raise ReceiverError("fresh durable event head does not cover the supplied lower bound")
    covered_lower = is_ancestor(pulp_repo, lower, cursor)
    covered_latest = is_ancestor(pulp_repo, latest, cursor)
    status = "noop" if covered_lower and covered_latest else "reconcile"
    return {
        "schema_version": 1,
        "status": status,
        "committed_cursor": cursor,
        "supplied_lower_bound": lower,
        "pulp_main": main,
        "latest_durable_event_head": latest,
        "oldest_uncovered_durable_event_commit": oldest_event,
        "reconcile_target": latest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pulp-repo", type=Path, required=True)
    parser.add_argument("--pulp-main", required=True)
    parser.add_argument("--lower-bound", required=True)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = None
        if args.payload:
            payload = json.loads(args.payload.read_text(encoding="utf-8"))
        result = plan(
            root=args.root.resolve(),
            pulp_repo=args.pulp_repo.resolve(),
            pulp_main=args.pulp_main,
            lower_bound=args.lower_bound,
            payload=payload,
        )
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    except (ReceiverError, OSError, json.JSONDecodeError) as error:
        print(f"receiver: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
