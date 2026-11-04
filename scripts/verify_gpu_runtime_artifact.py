#!/usr/bin/env python3
"""Verify a vellum-gpu-runtime archive before it is published or installed.

Checks, without trusting anything the archive says about itself:

  * every member is a plain file or directory with a relative, normalized
    path, so extraction cannot escape its directory or follow a link;
  * the archive holds exactly the files gpu-runtime-artifact.json lists, plus
    that record, each with the listed SHA-256 and size;
  * the record names this archive's version and target, and its renderer lock
    digest is the digest of the lock the archive ships;
  * the shipped libdawn_combined.a is byte for byte the library that lock
    names, and the license and notices for it ship beside it;
  * with --repo, the shipped lock and headers are byte-identical to the source.

With --consumer it then extracts the archive and builds and runs a consumer
that finds the package by exact version and links Device against the locked
skia-builder asset (--renderer-archive): the audio-only journey.

Exit 0 when every check passes, 1 with the first failure otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

METADATA_SCHEMA = "vellum.gpu-runtime-artifact.v1"
METADATA_NAME = "gpu-runtime-artifact.json"
LOCK_MEMBER = "lib/cmake/VellumGpuRuntime/renderer-lock.json"
NAME = re.compile(r"^vellum-gpu-runtime-([0-9]+\.[0-9]+\.[0-9]+)-(darwin-arm64)\.tar\.gz$")
ALLOWED_PREFIXES = ("include/gpu_runtime/", "lib/", "share/doc/VellumGpuRuntime/")
DAWN_MEMBER = "lib/libdawn_combined.a"
NOTICE_MEMBER = "share/doc/VellumGpuRuntime/NOTICE.md"
LICENSE_MEMBER = "share/doc/VellumGpuRuntime/LICENSE.md"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_members(archive: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(archive, mode="r:gz") as handle:
        for member in handle.getmembers():
            name = member.name
            path = PurePosixPath(name)
            require(not path.is_absolute() and ".." not in path.parts and str(path) == name
                    and name not in {"", "."},
                    f"archive member has an unsafe path: {name!r}")
            if member.isdir():
                continue
            require(member.isreg(), f"archive member is not a regular file: {name}")
            require(name not in files, f"archive member is duplicated: {name}")
            require(name == METADATA_NAME or name.startswith(ALLOWED_PREFIXES),
                    f"archive member is outside the gpu-runtime package: {name}")
            extracted = handle.extractfile(member)
            require(extracted is not None, f"archive member cannot be read: {name}")
            files[name] = extracted.read()
    return files


def verify(archive: Path, repo: Path | None) -> dict[str, object]:
    match = NAME.fullmatch(archive.name)
    require(match is not None, f"archive name is not vellum-gpu-runtime-<version>-<target>.tar.gz: "
                               f"{archive.name}")
    version, target = match.groups()
    files = read_members(archive)
    require(METADATA_NAME in files, f"archive has no {METADATA_NAME}")
    metadata = json.loads(files[METADATA_NAME])
    require(metadata.get("schema") == METADATA_SCHEMA, "artifact record schema is unsupported")
    require(metadata.get("name") == "vellum-gpu-runtime", "artifact record names another package")
    require(metadata.get("version") == version, "artifact record version differs from the name")
    require(metadata.get("target") == target, "artifact record target differs from the name")
    require(isinstance(metadata.get("source_commit"), str)
            and re.fullmatch(r"[0-9a-f]{40}", metadata["source_commit"]) is not None,
            "artifact record source commit is malformed")
    require(metadata.get("source_tree_clean") is True, "artifact was built from a dirty tree")

    rows = metadata.get("files")
    require(isinstance(rows, list) and rows, "artifact record lists no files")
    listed = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("path"), str),
                "artifact record file row is malformed")
        require(row["path"] not in listed, f"artifact record lists {row['path']} twice")
        listed[row["path"]] = row
    require(set(listed) == set(files) - {METADATA_NAME},
            "archive members differ from the artifact record: "
            + ", ".join(sorted(set(listed) ^ (set(files) - {METADATA_NAME}))))
    for name, row in listed.items():
        require(row.get("sha256") == sha256_bytes(files[name]), f"digest mismatch: {name}")
        require(row.get("size") == len(files[name]), f"size mismatch: {name}")

    require(LOCK_MEMBER in files, "archive ships no renderer lock")
    lock_digest = sha256_bytes(files[LOCK_MEMBER])
    require(metadata.get("renderer_lock_sha256") == lock_digest,
            "artifact record names a different renderer lock than it ships")
    lock = json.loads(files[LOCK_MEMBER])
    require(metadata.get("renderer_milestone") == lock.get("milestone"),
            "artifact record names a different renderer milestone than its lock")
    require(DAWN_MEMBER in files, "archive does not ship libdawn_combined.a")
    platform = (lock.get("platforms") or {}).get(target.replace("darwin", "macos"), {})
    require(sha256_bytes(files[DAWN_MEMBER]) == (platform.get("archives") or {}).get(
                "libdawn_combined.a"),
            "shipped libdawn_combined.a is not the library the renderer lock names")
    require(LICENSE_MEMBER in files, "archive ships no license")
    require(NOTICE_MEMBER in files and b"### Dawn and Tint" in files[NOTICE_MEMBER],
            "archive ships no Dawn notice")
    for library in ("lib/libgpu-runtime-bootstrap.a", "lib/libgpu-runtime-device.a"):
        require(library in files and files[library].startswith(b"!<arch>\n"),
                f"archive does not ship {library} as a static library")

    if repo is not None:
        source_lock = (repo / "gpu-runtime/renderer-lock.json").read_bytes()
        require(files[LOCK_MEMBER] == source_lock, "shipped renderer lock differs from the source")
        include_root = repo / "gpu-runtime/include"
        source_headers = {"include/" + path.relative_to(include_root).as_posix(): path.read_bytes()
                          for path in (include_root / "gpu_runtime").rglob("*.hpp")}
        shipped_headers = {name: data for name, data in files.items()
                           if name.startswith("include/")}
        require(shipped_headers == source_headers, "shipped headers differ from the source")
        require(files[LICENSE_MEMBER] == (repo / "LICENSE.md").read_bytes(),
                "shipped license differs from the source")
    return {
        "archive": archive.name,
        "sha256": sha256_bytes(archive.read_bytes()),
        "version": version,
        "target": target,
        "source_commit": metadata["source_commit"],
        "renderer_lock_sha256": lock_digest,
        "files_verified": len(listed),
    }


def run_consumer(archive: Path, renderer_archive: Path, version: str, repo: Path) -> str:
    source = repo / "gpu-runtime/tests/artifact-consumer"
    with tempfile.TemporaryDirectory(prefix="vellum-gpu-runtime-consumer-") as temporary_text:
        temporary = Path(temporary_text)
        prefix = temporary / "prefix"
        prefix.mkdir()
        with tarfile.open(archive, mode="r:gz") as handle:
            handle.extractall(prefix, filter="data")
        build = temporary / "build"
        configure = ["cmake", "-S", str(source), "-B", str(build),
                     "-DCMAKE_BUILD_TYPE=Release",
                     f"-DCMAKE_PREFIX_PATH={prefix}",
                     f"-DEXPECTED_VERSION={version}",
                     f"-DGPU_RUNTIME_RENDERER_ARCHIVE={renderer_archive.resolve()}"]
        if shutil.which("ninja"):
            configure.extend(["-G", "Ninja"])
        for command in (configure, ["cmake", "--build", str(build)],
                        [str(build / "gpu-runtime-artifact-consumer")]):
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            if completed.returncode:
                raise VerificationError(
                    f"consumer step failed: {' '.join(command)}\n"
                    f"{completed.stdout[-3000:]}{completed.stderr[-3000:]}")
        return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--repo", type=Path, help="also compare the lock and headers to this checkout")
    parser.add_argument("--consumer", action="store_true",
                        help="build and run the audio-only consumer against the archive")
    parser.add_argument("--renderer-archive", type=Path,
                        help="the locked skia-builder asset, for --consumer")
    parser.add_argument("--output", type=Path, help="write the verification record here")
    args = parser.parse_args(argv)
    try:
        if args.consumer and (args.renderer_archive is None or args.repo is None):
            raise VerificationError("--consumer needs --renderer-archive and --repo")
        result = verify(args.archive, args.repo)
        if args.consumer:
            result["consumer"] = run_consumer(args.archive, args.renderer_archive,
                                              str(result["version"]), args.repo)
    except (VerificationError, OSError, ValueError, tarfile.TarError) as error:
        print(f"verify-gpu-runtime-artifact: {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
