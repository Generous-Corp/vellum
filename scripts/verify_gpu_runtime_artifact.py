#!/usr/bin/env python3
"""Verify a vellum-gpu-runtime archive before it is published or installed.

The archive's own record (gpu-runtime-artifact.json) is checked, but never
trusted to say what belongs in the archive:

  * the tar stream is exactly the canonical stream the builder writes for the
    files it holds (normalized headers, sorted order, plain files and
    directories only), so no tar reader can see anything the verifier did not;
  * member names are relative and normalized, never collide after case folding
    and Unicode normalization (as they would on APFS), and stay inside the
    package's directories;
  * the file set is the package's fixed set: the three gpu-runtime libraries,
    libdawn_combined.a, the CMake package files, the license and notice, the
    record, and only gpu_runtime headers;
  * every file matches the record's digest, size and executable bit, and the
    record names this archive's version and target;
  * the shipped libdawn_combined.a is the library the shipped lock names, and
    the package's Targets files import only the shipped gpu-runtime libraries.

With --repo (and --renderer-archive) the archive is held to that checkout
instead of to itself: the file set is derived from the source; the lock,
headers, license, verbatim CMake modules and the assembled notice must be
byte-identical to what the source produces; the four generated package files
(config, version and both exports, all of which run at a consumer's configure
time) must equal what configuring the source with the builder's own configure
command generates on this host; and the record's source commit and version
must be the checkout's HEAD and version.

Without --repo the result is a consistency check of the archive, not a trust
decision: it says so on stderr and records checked_against_source false. A
publishing gate must use --repo at the exact source commit.

With --consumer it then extracts the archive and builds and runs a consumer
that finds the package by exact version and links Device against the Dawn
library the archive ships, with nothing else supplied: the audio-only journey.

Exit 0 when every check passes, 1 with the first failure otherwise.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata

METADATA_SCHEMA = "vellum.gpu-runtime-artifact.v1"
METADATA_NAME = "gpu-runtime-artifact.json"
PACKAGE_DIR = "lib/cmake/VellumGpuRuntime"
LOCK_MEMBER = f"{PACKAGE_DIR}/renderer-lock.json"
NAME = re.compile(r"^vellum-gpu-runtime-([0-9]+\.[0-9]+\.[0-9]+)-(darwin-arm64)\.tar\.gz$")
HEADER = re.compile(r"^include/gpu_runtime/(?:[a-z0-9_]+/)*[a-z0-9_]+\.hpp$")
DIRECTORY = re.compile(r"^(?:include|include/gpu_runtime(?:/[a-z0-9_]+)*|lib|lib/cmake|"
                       r"lib/cmake/VellumGpuRuntime|share|share/doc|share/doc/VellumGpuRuntime)$")
LIBRARIES = (
    "lib/libgpu-runtime-bootstrap.a",
    "lib/libgpu-runtime-device.a",
    "lib/libgpu-runtime-skia-log-bridge.a",
)
DAWN_MEMBER = "lib/libdawn_combined.a"
NOTICE_MEMBER = "share/doc/VellumGpuRuntime/NOTICE.md"
LICENSE_MEMBER = "share/doc/VellumGpuRuntime/LICENSE.md"
VERBATIM_MODULES = {
    f"{PACKAGE_DIR}/GpuRuntimeDawn.cmake": "gpu-runtime/cmake/GpuRuntimeDawn.cmake",
    f"{PACKAGE_DIR}/GpuRuntimeRendererLock.cmake": "gpu-runtime/cmake/GpuRuntimeRendererLock.cmake",
    LOCK_MEMBER: "gpu-runtime/renderer-lock.json",
}
GENERATED = (
    f"{PACKAGE_DIR}/VellumGpuRuntimeConfig.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeConfigVersion.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeTargets-release.cmake",
    f"{PACKAGE_DIR}/VellumGpuRuntimeTargets.cmake",
)
FIXED_FILES = frozenset({*LIBRARIES, DAWN_MEMBER, NOTICE_MEMBER, LICENSE_MEMBER,
                         *VERBATIM_MODULES, *GENERATED, METADATA_NAME})
NOTICE_HEADINGS = ("### Dawn and Tint", "### Skia and skcms")


class VerificationError(RuntimeError):
    pass


# What the package's generated export files may contain. They run at every
# consumer's configure time, so even without a checkout to regenerate them
# from, they are held to the standard export template: only these commands,
# exactly these targets with exactly these interface values, every path under
# the package, and no other include, file operation or process.
TARGET_LIBRARIES = {
    "VellumGpuRuntime::Bootstrap": "lib/libgpu-runtime-bootstrap.a",
    "VellumGpuRuntime::Device": "lib/libgpu-runtime-device.a",
    "VellumGpuRuntime::SkiaLogBridge": "lib/libgpu-runtime-skia-log-bridge.a",
}
TARGET_KINDS = {
    "VellumGpuRuntime::DeviceApi": "INTERFACE",
    **{name: "STATIC" for name in TARGET_LIBRARIES},
}
TARGET_INTERFACES = {
    "VellumGpuRuntime::DeviceApi": {
        "INTERFACE_COMPILE_FEATURES": "cxx_std_20",
        "INTERFACE_INCLUDE_DIRECTORIES": "${_IMPORT_PREFIX}/include",
    },
    "VellumGpuRuntime::Bootstrap": {"INTERFACE_LINK_LIBRARIES": "VellumGpuRuntime::DeviceApi"},
    "VellumGpuRuntime::Device": {
        "INTERFACE_LINK_LIBRARIES": "VellumGpuRuntime::DeviceApi;VellumGpuRuntime::Bootstrap;"
                                    "\\$<LINK_ONLY:>;VellumGpuRuntime::Dawn;-framework Cocoa;"
                                    "-framework Metal;-framework QuartzCore",
    },
    "VellumGpuRuntime::SkiaLogBridge": {"INTERFACE_LINK_LIBRARIES": "VellumGpuRuntime::Device"},
}
EXPORT_COMMANDS = frozenset({
    "if", "elseif", "else", "endif", "foreach", "endforeach", "message", "cmake_policy",
    "set", "unset", "list", "return", "string", "get_filename_component", "add_library",
    "set_target_properties", "set_property", "file", "include",
})
# The only variables an export file writes: its own scratch variables.
EXPORT_VARIABLE = re.compile(r"^(?:_cmake_[A-Za-z0-9_]+|_cmake_import_check_files_for_\$\{_cmake_target\}"
                             r"|_IMPORT_PREFIX|CMAKE_IMPORT_FILE_VERSION)$")
ARGUMENT = re.compile(r'"((?:\\.|[^"\\])*)"|([^\s"]+)')


def cmake_commands(text: str) -> list[tuple[str, str]]:
    """(name, raw arguments) for each command in a generated CMake file."""
    commands: list[tuple[str, str]] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character == "#":
            # A bracket comment can span lines and hide commands from a line
            # scan; the export template never writes one.
            require(not re.match(r"#\[=*\[", text[index:]),
                    "a generated export file contains a bracket comment")
            index = text.find("\n", index)
            index = length if index == -1 else index
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", text[index:])
        require(match is not None, f"unexpected text in a generated export file: {text[index:index + 40]!r}")
        name = match.group(1).lower()
        position = index + match.end()
        depth, quoted, start = 1, False, position
        while depth:
            require(position < length, "unterminated command in a generated export file")
            current = text[position]
            if quoted:
                if current == "\\":
                    position += 1
                elif current == '"':
                    quoted = False
            elif current == '"':
                quoted = True
            elif current in "#\\" or text.startswith(("[[", "[="), position):
                # CMake reads these differently from this scan (a line comment,
                # an escaped character, a bracket argument), so a command could
                # hide inside what looks like another's arguments. The export
                # template only ever uses them inside quoted text.
                require(False, "a generated export file uses a comment, escape or bracket "
                               "argument inside a command's arguments")
            elif current == "(":
                depth += 1
            elif current == ")":
                depth -= 1
            position += 1
        commands.append((name, text[start:position - 1]))
        index = position
    return commands


def arguments(raw: str) -> list[str]:
    return [quoted if quoted or bare == "" else bare
            for quoted, bare in ((m.group(1) or "", m.group(2) or "") for m in ARGUMENT.finditer(raw))
            if quoted or bare] if raw.strip() else []


def check_export_paths(name: str, raw: str, member: str) -> None:
    """Every path a command names must be inside the package (message text and
    the template's root-prefix comparison aside)."""
    if name == "message":
        return
    for value in arguments(raw):
        for part in value.split(";"):
            if part == "/" or part.startswith(("-framework", "\\$<")) or "/" not in part:
                continue
            require(part.startswith(("${_IMPORT_PREFIX}/", "${CMAKE_CURRENT_LIST_DIR}/")),
                    f"{member} names a path outside the package: {part}")


def check_targets_file(text: str, member: str) -> None:
    defined: dict[str, str] = {}
    properties: dict[str, dict[str, str]] = {}
    for name, raw in cmake_commands(text):
        require(name in EXPORT_COMMANDS, f"{member} runs {name}(), which an export file never does")
        check_export_paths(name, raw, member)
        values = arguments(raw)
        written = {"set": 0, "unset": 0, "get_filename_component": 0, "list": 1, "string": 3,
                   "foreach": 0}.get(name)
        if written is not None:
            require(len(values) > written and EXPORT_VARIABLE.fullmatch(values[written]),
                    f"{member} writes a variable outside its own scratch space: {values[:written + 1]}")
        if name == "cmake_policy":
            require(values in (["PUSH"], ["POP"]) or (len(values) == 2 and values[0] == "VERSION"),
                    f"{member} changes policies beyond the template")
        if name == "file":
            require(values == ["GLOB", "_cmake_config_files",
                               "${CMAKE_CURRENT_LIST_DIR}/VellumGpuRuntimeTargets-*.cmake"],
                    f"{member} has an unexpected file() call")
        elif name == "include":
            require(values == ["${_cmake_config_file}"], f"{member} includes something else")
        elif name == "add_library":
            require(len(values) == 3 and values[2] == "IMPORTED" and values[0] in TARGET_KINDS
                    and values[1] == TARGET_KINDS[values[0]] and values[0] not in defined,
                    f"{member} defines an unexpected target: {values}")
            defined[values[0]] = values[1]
        elif name == "set_target_properties":
            require(len(values) >= 4 and values[0] in TARGET_KINDS and values[1] == "PROPERTIES"
                    and len(values) % 2 == 0, f"{member} sets unexpected properties: {values[:2]}")
            properties.setdefault(values[0], {}).update(zip(values[2::2], values[3::2]))
        elif name == "set_property":
            require(False, f"{member} sets a property outside the export template")
    require(defined == TARGET_KINDS, f"{member} defines {sorted(defined)}, not the package's targets")
    require(properties == TARGET_INTERFACES,
            f"{member} gives the targets other interfaces than the package declares")


def check_targets_release_file(text: str, member: str) -> None:
    located: dict[str, str] = {}
    for name, raw in cmake_commands(text):
        check_export_paths(name, raw, member)
        values = arguments(raw)
        if name == "set":
            require(values in (["CMAKE_IMPORT_FILE_VERSION", "1"], ["CMAKE_IMPORT_FILE_VERSION"]),
                    f"{member} sets something other than the import file version")
        elif name == "set_property":
            require(len(values) == 6 and values[0] == "TARGET" and values[1] in TARGET_LIBRARIES
                    and values[2:] == ["APPEND", "PROPERTY", "IMPORTED_CONFIGURATIONS", "RELEASE"],
                    f"{member} sets an unexpected property: {values}")
        elif name == "set_target_properties":
            require(len(values) == 6 and values[0] in TARGET_LIBRARIES and values[1] == "PROPERTIES"
                    and values[2] == "IMPORTED_LINK_INTERFACE_LANGUAGES_RELEASE"
                    and values[3] in {"CXX", "CXX;OBJCXX"}
                    and values[4] == "IMPORTED_LOCATION_RELEASE"
                    and values[5] == "${_IMPORT_PREFIX}/" + TARGET_LIBRARIES[values[0]]
                    and values[0] not in located,
                    f"{member} imports something other than the shipped libraries: {values}")
            located[values[0]] = values[5]
        elif name == "list":
            target = TARGET_LIBRARIES.get(values[2]) if len(values) == 3 else None
            ok = (len(values) == 3 and values[:2] == ["APPEND", "_cmake_import_check_targets"]
                  and target is not None) or (
                len(values) == 3 and values[0] == "APPEND"
                and values[1].removeprefix("_cmake_import_check_files_for_") in TARGET_LIBRARIES
                and values[2] == "${_IMPORT_PREFIX}/"
                + TARGET_LIBRARIES[values[1].removeprefix("_cmake_import_check_files_for_")])
            require(ok, f"{member} records an unexpected import check: {values}")
        else:
            require(False, f"{member} runs {name}(), which a configuration export never does")
    require(set(located) == set(TARGET_LIBRARIES), f"{member} does not import every shipped library")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def read_members(archive: Path) -> tuple[dict[str, bytes], set[str], bytes]:
    raw = gzip.decompress(archive.read_bytes())
    files: dict[str, bytes] = {}
    directories: set[str] = set()
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
                require(DIRECTORY.fullmatch(name) is not None,
                        f"archive directory is outside the gpu-runtime package: {name}")
                directories.add(name)
                continue
            require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE),
                    f"archive member is not a regular file: {name}")
            require(name in FIXED_FILES or HEADER.fullmatch(name) is not None,
                    f"archive member is outside the gpu-runtime package: {name}")
            extracted = handle.extractfile(member)
            require(extracted is not None, f"archive member cannot be read: {name}")
            files[name] = extracted.read()
    return files, directories, raw


def git_head(repo: Path) -> str:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True,
                               capture_output=True, check=False)
    require(completed.returncode == 0, "could not read the checkout's HEAD")
    return completed.stdout.strip()


def load_builder(repo: Path):
    sys.path.insert(0, str(repo / "scripts"))
    import build_gpu_runtime_artifact as builder  # noqa: PLC0415
    return builder


def regenerate_package_files(repo: Path, renderer_archive: Path) -> dict[str, bytes]:
    """Configure the source with the builder's configure command (no compile)
    and return the package files CMake generates for it."""
    builder = load_builder(repo)
    with tempfile.TemporaryDirectory(prefix="vellum-gpu-runtime-regenerate-") as temporary_text:
        temporary = Path(temporary_text)
        command = builder.configure_command(repo, renderer_archive.resolve(),
                                            temporary / "build", temporary / "prefix")
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode:
            raise VerificationError("could not configure the source to regenerate the package "
                                    f"files:\n{completed.stderr[-3000:]}")
        return builder.generated_package_files(temporary / "build")


def verify_against_source(files: dict[str, bytes], metadata: dict, repo: Path,
                          generated: dict[str, bytes]) -> None:
    builder = load_builder(repo)

    expected = set(builder.expected_files(repo))
    require(set(files) == expected,
            "archive files differ from what the source installs: "
            + ", ".join(sorted(set(files) ^ expected)))
    for member, source in VERBATIM_MODULES.items():
        require(files[member] == (repo / source).read_bytes(),
                f"shipped {member} differs from the source")
    include_root = repo / "gpu-runtime/include"
    for path in (include_root / "gpu_runtime").rglob("*.hpp"):
        member = "include/" + path.relative_to(include_root).as_posix()
        require(files[member] == path.read_bytes(), f"shipped {member} differs from the source")
    require(files[LICENSE_MEMBER] == (repo / "LICENSE.md").read_bytes(),
            "shipped license differs from the source")
    require(files[NOTICE_MEMBER].decode("utf-8") == builder.dawn_notice(repo),
            "shipped notice differs from the one the source assembles")
    require(set(generated) == set(GENERATED), "regenerated package files are incomplete")
    for member in GENERATED:
        require(files[member] == generated[member],
                f"shipped {member} differs from what the source generates")
    require(metadata.get("source_commit") == git_head(repo),
            "artifact record names another source commit than the checkout")
    require(metadata.get("version") == builder.vellum_version(repo),
            "artifact record names another version than the checkout")


def verify(archive: Path, repo: Path | None,
           generated: dict[str, bytes] | None = None) -> dict[str, object]:
    match = NAME.fullmatch(archive.name)
    require(match is not None, f"archive name is not vellum-gpu-runtime-<version>-<target>.tar.gz: "
                               f"{archive.name}")
    version, target = match.groups()
    files, directories, raw = read_members(archive)
    require(FIXED_FILES <= set(files),
            "archive lacks package files: " + ", ".join(sorted(FIXED_FILES - set(files))))
    require(any(HEADER.fullmatch(name) for name in files), "archive ships no gpu_runtime headers")
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
    listed: dict[str, dict] = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("path"), str)
                and isinstance(row.get("executable"), bool),
                "artifact record file row is malformed")
        require(row["path"] not in listed, f"artifact record lists {row['path']} twice")
        listed[row["path"]] = row
    require(set(listed) == set(files) - {METADATA_NAME},
            "archive members differ from the artifact record: "
            + ", ".join(sorted(set(listed) ^ (set(files) - {METADATA_NAME}))))
    for name, row in listed.items():
        require(row.get("sha256") == sha256_bytes(files[name]), f"digest mismatch: {name}")
        require(row.get("size") == len(files[name]), f"size mismatch: {name}")
        require(row["executable"] is False, f"package file is marked executable: {name}")
    executable = {name: False for name in files}
    require(raw == canonical_tar(files, directories, executable),
            "archive is not the canonical tar stream of its files")

    lock_digest = sha256_bytes(files[LOCK_MEMBER])
    require(metadata.get("renderer_lock_sha256") == lock_digest,
            "artifact record names a different renderer lock than it ships")
    lock = json.loads(files[LOCK_MEMBER])
    require(metadata.get("renderer_milestone") == lock.get("milestone"),
            "artifact record names a different renderer milestone than its lock")
    platform = (lock.get("platforms") or {}).get(target.replace("darwin", "macos"), {})
    require(sha256_bytes(files[DAWN_MEMBER]) == (platform.get("archives") or {}).get(
                "libdawn_combined.a"),
            "shipped libdawn_combined.a is not the library the renderer lock names")
    for heading in NOTICE_HEADINGS:
        require(heading.encode() in files[NOTICE_MEMBER], f"archive notice lacks {heading}")
    for library in LIBRARIES:
        require(files[library].startswith(b"!<arch>\n"),
                f"archive does not ship {library} as a static library")
    check_targets_file(files[f"{PACKAGE_DIR}/VellumGpuRuntimeTargets.cmake"].decode("utf-8"),
                       "VellumGpuRuntimeTargets.cmake")
    check_targets_release_file(
        files[f"{PACKAGE_DIR}/VellumGpuRuntimeTargets-release.cmake"].decode("utf-8"),
        "VellumGpuRuntimeTargets-release.cmake")

    if repo is not None:
        require(generated is not None, "holding an archive to its source needs the "
                                       "regenerated package files (--renderer-archive)")
        verify_against_source(files, metadata, repo, generated)
    return {
        "archive": archive.name,
        "sha256": sha256_bytes(archive.read_bytes()),
        "version": version,
        "target": target,
        "source_commit": metadata["source_commit"],
        "renderer_lock_sha256": lock_digest,
        "files_verified": len(listed),
        "checked_against_source": repo is not None,
    }


def run_consumer(archive: Path, version: str, repo: Path, require_adapter: bool) -> str:
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
                     f"-DEXPECTED_VERSION={version}"]
        if shutil.which("ninja"):
            configure.extend(["-G", "Ninja"])
        env = dict(os.environ)
        env.pop("GPU_RUNTIME_SKIA_DIR", None)
        env.pop("GPU_RUNTIME_RENDERER_ARCHIVE", None)
        if require_adapter:
            env["GPU_RUNTIME_REQUIRE_ADAPTER"] = "1"
        for command in (configure, ["cmake", "--build", str(build)],
                        [str(build / "gpu-runtime-artifact-consumer")]):
            completed = subprocess.run(command, text=True, capture_output=True, check=False, env=env)
            if completed.returncode:
                raise VerificationError(
                    f"consumer step failed: {' '.join(command)}\n"
                    f"{completed.stdout[-3000:]}{completed.stderr[-3000:]}")
        return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--repo", type=Path, help="hold the archive to this checkout")
    parser.add_argument("--renderer-archive", type=Path,
                        help="with --repo: the locked skia-builder asset, to regenerate the "
                             "package files from the source")
    parser.add_argument("--consumer", action="store_true",
                        help="build and run the audio-only consumer against the archive")
    parser.add_argument("--require-adapter", action="store_true",
                        help="with --consumer: fail unless the consumer brings up a GPU adapter")
    parser.add_argument("--output", type=Path, help="write the verification record here")
    args = parser.parse_args(argv)
    try:
        if args.consumer and args.repo is None:
            raise VerificationError("--consumer needs --repo")
        if args.repo is not None and args.renderer_archive is None:
            raise VerificationError("--repo needs --renderer-archive")
        repo = args.repo.resolve() if args.repo else None
        generated = regenerate_package_files(repo, args.renderer_archive) if repo else None
        result = verify(args.archive, repo, generated)
        if repo is None:
            print("verify-gpu-runtime-artifact: checked for consistency only; without --repo "
                  "this is not a trust decision", file=sys.stderr)
        if args.consumer:
            result["consumer"] = run_consumer(args.archive, str(result["version"]),
                                              args.repo.resolve(), args.require_adapter)
    except (VerificationError, OSError, ValueError, tarfile.TarError, EOFError) as error:
        print(f"verify-gpu-runtime-artifact: {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
