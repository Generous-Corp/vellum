#!/usr/bin/env python3
"""Check GitHub's immutable-release attestation names this release's exact tag object.

The SDK finalizer runs this on `gh release verify --format json` output after
publication. The attestation's predicate must name the repository and tag, and
its subject list must hold exactly one package URL for the tag whose digest is
the annotated tag object's SHA-1.

GitHub writes that package URL as a purl, which percent-encodes the version:
a tag `a/b` appears as `pkg:github/<owner>/<repo>@a%2Fb`. A release tag
(`v1.2.3`) encodes to itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

SHA1 = re.compile(r"[0-9a-f]{40}")


def tag_purl(repository: str, tag: str) -> str:
    """The package URL GitHub records for a release tag."""
    return f"pkg:github/{repository}@{quote(tag, safe='')}"


def attestation_problems(
    value: Any, *, repository: str, tag: str, tag_object_sha: str
) -> list[str]:
    """Why the attestation does not bind this repository's tag to tag_object_sha."""
    if SHA1.fullmatch(tag_object_sha or "") is None:
        return ["the expected tag object is not a full SHA-1"]
    if not isinstance(value, dict):
        return ["immutable release attestation is malformed"]
    result = value.get("verificationResult")
    statement = result.get("statement") if isinstance(result, dict) else None
    if not isinstance(statement, dict):
        return ["immutable release attestation has no statement"]
    predicate = statement.get("predicate")
    if (
        not isinstance(predicate, dict)
        or predicate.get("repository") != repository
        or predicate.get("tag") != tag
    ):
        return ["immutable release attestation coordinates differ"]
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return ["immutable release attestation has no subjects"]
    expected = tag_purl(repository, tag)
    matches = [row for row in subjects if isinstance(row, dict) and row.get("uri") == expected]
    if len(matches) != 1 or matches[0].get("digest") != {"sha1": tag_object_sha}:
        return ["immutable release attestation tag object differs"]
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--tag-object-sha", required=True)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"vellum-release-attestation: {error}", file=sys.stderr)
        return 1
    problems = attestation_problems(
        value, repository=args.repository, tag=args.tag, tag_object_sha=args.tag_object_sha
    )
    for problem in problems:
        print(f"vellum-release-attestation: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
