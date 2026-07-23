#!/usr/bin/env python3
"""Transactional installer for verified Vellum SDK archives.

The shell and PowerShell bootstraps own acquisition. This module owns the
security boundary after an archive and SHA256SUMS exist locally: verification,
safe extraction, immutable storage, integrity receipts, and atomic activation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import unicodedata
import uuid


ARCHIVE_SCHEMA = "vellum.sdk-artifact.v1"
INSTALL_SCHEMA = "vellum.sdk-install.v1"
OWNERSHIP_SCHEMA = "vellum.install-ownership.v1"
STATE_SCHEMA = "vellum.installer-state.v1"
MAX_MEMBERS = 20_000
MAX_BYTES = 4 * 1024**3
MAX_METADATA_BYTES = 16 * 1024**2
SAFE_ROOTS = {
    ".agents",
    "bin",
    "design-ir",
    "metadata.json",
    "node",
    "sdk",
    "templates",
    "ui",
    "vellum_backend.py",
    "vellum_cli.py",
    "vellum_manifest.py",
    "vellum_native_backend.py",
    "vellum_png.py",
    "vellum_web_backend.py",
    "web",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
SAFE_INSTALL_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,255}")
MANAGED_LAUNCHER_MARKER = "# managed-by: vellum-install-core-v1"
PROBE_ENV_ALLOWLIST = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TMPDIR",
}


class InstallError(RuntimeError):
    pass


@contextmanager
def prefix_lock(prefix: Path):
    prefix.mkdir(parents=True, exist_ok=True)
    lock_path = prefix / ".vellum-installer.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise InstallError(f"cannot open the installer lock: {lock_path}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InstallError(f"installer lock is not a regular file: {lock_path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield prefix
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_handle(handle) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def artifact_probe_environment(**overrides: str) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in PROBE_ENV_ALLOWLIST
    }
    environment.update(overrides)
    return environment


def exact_checksum(archive: Path, checksums: Path) -> str:
    matches: list[str] = []
    for line in checksums.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        name = fields[1].removeprefix("*")
        if name == archive.name:
            matches.append(fields[0].lower())
    if len(matches) != 1:
        raise InstallError(
            f"expected exactly one checksum for {archive.name}; found {len(matches)}"
        )
    if not SHA256.fullmatch(matches[0]):
        raise InstallError(f"checksum for {archive.name} is not 64 lowercase hexadecimal characters")
    return matches[0]


def safe_relative_path(value: object) -> PurePosixPath | None:
    if not isinstance(value, str):
        return None
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or value != path.as_posix()
    ):
        return None
    return path


def validate_metadata(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, dict) or metadata.get("schema") != ARCHIVE_SCHEMA:
        raise InstallError("unsupported SDK artifact metadata")
    if (
        not isinstance(metadata.get("framework_version"), str)
        or not SAFE_ID.fullmatch(metadata["framework_version"])
        or metadata.get("cli_api") != 1
        or not isinstance(metadata.get("target"), str)
        or not SAFE_ID.fullmatch(metadata["target"])
        or not isinstance(metadata.get("source_commit"), str)
        or not COMMIT.fullmatch(metadata["source_commit"])
        or not isinstance(metadata.get("files"), list)
    ):
        raise InstallError("incompatible SDK artifact metadata")
    return metadata


def inspect_open_archive(
    handle: tarfile.TarFile,
) -> tuple[dict[str, object], list[tarfile.TarInfo]]:
    members = handle.getmembers()
    if (
        len(members) > MAX_MEMBERS
        or any(member.size < 0 for member in members)
        or sum(member.size for member in members) > MAX_BYTES
    ):
        raise InstallError("archive exceeds installer safety limits")
    by_name = {member.name: member for member in members}
    if len(by_name) != len(members):
        raise InstallError("archive contains duplicate member names")
    portable_names: dict[str, str] = {}
    for member in members:
        path = PurePosixPath(member.name)
        portable_name = unicodedata.normalize("NFC", member.name).casefold()
        prior_name = portable_names.get(portable_name)
        if prior_name is not None and prior_name != member.name:
            raise InstallError(
                f"archive contains a portable-path collision: "
                f"{prior_name}, {member.name}"
            )
        portable_names[portable_name] = member.name
        if (
            not path.parts
            or path.parts[0] not in SAFE_ROOTS
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in member.name
            or ":" in path.parts[0]
            or member.name != path.as_posix()
            or member.issym()
            or member.islnk()
            or not (member.isfile() or member.isdir())
            or member.mode & ~0o777
            or stat.S_IMODE(member.mode) != (
                0o755 if member.isdir() or member.mode & stat.S_IXUSR else 0o644
            )
        ):
            raise InstallError(f"unsafe archive member: {member.name}")
        for parent in path.parents:
            parent_member = by_name.get(parent.as_posix())
            if parent_member is not None and not parent_member.isdir():
                raise InstallError(
                    f"archive member has a non-directory parent: {member.name}"
                )
    metadata_member = by_name.get("metadata.json")
    if metadata_member is None or not metadata_member.isfile():
        raise InstallError("archive has no metadata.json")
    if metadata_member.size > MAX_METADATA_BYTES:
        raise InstallError("archive metadata exceeds the installer size limit")
    metadata_stream = handle.extractfile(metadata_member)
    if metadata_stream is None:
        raise InstallError("archive metadata cannot be read")
    metadata = validate_metadata(json.load(metadata_stream))
    declared = metadata["files"]
    expected_files = {
        member.name for member in members
        if member.isfile() and member.name != "metadata.json"
    }
    seen: set[str] = set()
    for row in declared:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256", "size", "executable"}
            or safe_relative_path(row.get("path")) is None
            or row["path"] in seen
            or row["path"] not in expected_files
            or not isinstance(row.get("size"), int)
            or not isinstance(row.get("executable"), bool)
            or not isinstance(row.get("sha256"), str)
            or not SHA256.fullmatch(row["sha256"])
        ):
            raise InstallError("artifact file inventory is malformed")
        member = by_name[row["path"]]
        stream = handle.extractfile(member)
        if stream is None:
            raise InstallError(f"artifact member cannot be read: {row['path']}")
        payload_size = 0
        payload_digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            payload_size += len(block)
            payload_digest.update(block)
        if (
            payload_size != row["size"]
            or payload_digest.hexdigest() != row["sha256"]
            or bool(member.mode & stat.S_IXUSR) != row["executable"]
        ):
            raise InstallError(f"artifact payload does not match metadata: {row['path']}")
        seen.add(row["path"])
    if seen != expected_files:
        raise InstallError("artifact metadata does not cover every payload file")
    return metadata, members


def inspect_archive(archive: Path) -> tuple[dict[str, object], list[tarfile.TarInfo]]:
    try:
        with archive.open("rb") as raw:
            with tarfile.open(fileobj=raw, mode="r:gz") as handle:
                return inspect_open_archive(handle)
    except (OSError, tarfile.TarError, json.JSONDecodeError) as error:
        raise InstallError(f"cannot inspect SDK archive: {error}") from error


def extract_archive(
    archive: Path,
    destination: Path,
    expected_digest: str,
) -> dict[str, object]:
    try:
        with archive.open("rb") as raw:
            before = sha256_handle(raw)
            if before != expected_digest:
                raise InstallError("verified archive cache changed before extraction")
            raw.seek(0)
            with tarfile.open(fileobj=raw, mode="r:gz") as handle:
                metadata, members = inspect_open_archive(handle)
                handle.extractall(destination)
                verify_extracted_archive(destination, metadata, members)
            raw.seek(0)
            after = sha256_handle(raw)
            if after != expected_digest:
                raise InstallError("verified archive cache changed during extraction")
            return metadata
    except (OSError, tarfile.TarError, json.JSONDecodeError) as error:
        raise InstallError(f"cannot extract SDK archive: {error}") from error


def verify_extracted_archive(
    destination: Path,
    metadata: dict[str, object],
    members: list[tarfile.TarInfo],
) -> None:
    expected_entries: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        expected_entries.add(member.name)
        expected_entries.update(
            parent.as_posix()
            for parent in path.parents
            if parent.as_posix() != "."
        )
    actual_entries: set[str] = set()
    for path in destination.rglob("*"):
        relative = path.relative_to(destination).as_posix()
        if path.is_symlink():
            raise InstallError(f"extracted SDK contains a symlink: {relative}")
        if not (path.is_file() or path.is_dir()):
            raise InstallError(f"extracted SDK contains a special entry: {relative}")
        actual_entries.add(relative)
    if actual_entries != expected_entries:
        raise InstallError("extracted SDK entries do not match the inspected archive")
    declared = {
        row["path"]: row
        for row in metadata["files"]
    }
    for member in members:
        path = destination.joinpath(*PurePosixPath(member.name).parts)
        if member.isdir():
            if not path.is_dir():
                raise InstallError(
                    f"extracted SDK directory is missing: {member.name}"
                )
            continue
        if not path.is_file() or path.is_symlink():
            raise InstallError(f"extracted SDK file is unsafe: {member.name}")
        if member.name == "metadata.json":
            continue
        row = declared[member.name]
        if (
            path.stat().st_size != row["size"]
            or sha256_file(path) != row["sha256"]
            or bool(path.stat().st_mode & stat.S_IXUSR) != row["executable"]
        ):
            raise InstallError(
                f"extracted SDK payload differs from metadata: {member.name}"
            )


def write_text_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def sdk_launcher() -> str:
    return "\n".join([
        "#!/bin/sh",
        "set -eu",
        MANAGED_LAUNCHER_MARKER,
        'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)',
        'sdk_root=$(CDPATH="" cd -- "$bindir/.." && pwd)',
        'export VELLUM_SDK_ROOT="$sdk_root"',
        'exec python3 "$sdk_root/vellum_cli.py" "$@"',
        "",
    ])


def backend_launcher(module: str) -> str:
    return "\n".join([
        "#!/bin/sh",
        "set -eu",
        MANAGED_LAUNCHER_MARKER,
        'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)',
        f'exec python3 "$bindir/../{module}" "$@"',
        "",
    ])


def import_launcher() -> str:
    return "\n".join([
        "#!/bin/sh",
        "set -eu",
        MANAGED_LAUNCHER_MARKER,
        'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)',
        'if [ -x "$bindir/../node/bin/node" ]; then',
        '  exec "$bindir/../node/bin/node" "$bindir/../design-ir/bin/vellum-backend.js" "$@"',
        "fi",
        'exec node "$bindir/../design-ir/bin/vellum-backend.js" "$@"',
        "",
    ])


def prefix_launcher() -> str:
    return "\n".join([
        "#!/bin/sh",
        "set -eu",
        MANAGED_LAUNCHER_MARKER,
        'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)',
        'prefix=$(CDPATH="" cd -- "$bindir/.." && pwd)',
        'exec "$prefix/lib/vellum/bin/vellum" "$@"',
        "",
    ])


def prepare_install_root(
    root: Path,
    metadata: dict[str, object],
    archive: Path,
    digest: str,
) -> None:
    manifest = {
        "schema": INSTALL_SCHEMA,
        "verified": True,
        "artifact": archive.name,
        "artifact_sha256": digest,
        "framework_version": metadata["framework_version"],
        "target": metadata["target"],
        "source_commit": metadata["source_commit"],
    }
    (root / "install-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_text_atomic(bin_dir / "vellum", sdk_launcher(), 0o755)
    write_text_atomic(bin_dir / "vellum-backend", backend_launcher("vellum_backend.py"), 0o755)
    write_text_atomic(bin_dir / "vellum-import-backend", import_launcher(), 0o755)
    if (root / "vellum_native_backend.py").is_file():
        write_text_atomic(
            bin_dir / "vellum-native-backend",
            backend_launcher("vellum_native_backend.py"),
            0o755,
        )
    if (root / "vellum_web_backend.py").is_file():
        write_text_atomic(
            bin_dir / "vellum-web-backend",
            backend_launcher("vellum_web_backend.py"),
            0o755,
        )


def owned_files(root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name == ".vellum-install-ownership.json":
            continue
        result.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "mode": stat.S_IMODE(path.stat().st_mode),
        })
    return result


def owned_directories(root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise InstallError(
                f"installed SDK contains an unsafe symlink: "
                f"{path.relative_to(root).as_posix()}"
            )
        if path.is_dir():
            result.append({
                "path": path.relative_to(root).as_posix(),
                "mode": stat.S_IMODE(path.stat().st_mode),
            })
        elif not path.is_file():
            raise InstallError(
                f"installed SDK contains a special entry: "
                f"{path.relative_to(root).as_posix()}"
            )
    return result


def write_ownership(root: Path, install_id: str) -> dict[str, object]:
    receipt = {
        "schema": OWNERSHIP_SCHEMA,
        "install_id": install_id,
        "root_mode": stat.S_IMODE(root.stat().st_mode),
        "directories": owned_directories(root),
        "files": owned_files(root),
    }
    (root / ".vellum-install-ownership.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def verify_install_root(
    root: Path,
    *,
    expected_install_id: str | None = None,
) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        raise InstallError(f"installed SDK root is not an immutable directory: {root}")
    receipt_path = root / ".vellum-install-ownership.json"
    if receipt_path.is_symlink():
        raise InstallError(f"installed SDK ownership receipt is unsafe: {root}")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"installed SDK has no valid ownership receipt: {root}") from error
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {
            "schema",
            "install_id",
            "root_mode",
            "directories",
            "files",
        }
        or receipt.get("schema") != OWNERSHIP_SCHEMA
        or not isinstance(receipt.get("install_id"), str)
        or not SAFE_INSTALL_ID.fullmatch(receipt["install_id"])
        or receipt["install_id"] != (expected_install_id or root.name)
        or not isinstance(receipt.get("root_mode"), int)
        or isinstance(receipt.get("root_mode"), bool)
        or not 0 <= receipt["root_mode"] <= 0o777
        or stat.S_IMODE(root.stat().st_mode) != receipt["root_mode"]
        or not isinstance(receipt.get("directories"), list)
        or not isinstance(receipt.get("files"), list)
    ):
        raise InstallError(f"installed SDK ownership receipt is incompatible: {root}")
    expected_directories: dict[str, int] = {}
    for row in receipt["directories"]:
        relative = safe_relative_path(row.get("path")) if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "mode"}
            or relative is None
            or row["path"] in expected_directories
            or not isinstance(row["mode"], int)
            or isinstance(row["mode"], bool)
            or not 0 <= row["mode"] <= 0o777
        ):
            raise InstallError(f"installed SDK directory ownership is malformed: {root}")
        expected_directories[row["path"]] = row["mode"]
    expected: set[str] = set()
    for row in receipt["files"]:
        relative = safe_relative_path(row.get("path")) if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256", "size", "mode"}
            or relative is None
            or row["path"] in expected
            or not isinstance(row["sha256"], str)
            or not SHA256.fullmatch(row["sha256"])
            or not isinstance(row["size"], int)
            or isinstance(row["size"], bool)
            or row["size"] < 0
            or not isinstance(row["mode"], int)
            or isinstance(row["mode"], bool)
            or row["mode"] not in {0o644, 0o755}
        ):
            raise InstallError(f"installed SDK ownership row is malformed: {root}")
        path = root.joinpath(*relative.parts)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != row["size"]
            or sha256_file(path) != row["sha256"]
            or stat.S_IMODE(path.stat().st_mode) != row["mode"]
        ):
            raise InstallError(f"installed SDK integrity check failed: {row['path']}")
        expected.add(row["path"])
    actual: set[str] = set()
    actual_directories: dict[str, int] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise InstallError(f"installed SDK contains an unsafe symlink: {relative}")
        if path.is_dir():
            actual_directories[relative] = stat.S_IMODE(path.stat().st_mode)
        elif path.is_file():
            if path != receipt_path:
                actual.add(relative)
        else:
            raise InstallError(f"installed SDK contains a special entry: {relative}")
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise InstallError(
            "installed SDK ownership differs"
            + (f"; extra={extra[:3]}" if extra else "")
            + (f"; missing={missing[:3]}" if missing else "")
        )
    if actual_directories != expected_directories:
        actual_names = set(actual_directories)
        expected_names = set(expected_directories)
        extra = sorted(actual_names - expected_names)
        missing = sorted(expected_names - actual_names)
        changed = sorted(
            name
            for name in actual_names & expected_names
            if actual_directories[name] != expected_directories[name]
        )
        raise InstallError(
            "installed SDK directory ownership differs"
            + (f"; extra={extra[:3]}" if extra else "")
            + (f"; missing={missing[:3]}" if missing else "")
            + (f"; mode_changed={changed[:3]}" if changed else "")
        )
    return receipt


def load_install_manifest(root: Path) -> dict[str, object]:
    manifest_path = root / "install-manifest.json"
    if manifest_path.is_symlink():
        raise InstallError(f"installed SDK manifest is unsafe: {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"installed SDK has no valid install manifest: {root}") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "schema",
            "verified",
            "artifact",
            "artifact_sha256",
            "framework_version",
            "target",
            "source_commit",
        }
        or manifest.get("schema") != INSTALL_SCHEMA
        or manifest.get("verified") is not True
        or not isinstance(manifest.get("artifact"), str)
        or Path(manifest["artifact"]).name != manifest["artifact"]
        or not SHA256.fullmatch(str(manifest.get("artifact_sha256", "")))
        or not SAFE_ID.fullmatch(str(manifest.get("framework_version", "")))
        or not SAFE_ID.fullmatch(str(manifest.get("target", "")))
        or not COMMIT.fullmatch(str(manifest.get("source_commit", "")))
        or root.name != (
            f"{manifest['framework_version']}-{manifest['target']}-"
            f"{manifest['artifact_sha256']}"
        )
    ):
        raise InstallError(f"installed SDK manifest is incompatible: {root}")
    return manifest


def installer_paths(prefix: Path) -> dict[str, Path]:
    return {
        "active": prefix / "lib/vellum",
        "cache": prefix / "lib/vellum-cache",
        "installs": prefix / "lib/vellum-installs",
        "launcher": prefix / "bin/vellum",
        "state": prefix / "lib/vellum-installer-state.json",
    }


def validate_prefix(prefix: Path) -> Path:
    resolved = prefix.expanduser().resolve()
    if (
        str(resolved) in {"/", ".", ""}
        or resolved == Path.home().resolve()
        or len(resolved.parts) < 3
    ):
        raise InstallError(f"refusing unsafe install prefix: {prefix}")
    for managed_path in (
        resolved / "bin",
        resolved / "lib",
        resolved / "lib/vellum-cache",
        resolved / "lib/vellum-installs",
    ):
        if managed_path.is_symlink():
            raise InstallError(
                f"refusing symlinked installer storage: {managed_path}"
            )
    return resolved


def managed_launcher(path: Path) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.read_text(encoding="utf-8") == prefix_launcher()
        )
    except OSError:
        return False


def activation_presence(paths: dict[str, Path]) -> tuple[bool, bool, bool]:
    active = paths["active"]
    launcher = paths["launcher"]
    state = paths["state"]
    return (
        active.is_symlink() or active.exists(),
        launcher.is_symlink() or launcher.exists(),
        state.is_symlink() or state.exists(),
    )


def verify_installer_state(
    state_path: Path,
    *,
    active_root: Path,
    install_id: str,
    launcher: Path,
) -> dict[str, object]:
    if state_path.is_symlink() or not state_path.is_file():
        raise InstallError("Vellum installer state is missing or unsafe")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError("Vellum installer state is invalid") from error
    if (
        not isinstance(state, dict)
        or set(state) != {
            "schema",
            "active_install_id",
            "active_install",
            "launcher_sha256",
        }
        or state.get("schema") != STATE_SCHEMA
        or state.get("active_install_id") != install_id
        or state.get("active_install") != str(active_root)
        or state.get("launcher_sha256") != sha256_file(launcher)
    ):
        raise InstallError("Vellum installer state does not match the active SDK")
    return state


def restore_file(path: Path, content: bytes | None, mode: int | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
    temporary.write_bytes(content)
    temporary.chmod(mode if mode is not None else 0o644)
    os.replace(temporary, path)


def restore_active_link(active: Path, previous_target: str | None) -> None:
    if previous_target is None:
        active.unlink(missing_ok=True)
        return
    temporary = active.with_name(f".vellum-active-{uuid.uuid4().hex}.rollback")
    temporary.symlink_to(previous_target)
    os.replace(temporary, active)


def activate(prefix: Path, install_root: Path, install_id: str) -> None:
    paths = installer_paths(prefix)
    active = paths["active"]
    presence = activation_presence(paths)
    if any(presence):
        if not all(presence):
            raise InstallError(
                "refusing to replace incomplete or unmanaged installer state"
            )
        verify_installed(prefix)
    if active.exists() and not active.is_symlink():
        raise InstallError(
            f"legacy mutable SDK exists at {active}; move it aside before transactional install"
        )
    launcher = paths["launcher"]
    if launcher.exists() and not managed_launcher(launcher):
        raise InstallError(f"refusing to replace unmanaged launcher: {launcher}")
    state_path = paths["state"]
    if state_path.exists() and not state_path.is_file():
        raise InstallError(f"refusing to replace invalid installer state: {state_path}")
    previous_target = os.readlink(active) if active.is_symlink() else None
    previous_launcher = launcher.read_bytes() if launcher.is_file() else None
    previous_launcher_mode = stat.S_IMODE(launcher.stat().st_mode) if launcher.is_file() else None
    previous_state = state_path.read_bytes() if state_path.is_file() else None
    previous_state_mode = stat.S_IMODE(state_path.stat().st_mode) if state_path.is_file() else None
    relative_target = Path("vellum-installs") / install_id
    active.parent.mkdir(parents=True, exist_ok=True)
    temporary_link = active.with_name(f".vellum-active-{uuid.uuid4().hex}")
    temporary_link.symlink_to(relative_target)
    state = {
        "schema": STATE_SCHEMA,
        "active_install_id": install_id,
        "active_install": str(install_root),
        "launcher_sha256": None,
    }
    try:
        write_text_atomic(launcher, prefix_launcher(), 0o755)
        state["launcher_sha256"] = sha256_file(launcher)
        os.replace(temporary_link, active)
        write_text_atomic(
            state_path,
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        if os.environ.get("VELLUM_INSTALL_FAIL_AFTER_ACTIVATE") == "1":
            raise InstallError("injected failure after activation")
        verify_installed(prefix)
    except Exception:
        temporary_link.unlink(missing_ok=True)
        restore_active_link(active, previous_target)
        restore_file(launcher, previous_launcher, previous_launcher_mode)
        restore_file(state_path, previous_state, previous_state_mode)
        raise


def cache_archive(prefix: Path, archive: Path, digest: str) -> Path:
    cache_root = installer_paths(prefix)["cache"]
    digest_root = cache_root / digest
    if digest_root.is_symlink():
        raise InstallError(f"verified archive cache path is a symlink: {digest_root}")
    digest_root.mkdir(parents=True, exist_ok=True)
    if digest_root.is_symlink() or not digest_root.is_dir():
        raise InstallError(f"verified archive cache path is unsafe: {digest_root}")
    destination = digest_root / archive.name
    if destination.is_symlink():
        raise InstallError(f"verified archive cache entry is a symlink: {destination}")
    if destination.is_file():
        if sha256_file(destination) != digest:
            raise InstallError(f"verified archive cache is corrupt: {destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(archive, temporary)
    if sha256_file(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise InstallError("verified archive changed while entering cache")
    os.replace(temporary, destination)
    return destination


def install(
    archive: Path,
    checksums: Path,
    prefix: Path,
    *,
    expected_version: str | None = None,
    expected_target: str | None = None,
) -> dict[str, object]:
    prefix = validate_prefix(prefix)
    expected = exact_checksum(archive, checksums)
    actual = sha256_file(archive)
    if actual != expected:
        raise InstallError(f"SHA-256 mismatch for {archive.name}; refusing to extract")
    cached = cache_archive(prefix, archive, actual)
    metadata, _ = inspect_archive(cached)
    if expected_version is not None and metadata["framework_version"] != expected_version:
        raise InstallError(
            "release asset framework version does not match the requested version"
        )
    if expected_target is not None and metadata["target"] != expected_target:
        raise InstallError(
            "release asset target does not match the requested target"
        )
    install_id = (
        f"{metadata['framework_version']}-{metadata['target']}-"
        f"{actual}"
    )
    paths = installer_paths(prefix)
    final = paths["installs"] / install_id
    if final.is_symlink():
        raise InstallError(f"immutable install path is an unsafe symlink: {final}")
    if final.exists():
        receipt = verify_install_root(final)
        manifest = load_install_manifest(final)
        if (
            manifest.get("artifact") != archive.name
            or manifest.get("artifact_sha256") != actual
            or receipt["install_id"] != install_id
        ):
            raise InstallError("existing immutable install has the wrong artifact identity")
        status = "already_installed"
    else:
        paths["installs"].mkdir(parents=True, exist_ok=True)
        staging = paths["installs"] / f".staging-{install_id}-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            extracted_metadata = extract_archive(cached, staging, actual)
            prepare_install_root(staging, extracted_metadata, archive, actual)
            probe = subprocess.run(
                [str(staging / "bin/vellum"), "--version"],
                text=True,
                capture_output=True,
                check=False,
                env=artifact_probe_environment(VELLUM_SDK_ROOT=str(staging)),
            )
            if probe.returncode != 0:
                raise InstallError(
                    "staged Vellum CLI self-test failed: "
                    + (probe.stderr.strip() or probe.stdout.strip())
                )
            write_ownership(staging, install_id)
            verify_install_root(staging, expected_install_id=install_id)
            os.replace(staging, final)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        status = "installed"
    if os.environ.get("VELLUM_INSTALL_FAIL_BEFORE_ACTIVATE") == "1":
        raise InstallError("injected failure before activation")
    activate(prefix, final, install_id)
    return {
        "schema": "vellum.install-result.v1",
        "status": status,
        "artifact_sha256": actual,
        "install_id": install_id,
        "active": str(final),
    }


def verify_installed(prefix: Path) -> dict[str, object]:
    prefix = validate_prefix(prefix)
    paths = installer_paths(prefix)
    active = paths["active"]
    if not active.is_symlink():
        raise InstallError(f"Vellum has no transactional active install at {active}")
    resolved = active.resolve()
    try:
        resolved.relative_to(paths["installs"].resolve())
    except ValueError as error:
        raise InstallError("active Vellum pointer escapes immutable install storage") from error
    if resolved.parent != paths["installs"].resolve():
        raise InstallError("active Vellum pointer is not a direct immutable install")
    receipt = verify_install_root(resolved)
    load_install_manifest(resolved)
    launcher = paths["launcher"]
    if not managed_launcher(launcher):
        raise InstallError("active Vellum launcher is missing or unmanaged")
    verify_installer_state(
        paths["state"],
        active_root=resolved,
        install_id=receipt["install_id"],
        launcher=launcher,
    )
    completed = subprocess.run(
        [str(launcher), "--version"],
        text=True,
        capture_output=True,
        check=False,
        env=artifact_probe_environment(),
    )
    if completed.returncode != 0:
        raise InstallError("active Vellum CLI self-test failed")
    return {
        "schema": "vellum.install-verification.v1",
        "status": "verified",
        "install_id": receipt["install_id"],
        "active": str(resolved),
    }


def remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def uninstall(prefix: Path) -> dict[str, object]:
    prefix = validate_prefix(prefix)
    paths = installer_paths(prefix)
    removed: list[str] = []
    active = paths["active"]
    launcher = paths["launcher"]
    if launcher.exists():
        if not managed_launcher(launcher):
            raise InstallError(f"refusing to remove unmanaged launcher: {launcher}")
    elif launcher.is_symlink():
        raise InstallError(f"refusing to remove unsafe launcher symlink: {launcher}")
    verified_installs: list[
        tuple[Path, dict[str, object], dict[str, object]]
    ] = []
    installs = paths["installs"]
    if installs.is_dir():
        for install_root in sorted(installs.iterdir()):
            if install_root.is_symlink():
                raise InstallError(
                    f"refusing to follow install-root symlink: {install_root}"
                )
            if not install_root.is_dir() or install_root.name.startswith(".staging-"):
                continue
            receipt_path = install_root / ".vellum-install-ownership.json"
            if not receipt_path.is_file():
                if any(install_root.iterdir()):
                    raise InstallError(
                        f"refusing to remove install without ownership receipt: {install_root}"
                    )
                continue
            receipt = verify_install_root(install_root)
            verified_installs.append(
                (install_root, receipt, load_install_manifest(install_root))
            )
    presence = activation_presence(paths)
    if any(presence):
        if not all(presence):
            raise InstallError(
                "refusing to remove incomplete or unmanaged installer state"
            )
        verify_installed(prefix)
    if active.is_symlink():
        active.unlink()
        removed.append(str(active))
    elif active.exists():
        raise InstallError(f"refusing to remove non-transactional SDK: {active}")
    if launcher.exists():
        launcher.unlink()
        removed.append(str(launcher))
    if all(presence):
        paths["state"].unlink()
    if installs.is_dir():
        for install_root, receipt, _manifest in verified_installs:
            receipt_path = install_root / ".vellum-install-ownership.json"
            for row in reversed(receipt["files"]):
                path = install_root / row["path"]
                path.unlink()
            receipt_path.unlink()
            for relative in sorted(
                receipt["directories"],
                key=lambda row: len(PurePosixPath(row["path"]).parts),
                reverse=True,
            ):
                install_root.joinpath(
                    *PurePosixPath(relative["path"]).parts
                ).rmdir()
            install_root.rmdir()
            removed.append(str(install_root))
        remove_empty_parents(installs, prefix / "lib")
    cache = paths["cache"]
    owned_cache_entries = {
        (manifest["artifact_sha256"], manifest["artifact"])
        for _root, _receipt, manifest in verified_installs
    }
    if cache.is_dir():
        for digest_dir in sorted(cache.iterdir()):
            if (
                digest_dir.is_symlink()
                or not digest_dir.is_dir()
                or not SHA256.fullmatch(digest_dir.name)
            ):
                continue
            for archive in sorted(digest_dir.iterdir()):
                if (
                    archive.is_file()
                    and not archive.is_symlink()
                    and (digest_dir.name, archive.name) in owned_cache_entries
                    and sha256_file(archive) == digest_dir.name
                ):
                    archive.unlink()
            remove_empty_parents(digest_dir, cache)
        remove_empty_parents(cache, prefix / "lib")
    remove_empty_parents(prefix / "bin", prefix)
    remove_empty_parents(prefix / "lib", prefix)
    return {
        "schema": "vellum.uninstall-result.v1",
        "status": "uninstalled" if removed else "already_absent",
        "removed": removed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--archive", type=Path, required=True)
    install_parser.add_argument("--checksums", type=Path, required=True)
    install_parser.add_argument("--prefix", type=Path, required=True)
    install_parser.add_argument("--expected-version")
    install_parser.add_argument("--expected-target")
    for name in ("verify-installed", "uninstall"):
        child = subparsers.add_parser(name)
        child.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        prefix = validate_prefix(args.prefix)
        with prefix_lock(prefix):
            if (prefix / ".vellum-local-installing").exists():
                raise InstallError(
                    "a local-development install is using this prefix"
                )
            if args.command == "install":
                result = install(
                    args.archive.resolve(),
                    args.checksums.resolve(),
                    prefix,
                    expected_version=args.expected_version,
                    expected_target=args.expected_target,
                )
            elif args.command == "verify-installed":
                result = verify_installed(prefix)
            else:
                result = uninstall(prefix)
    except (InstallError, OSError, json.JSONDecodeError) as error:
        print(f"vellum-install: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Vellum installer: {result['status']}")
        if "active" in result:
            print(f"Active SDK: {result['active']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
