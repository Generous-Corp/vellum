#!/usr/bin/env python3
"""Verify a signed, time-bounded admin check of immutable-release policy."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


SCHEMA = "vellum.immutable-release-preflight.v1"
MAXIMUM_ALLOWED_AGE_SECONDS = 21600
FUTURE_CLOCK_SKEW_SECONDS = 300


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
    if maximum_age != MAXIMUM_ALLOWED_AGE_SECONDS:
        raise ImmutableReleasePreflightError(
            f"maximum_age_seconds must equal {MAXIMUM_ALLOWED_AGE_SECONDS}"
        )
    administrator_check = payload.get("administrator_check")
    if not isinstance(administrator_check, dict):
        raise ImmutableReleasePreflightError(
            "administrator_check must be an object"
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ImmutableReleasePreflightError(
                "preflight root must be an object"
            )
        now = (
            parse_utc(args.now, "--now")
            if args.now is not None
            else datetime.now(timezone.utc)
        )
        verified = verify(
            payload,
            repository=args.repository,
            release_tag=args.release_tag,
            now=now,
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
