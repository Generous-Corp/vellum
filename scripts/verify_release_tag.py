#!/usr/bin/env python3
"""Verify a remote annotated release-tag object after checkout peels its ref."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
RETIRED_RELEASE_TAGS = (
    Path(__file__).resolve().parents[1] / "provenance/retired-release-tags.json"
)
RETIRED_SCHEMA = "vellum.retired-release-tags.v1"


class ReleaseTagVerificationError(RuntimeError):
    pass


def run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseTagVerificationError(
            f"{' '.join(arguments)} failed: {detail or f'exit {result.returncode}'}"
        )
    return result


def require_full_sha(value: str, label: str) -> None:
    if FULL_SHA.fullmatch(value) is None:
        raise ReleaseTagVerificationError(
            f"{label} {value!r} is not a full lowercase SHA"
        )


def tag_headers(raw: str) -> dict[str, str]:
    header_text, separator, _body = raw.partition("\n\n")
    if separator == "":
        raise ReleaseTagVerificationError("annotated tag object has no message body")
    headers: dict[str, str] = {}
    for line in header_text.splitlines():
        key, space, value = line.partition(" ")
        if space and key in {"object", "type", "tag"}:
            if key in headers:
                raise ReleaseTagVerificationError(
                    f"annotated tag has duplicate {key!r} header"
                )
            headers[key] = value
    missing = {"object", "type", "tag"} - headers.keys()
    if missing:
        raise ReleaseTagVerificationError(
            f"annotated tag is missing headers: {', '.join(sorted(missing))}"
        )
    return headers


def retired_release_tags(path: Path) -> set[str]:
    """Return tag names that must never be verified again.

    A retired name once identified published bytes that no longer exist. A new
    tag of the same name would silently give that name different contents, so
    the record fails closed when it is missing or malformed.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseTagVerificationError(
            f"retired release-tag record {path} is unreadable: {error}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema") != RETIRED_SCHEMA:
        raise ReleaseTagVerificationError(
            f"retired release-tag record {path} does not declare {RETIRED_SCHEMA}"
        )
    entries = payload.get("tags")
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("tag"), str)
        for entry in entries
    ):
        raise ReleaseTagVerificationError(
            f"retired release-tag record {path} has malformed tag entries"
        )
    return {entry["tag"] for entry in entries}


def require_unretired_tag(tag_name: str, retired_tags: Path) -> None:
    if tag_name in retired_release_tags(retired_tags):
        raise ReleaseTagVerificationError(
            f"release tag {tag_name!r} is retired and must never be reused; "
            "choose the next unused version"
        )


def allowed_signer_principals(path: Path) -> list[str]:
    principals: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3:
            raise ReleaseTagVerificationError(
                "allowed-signers file contains a malformed entry"
            )
        principals.extend(fields[0].split(","))
    return principals


# Each tag namespace has exactly one signer file. Release tags are signed by
# the release pipeline's key; authority tags were signed by the owner's key.
# The verifier picks the file by namespace itself, so no caller can verify a
# release tag against the other key.
SIGNERS_BY_NAMESPACE = (
    ("authority/", "authority-signers.allowed_signers"),
    ("v", "release-signers.allowed_signers"),
)


def required_signers_file(tag_name: str) -> str:
    for prefix, name in SIGNERS_BY_NAMESPACE:
        if tag_name.startswith(prefix):
            return name
    raise ReleaseTagVerificationError(f"tag {tag_name!r} is in no signed namespace")


def verify(
    *,
    repo: Path,
    repository: str,
    tag_name: str,
    tag_object_sha: str,
    source_commit: str,
    allowed_signers: Path,
    expected_principal: str,
    expected_fingerprint: str,
    retired_tags: Path = RETIRED_RELEASE_TAGS,
) -> dict[str, object]:
    require_unretired_tag(tag_name, retired_tags)
    require_full_sha(tag_object_sha, "tag object")
    require_full_sha(source_commit, "source commit")
    if not allowed_signers.is_file():
        raise ReleaseTagVerificationError(
            f"allowed-signers file does not exist: {allowed_signers}"
        )
    if allowed_signers.name != required_signers_file(tag_name):
        raise ReleaseTagVerificationError(
            f"{tag_name!r} must be verified against "
            f"{required_signers_file(tag_name)}, not {allowed_signers.name}"
        )

    object_type = run(
        ["git", "cat-file", "-t", tag_object_sha], cwd=repo
    ).stdout.strip()
    if object_type != "tag":
        raise ReleaseTagVerificationError(
            f"remote release ref resolves to {object_type!r}, not an annotated tag"
        )

    raw_tag = run(
        ["git", "cat-file", "-p", tag_object_sha], cwd=repo
    ).stdout
    headers = tag_headers(raw_tag)
    if headers["type"] != "commit":
        raise ReleaseTagVerificationError(
            f"annotated tag targets {headers['type']!r}, not a commit"
        )
    if headers["object"] != source_commit:
        raise ReleaseTagVerificationError(
            "annotated tag object header "
            f"{headers['object']!r} does not equal source commit "
            f"{source_commit!r}"
        )
    if headers["tag"] != tag_name:
        raise ReleaseTagVerificationError(
            f"annotated tag name {headers['tag']!r} does not equal {tag_name!r}"
        )

    peeled = run(
        ["git", "rev-parse", f"{tag_object_sha}^{{commit}}"], cwd=repo
    ).stdout.strip()
    if peeled != source_commit:
        raise ReleaseTagVerificationError(
            f"annotated tag peels to {peeled!r}, not source commit "
            f"{source_commit!r}"
        )

    principals = allowed_signer_principals(allowed_signers)
    if principals != [expected_principal]:
        raise ReleaseTagVerificationError(
            f"allowed-signers principals {principals!r} differ from trusted "
            f"release principal {[expected_principal]!r}"
        )

    fingerprint_rows = run(
        ["ssh-keygen", "-lf", str(allowed_signers)], cwd=repo
    ).stdout.splitlines()
    fingerprints = [
        fields[1]
        for row in fingerprint_rows
        if len(fields := row.split()) >= 2
    ]
    if fingerprints != [expected_fingerprint]:
        raise ReleaseTagVerificationError(
            f"allowed-signers fingerprints {fingerprints!r} differ from pinned "
            f"release fingerprint {[expected_fingerprint]!r}"
        )

    signature = run(
        [
            "git",
            "-c",
            "gpg.format=ssh",
            "-c",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            "verify-tag",
            tag_object_sha,
        ],
        cwd=repo,
    )

    return {
        "schema": "vellum.release-trust.v1",
        "repository": repository,
        "source_commit": source_commit,
        "tag": tag_name,
        "tag_object_sha": tag_object_sha,
        "tag_object_type": object_type,
        "peeled_commit": peeled,
        "trusted_release_signer": {
            "principal": expected_principal,
            "ssh_fingerprint": expected_fingerprint,
            "verification": (signature.stderr or signature.stdout).strip(),
        },
        "github_actions_build_attestation": {
            "status": "not-generated",
            "reason_code": "release-workflow-does-not-generate-build-attestations",
            "documentation": (
                "https://docs.github.com/en/code-security/getting-started/"
                "github-security-features#artifact-attestations"
            ),
        },
        "github_immutable_release_attestation": {
            "status": "required",
            "documentation": (
                "https://docs.github.com/en/code-security/concepts/"
                "supply-chain-security/immutable-releases"
            ),
        },
        "retained_controls": [
            "trusted-ssh-signed-annotated-tag-bound-to-source-commit",
            "remote-tag-object-rechecked-before-release-mutation",
            "same-run-byte-repeatability-bound-to-source-commit",
            "github-immutable-release-attestation",
            "github-release-asset-digests",
            "sha256sums",
            "sterile-installed-sdk-validation",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag-name", required=True)
    parser.add_argument("--tag-object-sha", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--allowed-signers", type=Path, required=True)
    parser.add_argument("--expected-principal", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--retired-tags", type=Path, default=RETIRED_RELEASE_TAGS
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = verify(
            repo=args.repo.resolve(),
            repository=args.repository,
            tag_name=args.tag_name,
            tag_object_sha=args.tag_object_sha,
            source_commit=args.source_commit,
            allowed_signers=args.allowed_signers.resolve(),
            expected_principal=args.expected_principal,
            expected_fingerprint=args.expected_fingerprint,
            retired_tags=args.retired_tags.resolve(),
        )
    except (OSError, ReleaseTagVerificationError) as error:
        print(f"release tag verification failed: {error}", file=sys.stderr)
        return 1
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
