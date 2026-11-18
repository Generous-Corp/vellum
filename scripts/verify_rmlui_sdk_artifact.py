#!/usr/bin/env python3
"""Verify a vellum-rmlui-sdk-<version>-<target>.tar.gz before it is trusted.

Checks, without building anything:
  - the archive name, and that every member is a regular file or directory at
    a safe relative path that no two members share on a case-insensitive,
    normalizing file system;
  - the tar stream is the canonical one build_rmlui_sdk_artifact.py writes
    (sorted, mtime 0, owner 0, fixed modes), so nothing was added or rewritten
    after the build;
  - rmlui-sdk-artifact.json names this archive's version and target and lists
    exactly the other members, each with its size and SHA-256;
  - the members are exactly what the RmlUi SDK carries: the VellumRmlUiNative
    package, Native's headers, RmlUi's headers and the static archives, and
    the notices. Nothing of the Vellum SDK's own VellumRmlUi package, and no
    shared library, may be inside;
  - the notices cover what is redistributed: RmlUi's MIT licence, and the
    FreeType License with its credit whenever FreeType's archive is present;
  - no package file names a build or source path.

    verify_rmlui_sdk_artifact.py --archive <file> [--source-commit <sha>]
        [--version <x.y.z>] [--output <record.json>] [--json]

--source-commit and --version hold the archive to the release it is published
in; --output writes the verification record the release carries.

    verify_rmlui_sdk_artifact.py --archive <file> --bind-record <record.json>
        --source-commit <sha> --checksum-output <file>

binds the bytes about to be published to the record the validation job wrote:
the record must name this archive, its SHA-256 and the release commit. It then
writes the archive's checksum line.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_rmlui_sdk_artifact as builder  # noqa: E402

NAME = re.compile(r"^vellum-rmlui-sdk-([0-9]+\.[0-9]+\.[0-9]+)-(darwin-arm64)\.tar\.gz$")
# Paths a package file may never contain: the builder's canonical roots and
# the usual locations of a checkout or build on a runner or a Mac.
FORBIDDEN_PATH_TEXT = ("/vellum-rmlui-sdk-artifact", "/vellum-rmlui-sdk-source",
                       "/Users/", "/private/", "/Volumes/", "/home/runner", "/tmp/")
NOTICE = f"{builder.DOC_DIR}/NOTICE-Native.md"
RMLUI_LICENSE = f"{builder.DOC_DIR}/RmlUi-LICENSE.txt"
FREETYPE_LICENSE = f"{builder.DOC_DIR}/FreeType-FTL.TXT"
FREETYPE_ARCHIVE = "lib/libfreetype.a"
FREETYPE_NOTICE_LINES = (
    "Portions of this software are copyright © 2024 The FreeType Project",
    "under the FreeType License (FTL)",
)


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def canonical_tar(files: dict[str, bytes], directories: set[str], executable: dict[str, bool]) -> bytes:
    """The tar stream write_archive() in build_sdk_artifact.py produces."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted([*files, *directories]):
            info = tarfile.TarInfo(name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if name in directories:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.mode = 0o755 if executable.get(name) else 0o644
                info.size = len(files[name])
                archive.addfile(info, io.BytesIO(files[name]))
    return stream.getvalue()


def read_members(archive: Path) -> tuple[dict[str, bytes], set[str], dict[str, bool], bytes]:
    try:
        raw = gzip.decompress(archive.read_bytes())
    except (OSError, EOFError) as error:
        raise VerificationError(f"the archive is not gzip: {error}") from error
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    executable: dict[str, bool] = {}
    folded: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as handle:
        for member in handle.getmembers():
            name = member.name
            path = PurePosixPath(name)
            require(not path.is_absolute() and ".." not in path.parts and str(path) == name
                    and name not in {"", "."} and "\\" not in name,
                    f"archive member has an unsafe path: {name!r}")
            key = fold(name)
            require(key not in folded,
                    f"archive members {folded.get(key)!r} and {name!r} collide on a "
                    "case-insensitive or normalizing file system")
            folded[key] = name
            if member.type == tarfile.DIRTYPE:
                directories.add(name)
                continue
            require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE),
                    f"archive member is not a regular file: {name}")
            extracted = handle.extractfile(member)
            require(extracted is not None, f"archive member cannot be read: {name}")
            files[name] = extracted.read()
            executable[name] = bool(member.mode & 0o100)
    return files, directories, executable, raw


def verify(archive: Path, source_commit: str | None = None,
           expected_version: str | None = None) -> dict[str, object]:
    match = NAME.fullmatch(archive.name)
    require(match is not None, f"not an RmlUi SDK archive name: {archive.name}")
    version, target = match.groups()
    files, directories, executable, raw = read_members(archive)

    # Directories may only be parents of files the archive carries.
    parents = {str(parent) for name in files for parent in PurePosixPath(name).parents
               if str(parent) != "."}
    stray = sorted(directories - parents)
    require(not stray, f"archive directories hold no file: {stray}")
    require(raw == canonical_tar(files, directories, executable),
            "the tar stream is not the canonical one the builder writes")

    try:
        builder.check_payload(files)
    except builder.ArtifactError as error:
        raise VerificationError(str(error)) from error
    require(not any(name.startswith("lib/cmake/VellumRmlUi/") for name in files),
            "the archive carries files of the Vellum SDK's VellumRmlUi package")
    require(not any(name.endswith((".dylib", ".so")) for name in files),
            "the archive carries a shared library")

    try:
        metadata = json.loads(files[builder.METADATA_NAME])
    except (KeyError, json.JSONDecodeError) as error:
        raise VerificationError(f"{builder.METADATA_NAME} is missing or not JSON") from error
    require(metadata.get("schema") == builder.METADATA_SCHEMA, "unexpected metadata schema")
    require(metadata.get("name") == "vellum-rmlui-sdk", "unexpected metadata name")
    require(metadata.get("version") == version and metadata.get("target") == target,
            "the metadata's version or target differs from the archive name")
    require(metadata.get("package") == "VellumRmlUiNative", "unexpected package name")
    require(re.fullmatch(r"[0-9a-f]{40}", str(metadata.get("source_commit", ""))) is not None,
            "the metadata names no source commit")
    require(source_commit is None or metadata["source_commit"] == source_commit,
            f"the archive was built from {metadata['source_commit']}, not {source_commit}")
    require(expected_version is None or version == expected_version,
            f"the archive is version {version}, not {expected_version}")
    rows = metadata.get("files")
    require(isinstance(rows, list), "the metadata has no file list")
    listed = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("path"), str),
                "a metadata file row is malformed")
        require(row["path"] not in listed, f"the metadata lists {row['path']} twice")
        listed[row["path"]] = row
    members = set(files) - {builder.METADATA_NAME}
    require(set(listed) == members,
            "the metadata's file list differs from the archive: "
            + ", ".join(sorted(set(listed) ^ members)))
    for name, row in listed.items():
        data = files[name]
        require(row.get("size") == len(data), f"{name}: size differs from the metadata")
        require(row.get("sha256") == hashlib.sha256(data).hexdigest(),
                f"{name}: SHA-256 differs from the metadata")
        require(bool(row.get("executable")) == executable[name],
                f"{name}: executable bit differs from the metadata")

    require(b"Permission is hereby granted, free of charge" in files[RMLUI_LICENSE],
            "RmlUi-LICENSE.txt is not RmlUi's MIT licence")
    notice = files[NOTICE].decode("utf-8", errors="replace")
    require("MIT License" in notice, "NOTICE-Native.md does not carry RmlUi's MIT notice")
    if FREETYPE_ARCHIVE in files:
        require(FREETYPE_LICENSE in files, "the archive ships FreeType without its licence")
        for line in FREETYPE_NOTICE_LINES:
            require(line in notice, f"NOTICE-Native.md is missing: {line}")

    for name, data in files.items():
        if name.endswith(".cmake"):
            text = data.decode("utf-8", errors="replace")
            for fragment in FORBIDDEN_PATH_TEXT:
                require(fragment not in text, f"{name} names a build or source path ({fragment})")

    return {
        "archive": archive.name,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "version": version,
        "target": target,
        "source_commit": metadata["source_commit"],
        "files": len(files),
    }


RECORD_SCHEMA = "vellum.rmlui-sdk-verification.v1"


def bind(archive: Path, record_path: Path, source_commit: str) -> str:
    """The checksum line for `archive`, once it matches the validated record."""
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read the verification record: {error}") from error
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    require(record.get("schema") == RECORD_SCHEMA, "the record is not an RmlUi SDK verification record")
    require(record.get("archive") == archive.name, f"the record names {record.get('archive')}, not {archive.name}")
    require(record.get("sha256") == digest, "the archive's bytes differ from the validated record")
    require(record.get("source_commit") == source_commit,
            f"the record was validated for {record.get('source_commit')}, not {source_commit}")
    return f"{digest}  {archive.name}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--version")
    parser.add_argument("--output", type=Path, help="write the verification record here")
    parser.add_argument("--bind-record", type=Path,
                        help="bind the archive to this verification record instead of verifying it")
    parser.add_argument("--checksum-output", type=Path, help="with --bind-record: write the checksum line here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.bind_record:
        if not args.source_commit or not args.checksum_output:
            parser.error("--bind-record needs --source-commit and --checksum-output")
        try:
            line = bind(args.archive, args.bind_record, args.source_commit)
        except (VerificationError, OSError) as error:
            print(f"vellum-rmlui-sdk-verify: {error}", file=sys.stderr)
            return 1
        args.checksum_output.write_text(line, encoding="utf-8")
        return 0
    try:
        result = verify(args.archive, args.source_commit, args.version)
    except (VerificationError, OSError, tarfile.TarError) as error:
        print(f"vellum-rmlui-sdk-verify: {error}", file=sys.stderr)
        return 1
    if args.output:
        record = {"schema": RECORD_SCHEMA, **result}
        args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True) if args.json
          else f"{result['archive']}: verified ({result['files']} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
