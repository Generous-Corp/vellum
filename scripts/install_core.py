#!/usr/bin/env python3
"""Transactional installer for verified Vellum SDK archives.

Supported release bootstraps own acquisition. This module owns the security
boundary after an archive and SHA256SUMS exist locally: verification, safe
extraction, immutable storage, integrity receipts, and atomic activation.
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
MAX_CHECKSUM_BYTES = 1024 * 1024
MAX_CHECKSUM_LINES = 10_000
MAX_RETAINED_FILE_BYTES = 8 * 1024**2
MAX_RETAINED_TOTAL_BYTES = 32 * 1024**2
CONTAMINATION_OVERLAP_BYTES = 256
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
    "vellum_dev.py",
    "vellum_manifest.py",
    "vellum_native_backend.py",
    "vellum_png.py",
    "vellum_image_compare.py",
    "vellum_scenario.py",
    "vellum_cdp.py",
    "vellum_cdp_client.py",
    "vellum_browser.py",
    "vellum_interaction.py",
    "vellum_html_source.py",
    "vellum_web_backend.py",
    "web",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
SAFE_INSTALL_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,255}")
MANAGED_LAUNCHER_MARKER = "# managed-by: vellum-install-core-v1"
COMMANDS = {"import", "reimport", "build", "run", "test", "capture", "package"}
NATIVE_COMMANDS = {"build", "run", "test", "capture", "package"}
NODE_PROVENANCE_SCHEMA = "vellum.node-runtime-provenance.v1"
AGENT_INSTRUCTION_FILES = {
    ".agents/skills/vellum-app-authoring/SKILL.md",
    ".agents/skills/vellum-app-authoring/manifest.v1.json",
}
CONTRACT_RETAINED_FILES = {
    *AGENT_INSTRUCTION_FILES,
    "node/LICENSE",
    "node/provenance.json",
    "ui/package.json",
    "ui/package-lock.json",
    "ui/node_modules/esbuild/package.json",
    "ui/node_modules/@esbuild/darwin-arm64/package.json",
    "ui/node_modules/typescript/package.json",
    "web/manifest.json",
}
FORBIDDEN_PAYLOAD_PATH_PATTERNS = {
    "retired-projection-path": re.compile(
        r"(?:^|/)(?:core|external/(?:fonts|nanosvg)|packages/pulp-import-ir|tools/figma-plugin)(?:/|$)",
        re.IGNORECASE,
    ),
    "pulp-named-payload": re.compile(
        r"(?:^|/)(?:pulp(?:[-_][^/]*)?)(?:/|$)", re.IGNORECASE
    ),
}
FORBIDDEN_PAYLOAD_CONTENT_PATTERNS = {
    "pulp-public-namespace": re.compile(
        rb"(?:\bnamespace\s+pulp\b|\bpulp::|#\s*include\s*[<\"]pulp/)",
        re.IGNORECASE,
    ),
    "pulp-package-or-target": re.compile(
        rb"(?:@pulp/|\bPULP_[A-Z0-9_]+|\bpulp[-_]"
        rb"(?:audio|canvas|format|gpu|graph|host|midi|plugin|render|runtime|signal|view)\b)",
        re.IGNORECASE,
    ),
    "audio-plugin-sdk": re.compile(
        rb"\b(?:AudioUnit|VST3|CLAP|LV2|Oboe)\b", re.IGNORECASE
    ),
}
CONTENT_SCAN_SUFFIXES = frozenset({
    ".c",
    ".cc",
    ".cmake",
    ".cpp",
    ".cs",
    ".css",
    ".cxx",
    ".entitlements",
    ".frag",
    ".glsl",
    ".go",
    ".gradle",
    ".h",
    ".hh",
    ".hpp",
    ".html",
    ".hxx",
    ".in",
    ".inc",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".lock",
    ".m",
    ".manifest",
    ".map",
    ".md",
    ".metal",
    ".mjs",
    ".mm",
    ".pc",
    ".plist",
    ".properties",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".sksl",
    ".svg",
    ".swift",
    ".template",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vert",
    ".wgsl",
    ".xml",
    ".yaml",
    ".yml",
})
CONTENT_SCAN_BASENAMES = frozenset({
    "LICENSE",
    "Makefile",
    "NOTICE",
    "vellum",
    "vellum-backend",
    "vellum-import-backend",
    "vellum-native-backend",
    "vellum-web-backend",
})
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
        lock_details = os.fstat(descriptor)
        if not stat.S_ISREG(lock_details.st_mode):
            raise InstallError(f"installer lock is not a regular file: {lock_path}")
        if lock_details.st_nlink != 1:
            raise InstallError(
                f"installer lock has unexpected hard links: {lock_path}"
            )
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def should_scan_payload_content(name: str) -> bool:
    """Return whether a payload path is explicitly known to contain text.

    Path rules apply to every payload. Content rules apply only to this
    allowlist so executable/native/Wasm/archive/image/font bytes cannot produce
    accidental text-regex matches. Binary integrity remains covered by the
    archive inventory, checksums, and payload-specific provenance checks.
    """

    path = PurePosixPath(name)
    return (
        path.name in CONTENT_SCAN_BASENAMES
        or path.suffix.lower() in CONTENT_SCAN_SUFFIXES
    )


def payload_contamination_findings(
    name: str, content: bytes
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for rule, pattern in FORBIDDEN_PAYLOAD_PATH_PATTERNS.items():
        match = pattern.search(name)
        if match:
            findings.append(
                {"rule": rule, "path": name, "match": match.group(0)}
            )
    if should_scan_payload_content(name):
        for rule, pattern in FORBIDDEN_PAYLOAD_CONTENT_PATTERNS.items():
            match = pattern.search(content)
            if match:
                findings.append(
                    {
                        "rule": rule,
                        "path": name,
                        "match": match.group(0).decode(
                            "utf-8", errors="replace"
                        )[:120],
                    }
                )
    return findings


def retained_contract_path(name: str) -> bool:
    return (
        name in CONTRACT_RETAINED_FILES
        or (
            name.startswith("sdk/lib/cmake/Vellum/")
            and name.endswith(".cmake")
        )
    )


def inspect_payload_stream(
    name: str,
    handle,
    *,
    retain: bool,
) -> tuple[int, str, bytes | None, list[dict[str, str]]]:
    digest = hashlib.sha256()
    size = 0
    retained = bytearray() if retain else None
    tail = b""
    findings = payload_contamination_findings(name, b"")
    scan_content = should_scan_payload_content(name)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
        if retained is not None:
            retained.extend(chunk)
        if scan_content:
            window = tail + chunk
            for rule, pattern in FORBIDDEN_PAYLOAD_CONTENT_PATTERNS.items():
                match = pattern.search(window)
                if match:
                    findings.append(
                        {
                            "rule": rule,
                            "path": name,
                            "match": match.group(0).decode(
                                "utf-8", errors="replace"
                            )[:120],
                        }
                    )
            tail = window[-CONTAMINATION_OVERLAP_BYTES:]
    return (
        size,
        digest.hexdigest(),
        bytes(retained) if retained is not None else None,
        findings,
    )


def require_single_link_regular(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as error:
        raise InstallError(f"{label} is missing: {path}") from error
    if not stat.S_ISREG(details.st_mode):
        raise InstallError(f"{label} is not a regular file: {path}")
    if details.st_nlink != 1:
        raise InstallError(f"{label} has unexpected hard links: {path}")
    return details


def payload_has_cmake_target(contents: dict[str, bytes], target: str) -> bool:
    marker = f"Vellum::{target}".encode()
    return any(
        name.startswith("sdk/lib/cmake/Vellum/")
        and name.endswith(".cmake")
        and marker in content
        for name, content in contents.items()
    )


def validate_ui_payload(contents: dict[str, bytes]) -> bool:
    if not any(name.startswith("ui/") for name in contents):
        return False
    required = {
        "ui/package.json",
        "ui/package-lock.json",
        "ui/src/index.js",
        "ui/node_modules/esbuild/package.json",
        "ui/node_modules/@esbuild/darwin-arm64/package.json",
        "ui/node_modules/@esbuild/darwin-arm64/bin/esbuild",
        "ui/node_modules/typescript/package.json",
    }
    if not required.issubset(contents):
        raise InstallError("@vellum/ui payload is incomplete")
    try:
        package = json.loads(contents["ui/package.json"])
        lock = json.loads(contents["ui/package-lock.json"])
        installed = {
            name: json.loads(
                contents[f"ui/node_modules/{name}/package.json"]
            )["version"]
            for name in ("esbuild", "typescript")
        }
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise InstallError(
            f"@vellum/ui dependency metadata is malformed: {error}"
        ) from error
    runtime_dependencies = package.get("dependencies")
    development_dependencies = package.get("devDependencies")
    locked_root = lock.get("packages", {}).get("", {})
    locked_runtime_dependencies = locked_root.get("dependencies")
    locked_development_dependencies = locked_root.get("devDependencies")
    dependency_maps = (
        runtime_dependencies,
        development_dependencies,
        locked_runtime_dependencies,
        locked_development_dependencies,
    )
    dependencies = (
        {**runtime_dependencies, **development_dependencies}
        if all(isinstance(value, dict) for value in dependency_maps)
        else {}
    )
    if (
        not all(isinstance(value, dict) for value in dependency_maps)
        or runtime_dependencies != locked_runtime_dependencies
        or development_dependencies != locked_development_dependencies
        or set(runtime_dependencies) & set(development_dependencies)
        or set(dependencies) != {"esbuild", "typescript"}
        or any(
            not isinstance(version, str)
            or version[:1] in {"^", "~", ">", "<", "*"}
            for version in dependencies.values()
        )
        or installed != dependencies
    ):
        raise InstallError(
            "@vellum/ui dependencies do not match their exact lock"
        )
    return True


def validate_node_runtime(
    contents: dict[str, bytes],
    payload_records: dict[str, dict[str, object]],
    target: str,
) -> bool:
    node_paths = {name for name in contents if name.startswith("node/")}
    if not node_paths:
        return False
    binaries = node_paths & {"node/bin/node", "node/bin/node.exe"}
    required = {*binaries, "node/LICENSE", "node/provenance.json"}
    if len(binaries) != 1 or node_paths != required:
        raise InstallError(
            "SDK-local Node runtime is partial or has unknown files"
        )
    try:
        provenance = json.loads(contents["node/provenance.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallError(f"Node provenance is malformed: {error}") from error
    fields = {
        "schema",
        "name",
        "version",
        "target",
        "source_url",
        "distribution_sha256",
        "binary_sha256",
        "license_file",
        "license_sha256",
    }
    binary = next(iter(binaries))
    if (
        not isinstance(provenance, dict)
        or set(provenance) != fields
        or provenance.get("schema") != NODE_PROVENANCE_SCHEMA
        or provenance.get("name") != "Node.js"
        or not isinstance(provenance.get("version"), str)
        or re.fullmatch(r"\d+(?:\.\d+){1,2}", provenance["version"]) is None
        or provenance.get("target") != target
        or not isinstance(provenance.get("source_url"), str)
        or not provenance["source_url"].startswith("https://")
        or provenance.get("license_file") != "LICENSE"
        or provenance.get("binary_sha256")
        != payload_records[binary]["sha256"]
        or provenance.get("license_sha256")
        != sha256_bytes(contents["node/LICENSE"])
        or re.fullmatch(
            r"[0-9a-f]{64}", provenance.get("distribution_sha256", "")
        )
        is None
        or len(contents["node/LICENSE"]) < 100
    ):
        raise InstallError(
            "Node license/provenance does not match the packaged runtime"
        )
    return True


def derived_capabilities(
    contents: dict[str, bytes],
    payload_records: dict[str, dict[str, object]],
    target: str,
) -> dict[str, object]:
    if not {"vellum_dev.py", "vellum_manifest.py", "vellum_png.py", "vellum_image_compare.py"}.issubset(contents):
        raise InstallError(
            "SDK artifact lacks application manifest or capture support"
        )
    cmake_sdk = "sdk/lib/cmake/Vellum/VellumConfig.cmake" in contents
    gpu_renderer = payload_has_cmake_target(contents, "Gpu")
    authoring_runtime = payload_has_cmake_target(contents, "Authoring")
    ui_runtime = validate_ui_payload(contents)
    import_backend = "design-ir/bin/vellum-backend.js" in contents
    native_backend = {
        "vellum_native_backend.py",
        "vellum_png.py",
        "vellum_scenario.py",
    }.issubset(contents)
    native_host = all(
        path in contents
        for path in (
            "sdk/bin/vellum-app-host",
            "sdk/lib/libvellum-authoring.dylib",
            "sdk/lib/libvellum-gpu.dylib",
        )
    )
    node_runtime = validate_node_runtime(
        contents, payload_records, target
    )
    native_ready = (
        gpu_renderer
        and authoring_runtime
        and ui_runtime
        and native_backend
        and native_host
        and node_runtime
    )
    web_required = {
        "web/manifest.json",
        "web/vellum_web_core.js",
        "web/vellum_web_core.wasm",
        "web/index.html",
        "web/style.css",
        "web/vellum_host.js",
        "web/browser_component_adapter.cpp",
        "web/text_semantics.js",
        "web/check_wasm_no_engine.py",
        "vellum_web_backend.py",
        "vellum_scenario.py",
        "vellum_cdp.py",
        "vellum_cdp_client.py",
        "vellum_browser.py",
        "vellum_interaction.py",
        "vellum_html_source.py",
    }
    web_runtime = web_required.issubset(contents)
    web_present = (
        any(name.startswith("web/") for name in contents)
        or "vellum_web_backend.py" in contents
    )
    if web_present and not web_runtime:
        raise InstallError("web payload is partial")
    if web_runtime:
        try:
            web_manifest = json.loads(contents["web/manifest.json"])
        except json.JSONDecodeError as error:
            raise InstallError(
                f"web payload manifest is malformed: {error}"
            ) from error
        if web_manifest.get("schema") != "vellum.web-payload.v1":
            raise InstallError("web payload schema is unsupported")
        records = web_manifest.get("files")
        expected_names = {
            name.removeprefix("web/")
            for name in web_required
            if name.startswith("web/")
        }
        if (
            not isinstance(records, dict)
            or set(records) != expected_names - {"manifest.json"}
        ):
            raise InstallError("web payload manifest inventory is incomplete")
        for name, record in records.items():
            payload_record = payload_records[f"web/{name}"]
            if (
                not isinstance(record, dict)
                or record.get("size") != payload_record["size"]
                or record.get("sha256") != payload_record["sha256"]
            ):
                raise InstallError(f"web payload content mismatch: {name}")
    web_ready = web_runtime and ui_runtime and node_runtime
    custom_components = (
        native_ready
        and payload_has_cmake_target(contents, "ComponentAbi")
        and "sdk/include/vellum/components/abi.h" in contents
    )
    commands = {name: False for name in COMMANDS}
    commands["import"] = import_backend
    commands["reimport"] = import_backend
    for name in NATIVE_COMMANDS:
        commands[name] = native_ready
    for name in {"build", "run", "test", "package"}:
        commands[name] = commands[name] or web_ready
    return {
        "cmake_sdk": cmake_sdk,
        "authoring_cli": "vellum_cli.py" in contents,
        "gpu_renderer": gpu_renderer,
        "node_runtime": node_runtime,
        "custom_components": custom_components,
        "commands": commands,
        "authoring": {
            "text_input_v1": {
                "retained_tree": ui_runtime,
                "native_pointer_focus": native_ready,
                "native_direct_text": native_ready,
                "ime_composition": native_ready or web_ready,
                "caret_and_selection": native_ready or web_ready,
                "clipboard_editing": False,
                "accessibility_text": native_ready or web_ready,
                "mobile": False,
            },
            "scenario_v1": {
                "input": native_ready,
                "key": native_ready,
                "maximum_steps": 1000,
                "maximum_input_utf8_bytes": 64 * 1024,
                "keys": [
                    "Enter",
                    "Escape",
                    "Backspace",
                    "Tab",
                    "ArrowUp",
                    "ArrowDown",
                    "ArrowLeft",
                    "ArrowRight",
                    "Home",
                    "End",
                    "Delete",
                ]
                if native_ready
                else [],
            },
            "persistence": {
                "state_v1": native_ready,
                "macos_application_support": native_ready,
                "atomic_snapshot_write": native_ready,
                "migration_api": False,
                "key_value_store": False,
                "sync": False,
            },
        },
        "targets": {
            "macos": {
                "commands": {
                    name: native_ready for name in NATIVE_COMMANDS
                }
            },
            "web": {
                "commands": {
                    name: (
                        web_ready
                        if name in {"build", "run", "test", "package"}
                        else False
                    )
                    for name in NATIVE_COMMANDS
                }
            },
        },
    }


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
    try:
        with checksums.open("rb") as handle:
            content = handle.read(MAX_CHECKSUM_BYTES + 1)
    except (OSError, UnicodeError) as error:
        raise InstallError(
            f"cannot read checksum manifest: {checksums}"
        ) from error
    if len(content) > MAX_CHECKSUM_BYTES:
        raise InstallError("checksum manifest exceeds the installer size limit")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise InstallError(
            f"cannot read checksum manifest: {checksums}"
        ) from error
    if len(lines) > MAX_CHECKSUM_LINES:
        raise InstallError("checksum manifest exceeds the installer line limit")
    for line in lines:
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
    required_metadata = {
        "schema",
        "framework_version",
        "cli_version",
        "cli_api",
        "source_commit",
        "source_tree_clean",
        "target",
        "capabilities",
        "files",
    }
    if set(metadata) != required_metadata:
        raise InstallError(
            "SDK artifact metadata has missing or unknown fields"
        )
    if (
        not isinstance(metadata.get("framework_version"), str)
        or not SAFE_ID.fullmatch(metadata["framework_version"])
        or not isinstance(metadata.get("cli_version"), str)
        or metadata.get("cli_api") != 1
        or not isinstance(metadata.get("target"), str)
        or not SAFE_ID.fullmatch(metadata["target"])
        or not isinstance(metadata.get("source_commit"), str)
        or not COMMIT.fullmatch(metadata["source_commit"])
        or not isinstance(metadata.get("source_tree_clean"), bool)
        or not isinstance(metadata.get("files"), list)
    ):
        raise InstallError(
            "SDK artifact compatibility/provenance fields are malformed"
        )
    if metadata["cli_version"] != metadata["framework_version"]:
        raise InstallError(
            "SDK artifact CLI version does not match framework version"
        )
    capabilities = metadata["capabilities"]
    if (
        not isinstance(capabilities, dict)
        or set(capabilities)
        != {
            "cmake_sdk",
            "authoring_cli",
            "gpu_renderer",
            "node_runtime",
            "custom_components",
            "commands",
            "authoring",
            "targets",
        }
        or not isinstance(capabilities.get("cmake_sdk"), bool)
        or not isinstance(capabilities.get("authoring_cli"), bool)
        or not isinstance(capabilities.get("gpu_renderer"), bool)
        or not isinstance(capabilities.get("node_runtime"), bool)
        or not isinstance(capabilities.get("custom_components"), bool)
        or not isinstance(capabilities.get("commands"), dict)
        or not isinstance(capabilities.get("authoring"), dict)
        or set(capabilities["commands"]) != COMMANDS
        or not all(
            isinstance(value, bool)
            for value in capabilities["commands"].values()
        )
        or set(capabilities.get("targets", {})) != {"macos", "web"}
        or any(
            not isinstance(capabilities["targets"].get(target), dict)
            or set(capabilities["targets"][target]) != {"commands"}
            or set(capabilities["targets"][target]["commands"])
            != NATIVE_COMMANDS
            or not all(
                isinstance(value, bool)
                for value in capabilities["targets"][target][
                    "commands"
                ].values()
            )
            for target in ("macos", "web")
        )
    ):
        raise InstallError("SDK artifact capability claims are malformed")
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
    if declared != sorted(
        declared,
        key=lambda row: (
            row.get("path", "") if isinstance(row, dict) else ""
        ),
    ):
        raise InstallError(
            "artifact file inventory is not in canonical path order"
        )
    expected_files = {
        member.name for member in members
        if member.isfile() and member.name != "metadata.json"
    }
    seen: set[str] = set()
    payload_contents: dict[str, bytes] = {}
    payload_records: dict[str, dict[str, object]] = {}
    contamination_findings: list[dict[str, str]] = []
    retained_total = 0
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
        retain = retained_contract_path(row["path"])
        if retain:
            if member.size > MAX_RETAINED_FILE_BYTES:
                raise InstallError(
                    "contract-retained payload exceeds the per-file "
                    f"memory limit: {row['path']}"
                )
            retained_total += member.size
            if retained_total > MAX_RETAINED_TOTAL_BYTES:
                raise InstallError(
                    "contract-retained payloads exceed the total memory limit"
                )
        payload_size, payload_digest, retained, findings = (
            inspect_payload_stream(row["path"], stream, retain=retain)
        )
        if (
            payload_size != row["size"]
            or payload_digest != row["sha256"]
            or bool(member.mode & stat.S_IXUSR) != row["executable"]
        ):
            raise InstallError(
                f"artifact payload does not match metadata: {row['path']}"
            )
        contamination_findings.extend(findings)
        payload_contents[row["path"]] = (
            retained if retained is not None else b""
        )
        payload_records[row["path"]] = row
        seen.add(row["path"])
    if seen != expected_files:
        raise InstallError("artifact metadata does not cover every payload file")
    capabilities = metadata["capabilities"]
    if capabilities["node_runtime"]:
        node_rows = [
            row
            for row in declared
            if row["path"] in {"node/bin/node", "node/bin/node.exe"}
        ]
        if len(node_rows) != 1 or node_rows[0]["executable"] is not True:
            raise InstallError(
                "SDK-local Node runtime is not uniquely executable"
            )
    if not AGENT_INSTRUCTION_FILES.issubset(payload_contents):
        raise InstallError(
            "SDK artifact has no complete versioned agent-authoring contract"
        )
    try:
        agent_manifest = json.loads(
            payload_contents[
                ".agents/skills/vellum-app-authoring/manifest.v1.json"
            ]
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallError(
            f"agent-authoring manifest is malformed: {error}"
        ) from error
    agent_skill = (
        agent_manifest.get("skill")
        if isinstance(agent_manifest, dict)
        else None
    )
    if (
        not isinstance(agent_manifest, dict)
        or agent_manifest.get("schema") != "vellum.agent-instructions.v1"
        or agent_manifest.get("version") != 1
        or not isinstance(agent_skill, dict)
        or agent_skill.get("path")
        != ".agents/skills/vellum-app-authoring/SKILL.md"
        or b"vellum.agent-instructions.v1"
        not in payload_contents[
            ".agents/skills/vellum-app-authoring/SKILL.md"
        ]
    ):
        raise InstallError(
            "agent-authoring manifest and skill are incompatible"
        )
    if contamination_findings:
        first = contamination_findings[0]
        raise InstallError(
            f"artifact contamination: {first['rule']} in {first['path']}"
        )
    actual_capabilities = derived_capabilities(
        payload_contents, payload_records, metadata["target"]
    )
    if capabilities != actual_capabilities:
        raise InstallError(
            "SDK artifact capability claims do not match installed payloads"
        )
    if (
        "web/manifest.json" in payload_contents
        and json.loads(payload_contents["web/manifest.json"]).get(
            "source_commit"
        )
        != metadata["source_commit"]
    ):
        raise InstallError(
            "web payload source commit does not match SDK provenance"
        )
    if (
        capabilities["commands"]["import"] is not True
        or capabilities["commands"]["reimport"] is not True
    ):
        raise InstallError(
            "SDK artifact must expose its packaged import/reimport backend"
        )
    if (
        capabilities["gpu_renderer"]
        and metadata["target"] != "darwin-arm64"
    ):
        raise InstallError(
            "native GPU application capability is currently darwin-arm64 only"
        )
    return metadata, members


def inspect_archive(archive: Path) -> tuple[dict[str, object], list[tarfile.TarInfo]]:
    try:
        with archive.open("rb") as raw:
            with tarfile.open(fileobj=raw, mode="r:gz") as handle:
                return inspect_open_archive(handle)
    except (OSError, tarfile.TarError, json.JSONDecodeError) as error:
        raise InstallError(f"cannot inspect SDK archive: {error}") from error


def verify_archive_contract(
    archive: Path, checksums: Path
) -> dict[str, object]:
    expected = exact_checksum(archive, checksums)
    actual = sha256_file(archive)
    if actual != expected:
        raise InstallError(f"SHA-256 mismatch for {archive.name}")
    metadata, _members = inspect_archive(archive)
    return {
        "schema": "vellum.sdk-artifact-verification.v1",
        "ok": True,
        "artifact": archive.name,
        "sha256": actual,
        "framework_version": metadata["framework_version"],
        "cli_version": metadata["cli_version"],
        "source_commit": metadata["source_commit"],
        "source_tree_clean": metadata["source_tree_clean"],
        "target": metadata["target"],
        "claims": metadata["capabilities"],
        "file_count": len(metadata["files"]),
        "contamination_free": True,
        "contamination_findings": [],
    }


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
        "export PYTHONDONTWRITEBYTECODE=1",
        'exec python3 "$sdk_root/vellum_cli.py" "$@"',
        "",
    ])


def backend_launcher(module: str) -> str:
    return "\n".join([
        "#!/bin/sh",
        "set -eu",
        MANAGED_LAUNCHER_MARKER,
        'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)',
        "export PYTHONDONTWRITEBYTECODE=1",
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
    write_text_atomic(
        root / "install-manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        0o644,
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
        details = require_single_link_regular(
            path, "installed SDK managed file"
        )
        result.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size": details.st_size,
            "mode": stat.S_IMODE(details.st_mode),
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
        require_single_link_regular(
            receipt_path, "installed SDK ownership receipt"
        )
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
        details = require_single_link_regular(
            path, "installed SDK managed file"
        )
        if (
            details.st_size != row["size"]
            or sha256_file(path) != row["sha256"]
            or stat.S_IMODE(details.st_mode) != row["mode"]
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
        require_single_link_regular(
            manifest_path, "installed SDK manifest"
        )
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
        details = path.lstat()
        return (
            stat.S_ISREG(details.st_mode)
            and details.st_nlink == 1
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
        require_single_link_regular(
            state_path, "Vellum installer state"
        )
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
        require_single_link_regular(
            destination, "verified archive cache entry"
        )
        if sha256_file(destination) != digest:
            raise InstallError(f"verified archive cache is corrupt: {destination}")
        return destination
    if destination.exists():
        raise InstallError(
            f"verified archive cache entry is unsafe: {destination}"
        )
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(archive, temporary)
    require_single_link_regular(
        temporary, "staged verified archive cache entry"
    )
    if sha256_file(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise InstallError("verified archive changed while entering cache")
    os.replace(temporary, destination)
    require_single_link_regular(
        destination, "verified archive cache entry"
    )
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
    verification = verify_archive_contract(archive, checksums)
    actual = verification["sha256"]
    cached = cache_archive(prefix, archive, actual)
    framework_version = verification["framework_version"]
    target = verification["target"]
    if expected_version is not None and framework_version != expected_version:
        raise InstallError(
            "release asset framework version does not match the requested version"
        )
    if expected_target is not None and target != expected_target:
        raise InstallError(
            "release asset target does not match the requested target"
        )
    install_id = (
        f"{framework_version}-{target}-{actual}"
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
    presence = activation_presence(paths)
    if status == "already_installed" and all(presence):
        active_verification = verify_installed(prefix)
        if (
            active_verification["install_id"] == install_id
            and paths["active"].resolve() == final.resolve()
        ):
            return {
                "schema": "vellum.install-result.v1",
                "status": status,
                "artifact_sha256": actual,
                "install_id": install_id,
                "active": str(final),
            }
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
    if active.lstat().st_nlink != 1:
        raise InstallError("active Vellum pointer has unexpected hard links")
    resolved = active.resolve()
    try:
        resolved.relative_to(paths["installs"].resolve())
    except ValueError as error:
        raise InstallError("active Vellum pointer escapes immutable install storage") from error
    if resolved.parent != paths["installs"].resolve():
        raise InstallError("active Vellum pointer is not a direct immutable install")
    receipt = verify_install_root(resolved)
    manifest = load_install_manifest(resolved)
    cached = (
        paths["cache"]
        / manifest["artifact_sha256"]
        / manifest["artifact"]
    )
    require_single_link_regular(cached, "verified archive cache entry")
    if sha256_file(cached) != manifest["artifact_sha256"]:
        raise InstallError(f"verified archive cache is corrupt: {cached}")
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
    cache = paths["cache"]
    owned_cache_entries = {
        (manifest["artifact_sha256"], manifest["artifact"])
        for _root, _receipt, manifest in verified_installs
    }
    owned_cache_paths: list[Path] = []
    for digest, artifact in sorted(owned_cache_entries):
        cached = cache / digest / artifact
        require_single_link_regular(
            cached, "verified archive cache entry"
        )
        if sha256_file(cached) != digest:
            raise InstallError(f"verified archive cache is corrupt: {cached}")
        owned_cache_paths.append(cached)
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
    if cache.is_dir():
        for cached in owned_cache_paths:
            cached.unlink()
            remove_empty_parents(cached.parent, cache)
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
