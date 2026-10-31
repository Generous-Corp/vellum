#!/usr/bin/env python3
"""Regenerate provenance/immutable-release-preflight.json from the live API.

The release gates accept the committed preflight only while it is younger
than six hours, so it must be refreshed in the commit that the signed release
tag points at. This helper records a real administrator read of the
repository's immutable-release setting; it never invents the answer and
refuses to write a file when immutable releases are not enabled.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

SCHEMA = "vellum.immutable-release-preflight.v1"
TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RefreshError(RuntimeError):
    pass


def gh_fetch(repository: str) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/immutable-releases"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RefreshError(
            "could not read immutable-release setting (needs Administration: "
            f"read): {result.stderr.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RefreshError("immutable-release response is not JSON") from error
    if not isinstance(value, dict):
        raise RefreshError("immutable-release response is not an object")
    return value


def build(
    *,
    repository: str,
    release_tag: str,
    now: datetime,
    fetch: Callable[[str], dict[str, Any]] = gh_fetch,
) -> dict[str, Any]:
    if not REPOSITORY_PATTERN.match(repository):
        raise RefreshError(f"invalid repository: {repository!r}")
    if not TAG_PATTERN.match(release_tag):
        raise RefreshError(f"invalid release tag: {release_tag!r}")
    response = fetch(repository)
    if response.get("enabled") is not True:
        raise RefreshError("immutable releases are not enabled")
    enforced = response.get("enforced_by_owner")
    if not isinstance(enforced, bool):
        raise RefreshError("enforced_by_owner missing from API response")
    return {
        "administrator_check": {
            "checked_at": now.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
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
        "maximum_age_seconds": 21600,
        "release_tag": release_tag,
        "repository": repository,
        "schema": SCHEMA,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default="Generous-Corp/vellum")
    parser.add_argument("--release-tag", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "provenance/immutable-release-preflight.json",
    )
    args = parser.parse_args()
    try:
        payload = build(
            repository=args.repository,
            release_tag=args.release_tag,
            now=datetime.now(timezone.utc),
        )
    except RefreshError as error:
        print(f"preflight refresh failed: {error}", file=sys.stderr)
        return 1
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output} (valid for 6h; commit, sign tag, push now)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
