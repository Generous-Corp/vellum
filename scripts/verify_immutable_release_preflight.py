#!/usr/bin/env python3
"""Verify the live answer to GitHub's immutable-release check and record it.

The release jobs read GET /repos/{repository}/immutable-releases with an App
token that has Administration read, in a step that runs no repository code,
and pass the answer here. A release is refused unless immutable releases are
enabled. The record written for the release evidence keeps the
`vellum.immutable-release-preflight.v1` shape, stamped with the time of that
live read; nothing is committed or reused across runs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA = "vellum.immutable-release-preflight.v1"
MAXIMUM_ALLOWED_AGE_SECONDS = 21600
FUTURE_CLOCK_SKEW_SECONDS = 300
# The record's exact shape: an unknown key is refused rather than ignored.
TOP_LEVEL_KEYS = frozenset({
    "administrator_check", "documentation", "integrity_model", "maximum_age_seconds",
    "release_tag", "repository", "schema",
})
ADMINISTRATOR_KEYS = frozenset({
    "checked_at", "enabled", "endpoint", "enforced_by_owner", "required_permission",
})


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ImmutableReleasePreflightError(f"duplicate keys: {', '.join(duplicates)}")
    return dict(pairs)


class ImmutableReleasePreflightError(RuntimeError):
    pass


def parse_utc(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ImmutableReleasePreflightError(
            f"{label} must be an RFC 3339 UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ImmutableReleasePreflightError(
            f"{label} is not a valid timestamp: {value!r}"
        ) from error
    return parsed.astimezone(timezone.utc)


def verify(
    payload: dict[str, Any],
    *,
    repository: str,
    release_tag: str,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != TOP_LEVEL_KEYS:
        raise ImmutableReleasePreflightError(
            "preflight keys must be exactly " + ", ".join(sorted(TOP_LEVEL_KEYS))
        )
    if payload.get("schema") != SCHEMA:
        raise ImmutableReleasePreflightError("unexpected preflight schema")
    if payload.get("repository") != repository:
        raise ImmutableReleasePreflightError(
            f"preflight repository {payload.get('repository')!r} does not "
            f"equal {repository!r}"
        )
    if payload.get("release_tag") != release_tag:
        raise ImmutableReleasePreflightError(
            f"preflight release tag {payload.get('release_tag')!r} does not "
            f"equal {release_tag!r}"
        )
    if (
        payload.get("integrity_model")
        != "covered-by-exact-signed-annotated-release-tag"
    ):
        raise ImmutableReleasePreflightError(
            "preflight integrity model must inherit from the exact signed tag"
        )
    maximum_age = payload.get("maximum_age_seconds")
    if type(maximum_age) is not int or maximum_age != MAXIMUM_ALLOWED_AGE_SECONDS:
        raise ImmutableReleasePreflightError(
            f"maximum_age_seconds must equal {MAXIMUM_ALLOWED_AGE_SECONDS}"
        )
    administrator_check = payload.get("administrator_check")
    if not isinstance(administrator_check, dict) or set(administrator_check) != ADMINISTRATOR_KEYS:
        raise ImmutableReleasePreflightError(
            "administrator_check keys must be exactly " + ", ".join(sorted(ADMINISTRATOR_KEYS))
        )
    expected_endpoint = f"GET /repos/{repository}/immutable-releases"
    if administrator_check.get("endpoint") != expected_endpoint:
        raise ImmutableReleasePreflightError(
            f"administrator endpoint must equal {expected_endpoint!r}"
        )
    if administrator_check.get("required_permission") != "Administration: read":
        raise ImmutableReleasePreflightError(
            "administrator check must require Administration: read"
        )
    if administrator_check.get("enabled") is not True:
        raise ImmutableReleasePreflightError(
            "immutable releases were not enabled at the admin preflight"
        )
    if not isinstance(administrator_check.get("enforced_by_owner"), bool):
        raise ImmutableReleasePreflightError(
            "enforced_by_owner must be a boolean"
        )
    checked_at = parse_utc(
        administrator_check.get("checked_at"), "administrator_check.checked_at"
    )
    normalized_now = now.astimezone(timezone.utc)
    age_seconds = (normalized_now - checked_at).total_seconds()
    if age_seconds < -FUTURE_CLOCK_SKEW_SECONDS:
        raise ImmutableReleasePreflightError(
            f"admin preflight timestamp is {abs(age_seconds):.0f}s in the future"
        )
    if age_seconds > maximum_age:
        raise ImmutableReleasePreflightError(
            f"admin preflight is stale by {age_seconds - maximum_age:.0f}s"
        )
    return payload


def verify_live(
    answer: Any,
    *,
    repository: str,
    release_tag: str,
    checked_at: datetime,
) -> dict[str, Any]:
    """The verified record of a live GET /repos/{repository}/immutable-releases answer.

    The answer must say `enabled: true` and carry a boolean
    `enforced_by_owner`; any other key GitHub adds is ignored, because only
    those two decide anything.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ImmutableReleasePreflightError(f"invalid repository: {repository!r}")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", release_tag):
        raise ImmutableReleasePreflightError(f"invalid release tag: {release_tag!r}")
    if not isinstance(answer, dict):
        raise ImmutableReleasePreflightError("the immutable-release answer must be an object")
    if answer.get("enabled") is not True:
        raise ImmutableReleasePreflightError("immutable releases are not enabled")
    enforced = answer.get("enforced_by_owner")
    if not isinstance(enforced, bool):
        raise ImmutableReleasePreflightError("the immutable-release answer has no boolean enforced_by_owner")
    stamp = checked_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "administrator_check": {
            "checked_at": stamp,
            "enabled": True,
            "endpoint": f"GET /repos/{repository}/immutable-releases",
            "enforced_by_owner": enforced,
            "required_permission": "Administration: read",
        },
        "documentation": (
            "https://docs.github.com/en/rest/repos/repos"
            "#check-if-immutable-releases-are-enabled-for-a-repository"
        ),
        "integrity_model": "covered-by-exact-signed-annotated-release-tag",
        "maximum_age_seconds": MAXIMUM_ALLOWED_AGE_SECONDS,
        "release_tag": release_tag,
        "repository": repository,
        "schema": SCHEMA,
    }
    return verify(record, repository=repository, release_tag=release_tag,
                  now=parse_utc(stamp, "checked_at"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True,
                        help="the live GET /repos/{repository}/immutable-releases answer")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", help="when the answer was read (default: now)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(
            args.input.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
        now = (
            parse_utc(args.now, "--now")
            if args.now is not None
            else datetime.now(timezone.utc)
        )
        verified = verify_live(
            payload,
            repository=args.repository,
            release_tag=args.release_tag,
            checked_at=now,
        )
    except (OSError, json.JSONDecodeError, ImmutableReleasePreflightError) as error:
        print(
            f"immutable release preflight verification failed: {error}",
            file=sys.stderr,
        )
        return 1
    args.output.write_text(
        json.dumps(verified, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
