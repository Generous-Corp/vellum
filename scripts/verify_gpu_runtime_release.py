#!/usr/bin/env python3
"""Hold a release's gpu-runtime archive to its source before the finalizer publishes it.

The GPU proof (gpu-macos.yml) builds vellum-gpu-runtime-<version>-darwin-arm64.tar.gz
twice, checks the two builds match, verifies the archive and runs the audio-only
consumer, and uploads all of it with its SDK evidence. That run executed the
tagged commit's code. The finalizer therefore re-decides with the trusted main
checkout's verifiers, as it does for the SDK archive.

`validate` (validate-sdk-artifacts, macOS) checks, at the exact release source:
  * the source checkout's HEAD is the release source commit;
  * the archive is a regular file, and the source run's checksum line and
    verification record name it and agree with its bytes, version and commit;
  * the renderer archive the source's gpu-runtime/renderer-lock.json pins is
    fetched from the pinned builder and matches the locked digest;
  * main's verify_gpu_runtime_artifact holds the archive to the source (the
    file set, every byte, the regenerated package files) and builds and runs
    the audio-only consumer against it, with a GPU adapter required: the
    installed journey;
  * main's device API closure check passes on the source's headers.
It writes gpu-runtime-verification.json, the record the release publishes.

`finalize` (finalize-sdk-release) checks the downloaded archive's digest
against that record and the source run's checksum line, and writes the
vellum-gpu-runtime.sha256 the release publishes from the bytes it publishes.

Exit 0 when every check passes, 1 with the first failure otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

SCHEMA = "vellum.gpu-runtime-release-verification.v1"
TARGET = "darwin-arm64"
CHECKSUM_NAME = "vellum-gpu-runtime.sha256"
SOURCE_RECORD = "gpu-runtime-verification.json"
LOCK = Path("gpu-runtime/renderer-lock.json")
HEADERS = Path("gpu-runtime/include")
BUILDER = "https://github.com/danielraffel/skia-builder"
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HERE = Path(__file__).resolve().parent


class ReleaseError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_name(version: str) -> str:
    require(bool(VERSION.fullmatch(version)), f"version {version!r} is not <major>.<minor>.<patch>")
    return f"vellum-gpu-runtime-{version}-{TARGET}.tar.gz"


def checksum_line(path: Path, name: str) -> str:
    """The single digest a `shasum -a 256` file gives `name`."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(len(lines) == 1, f"{path.name} must hold exactly one line, not {len(lines)}")
    fields = lines[0].split()
    require(len(fields) == 2 and fields[1].removeprefix("*") == name,
            f"{path.name} does not name {name}")
    require(bool(SHA.fullmatch(fields[0])), f"{path.name} does not hold a sha256")
    return fields[0]


def renderer_asset(lock_path: Path) -> tuple[str, str, str]:
    """`(url, asset, sha256)` of the macOS renderer archive the lock pins, refusing
    anything but the pinned builder's chrome/mNNN asset (as gpu-macos.yml does)."""
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    platform = lock["platforms"]["macos-arm64"]
    repository, milestone = lock["builder"]["repository"], lock["milestone"]
    asset, sha256 = platform["asset"], platform["sha256"]
    require(repository == BUILDER and bool(re.fullmatch(r"chrome/m[0-9]+", milestone))
            and bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", asset)) and bool(SHA.fullmatch(sha256)),
            "renderer lock names an unexpected skia-builder asset")
    return "/".join((repository, "releases/download", milestone, asset)), asset, sha256


def fetch_renderer(lock_path: Path, directory: Path, opener: Callable[[str], Any] = urllib.request.urlopen) -> Path:
    url, asset, sha256 = renderer_asset(lock_path)
    target = directory / asset
    with opener(url) as response, target.open("wb") as handle:
        for block in iter(lambda: response.read(1 << 20), b""):
            handle.write(block)
    require(sha256_file(target) == sha256, f"the renderer archive {asset} does not match the locked digest")
    return target


def load_sibling(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trusted_verify(archive: Path, source_root: Path, renderer: Path) -> dict[str, Any]:
    """Main's verifier, held to the source, with the consumer and a GPU adapter."""
    verifier = load_sibling("verify_gpu_runtime_artifact", HERE / "verify_gpu_runtime_artifact.py")
    generated = verifier.regenerate_package_files(source_root, renderer)
    result = verifier.verify(archive, source_root, generated)
    result["consumer"] = verifier.run_consumer(archive, str(result["version"]), source_root, True)
    return result


def trusted_closure(include: Path) -> list[str]:
    checker = load_sibling("check_device_api_closure", HERE.parent / "gpu-runtime/tools/check_device_api_closure.py")
    return checker.check(include)


def source_head(source_root: Path) -> str:
    return subprocess.run(["git", "-C", str(source_root), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def validate(gpu_dir: Path, source_root: Path, version: str, source_commit: str, *,
             verify_fn=trusted_verify, closure_fn=trusted_closure, fetch_fn=fetch_renderer,
             head_fn=source_head) -> dict[str, Any]:
    require(bool(COMMIT.fullmatch(source_commit)), "the release source commit is not a full sha")
    require(head_fn(source_root) == source_commit, "the source checkout is not at the release source commit")
    name = archive_name(version)
    archive = gpu_dir / name
    require(archive.is_file() and not archive.is_symlink(), f"the source run did not upload {name}")
    digest = sha256_file(archive)
    require(checksum_line(gpu_dir / CHECKSUM_NAME, name) == digest,
            f"the source run's {CHECKSUM_NAME} does not match {name}")
    source_record = json.loads((gpu_dir / SOURCE_RECORD).read_text(encoding="utf-8"))
    for key, expected in (("archive", name), ("sha256", digest), ("version", version), ("target", TARGET),
                          ("source_commit", source_commit), ("checked_against_source", True)):
        require(source_record.get(key) == expected, f"the source run's {SOURCE_RECORD} {key} differs")
    require(bool(source_record.get("consumer")), f"the source run's {SOURCE_RECORD} has no consumer run")
    with tempfile.TemporaryDirectory(prefix="vellum-gpu-runtime-renderer-") as temporary:
        renderer = fetch_fn(source_root / LOCK, Path(temporary))
        trusted = verify_fn(archive, source_root, renderer)
    for key, expected in (("archive", name), ("sha256", digest), ("version", version), ("target", TARGET),
                          ("source_commit", source_commit), ("checked_against_source", True)):
        require(trusted.get(key) == expected, f"the trusted gpu-runtime verification {key} differs")
    require(bool(trusted.get("consumer")), "the trusted gpu-runtime verification ran no consumer")
    problems = closure_fn(source_root / HEADERS)
    require(not problems, f"the device API closure fails at the release source: {problems[0] if problems else ''}")
    return {
        "schema": SCHEMA,
        "ok": True,
        "archive": name,
        "sha256": digest,
        "version": version,
        "target": TARGET,
        "source_commit": source_commit,
        "renderer_lock_sha256": trusted.get("renderer_lock_sha256"),
        "files_verified": trusted.get("files_verified"),
        "checked_against_source": True,
        "consumer": trusted["consumer"],
        "gpu_adapter_required": True,
        "device_api_closure": "ok",
    }


def finalize(gpu_dir: Path, record_path: Path, version: str, source_commit: str, output: Path) -> str:
    """Bind the archive the finalizer publishes to the validated record, and write
    the published checksum from those bytes."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    name = archive_name(version)
    for key, expected in (("schema", SCHEMA), ("ok", True), ("archive", name), ("version", version),
                          ("target", TARGET), ("source_commit", source_commit), ("checked_against_source", True),
                          ("gpu_adapter_required", True), ("device_api_closure", "ok")):
        require(record.get(key) == expected, f"the validated gpu-runtime record {key} differs")
    archive = gpu_dir / name
    require(archive.is_file() and not archive.is_symlink(), f"the release-shaped artifact has no {name}")
    digest = sha256_file(archive)
    require(digest == record.get("sha256"), f"{name} is not the archive the validation job verified")
    require(checksum_line(gpu_dir / CHECKSUM_NAME, name) == digest,
            f"the source run's {CHECKSUM_NAME} does not match {name}")
    output.write_text(f"{digest}  {name}\n", encoding="utf-8")
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("validate")
    one.add_argument("--gpu-dir", type=Path, required=True)
    one.add_argument("--source-root", type=Path, required=True)
    one.add_argument("--version", required=True)
    one.add_argument("--source-commit", required=True)
    one.add_argument("--output", type=Path, required=True)
    two = sub.add_parser("finalize")
    two.add_argument("--gpu-dir", type=Path, required=True)
    two.add_argument("--record", type=Path, required=True)
    two.add_argument("--version", required=True)
    two.add_argument("--source-commit", required=True)
    two.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            record = validate(args.gpu_dir, args.source_root.resolve(), args.version, args.source_commit)
            text = json.dumps(record, indent=2, sort_keys=True) + "\n"
            args.output.write_text(text, encoding="utf-8")
            sys.stdout.write(text)
        else:
            digest = finalize(args.gpu_dir, args.record, args.version, args.source_commit, args.output)
            print(f"verify-gpu-runtime-release: {archive_name(args.version)} {digest}")
    except (ReleaseError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"verify-gpu-runtime-release: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - a verifier error of any kind refuses the release
        print(f"verify-gpu-runtime-release: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
