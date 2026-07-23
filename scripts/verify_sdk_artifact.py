#!/usr/bin/env python3
"""Fail-closed verification for a Vellum SDK archive and its payload manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import PurePosixPath, Path
import re
import sys
import tarfile
from typing import Iterable


SCHEMA = "vellum.sdk-artifact.v1"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_MEMBERS = 20_000
MAX_BYTES = 4 * 1024**3
COMMANDS = {"import", "reimport", "build", "run", "test", "capture", "package"}


class VerificationError(RuntimeError):
    pass


def checksum_entry(archive: Path, checksums: Path) -> str:
    matches: list[str] = []
    for line in checksums.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        name = parts[1].removeprefix("*")
        if name == archive.name:
            matches.append(parts[0].lower())
    if len(matches) != 1 or not SHA_RE.fullmatch(matches[0]):
        raise VerificationError(f"expected exactly one valid checksum for {archive.name}")
    return matches[0]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify(archive: Path, checksums: Path) -> dict[str, object]:
    expected = checksum_entry(archive, checksums)
    actual = sha256_bytes(archive.read_bytes())
    if actual != expected:
        raise VerificationError(f"SHA-256 mismatch for {archive.name}")

    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        if len(members) > MAX_MEMBERS or sum(member.size for member in members) > MAX_BYTES:
            raise VerificationError("archive exceeds verification safety limits")
        by_name = {member.name: member for member in members}
        if len(by_name) != len(members):
            raise VerificationError("archive contains duplicate member names")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not path.parts
                or path.parts[0] not in {"vellum_cli.py", "vellum_backend.py", "templates", "sdk", "bin", "design-ir", "metadata.json"}
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                raise VerificationError(f"unsafe archive member: {member.name}")
        metadata_member = by_name.get("metadata.json")
        if metadata_member is None or not metadata_member.isfile():
            raise VerificationError("archive has no metadata.json")
        extracted = handle.extractfile(metadata_member)
        if extracted is None:
            raise VerificationError("could not read metadata.json")
        metadata = json.load(extracted)
        if not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA:
            raise VerificationError("unsupported SDK artifact metadata")
        required_metadata = {
            "schema", "framework_version", "cli_version", "cli_api",
            "source_commit", "source_tree_clean", "target", "capabilities", "files",
        }
        if set(metadata) != required_metadata:
            raise VerificationError("SDK artifact metadata has missing or unknown fields")
        if (
            not isinstance(metadata["framework_version"], str)
            or not isinstance(metadata["cli_version"], str)
            or metadata["cli_api"] != 1
            or not isinstance(metadata["source_commit"], str)
            or not COMMIT_RE.fullmatch(metadata["source_commit"])
            or not isinstance(metadata["source_tree_clean"], bool)
            or not isinstance(metadata["target"], str)
            or not metadata["target"]
        ):
            raise VerificationError("SDK artifact compatibility/provenance fields are malformed")
        capabilities = metadata["capabilities"]
        if (
            not isinstance(capabilities, dict)
            or set(capabilities) != {"cmake_sdk", "authoring_cli", "gpu_renderer", "commands"}
            or not isinstance(capabilities.get("cmake_sdk"), bool)
            or not isinstance(capabilities.get("authoring_cli"), bool)
            or not isinstance(capabilities.get("gpu_renderer"), bool)
            or not isinstance(capabilities.get("commands"), dict)
            or set(capabilities["commands"]) != COMMANDS
            or not all(isinstance(value, bool) for value in capabilities["commands"].values())
        ):
            raise VerificationError("SDK artifact capability claims are malformed")
        if capabilities["commands"]["import"] is not True or capabilities["commands"]["reimport"] is not True:
            raise VerificationError("SDK artifact must expose its packaged import/reimport backend")
        if any(capabilities["commands"][name] for name in COMMANDS - {"import", "reimport"}):
            raise VerificationError("SDK artifact claims an unimplemented native application command")
        files = metadata.get("files")
        if not isinstance(files, list):
            raise VerificationError("artifact metadata has no file inventory")
        if files != sorted(files, key=lambda row: row.get("path", "") if isinstance(row, dict) else ""):
            raise VerificationError("artifact file inventory is not in canonical path order")
        expected_files = {member.name for member in members if member.isfile() and member.name != "metadata.json"}
        declared: set[str] = set()
        for row in files:
            if not isinstance(row, dict) or set(row) != {"path", "sha256", "size", "executable"}:
                raise VerificationError("artifact file inventory row is malformed")
            name = row["path"]
            if not isinstance(name, str) or name in declared or name not in expected_files:
                raise VerificationError(f"artifact file inventory path is invalid: {name!r}")
            member = by_name[name]
            payload = handle.extractfile(member)
            if payload is None:
                raise VerificationError(f"could not read artifact member: {name}")
            content = payload.read()
            if row["size"] != len(content) or row["sha256"] != sha256_bytes(content):
                raise VerificationError(f"artifact payload does not match metadata: {name}")
            if row["executable"] is not bool(member.mode & 0o100):
                raise VerificationError(f"artifact executable mode does not match metadata: {name}")
            declared.add(name)
        if declared != expected_files:
            raise VerificationError("artifact metadata does not cover every payload file")

    return {
        "schema": "vellum.sdk-artifact-verification.v1",
        "ok": True,
        "artifact": archive.name,
        "sha256": actual,
        "framework_version": metadata.get("framework_version"),
        "source_commit": metadata.get("source_commit"),
        "source_tree_clean": metadata.get("source_tree_clean"),
        "target": metadata.get("target"),
        "claims": metadata.get("capabilities"),
        "file_count": len(declared),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify(args.archive, args.checksums)
    except (OSError, tarfile.TarError, json.JSONDecodeError, VerificationError) as error:
        print(f"vellum-sdk-verify: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Verified SHA-256: {result['sha256']}")
        print(f"Verified {result['file_count']} SDK payload files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
