#!/usr/bin/env python3
"""Installed Vellum backend dispatcher and compatibility handshake."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


RESULT_SCHEMA = "vellum.backend.result.v1"
SDK_METADATA_SCHEMA = "vellum.sdk-artifact.v1"
IMPORT_COMMANDS = {"import", "reimport"}
NATIVE_COMMANDS = {"build", "run", "test", "capture", "package"}


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


def backend_path(root: Path, command: str) -> Path:
    bin_dir = root / "bin"
    if command in IMPORT_COMMANDS:
        names = ("vellum-import-backend.cmd",) if os.name == "nt" else ("vellum-import-backend",)
    else:
        names = ("vellum-native-backend.exe", "vellum-native-backend.cmd") if os.name == "nt" else ("vellum-native-backend",)
    for name in names:
        candidate = bin_dir / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"installed backend executable is missing for '{command}'")


def main() -> int:
    if len(sys.argv) < 2:
        return emit_failure("invalid_arguments", "backend command is required", exit_code=2)
    command = sys.argv[1]
    if command not in IMPORT_COMMANDS | NATIVE_COMMANDS:
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
        available = metadata["capabilities"]["commands"].get(command)
        if available is not True:
            return emit_failure(
                "capability_unavailable",
                f"The installed SDK does not yet provide the '{command}' capability",
                exit_code=4,
            )
        backend = backend_path(root, command)
        completed = subprocess.run([str(backend), command, *forwarded], check=False)
        return completed.returncode
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return emit_failure("backend_dispatch_error", str(error), exit_code=5)


if __name__ == "__main__":
    raise SystemExit(main())
