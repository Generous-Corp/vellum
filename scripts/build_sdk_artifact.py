#!/usr/bin/env python3
"""Build a deterministic, checksummed Vellum SDK archive.

The archive is the installation boundary: it contains the authoring CLI,
DesignIR import backend, SDK metadata, and a relocatable CMake install tree.
An explicit pinned-Skia mode additionally composes the GPU/authoring runtime,
the generic native application host, the TypeScript UI package, and the native
CLI backend when every required payload is present.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Iterable


REPO = Path(__file__).resolve().parents[1]
FRAMEWORK_VERSION = "0.1.0"
CLI_VERSION = "0.1.0-dev"
CLI_API = 1
METADATA_SCHEMA = "vellum.sdk-artifact.v1"
EVIDENCE_SCHEMA = "vellum.sdk-artifact-evidence.v1"
COMMAND_NAMES = ("import", "reimport", "build", "run", "test", "capture", "package")
NATIVE_COMMANDS = ("build", "run", "test", "capture", "package")
DESIGN_IR_PAYLOAD_ENTRIES = ("LICENSE.md", "README.md", "bin", "package.json", "schema", "src")
UI_PAYLOAD_ENTRIES = (
    "LICENSE.md", "README.md", "package.json", "package-lock.json", "scripts", "src", "test",
)


class ArtifactError(RuntimeError):
    pass


def run(arguments: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode:
        raise ArtifactError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stdout}"
        )


def installed_cmake_target(install_tree: Path, target: str) -> bool:
    package = install_tree / "lib/cmake/Vellum"
    marker = f"Vellum::{target}"
    return any(marker in path.read_text(encoding="utf-8") for path in package.glob("*.cmake"))


def prepare_ui_payload(repo: Path, payload: Path) -> None:
    source = repo / "packages/vellum-ui"
    destination = payload / "ui"
    destination.mkdir()
    for entry in UI_PAYLOAD_ENTRIES:
        item = source / entry
        if not item.exists():
            raise ArtifactError(f"@vellum/ui payload entry is missing: {item}")
        if item.is_dir():
            shutil.copytree(item, destination / entry)
        else:
            shutil.copy2(item, destination / entry)

    package = json.loads((destination / "package.json").read_text(encoding="utf-8"))
    dependencies = package.get("devDependencies")
    if not isinstance(dependencies, dict) or not dependencies:
        raise ArtifactError("@vellum/ui has no pinned authoring dependencies")
    if any(not isinstance(version, str) or version[:1] in {"^", "~", ">", "<", "*"}
           for version in dependencies.values()):
        raise ArtifactError("@vellum/ui authoring dependencies must use exact versions")
    npm = shutil.which("npm")
    if npm is None:
        raise ArtifactError("--skia-archive requires npm to package @vellum/ui dependencies")
    run([
        npm, "ci", "--include=dev", "--ignore-scripts", "--no-audit", "--no-fund",
    ], cwd=destination)
    shutil.rmtree(destination / "node_modules/.bin", ignore_errors=True)
    for name, version in dependencies.items():
        installed = destination / "node_modules" / name / "package.json"
        if not installed.is_file():
            raise ArtifactError(f"@vellum/ui dependency was not installed: {name}@{version}")
        actual = json.loads(installed.read_text(encoding="utf-8")).get("version")
        if actual != version:
            raise ArtifactError(
                f"@vellum/ui dependency version mismatch: expected {name}@{version}, got {actual}"
            )


def derive_capabilities(payload: Path, install_tree: Path) -> dict[str, object]:
    cmake_sdk = (install_tree / "lib/cmake/Vellum/VellumConfig.cmake").is_file()
    gpu_renderer = installed_cmake_target(install_tree, "Gpu")
    authoring_runtime = installed_cmake_target(install_tree, "Authoring")
    import_backend = (payload / "design-ir/bin/vellum-backend.js").is_file()
    ui_runtime = all((payload / path).exists() for path in (
        "ui/package.json",
        "ui/package-lock.json",
        "ui/src/index.js",
        "ui/node_modules/esbuild/package.json",
        "ui/node_modules/typescript/package.json",
    ))
    native_backend = (payload / "vellum_native_backend.py").is_file()
    native_host = all((install_tree / path).is_file() for path in (
        "bin/vellum-app-host",
        "lib/libvellum-authoring.dylib",
        "lib/libvellum-gpu.dylib",
    ))
    native_ready = (
        gpu_renderer and authoring_runtime and ui_runtime and native_backend and native_host
    )
    commands = {name: False for name in COMMAND_NAMES}
    commands["import"] = import_backend
    commands["reimport"] = import_backend
    for name in NATIVE_COMMANDS:
        commands[name] = native_ready
    return {
        "cmake_sdk": cmake_sdk,
        "authoring_cli": (payload / "vellum_cli.py").is_file(),
        "gpu_renderer": gpu_renderer,
        "commands": commands,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_static_archive(path: Path) -> None:
    """Remove BSD ar member timestamps/owner ids without changing object bytes."""

    data = bytearray(path.read_bytes())
    if not data.startswith(b"!<arch>\n"):
        raise ArtifactError(f"installed static library is not an ar archive: {path}")
    offset = 8
    while offset < len(data):
        if offset + 60 > len(data) or data[offset + 58:offset + 60] != b"`\n":
            raise ArtifactError(f"installed static library has a malformed member header: {path}")
        try:
            member_size = int(bytes(data[offset + 48:offset + 58]).decode("ascii").strip())
        except ValueError as error:
            raise ArtifactError(f"installed static library has an invalid member size: {path}") from error
        data[offset + 16:offset + 28] = b"0".ljust(12)
        data[offset + 28:offset + 34] = b"0".ljust(6)
        data[offset + 34:offset + 40] = b"0".ljust(6)
        offset += 60 + member_size
        if offset % 2:
            offset += 1
    if offset != len(data):
        raise ArtifactError(f"installed static library has trailing malformed data: {path}")
    path.write_bytes(data)


def target_name() -> str:
    os_name = platform.system().lower()
    os_part = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(os_name)
    if not os_part:
        raise ArtifactError(f"unsupported artifact operating system: {platform.system()}")
    machine = platform.machine().lower()
    arch_part = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    if not arch_part:
        raise ArtifactError(f"unsupported artifact architecture: {platform.machine()}")
    return f"{os_part}-{arch_part}"


def source_identity(repo: Path, override: str | None, allow_dirty: bool) -> tuple[str, bool]:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=False
    )
    value = completed.stdout.strip()
    if completed.returncode or len(value) != 40:
        raise ArtifactError("could not resolve the Vellum source commit")
    if override:
        if len(override) != 40 or any(character not in "0123456789abcdef" for character in override):
            raise ArtifactError("--source-commit must be a lowercase 40-character Git SHA")
        if override != value:
            raise ArtifactError("--source-commit must equal the checked-out Vellum HEAD")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if status.returncode:
        raise ArtifactError("could not inspect the Vellum source tree")
    clean = not bool(status.stdout.strip())
    if not clean and not allow_dirty:
        raise ArtifactError(
            "refusing to attribute an SDK artifact to HEAD from a dirty source tree; "
            "commit the source first"
        )
    return value, clean


def payload_files(payload: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(item for item in payload.rglob("*") if item.is_file()):
        relative = path.relative_to(payload).as_posix()
        rows.append(
            {
                "path": relative,
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "executable": bool(path.stat().st_mode & stat.S_IXUSR),
            }
        )
    return rows


def normalized_tarinfo(path: Path, arcname: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(arcname)
    mode = path.stat().st_mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
    elif path.is_file():
        info.type = tarfile.REGTYPE
        info.mode = 0o755 if mode & stat.S_IXUSR else 0o644
        info.size = path.stat().st_size
    else:
        raise ArtifactError(f"artifact payload contains unsupported entry: {path}")
    return info


def write_archive(payload: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(payload.rglob("*"), key=lambda item: item.relative_to(payload).as_posix()):
                    arcname = path.relative_to(payload).as_posix()
                    info = normalized_tarinfo(path, arcname)
                    if path.is_file():
                        with path.open("rb") as handle:
                            archive.addfile(info, handle)
                    else:
                        archive.addfile(info)
    os.replace(temporary, output)


def copy_payload(
    repo: Path,
    install_tree: Path,
    payload: Path,
    commit: str,
    source_tree_clean: bool,
    target: str,
    *,
    include_ui: bool,
) -> dict[str, object]:
    shutil.copy2(repo / "cli/vellum_cli.py", payload / "vellum_cli.py")
    shutil.copy2(repo / "cli/vellum_backend.py", payload / "vellum_backend.py")
    shutil.copy2(repo / "cli/vellum_manifest.py", payload / "vellum_manifest.py")
    shutil.copytree(repo / ".agents", payload / ".agents")
    shutil.copytree(repo / "templates", payload / "templates")
    shutil.copytree(install_tree, payload / "sdk")
    design_ir_source = repo / "packages/vellum-design-ir"
    design_ir_payload = payload / "design-ir"
    design_ir_payload.mkdir()
    for entry in DESIGN_IR_PAYLOAD_ENTRIES:
        source = design_ir_source / entry
        destination = design_ir_payload / entry
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    if include_ui:
        prepare_ui_payload(repo, payload)
        native_backend = repo / "cli/vellum_native_backend.py"
        if native_backend.is_file():
            shutil.copy2(native_backend, payload / "vellum_native_backend.py")
    capabilities = derive_capabilities(payload, install_tree)
    metadata: dict[str, object] = {
        "schema": METADATA_SCHEMA,
        "framework_version": FRAMEWORK_VERSION,
        "cli_version": CLI_VERSION,
        "cli_api": CLI_API,
        "source_commit": commit,
        "source_tree_clean": source_tree_clean,
        "target": target,
        "capabilities": capabilities,
    }
    metadata["files"] = payload_files(payload)
    (payload / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def build(args: argparse.Namespace) -> dict[str, object]:
    repo = args.repo.resolve()
    output_dir = args.output_dir.resolve()
    commit, source_tree_clean = source_identity(repo, args.source_commit, args.allow_dirty)
    target = args.target or target_name()
    skia_archive = args.skia_archive.resolve() if args.skia_archive else None
    if skia_archive and not skia_archive.is_file():
        raise ArtifactError(f"--skia-archive does not exist: {skia_archive}")
    if skia_archive and args.graphics == "off":
        raise ArtifactError("--skia-archive cannot be combined with --graphics off")
    graphics = bool(skia_archive) or args.graphics == "on" or (
        args.graphics == "auto" and platform.system() == "Darwin"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vellum-sdk-artifact-") as temporary_text:
        temporary = Path(temporary_text)
        build_dir = temporary / "build"
        install_tree = temporary / "install"
        payload = temporary / "payload"
        payload.mkdir()
        configure = [
            "cmake", "-S", str(repo), "-B", str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DVELLUM_BUILD_TESTS=OFF",
            "-DVELLUM_BUILD_SMOKE_NATIVE=OFF",
            f"-DVELLUM_ENABLE_GRAPHICS={'ON' if graphics else 'OFF'}",
        ]
        if skia_archive:
            configure.extend([
                "-DVELLUM_ENABLE_GPU=ON",
                "-DVELLUM_REQUIRE_GPU=ON",
                "-DVELLUM_ENABLE_AUTHORING=ON",
                f"-DVELLUM_SKIA_ARCHIVE={skia_archive}",
            ])
        if shutil.which("ninja"):
            configure.extend(["-G", "Ninja"])
        run(configure)
        cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8")
        if f"CMAKE_PROJECT_VERSION:STATIC={FRAMEWORK_VERSION}" not in cache:
            raise ArtifactError(
                "artifact framework version does not match the configured CMake project version"
            )
        run(["cmake", "--build", str(build_dir), "--config", "Release", "--parallel"])
        run(["cmake", "--install", str(build_dir), "--config", "Release", "--prefix", str(install_tree)])
        for archive in sorted(install_tree.rglob("*.a")):
            normalize_static_archive(archive)
        if skia_archive and not (
            installed_cmake_target(install_tree, "Gpu") and
            installed_cmake_target(install_tree, "Authoring") and
            (install_tree / "bin/vellum-app-host").is_file() and
            (install_tree / "lib/libvellum-authoring.dylib").is_file() and
            (install_tree / "lib/libvellum-gpu.dylib").is_file()
        ):
            raise ArtifactError(
                "--skia-archive build did not install the complete native application runtime"
            )
        if skia_archive and target != "darwin-arm64":
            raise ArtifactError("native application artifacts currently require target darwin-arm64")
        metadata = copy_payload(
            repo, install_tree, payload, commit, source_tree_clean, target,
            include_ui=bool(skia_archive),
        )
        if skia_archive and not all(
            metadata["capabilities"]["commands"][name] for name in NATIVE_COMMANDS
        ):
            raise ArtifactError(
                "--skia-archive payload did not derive every native application capability"
            )

        asset_name = f"vellum-sdk-{FRAMEWORK_VERSION}-{target}.tar.gz"
        archive = output_dir / asset_name
        write_archive(payload, archive)

    digest = sha256(archive)
    checksums = output_dir / "SHA256SUMS"
    checksums.write_text(f"{digest}  {asset_name}\n", encoding="utf-8")
    evidence: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "artifact": asset_name,
        "artifact_sha256": digest,
        "checksums": checksums.name,
        "framework_version": FRAMEWORK_VERSION,
        "source_commit": commit,
        "source_tree_clean": source_tree_clean,
        "target": target,
        "claims": metadata["capabilities"],
    }
    evidence_path = output_dir / f"{asset_name}.evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**evidence, "artifact_path": str(archive), "evidence_path": str(evidence_path)}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repo", type=Path, default=REPO)
    value.add_argument("--output-dir", type=Path, default=REPO / "dist")
    value.add_argument("--source-commit")
    value.add_argument(
        "--allow-dirty",
        action="store_true",
        help="development/test only: build while recording source_tree_clean=false",
    )
    value.add_argument("--target", help="override the detected artifact target (tests/release coordinator)")
    value.add_argument("--graphics", choices=("auto", "on", "off"), default="auto")
    value.add_argument(
        "--skia-archive",
        type=Path,
        help="compose the exact pinned native GPU host, Authoring, @vellum/ui, and CLI payload",
    )
    value.add_argument("--json", action="store_true")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        evidence = build(args)
    except (ArtifactError, OSError) as error:
        print(f"vellum-sdk-artifact: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Built {evidence['artifact_path']}")
        print(f"SHA-256 {evidence['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
