#!/usr/bin/env python3
"""Installed Vellum backend dispatcher and compatibility handshake."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any
import unicodedata
import zipfile


RESULT_SCHEMA = "vellum.backend.result.v1"
SDK_METADATA_SCHEMA = "vellum.sdk-artifact.v1"
IMPORT_COMMANDS = {"import", "reimport"}
DESIGN_COMMANDS = {"design-check", "design-diff"}
NATIVE_COMMANDS = {"build", "run", "test", "capture", "package"}
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_SCENE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_PATH_BYTES = 1024
READ_CHUNK_BYTES = 1024 * 1024
DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
ALLOWED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class ArchiveFailure(ValueError):
    def __init__(self, message: str, *, status: str = "invalid_source_archive"):
        super().__init__(message)
        self.status = status


def emit_failure(status: str, message: str, *, exit_code: int) -> int:
    print(json.dumps({
        "data": {},
        "diagnostics": [],
        "message": message,
        "ok": False,
        "schema": RESULT_SCHEMA,
        "status": status,
    }, sort_keys=True, separators=(",", ":")))
    return exit_code


def consume_option(arguments: list[str], name: str) -> str | None:
    indexes = [index for index, value in enumerate(arguments) if value == name]
    if not indexes:
        return None
    if len(indexes) != 1:
        raise ValueError(f"{name} must be provided exactly once")
    index = indexes[0]
    if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
        raise ValueError(f"{name} requires a value")
    value = arguments[index + 1]
    del arguments[index:index + 2]
    return value


def sdk_root() -> Path:
    configured = os.environ.get("VELLUM_SDK_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent


def load_metadata(root: Path) -> dict[str, Any]:
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != SDK_METADATA_SCHEMA:
        raise ValueError("installed SDK metadata has an unsupported schema")
    commands = metadata.get("capabilities", {}).get("commands")
    if not isinstance(commands, dict):
        raise ValueError("installed SDK metadata has no command capability map")
    return metadata


def backend_path(root: Path, command: str, arguments: list[str]) -> Path:
    bin_dir = root / "bin"
    if command in IMPORT_COMMANDS | DESIGN_COMMANDS:
        names = ("vellum-import-backend.cmd",) if os.name == "nt" else ("vellum-import-backend",)
    else:
        target = option_value(arguments, "--target") or "macos"
        if target == "web":
            names = ("vellum-web-backend.exe", "vellum-web-backend.cmd") if os.name == "nt" else ("vellum-web-backend",)
        elif target == "macos":
            names = ("vellum-native-backend.exe", "vellum-native-backend.cmd") if os.name == "nt" else ("vellum-native-backend",)
        else:
            raise ValueError(f"unsupported application target: {target}")
    for name in names:
        candidate = bin_dir / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"installed backend executable is missing for '{command}'")


def source_argument(arguments: list[str], command: str) -> tuple[int, Path] | None:
    if command == "reimport":
        indexes = [index for index, value in enumerate(arguments) if value == "--source"]
        if not indexes:
            return None
        if len(indexes) != 1 or indexes[0] + 1 >= len(arguments):
            raise ArchiveFailure("--source must be provided exactly once")
        return indexes[0] + 1, Path(arguments[indexes[0] + 1])

    options_with_values = {
        "--project", "--source-type", "--as", "--source-key",
    }
    index = 0
    positional: list[tuple[int, str]] = []
    while index < len(arguments):
        value = arguments[index]
        if value in options_with_values:
            if index + 1 >= len(arguments):
                raise ArchiveFailure(f"{value} requires a value")
            index += 2
            continue
        if value == "--json":
            index += 1
            continue
        if value.startswith("--"):
            # Unknown options belong to the Node backend. Do not guess whether
            # they consume the following argument; archive staging only needs
            # the stable public import grammar above.
            index += 1
            continue
        positional.append((index, value))
        index += 1
    if not positional:
        return None
    if len(positional) != 1:
        raise ArchiveFailure("import must contain exactly one positional source")
    return positional[0][0], Path(positional[0][1])


def option_value(arguments: list[str], name: str) -> str | None:
    indexes = [index for index, value in enumerate(arguments) if value == name]
    if not indexes:
        return None
    if len(indexes) != 1 or indexes[0] + 1 >= len(arguments):
        raise ArchiveFailure(f"{name} must be provided exactly once with a value")
    return arguments[indexes[0] + 1]


def is_pulp_zip_candidate(path: Path) -> bool:
    if path.name.lower().endswith(".pulp.zip"):
        return True
    if path.suffix.lower() != ".zip":
        return False
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return False
        descriptor = os.open(path, flags)
    except OSError:
        return False
    try:
        return os.read(descriptor, 4)[:2] == b"PK"
    finally:
        os.close(descriptor)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read_stable_archive(path: Path) -> tuple[bytes, tuple[int, int, int, int, int]]:
    try:
        before = path.lstat()
    except OSError as error:
        raise ArchiveFailure(f"Cannot inspect source archive: {error}") from error
    if not stat.S_ISREG(before.st_mode):
        raise ArchiveFailure("Pulp source archive must be a regular file")
    if before.st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveFailure(
            f"Pulp source archive exceeds {MAX_ARCHIVE_BYTES} bytes",
            status="source_archive_too_large",
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArchiveFailure(f"Cannot open source archive safely: {error}") from error
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode) or _stat_identity(opened_before) != _stat_identity(before):
            raise ArchiveFailure("Pulp source archive changed before it could be read", status="source_archive_mutated")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ArchiveFailure(
                    f"Pulp source archive exceeds {MAX_ARCHIVE_BYTES} bytes",
                    status="source_archive_too_large",
                )
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as error:
        raise ArchiveFailure("Pulp source archive disappeared while it was read", status="source_archive_mutated") from error
    identity = _stat_identity(before)
    if _stat_identity(opened_after) != identity or _stat_identity(after) != identity:
        raise ArchiveFailure("Pulp source archive changed while it was read", status="source_archive_mutated")
    return b"".join(chunks), identity


def safe_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if info.orig_filename != name or "\0" in info.orig_filename:
        raise ArchiveFailure("Pulp archive member contains a NUL byte")
    if (
        not name or len(name.encode("utf-8")) > MAX_MEMBER_PATH_BYTES or
        any(ord(character) < 32 or ord(character) == 127 for character in name) or
        "\\" in name or name.startswith("/") or DRIVE_PATH_RE.match(name)
    ):
        raise ArchiveFailure(f"Unsafe Pulp archive member path: {name!r}")
    normalized = unicodedata.normalize("NFC", name)
    if normalized != name:
        raise ArchiveFailure(f"Pulp archive member path is not NFC-normalized: {name!r}")
    path = PurePosixPath(name.rstrip("/"))
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveFailure(f"Unsafe Pulp archive member path: {name!r}")
    for part in path.parts:
        device = part.split(".", 1)[0].casefold()
        if ":" in part or part.endswith((" ", ".")) or device in WINDOWS_RESERVED_NAMES:
            raise ArchiveFailure(f"Unsafe cross-platform Pulp archive member path: {name!r}")
    if path.as_posix() != name.rstrip("/"):
        raise ArchiveFailure(f"Non-canonical Pulp archive member path: {name!r}")
    return path


def validate_member_type(info: zipfile.ZipInfo) -> None:
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if info.is_dir():
        if file_type not in {0, stat.S_IFDIR}:
            raise ArchiveFailure(f"Pulp archive directory has a special file type: {info.filename!r}")
    elif file_type not in {0, stat.S_IFREG}:
        raise ArchiveFailure(f"Pulp archive member is a link or special file: {info.filename!r}")


def validated_members(archive: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, PurePosixPath]], PurePosixPath]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ArchiveFailure(
            f"Pulp archive contains more than {MAX_ARCHIVE_MEMBERS} members",
            status="source_archive_too_large",
        )
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    collision_keys: set[str] = set()
    file_keys: set[str] = set()
    directory_keys: set[str] = set()
    implied_directory_keys: set[str] = set()
    declared_total = 0
    scenes: list[PurePosixPath] = []
    for info in infos:
        path = safe_member_path(info)
        key = path.as_posix().casefold()
        if key in collision_keys:
            raise ArchiveFailure(f"Pulp archive contains duplicate or case-colliding member: {info.filename!r}")
        collision_keys.add(key)
        validate_member_type(info)
        if info.flag_bits & 0x1:
            raise ArchiveFailure(f"Encrypted Pulp archive member is unsupported: {info.filename!r}")
        if not info.is_dir() and info.compress_type not in ALLOWED_ZIP_COMPRESSION:
            raise ArchiveFailure(f"Unsupported Pulp archive compression method: {info.compress_type}")
        if info.file_size < 0 or info.compress_size < 0 or info.file_size > MAX_MEMBER_BYTES:
            raise ArchiveFailure(
                f"Pulp archive member exceeds {MAX_MEMBER_BYTES} bytes: {info.filename!r}",
                status="source_archive_too_large",
            )
        if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
            raise ArchiveFailure(
                f"Pulp archive member exceeds compression ratio limit: {info.filename!r}",
                status="source_archive_too_large",
            )
        declared_total += info.file_size
        if declared_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ArchiveFailure(
                f"Pulp archive exceeds {MAX_TOTAL_UNCOMPRESSED_BYTES} uncompressed bytes",
                status="source_archive_too_large",
            )
        parent_keys = [PurePosixPath(*path.parts[:index]).as_posix().casefold()
                       for index in range(1, len(path.parts))]
        if any(parent in file_keys for parent in parent_keys):
            raise ArchiveFailure(f"Pulp archive file shadows a parent directory: {info.filename!r}")
        implied_directory_keys.update(parent_keys)
        if info.is_dir():
            if key in file_keys:
                raise ArchiveFailure(f"Pulp archive member collides as file and directory: {info.filename!r}")
            directory_keys.add(key)
        else:
            if key in directory_keys or key in implied_directory_keys:
                raise ArchiveFailure(f"Pulp archive member collides as file and directory: {info.filename!r}")
            file_keys.add(key)
            if path.name.lower().endswith(".pulp.json"):
                scenes.append(path)
        members.append((info, path))
    if len(scenes) != 1:
        raise ArchiveFailure(
            f"Pulp archive must contain exactly one .pulp.json scene; found {len(scenes)}"
        )
    scene_info = next(info for info, path in members if path == scenes[0])
    if scene_info.file_size > MAX_SCENE_BYTES:
        raise ArchiveFailure(
            f"Pulp scene JSON exceeds {MAX_SCENE_BYTES} bytes",
            status="source_archive_too_large",
        )
    return members, scenes[0]


@contextmanager
def stage_pulp_archive(path: Path):
    archive_bytes, _ = read_stable_archive(path)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes), "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise ArchiveFailure(f"Pulp source archive is not a valid ZIP: {error}") from error
    with archive, tempfile.TemporaryDirectory(prefix="vellum-pulp-zip-") as temporary:
        members, scene_member = validated_members(archive)
        root = Path(temporary)
        content = root / "content"
        content.mkdir()
        actual_total = 0
        for info, relative in members:
            output = content.joinpath(*relative.parts)
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            member_total = 0
            try:
                with archive.open(info, "r") as source, output.open("xb") as destination:
                    while True:
                        chunk = source.read(READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        member_total += len(chunk)
                        actual_total += len(chunk)
                        if member_total > MAX_MEMBER_BYTES or actual_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                            raise ArchiveFailure(
                                "Pulp archive exceeded its extraction byte limit",
                                status="source_archive_too_large",
                            )
                        destination.write(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise ArchiveFailure(f"Could not extract Pulp archive member {info.filename!r}: {error}") from error
            if member_total != info.file_size:
                raise ArchiveFailure(f"Pulp archive member size changed during extraction: {info.filename!r}")
        staged_archive = root / "source.pulp.zip"
        staged_archive.write_bytes(archive_bytes)
        yield {
            "archive": staged_archive,
            "archive_name": path.name,
            "archive_sha256": f"sha256:{digest}",
            "scene": content.joinpath(*scene_member.parts),
            "scene_member": scene_member.as_posix(),
        }


def invoke_backend_with_archive_staging(
    backend: Path, command: str, forwarded: list[str]
) -> subprocess.CompletedProcess[Any]:
    if command not in IMPORT_COMMANDS:
        return subprocess.run([str(backend), command, *forwarded], check=False)
    source = source_argument(forwarded, command)
    if source is None or not is_pulp_zip_candidate(source[1]):
        return subprocess.run([str(backend), command, *forwarded], check=False)
    if command == "import" and (option_value(forwarded, "--source-type") or "figma") != "figma":
        raise ArchiveFailure("Pulp ZIP sources require --source-type figma")
    with stage_pulp_archive(source[1]) as staged:
        staged_arguments = list(forwarded)
        staged_arguments[source[0]] = str(staged["scene"])
        staged_arguments.extend([
            "--source-archive", str(staged["archive"]),
            "--source-archive-sha256", str(staged["archive_sha256"]),
            "--source-archive-name", str(staged["archive_name"]),
            "--source-archive-member", str(staged["scene_member"]),
        ])
        return subprocess.run([str(backend), command, *staged_arguments], check=False)


def main() -> int:
    if len(sys.argv) < 2:
        return emit_failure("invalid_arguments", "backend command is required", exit_code=2)
    command = sys.argv[1]
    if command not in IMPORT_COMMANDS | DESIGN_COMMANDS | NATIVE_COMMANDS:
        return emit_failure("unsupported_command", f"vellum-backend does not implement '{command}'", exit_code=2)
    forwarded = sys.argv[2:]
    try:
        framework_version = consume_option(forwarded, "--framework-version")
        cli_api_text = consume_option(forwarded, "--cli-api")
        if framework_version is None or cli_api_text is None:
            raise ValueError("--framework-version and --cli-api are required")
        cli_api = int(cli_api_text)
        root = sdk_root()
        metadata = load_metadata(root)
        if metadata.get("framework_version") != framework_version or metadata.get("cli_api") != cli_api:
            return emit_failure(
                "backend_handshake_mismatch",
                "project/CLI compatibility handshake does not match the installed SDK",
                exit_code=3,
            )
        target = option_value(forwarded, "--target") if command in NATIVE_COMMANDS else None
        capability = "import" if command in DESIGN_COMMANDS else command
        available = metadata["capabilities"]["commands"].get(capability)
        if target is not None:
            available = metadata["capabilities"].get("targets", {}).get(target, {}).get(
                "commands", {}
            ).get(command)
        if available is not True:
            return emit_failure(
                "capability_unavailable",
                f"The installed SDK does not yet provide the '{command}' capability",
                exit_code=4,
            )
        backend = backend_path(root, command, forwarded)
        completed = invoke_backend_with_archive_staging(backend, command, forwarded)
        return completed.returncode
    except ArchiveFailure as error:
        return emit_failure(error.status, str(error), exit_code=5)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return emit_failure("backend_dispatch_error", str(error), exit_code=5)


if __name__ == "__main__":
    raise SystemExit(main())
