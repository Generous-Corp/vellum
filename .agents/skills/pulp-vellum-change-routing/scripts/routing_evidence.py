#!/usr/bin/env python3
"""Durable evidence validators for the Pulp/Vellum change router."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ISSUE_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/issues/[1-9][0-9]*$")
OWNER_RE = re.compile(
    r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
    r"(?:/[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99}))?$"
)
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class EvidenceReadError(RuntimeError):
    """Committed evidence could not be read from the selected checkout."""


def _git_text(repo: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise EvidenceReadError(
            f"cannot read Git evidence in {repo}: {' '.join(arguments)}"
        ) from exc


def commit_exists(repo: Path, commit: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def commit_is_ancestor(repo: Path, commit: str, head: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, head],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def validate_emergency(
    authority: Any,
    event_path: str | None,
    owner: str | None,
    created: str | None,
    expiry: str | None,
    follow_up: str | None,
    matched_slices: set[str],
    now: dt.date,
) -> list[str]:
    """Validate an emergency against immutable, append-only Pulp event state."""
    problems: list[str] = []
    parsed_path = PurePosixPath(event_path or "")
    if (
        not event_path
        or parsed_path.is_absolute()
        or "\\" in event_path
        or any(part in {".", ".."} for part in parsed_path.parts)
        or parsed_path.as_posix() != event_path
        or parsed_path.parent.as_posix() != ".github/vellum-change-events"
        or parsed_path.suffix != ".json"
    ):
        return [
            "emergency requires a normalized committed Pulp event under .github/vellum-change-events"
        ]
    try:
        event_text = _git_text(
            authority.pulp, "show", f"{authority.pulp_head}:{event_path}"
        )
    except EvidenceReadError:
        return ["emergency Pulp event is not committed at the supplied Pulp HEAD"]
    try:
        event = json.loads(event_text)
    except json.JSONDecodeError:
        return ["emergency Pulp event must be valid committed JSON"]
    if not isinstance(event, dict):
        return ["emergency Pulp event must contain an object"]
    required = {
        "schema_version",
        "event_id",
        "kind",
        "created_at",
        "slices",
        "rationale",
        "tests",
        "disposition",
        "owner",
        "expiry",
        "follow_up",
    }
    if set(event) != required:
        problems.append("emergency Pulp event has missing or unknown fields")
    if (
        event.get("schema_version") != 1
        or event.get("event_id") != parsed_path.stem
        or event.get("kind") != "change"
        or event.get("disposition") != "emergency-exception"
    ):
        problems.append("emergency Pulp event identity or disposition is invalid")
    if (
        not isinstance(event.get("slices"), list)
        or set(event.get("slices", [])) != matched_slices
        or any(
            not isinstance(value, str) or not value
            for value in event.get("slices", [])
        )
    ):
        problems.append("emergency Pulp event slices must exactly match the routed slices")
    if not isinstance(event.get("rationale"), str) or not event["rationale"].strip():
        problems.append("emergency Pulp event requires rationale")
    if (
        not isinstance(event.get("tests"), list)
        or any(
            not isinstance(value, str) or not value
            for value in event.get("tests", [])
        )
    ):
        problems.append("emergency Pulp event tests must be a string array")
    try:
        event_history = _git_text(
            authority.pulp,
            "log",
            "--format=",
            "--name-status",
            "--diff-merges=separate",
            "--no-renames",
            f"{authority.coordinates['pulp_activation_commit']}..{authority.pulp_head}",
            "--",
            ".github/vellum-change-events",
        ).splitlines()
    except EvidenceReadError:
        problems.append("cannot verify Pulp change-event history")
        event_history = []
    event_changes = [line for line in event_history if line.strip()]
    if any(not line.startswith("A\t") for line in event_changes):
        problems.append(
            "Pulp change-event history after authority activation must be append-only"
        )
    if sum(line == f"A\t{event_path}" for line in event_changes) != 1:
        problems.append("emergency Pulp event must be committed exactly once")
    if not owner or not OWNER_RE.fullmatch(owner):
        problems.append("emergency owner must be a valid @account or @organization/team")
    elif event.get("owner") != owner:
        problems.append("emergency owner disagrees with the committed Pulp event")
    if not follow_up or not ISSUE_RE.fullmatch(follow_up):
        problems.append("emergency follow-up must be a GitHub issue URL")
    elif event.get("follow_up") != follow_up:
        problems.append("emergency follow-up disagrees with the committed Pulp event")
    try:
        created_at = dt.datetime.fromisoformat(
            str(event.get("created_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        created_at = None
        problems.append("emergency Pulp event created_at must be an ISO-8601 timestamp")
    if created_at is not None and created_at.tzinfo is None:
        problems.append("emergency Pulp event created_at must include a timezone")
        created_at = None
    try:
        created_date = dt.date.fromisoformat(created or "")
    except ValueError:
        created_date = None
        problems.append("emergency creation date must be YYYY-MM-DD")
    else:
        if created_date > now:
            problems.append("emergency creation date cannot be in the future")
        if created_at is not None and created_date != created_at.date():
            problems.append(
                "emergency creation date disagrees with the committed Pulp event"
            )
    try:
        expiry_date = dt.date.fromisoformat(expiry or "")
    except ValueError:
        expiry_date = None
        problems.append("emergency expiry must be YYYY-MM-DD")
    if expiry_date is not None:
        if event.get("expiry") != expiry:
            problems.append("emergency expiry disagrees with the committed Pulp event")
        if expiry_date < now:
            problems.append("emergency is already expired")
        if created_date is not None:
            if expiry_date < created_date:
                problems.append("emergency expiry predates its creation date")
            if expiry_date > created_date + dt.timedelta(days=14):
                problems.append(
                    "emergency expiry is more than 14 days after its creation date"
                )
    return problems


def validate_adoption_contract(authority: Any, contract_text: str) -> list[str]:
    """Validate a committed Pulp SDK adoption pin against selected authority."""
    try:
        contract = json.loads(contract_text)
    except json.JSONDecodeError:
        return ["SDK adoption contract must be valid committed JSON"]
    required = {
        "schema",
        "state",
        "framework_repository",
        "vellum_authority_commit",
        "authority_record_path",
        "pulp_activation_commit",
        "recorded_by",
        "recorded_at",
        "sdk",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        return ["SDK adoption contract has missing or unknown fields"]
    expected = {
        "schema": "pulp.vellum.sdk-adoption.v1",
        "state": "active",
        "framework_repository": "Generous-Corp/vellum",
        "vellum_authority_commit": authority.coordinates[
            "vellum_authority_record_commit"
        ],
        "authority_record_path": authority.coordinates["authority_record_path"],
        "pulp_activation_commit": authority.coordinates["pulp_activation_commit"],
    }
    for key, value in expected.items():
        if contract.get(key) != value:
            return [f"SDK adoption contract disagrees on {key}"]
    if not OWNER_RE.fullmatch(str(contract.get("recorded_by", ""))):
        return ["SDK adoption contract recorded_by is not an accountable owner"]
    try:
        recorded_at = dt.datetime.fromisoformat(
            str(contract.get("recorded_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return ["SDK adoption contract recorded_at is not an ISO-8601 timestamp"]
    if recorded_at.tzinfo is None:
        return ["SDK adoption contract recorded_at must include a timezone"]
    sdk = contract.get("sdk")
    if not isinstance(sdk, dict) or set(sdk) != {
        "version",
        "source_commit",
        "artifact_sha256",
    }:
        return ["SDK adoption contract requires an exact immutable SDK pin"]
    version = sdk.get("version")
    source_commit = sdk.get("source_commit")
    artifact_sha256 = sdk.get("artifact_sha256")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        return ["SDK adoption contract has an invalid SDK version"]
    if not isinstance(source_commit, str) or not SHA_RE.fullmatch(source_commit):
        return ["SDK adoption contract has an invalid SDK source commit"]
    if not commit_exists(authority.vellum, source_commit):
        return ["SDK adoption contract source commit does not resolve in Vellum"]
    if not commit_is_ancestor(
        authority.vellum, source_commit, authority.vellum_head
    ):
        return [
            "SDK adoption contract source commit is outside the selected Vellum HEAD history"
        ]
    if (
        not isinstance(artifact_sha256, str)
        or not SHA256_RE.fullmatch(artifact_sha256)
    ):
        return ["SDK adoption contract has an invalid artifact SHA-256"]
    return []
