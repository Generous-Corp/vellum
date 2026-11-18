#!/usr/bin/env python3
"""Build the deterministic RmlUi SDK archive published beside the Vellum SDK.

vellum-rmlui-sdk-<version>-<target>.tar.gz is the VellumRmlUiNative CMake
package: VellumRmlUi::Native and VellumRmlUi::Metal, the pinned RmlUi they
expose (headers and static archives, under RmlUi's own target names), the
pinned FreeType archive RmlUi's font engine links, the RmlUi licence, the
FreeType License and NOTICE-Native.md, and rmlui-sdk-artifact.json, which
names the source commit, the pins and every file's digest.

The Vellum SDK itself carries no RmlUi; this archive is optional. Its package
installs beside the SDK's VellumRmlUi package rather than inside it, and finds
that package for any VellumRmlUi component a consumer also asks for. Native
and Core each carry their own RmlUi, so a program links one or the other.

The archive is built from `cmake -S rmlui` alone (no Vellum, no Skia) with the
per-build temporary root remapped out of every object and static-library
member headers normalized, so two builds of one commit are byte-identical. It
runs the CMake that gpu-runtime/cmake-lock.json pins (see pinned_cmake.py), so
the package files CMake writes do not depend on the CMake on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import pinned_cmake  # noqa: E402
from build_gpu_runtime_artifact import (  # noqa: E402
    GENERATOR,
    normalize_library,
    run,
    toolchain_identity,
    vellum_version,
)
from build_sdk_artifact import (  # noqa: E402
    ArtifactError,
    payload_files,
    sha256,
    source_identity,
    target_name,
    write_archive,
)

METADATA_SCHEMA = "vellum.rmlui-sdk-artifact.v1"
METADATA_NAME = "rmlui-sdk-artifact.json"
COMPONENT = "VellumRmlUiNative"
PACKAGE_DIR = "lib/cmake/VellumRmlUiNative"
DOC_DIR = "share/doc/VellumRmlUiNative"
DEPENDENCIES = "rmlui/cmake/VellumRmlUiDependencies.cmake"
# The Vellum headers Native installs; each includes RmlUi's.
NATIVE_HEADERS = (
    "include/vellum/rmlui/cpu_render_interface.hpp",
    "include/vellum/rmlui/element_classes_native.hpp",
    "include/vellum/rmlui/metal_render_interface.hpp",
    "include/vellum/rmlui/midi_learn_native.hpp",
    "include/vellum/rmlui/native.hpp",
)
# The VellumRmlUi headers those include, carried so Native builds from this
# archive alone; byte-identical to the Vellum SDK's copies.
SHARED_HEADERS = (
    "include/vellum/rmlui/assets.hpp",
    "include/vellum/rmlui/backend.hpp",
    "include/vellum/rmlui/element_classes.hpp",
    "include/vellum/rmlui/export.hpp",
    "include/vellum/rmlui/host_bridge.hpp",
    "include/vellum/rmlui/midi_learn.hpp",
)
LIBRARIES = (
    "lib/libfreetype.a",
    "lib/librmlui.a",
    "lib/librmlui_debugger.a",
    "lib/libvellum-rmlui-metal.a",
    "lib/libvellum-rmlui-native.a",
)
PACKAGE_FILES = (
    f"{PACKAGE_DIR}/VellumRmlUiMetalTargets-release.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiMetalTargets.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiNativeConfig.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiNativeConfigVersion.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiNativeTargets-release.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiNativeTargets.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiRmlUiTargets-release.cmake",
    f"{PACKAGE_DIR}/VellumRmlUiRmlUiTargets.cmake",
)
DOC_FILES = (
    f"{DOC_DIR}/FreeType-FTL.TXT",
    f"{DOC_DIR}/NOTICE-Native.md",
    f"{DOC_DIR}/RmlUi-LICENSE.txt",
)
# RmlUi's own public headers, whatever their names, all under one root.
RMLUI_HEADER_ROOT = "include/RmlUi/"
# The licence of the third-party container headers RmlUi bundles under its
# headers; it travels with them.
RMLUI_HEADER_LICENSES = frozenset({f"{RMLUI_HEADER_ROOT}Core/Containers/LICENSE.txt"})
# RmlUi installs its own CMake package here. The archive's package exports the
# same targets as VellumRmlUiRmlUiTargets, so the upstream copy, whose
# targets file is not installed from a subproject, is left out.
RMLUI_UPSTREAM_PACKAGE_DIR = "lib/cmake/RmlUi"


def archive_name(version: str, target: str) -> str:
    return f"vellum-rmlui-sdk-{version}-{target}.tar.gz"


def configure_command(repo: Path, build_dir: Path, prefix: Path, flags: str = "") -> list[str]:
    """The one configure the builder uses: rmlui/ alone, with Native, Metal and
    fonts, installed as the VellumRmlUiNative package."""
    command = [
        "cmake", "-S", str(repo / "rmlui"), "-B", str(build_dir),
        "-G", GENERATOR,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_OSX_ARCHITECTURES=arm64",
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.3",
        "-DVELLUM_RMLUI_BUILD_TESTS=OFF",
        "-DVELLUM_RMLUI_INSTALL=ON",
        "-DVELLUM_RMLUI_NATIVE=ON",
        "-DVELLUM_RMLUI_METAL=ON",
        "-DVELLUM_RMLUI_FONTS=ON",
        "-DVELLUM_RMLUI_NATIVE_PACKAGE=ON",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
    ]
    if flags:
        for language in ("C", "CXX", "OBJCXX"):
            command.append(f"-DCMAKE_{language}_FLAGS={flags}")
    return command


def pins(repo: Path) -> dict[str, str]:
    """The RmlUi commit and FreeType digest the module builds from."""
    text = (repo / DEPENDENCIES).read_text(encoding="utf-8")
    found = {}
    for key, name in (("rmlui_commit", "VELLUM_RMLUI_RMLUI_COMMIT"),
                      ("freetype_sha256", "VELLUM_RMLUI_FREETYPE_SHA256")):
        match = re.search(rf"set\({name}\s+([0-9a-f]{{40,64}})\)", text)
        if match is None:
            raise ArtifactError(f"could not read {name} from {DEPENDENCIES}")
        found[key] = match.group(1)
    return found


def is_allowed(relative: str) -> bool:
    """Whether an installed path belongs in the RmlUi SDK."""
    if relative.startswith(RMLUI_HEADER_ROOT):
        return relative.endswith((".h", ".hpp", ".inl")) or relative in RMLUI_HEADER_LICENSES
    return relative in {*NATIVE_HEADERS, *SHARED_HEADERS, *LIBRARIES, *PACKAGE_FILES, *DOC_FILES,
                        METADATA_NAME}


def required_files() -> list[str]:
    return sorted([*NATIVE_HEADERS, *SHARED_HEADERS, *LIBRARIES, *PACKAGE_FILES, *DOC_FILES,
                   f"{RMLUI_HEADER_ROOT}Core.h", f"{RMLUI_HEADER_ROOT}Debugger.h", METADATA_NAME])


def check_payload(files: Iterable[str]) -> None:
    """Every installed file is one the RmlUi SDK carries, and every file it
    must carry is there."""
    files = set(files)
    unexpected = sorted(path for path in files if not is_allowed(path))
    missing = sorted(set(required_files()) - files)
    if unexpected or missing:
        raise ArtifactError(f"unexpected RmlUi SDK files; missing {missing}, extra {unexpected}")


def build(args: argparse.Namespace) -> dict[str, object]:
    repo = args.repo.resolve()
    commit, clean = source_identity(repo, args.source_commit, args.allow_dirty)
    version = vellum_version(repo)
    target = target_name()
    if target != "darwin-arm64":
        raise ArtifactError("the RmlUi SDK artifact is built for darwin-arm64 only")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vellum-rmlui-sdk-artifact-") as temporary_text:
        temporary = Path(temporary_text)
        build_dir = temporary / "build"
        payload = temporary / "payload"
        canonical_build = "/vellum-rmlui-sdk-artifact"
        canonical_source = "/vellum-rmlui-sdk-source"
        maps = []
        for flag in ("file", "macro", "debug"):
            maps.append(f"-f{flag}-prefix-map={temporary}={canonical_build}")
            maps.append(f"-f{flag}-prefix-map={repo}={canonical_source}")
        env = dict(os.environ, ZERO_AR_DATE="1")
        cmake, cmake_version = pinned_cmake.provide(repo, temporary / "cmake")
        run(pinned_cmake.use(configure_command(repo, build_dir, payload, " ".join(maps)), cmake),
            env=env)
        jobs = str(max(1, min(args.jobs, os.cpu_count() or 1)))
        run([str(cmake), "--build", str(build_dir), "--parallel", jobs], env=env)
        run([str(cmake), "--install", str(build_dir), "--component", COMPONENT], env=env)
        toolchain = toolchain_identity(build_dir)
        if toolchain["cmake"] != cmake_version:
            raise ArtifactError(f"the build was configured by CMake {toolchain['cmake']}, "
                                f"not the pinned {cmake_version}")
        upstream_package = payload / RMLUI_UPSTREAM_PACKAGE_DIR
        if upstream_package.is_dir():
            shutil.rmtree(upstream_package)
        for relative in LIBRARIES:
            normalize_library(payload / relative)

        metadata = {
            "schema": METADATA_SCHEMA,
            "name": "vellum-rmlui-sdk",
            "version": version,
            "target": target,
            "source_commit": commit,
            "source_tree_clean": clean,
            "package": "VellumRmlUiNative",
            "components": ["Native", "Metal"],
            "pins": pins(repo),
            "toolchain": toolchain,
            "files": payload_files(payload),
        }
        (payload / METADATA_NAME).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        check_payload(path.relative_to(payload).as_posix()
                      for path in payload.rglob("*") if path.is_file())
        output = output_dir / archive_name(version, target)
        write_archive(payload, output)
    return {
        "archive": str(output),
        "sha256": sha256(output),
        "version": version,
        "target": target,
        "source_commit": commit,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--repo", type=Path, default=REPO)
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
    except (ArtifactError, pinned_cmake.PinnedCMakeError) as error:
        print(f"vellum-rmlui-sdk-artifact: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['archive']}  {result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
