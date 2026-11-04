#!/usr/bin/env python3
"""Build the deterministic gpu-runtime archive published beside the Vellum SDK.

vellum-gpu-runtime-<version>-<target>.tar.gz is gpu-runtime's installed CMake
package: the device API headers, the Bootstrap, Device and SkiaLogBridge static
libraries, the locked libdawn_combined.a (exactly the bytes the renderer lock
names, never rewritten), the renderer lock and its readers, the license and the
third-party notices for everything linked from it, and gpu-runtime-artifact.json,
which names the source commit, the lock and every file's digest. An audio-only
GPU plug-in fetches this archive alone.

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
# Fixed, so PATH cannot change the build graph and with it the archive bytes.
GENERATOR = "Unix Makefiles"
DAWN_LIBRARY = "lib/libdawn_combined.a"
DOC_DIR = "share/doc/VellumGpuRuntime"
DOC_FILES = (f"{DOC_DIR}/LICENSE.md", f"{DOC_DIR}/NOTICE.md")
DAWN_ARCHIVE = "libdawn_combined.a"
APACHE_HEADING = "Apache License, Version 2.0"
SKIA_HEADING = "Skia and skcms"
PACKAGE_FILES = (
    f"{PACKAGE_DIR}/GpuRuntimeDawn.cmake",
    f"{PACKAGE_DIR}/GpuRuntimeRendererLock.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeConfig.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeConfigVersion.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeTargets-release.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeTargets.cmake",
    f"{PACKAGE_DIR}/renderer-lock.json",
)


def normalize_library(path: Path) -> None:
    """normalize_static_archive, plus the member mode field, which Apple's ar
    copies from each object's st_mode and so differs with the host's umask."""
    normalize_static_archive(path)
    data = bytearray(path.read_bytes())
    offset = 8
    while offset < len(data):
        size = int(bytes(data[offset + 48:offset + 58]).decode("ascii").strip())
        data[offset + 40:offset + 48] = b"100644".ljust(8)
        offset += 60 + size
        if offset % 2:
            offset += 1
    path.write_bytes(data)


def toolchain_identity(build_dir: Path) -> dict[str, str]:
    """The compiler, SDK and CMake this build actually used, read from the
    configured build rather than from whatever is first on PATH."""
    cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8")

    def cached(name: str) -> str:
        match = re.search(rf"^{name}:[A-Z]+=(.*)$", cache, re.M)
        return match.group(1) if match else ""

    version = ""
    for compiler_file in sorted((build_dir / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake")):
        match = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)"\)',
                          compiler_file.read_text(encoding="utf-8"))
        if match:
            version = match.group(1)
    identity = {
        "compiler": cached("CMAKE_CXX_COMPILER"),
        "compiler_version": version,
        "sysroot": cached("CMAKE_OSX_SYSROOT"),
        "cmake": f"{cached('CMAKE_CACHE_MAJOR_VERSION')}.{cached('CMAKE_CACHE_MINOR_VERSION')}."
                 f"{cached('CMAKE_CACHE_PATCH_VERSION')}",
        "generator": cached("CMAKE_GENERATOR"),
    }
    if not identity["compiler"] or not version or identity["generator"] != GENERATOR:
        raise ArtifactError("could not read the build's toolchain from its CMake cache")
    return identity


def configure_command(repo: Path, renderer_archive: Path, build_dir: Path, prefix: Path,
                      flags: str = "") -> list[str]:
    """The one configure both the builder and the verifier's regeneration use,
    so the generated package files they produce are comparable byte for byte."""
    command = [
        "cmake", "-S", str(repo / "gpu-runtime"), "-B", str(build_dir),
        "-G", GENERATOR,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_OSX_ARCHITECTURES=arm64",
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.3",
        "-DGPU_RUNTIME_BUILD_TESTS=OFF",
        "-DGPU_RUNTIME_INSTALL=ON",
        "-DGPU_RUNTIME_REQUIRE_DEVICE=ON",
        f"-DGPU_RUNTIME_RENDERER_ARCHIVE={renderer_archive}",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
    ]
    if flags:
        for language in ("CXX", "OBJCXX"):
            command.append(f"-DCMAKE_{language}_FLAGS={flags}")
    return command


def generated_package_files(build_dir: Path) -> dict[str, bytes]:
    """The four package files CMake generates for a configured build: the
    config and version files at configure time, the exports at generate."""
    exports = sorted((build_dir / "CMakeFiles" / "Export").glob("*/VellumGpuRuntimeTargets*.cmake"))
    files = {f"{PACKAGE_DIR}/{path.name}": path.read_bytes() for path in exports}
    for name in ("VellumGpuRuntimeConfig.cmake", "VellumGpuRuntimeConfigVersion.cmake"):
        files[f"{PACKAGE_DIR}/{name}"] = (build_dir / name).read_bytes()
    expected = {f"{PACKAGE_DIR}/{name}" for name in (
        "VellumGpuRuntimeConfig.cmake", "VellumGpuRuntimeConfigVersion.cmake",
        "VellumGpuRuntimeTargets.cmake", "VellumGpuRuntimeTargets-release.cmake")}
    if set(files) != expected:
        raise ArtifactError(f"unexpected generated package files: {sorted(files)}")
    return files


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
    return sorted([*expected_headers(repo), *LIBRARIES, DAWN_LIBRARY, *PACKAGE_FILES,
                   *DOC_FILES, METADATA_NAME])


def notice_sections(text: str) -> dict[str, str]:
    """The `### <name>` sections of NOTICE.md's renderer-components part."""
    start = text.index("## Renderer components\n")
    end = text.index("\n## ", start + 1)
    body = text[start:end]
    sections: dict[str, str] = {}
    for chunk in re.split(r"(?m)^(?=### )", body)[1:]:
        heading, _, rest = chunk.partition("\n")
        sections[heading.removeprefix("### ").strip()] = chunk.rstrip() + "\n"
    return sections


def dawn_notice(repo: Path) -> str:
    """NOTICE.md for the archive: Dawn's own notice, every component the
    renderer legal inventory places inside libdawn_combined.a, and Skia's
    (SkiaLogBridge), copied verbatim from Vellum's NOTICE.md. A component
    without a notice section stops the build."""
    lock = json.loads((repo / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
    renderer = lock["runtime_toolchain_dependencies"]["renderer"]
    if renderer.get("bundled_components_completeness", {}).get("claim") != "exhaustive":
        raise ArtifactError("the renderer legal inventory is not exhaustive")
    components = [row for row in renderer["bundled_components"]
                  if row.get("parent_archive") == DAWN_ARCHIVE]
    sections = notice_sections((repo / "NOTICE.md").read_text(encoding="utf-8"))
    # SkiaLogBridge's library holds code compiled from Skia headers.
    names = ["Dawn and Tint", *(row["name"] for row in components), SKIA_HEADING]
    missing = [name for name in names if name not in sections]
    if missing:
        raise ArtifactError(f"NOTICE.md has no section for {', '.join(missing)}")
    parts = [
        "# Third-Party Notices\n",
        "This archive redistributes `libdawn_combined.a` exactly as the locked\n"
        f"skia-builder `{json.loads((repo / LOCK).read_text())['milestone']}` asset ships it "
        "(see `lib/cmake/VellumGpuRuntime/renderer-lock.json`).\n"
        "Its legal inventory places the components below inside that archive, and\n"
        "`libgpu-runtime-skia-log-bridge.a` holds code compiled from Skia's headers;\n"
        "the texts are Vellum's NOTICE.md sections for them, unchanged.\n",
    ]
    parts.extend(sections[name] for name in names)
    if any("Apache-2.0" in row.get("license", "") for row in components):
        parts.append(sections[APACHE_HEADING])
    return "\n".join(parts)


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
        configure = configure_command(repo, renderer_archive, build_dir, payload, flags)
        run(configure, env=env)
        jobs = str(max(1, min(args.jobs, os.cpu_count() or 1)))
        run(["cmake", "--build", str(build_dir), "--parallel", jobs], env=env)
        run(["cmake", "--install", str(build_dir)], env=env)
        for relative in LIBRARIES:
            normalize_library(payload / relative)
        for name, data in generated_package_files(build_dir).items():
            if (payload / name).read_bytes() != data:
                raise ArtifactError(f"installed {name} differs from the generated one")
        lock = json.loads((repo / LOCK).read_text(encoding="utf-8"))
        locked_dawn = lock["platforms"]["macos-arm64"]["archives"][DAWN_ARCHIVE]
        if sha256(payload / DAWN_LIBRARY) != locked_dawn:
            raise ArtifactError("the installed libdawn_combined.a differs from the lock")
        docs = payload / DOC_DIR
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "LICENSE.md").write_bytes((repo / "LICENSE.md").read_bytes())
        (docs / "NOTICE.md").write_text(dawn_notice(repo), encoding="utf-8")

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
            "toolchain": toolchain_identity(build_dir),
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
