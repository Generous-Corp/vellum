#!/usr/bin/env python3
"""Build the deterministic gpu-runtime archive published beside the Vellum SDK.

vellum-gpu-runtime-<version>-<target>.tar.gz is gpu-runtime's installed CMake
package: the device API headers, the Bootstrap, Device and SkiaLogBridge static
libraries, the renderer lock and its readers, and gpu-runtime-artifact.json,
which names the source commit, the lock and every file's digest. An audio-only
GPU plug-in fetches this archive alone. It carries no Dawn: a consumer supplies
the locked skia-builder asset, which the package checks against its lock.

The archive is built from `cmake -S gpu-runtime` with the per-build temporary
root remapped out of every object, and static-library member headers
normalized, so two builds of one commit are byte-identical.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from build_sdk_artifact import (  # noqa: E402
    ArtifactError,
    normalize_static_archive,
    payload_files,
    sha256,
    source_identity,
    target_name,
    write_archive,
)

METADATA_SCHEMA = "vellum.gpu-runtime-artifact.v1"
METADATA_NAME = "gpu-runtime-artifact.json"
LOCK = "gpu-runtime/renderer-lock.json"
PACKAGE_DIR = "lib/cmake/VellumGpuRuntime"
LIBRARIES = (
    "lib/libgpu-runtime-bootstrap.a",
    "lib/libgpu-runtime-device.a",
    "lib/libgpu-runtime-skia-log-bridge.a",
)
PACKAGE_FILES = (
    f"{PACKAGE_DIR}/GpuRuntimeDawn.cmake",
    f"{PACKAGE_DIR}/GpuRuntimeRendererLock.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeConfig.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeConfigVersion.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeTargets-release.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeTargets.cmake",
    f"{PACKAGE_DIR}/renderer-lock.json",
)


def archive_name(version: str, target: str) -> str:
    return f"vellum-gpu-runtime-{version}-{target}.tar.gz"


def vellum_version(repo: Path) -> str:
    text = (repo / "CMakeLists.txt").read_text(encoding="utf-8")
    match = re.search(r"^project\(Vellum\s*\n\s*VERSION ([0-9]+\.[0-9]+\.[0-9]+)\s*$", text, re.M)
    if match is None:
        raise ArtifactError("could not read the Vellum version from CMakeLists.txt")
    return match.group(1)


def run(arguments: list[str], *, env: dict[str, str]) -> None:
    completed = subprocess.run(arguments, env=env, text=True, capture_output=True, check=False)
    if completed.returncode:
        sys.stderr.write(completed.stdout[-4000:])
        sys.stderr.write(completed.stderr[-4000:])
        raise ArtifactError(f"command failed ({completed.returncode}): {' '.join(arguments)}")


def expected_headers(repo: Path) -> list[str]:
    root = repo / "gpu-runtime" / "include"
    return sorted("include/" + path.relative_to(root).as_posix()
                  for path in (root / "gpu_runtime").rglob("*.hpp"))


def expected_files(repo: Path) -> list[str]:
    return sorted([*expected_headers(repo), *LIBRARIES, *PACKAGE_FILES, METADATA_NAME])


def build(args: argparse.Namespace) -> dict[str, object]:
    repo = args.repo.resolve()
    renderer_archive = args.renderer_archive.resolve()
    if not renderer_archive.is_file():
        raise ArtifactError(f"--renderer-archive does not exist: {renderer_archive}")
    commit, clean = source_identity(repo, args.source_commit, args.allow_dirty)
    version = vellum_version(repo)
    target = target_name()
    if target != "darwin-arm64":
        raise ArtifactError("the gpu-runtime artifact is built for darwin-arm64 only")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vellum-gpu-runtime-artifact-") as temporary_text:
        temporary = Path(temporary_text)
        build_dir = temporary / "build"
        payload = temporary / "payload"
        # Builds of one commit must match byte for byte, so neither the
        # temporary root nor the checkout path may reach an object.
        canonical_build = "/vellum-gpu-runtime-artifact"
        canonical_source = "/vellum-gpu-runtime-source"
        maps = []
        for flag in ("file", "macro", "debug"):
            maps.append(f"-f{flag}-prefix-map={temporary}={canonical_build}")
            maps.append(f"-f{flag}-prefix-map={repo}={canonical_source}")
        flags = " ".join(maps)
        env = dict(os.environ, ZERO_AR_DATE="1")
        configure = [
            "cmake", "-S", str(repo / "gpu-runtime"), "-B", str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_OSX_ARCHITECTURES=arm64",
            "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.3",
            "-DGPU_RUNTIME_BUILD_TESTS=OFF",
            "-DGPU_RUNTIME_INSTALL=ON",
            "-DGPU_RUNTIME_REQUIRE_DEVICE=ON",
            f"-DGPU_RUNTIME_RENDERER_ARCHIVE={renderer_archive}",
            f"-DCMAKE_INSTALL_PREFIX={payload}",
        ]
        for language in ("CXX", "OBJCXX"):
            configure.append(f"-DCMAKE_{language}_FLAGS={flags}")
        if shutil.which("ninja"):
            configure.extend(["-G", "Ninja"])
        run(configure, env=env)
        jobs = str(max(1, min(args.jobs, os.cpu_count() or 1)))
        run(["cmake", "--build", str(build_dir), "--parallel", jobs], env=env)
        run(["cmake", "--install", str(build_dir)], env=env)
        for relative in LIBRARIES:
            normalize_static_archive(payload / relative)

        lock_bytes = (repo / LOCK).read_bytes()
        if (payload / PACKAGE_DIR / "renderer-lock.json").read_bytes() != lock_bytes:
            raise ArtifactError("the installed renderer lock differs from the source lock")
        metadata = {
            "schema": METADATA_SCHEMA,
            "name": "vellum-gpu-runtime",
            "version": version,
            "target": target,
            "source_commit": commit,
            "source_tree_clean": clean,
            "renderer_lock_sha256": sha256(repo / LOCK),
            "renderer_milestone": json.loads(lock_bytes)["milestone"],
            "files": payload_files(payload),
        }
        (payload / METADATA_NAME).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        found = sorted(path.relative_to(payload).as_posix()
                       for path in payload.rglob("*") if path.is_file())
        if found != expected_files(repo):
            missing = sorted(set(expected_files(repo)) - set(found))
            extra = sorted(set(found) - set(expected_files(repo)))
            raise ArtifactError(f"unexpected installed files; missing {missing}, extra {extra}")
        output = output_dir / archive_name(version, target)
        write_archive(payload, output)
    return {
        "archive": str(output),
        "sha256": sha256(output),
        "version": version,
        "target": target,
        "source_commit": commit,
        "renderer_lock_sha256": metadata["renderer_lock_sha256"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--repo", type=Path, default=REPO)
    result.add_argument("--renderer-archive", type=Path, required=True,
                        help="the locked skia-builder asset named by gpu-runtime/renderer-lock.json")
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--source-commit", help="require HEAD to be exactly this commit")
    result.add_argument("--allow-dirty", action="store_true",
                        help="development only: attribute a dirty tree to HEAD")
    result.add_argument("--jobs", type=int, default=3)
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = build(args)
    except ArtifactError as error:
        print(f"vellum-gpu-runtime-artifact: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['archive']}  {result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
