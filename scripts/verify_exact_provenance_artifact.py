#!/usr/bin/env python3
"""Bind a successful sibling provenance artifact to the current tag object."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SHA40 = re.compile(r"^[0-9a-f]{40}$")


class VerificationError(ValueError):
    pass


def strict_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)


def verify(
    trust: Any,
    readiness: Any,
    *,
    repository: str,
    tag: str,
    source_commit: str,
    tag_object_sha: str,
) -> None:
    if repository != "Generous-Corp/vellum":
        raise VerificationError("repository must be permanent Vellum authority")
    for label, value in (("source commit", source_commit), ("tag object", tag_object_sha)):
        if not SHA40.fullmatch(value):
            raise VerificationError(f"{label} must be a full lowercase SHA")
    if not tag or tag.startswith("refs/"):
        raise VerificationError("tag must be a bare release tag")
    if not isinstance(trust, dict):
        raise VerificationError("tag trust evidence must be an object")
    expected = {
        "repository": repository,
        "tag": tag,
        "source_commit": source_commit,
        "tag_object_sha": tag_object_sha,
        "tag_object_type": "tag",
        "peeled_commit": source_commit,
    }
    for key, value in expected.items():
        if trust.get(key) != value:
            raise VerificationError(f"tag trust evidence {key} differs")
    if not isinstance(readiness, dict):
        raise VerificationError("authority readiness evidence must be an object")
    if readiness.get("status") != "pass":
        raise VerificationError("authority readiness did not pass")
    if readiness.get("release_readiness_requested") is not True:
        raise VerificationError("authority readiness was not release-scoped")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trust-json", type=Path, required=True)
    parser.add_argument("--readiness-json", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--tag-object-sha", required=True)
    args = parser.parse_args()
    try:
        verify(
            load(args.trust_json),
            load(args.readiness_json),
            repository=args.repository,
            tag=args.tag,
            source_commit=args.source_commit,
            tag_object_sha=args.tag_object_sha,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, VerificationError) as exc:
        print(f"exact provenance artifact verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
