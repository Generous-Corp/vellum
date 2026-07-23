#!/usr/bin/env python3
"""Vellum's dependency-free project CLI and backend protocol adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


CLI_VERSION = "0.1.0-dev"
FRAMEWORK_VERSION = "0.1.0"
CLI_API_VERSION = 1
RESULT_SCHEMA = "vellum.cli.result.v1"
LOCK_SCHEMA = "vellum.project-lock.v1"
SDK_METADATA_SCHEMA = "vellum.sdk-artifact.v1"
LOCK_NAME = "vellum.lock.json"
BACKEND_NAME = "vellum-backend.exe" if os.name == "nt" else "vellum-backend"
EXIT_USAGE = 2
EXIT_PROJECT = 3
EXIT_UNAVAILABLE = 4
EXIT_BACKEND = 5


class CliFailure(RuntimeError):
    def __init__(self, message: str, *, status: str, exit_code: int, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code
        self.diagnostics = diagnostics or []


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliFailure(message, status="invalid_arguments", exit_code=EXIT_USAGE)


def result(command: str, *, ok: bool, status: str, message: str, data: dict[str, Any] | None = None,
           diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "cli_version": CLI_VERSION,
        "command": command,
        "ok": ok,
        "status": status,
        "message": message,
        "data": data or {},
        "diagnostics": diagnostics or [],
    }


def emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    stream = sys.stdout if payload["ok"] else sys.stderr
    print(payload["message"], file=stream)
    for diagnostic in payload["diagnostics"]:
        label = diagnostic.get("level", "info").upper()
        print(f"{label}: {diagnostic.get('message', '')}", file=stream)


def slugify(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 100 or any(character in normalized for character in "\r\n\0"):
        raise CliFailure(
            "Project name must be 1-100 characters on one line.",
            status="invalid_project_name",
            exit_code=EXIT_USAGE,
        )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise CliFailure("Project name must contain a letter or number.", status="invalid_project_name", exit_code=EXIT_USAGE)
    return slug


def template_root() -> Path:
    override = os.environ.get("VELLUM_TEMPLATE_DIR")
    candidates = [
        Path(override).expanduser() if override else None,
        Path(__file__).resolve().parent.parent / "templates",
        Path(__file__).resolve().parent / "templates",
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir():
            return candidate
    raise CliFailure(
        "Vellum templates are not installed. Set VELLUM_TEMPLATE_DIR to the templates directory.",
        status="templates_unavailable",
        exit_code=EXIT_UNAVAILABLE,
    )


def render_template(source: Path, destination: Path, replacements: dict[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_text(encoding="utf-8")
    for token, value in replacements.items():
        content = content.replace("{{" + token + "}}", value)
    destination.write_text(content, encoding="utf-8")
    destination.chmod(source.stat().st_mode & 0o777)


def create_project(args: argparse.Namespace) -> dict[str, Any]:
    slug = slugify(args.name)
    destination = Path(args.directory or slug).expanduser().resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise CliFailure(
            f"Destination is not empty: {destination}",
            status="destination_not_empty",
            exit_code=EXIT_PROJECT,
        )
    source = template_root() / args.template
    if not source.is_dir():
        raise CliFailure(f"Unknown template: {args.template}", status="unknown_template", exit_code=EXIT_USAGE)

    project_id = hashlib.sha256(f"vellum-project-v1:{slug}".encode()).hexdigest()[:24]
    replacements = {
        "PROJECT_NAME": args.name.strip(),
        "PROJECT_NAME_JSON": json.dumps(args.name.strip(), ensure_ascii=False)[1:-1],
        "PROJECT_SLUG": slug,
        "PROJECT_ID": project_id,
        "CLI_VERSION": CLI_VERSION,
        "FRAMEWORK_VERSION": FRAMEWORK_VERSION,
    }
    destination.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for template in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = template.relative_to(source)
        output_relative = Path(str(relative).removesuffix(".template"))
        render_template(template, destination / output_relative, replacements)
        created.append(output_relative.as_posix())

    return result(
        "create",
        ok=True,
        status="created",
        message=f"Created {args.name.strip()} at {destination}",
        data={
            "project_root": str(destination),
            "project_id": project_id,
            "template": args.template,
            "files": created,
            "next_steps": [f"cd {destination}", "vellum doctor", "vellum build"],
        },
    )


def find_project(start: Path) -> Path:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / LOCK_NAME).is_file():
            return candidate
    raise CliFailure(
        f"No {LOCK_NAME} found at or above {current}.",
        status="project_not_found",
        exit_code=EXIT_PROJECT,
    )


def load_project(path: str | None) -> tuple[Path, dict[str, Any]]:
    root = find_project(Path(path or os.getcwd()))
    lock_path = root / LOCK_NAME
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliFailure(f"Cannot read {lock_path}: {error}", status="invalid_project_lock", exit_code=EXIT_PROJECT) from error
    if not isinstance(lock, dict) or lock.get("schema") != LOCK_SCHEMA:
        raise CliFailure(f"Unsupported project lock schema in {lock_path}.", status="invalid_project_lock", exit_code=EXIT_PROJECT)
    framework_version = lock.get("framework", {}).get("version")
    if not isinstance(framework_version, str) or not framework_version.strip():
        raise CliFailure(
            f"Project lock has no valid framework version in {lock_path}.",
            status="invalid_project_lock",
            exit_code=EXIT_PROJECT,
        )
    if lock.get("cli", {}).get("api") != CLI_API_VERSION:
        raise CliFailure(
            f"Project requires CLI API {lock.get('cli', {}).get('api')}; this CLI supports API {CLI_API_VERSION}.",
            status="cli_api_mismatch",
            exit_code=EXIT_PROJECT,
        )
    project_id = lock.get("project", {}).get("id")
    if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f]{24}", project_id):
        raise CliFailure(f"Invalid project id in {lock_path}.", status="invalid_project_lock", exit_code=EXIT_PROJECT)
    return root, lock


def locate_backend() -> Path | None:
    override = os.environ.get("VELLUM_BACKEND")
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if candidate.is_file() and os.access(candidate, os.X_OK) else None
    sdk_root = os.environ.get("VELLUM_SDK_ROOT")
    if sdk_root:
        candidate = Path(sdk_root).expanduser() / "bin" / BACKEND_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    located = shutil.which(BACKEND_NAME)
    return Path(located).resolve() if located else None


def load_sdk_metadata() -> tuple[Path, dict[str, Any]] | None:
    sdk_root_value = os.environ.get("VELLUM_SDK_ROOT")
    if not sdk_root_value:
        return None
    sdk_root = Path(sdk_root_value).expanduser().resolve()
    metadata_path = sdk_root / "metadata.json"
    if not metadata_path.is_file():
        raise CliFailure(
            f"VELLUM_SDK_ROOT has no artifact metadata: {metadata_path}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliFailure(
            f"Cannot read SDK artifact metadata at {metadata_path}: {error}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        ) from error
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != SDK_METADATA_SCHEMA
        or not isinstance(metadata.get("framework_version"), str)
        or metadata.get("cli_api") != CLI_API_VERSION
        or not isinstance(metadata.get("capabilities"), dict)
    ):
        raise CliFailure(
            f"SDK artifact metadata is incompatible: {metadata_path}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        )
    return sdk_root, metadata


def validate_project_sdk(lock: dict[str, Any], sdk: tuple[Path, dict[str, Any]] | None) -> None:
    if sdk is None:
        return
    sdk_root, metadata = sdk
    locked_version = lock["framework"]["version"]
    installed_version = metadata["framework_version"]
    if locked_version != installed_version:
        raise CliFailure(
            f"Project requires Vellum {locked_version}, but SDK {installed_version} is installed at {sdk_root}.",
            status="sdk_version_mismatch",
            exit_code=EXIT_PROJECT,
            diagnostics=[{
                "level": "error",
                "code": "framework_pin_mismatch",
                "message": "Install the exact framework version pinned by vellum.lock.json; do not edit the lock to bypass compatibility.",
            }],
        )


def check_item(name: str, *, required: bool, available: bool, detail: str, fix: str | None = None) -> dict[str, Any]:
    return {"name": name, "required": required, "available": available, "detail": detail, "fix": fix}


def doctor(args: argparse.Namespace) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    project_root: Path | None = None
    project_lock: dict[str, Any] | None = None
    lock_valid = False
    try:
        project_root, project_lock = load_project(args.project)
        lock_valid = True
    except CliFailure as error:
        if args.project:
            diagnostics.append({"level": "error", "code": error.status, "message": str(error)})
        else:
            diagnostics.append({"level": "info", "code": error.status, "message": str(error)})

    created: list[str] = []
    if args.fix and project_root:
        for relative in (".vellum/cache", ".vellum/state"):
            target = project_root / relative
            target.mkdir(parents=True, exist_ok=True)
            created.append(relative)

    backend = locate_backend()
    sdk_configured = bool(os.environ.get("VELLUM_SDK_ROOT"))
    sdk: tuple[Path, dict[str, Any]] | None = None
    sdk_error: CliFailure | None = None
    try:
        sdk = load_sdk_metadata()
        if project_lock:
            validate_project_sdk(project_lock, sdk)
    except CliFailure as error:
        sdk_error = error
        diagnostics.append({"level": "error", "code": error.status, "message": str(error)})
    sdk_detail = "not installed"
    if sdk:
        sdk_detail = f"{sdk[1]['framework_version']} at {sdk[0]}"
    elif sdk_error:
        sdk_detail = str(sdk_error)
    checks = [
        check_item("python", required=True, available=sys.version_info >= (3, 9), detail=sys.version.split()[0]),
        check_item("project-lock", required=bool(args.project), available=lock_valid, detail=str(project_root) if project_root else "not in a Vellum project"),
        check_item("git", required=False, available=bool(shutil.which("git")), detail=shutil.which("git") or "not found"),
        check_item("cmake", required=False, available=bool(shutil.which("cmake")), detail=shutil.which("cmake") or "not found", fix="Install CMake for native builds."),
        check_item("ninja", required=False, available=bool(shutil.which("ninja")), detail=shutil.which("ninja") or "not found", fix="Install Ninja for native builds."),
        check_item("node", required=False, available=bool(shutil.which("node")), detail=shutil.which("node") or "not found", fix="Install Node.js for TypeScript authoring tools."),
        check_item("sdk-artifact", required=sdk_configured, available=sdk is not None, detail=sdk_detail, fix="Install a checksummed SDK artifact for native CMake consumption."),
        check_item("project-sdk-compatibility", required=sdk_configured and project_lock is not None, available=sdk_error is None and (sdk is not None or not sdk_configured), detail="exact framework pin matched" if sdk and not sdk_error and project_lock else sdk_detail),
        check_item("native-backend", required=False, available=backend is not None, detail=str(backend) if backend else "not installed in this extraction milestone", fix="Set VELLUM_SDK_ROOT or VELLUM_BACKEND when a backend artifact is available."),
    ]
    required_ok = all(item["available"] for item in checks if item["required"])
    status = "ready" if required_ok else "needs_attention"
    return result(
        "doctor",
        ok=required_ok,
        status=status,
        message="Vellum authoring prerequisites are ready." if required_ok else "Vellum prerequisites need attention.",
        data={"checks": checks, "project_root": str(project_root) if project_root else None, "fixed": created},
        diagnostics=diagnostics,
    )


def backend_command(args: argparse.Namespace, forwarded: list[str]) -> dict[str, Any]:
    root, lock = load_project(args.project)
    validate_project_sdk(lock, load_sdk_metadata())
    backend = locate_backend()
    if backend is None:
        raise CliFailure(
            f"'{args.command}' needs the Vellum SDK backend, which is not installed in this extraction milestone.",
            status="capability_unavailable",
            exit_code=EXIT_UNAVAILABLE,
            diagnostics=[{
                "level": "info",
                "code": "backend_missing",
                "message": "Set VELLUM_SDK_ROOT or VELLUM_BACKEND after installing a compatible SDK artifact.",
            }],
        )
    invocation = [str(backend), args.command, "--project", str(root), "--json", *forwarded]
    try:
        completed = subprocess.run(invocation, text=True, capture_output=True, check=False)
    except OSError as error:
        raise CliFailure(
            f"Could not execute Vellum backend: {error}",
            status="backend_execution_error",
            exit_code=EXIT_BACKEND,
        ) from error
    try:
        backend_payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CliFailure(
            f"Backend returned invalid JSON for '{args.command}'.",
            status="backend_protocol_error",
            exit_code=EXIT_BACKEND,
            diagnostics=[{"level": "error", "code": "invalid_backend_json", "message": completed.stderr.strip() or str(error)}],
        ) from error
    if not isinstance(backend_payload, dict):
        raise CliFailure("Backend response must be a JSON object.", status="backend_protocol_error", exit_code=EXIT_BACKEND)
    backend_diagnostics = backend_payload.get("diagnostics", [])
    if not isinstance(backend_diagnostics, list) or not all(isinstance(item, dict) for item in backend_diagnostics):
        raise CliFailure("Backend diagnostics must be an array of objects.", status="backend_protocol_error", exit_code=EXIT_BACKEND)
    return result(
        args.command,
        ok=completed.returncode == 0 and bool(backend_payload.get("ok")),
        status=str(backend_payload.get("status", "backend_failed")),
        message=str(backend_payload.get("message", f"Backend completed '{args.command}'.")),
        data={"project_root": str(root), "project_id": lock["project"]["id"], "backend": backend_payload},
        diagnostics=backend_diagnostics,
    )


def parser() -> argparse.ArgumentParser:
    root = CliArgumentParser(prog="vellum", description="Build GPU-rendered applications without a browser UI runtime.")
    root.add_argument("--json", action="store_true", help="emit one stable JSON result object")
    root.add_argument("--version", action="version", version=f"vellum {CLI_VERSION}")
    commands = root.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a deterministic Vellum project")
    create.add_argument("name")
    create.add_argument("--directory", "-d")
    create.add_argument("--template", default="basic")

    doctor_parser = commands.add_parser("doctor", help="inspect authoring and SDK prerequisites")
    doctor_parser.add_argument("--fix", action="store_true", help="create safe project-local cache/state directories")
    doctor_parser.add_argument("--project")

    backend_specs = {
        "import": [("source", {}), ("--source-type", {"choices": ["figma", "web", "claude", "design-ir"], "default": "figma"})],
        "reimport": [("--source", {})],
        "build": [("--target", {"default": "macos"})],
        "run": [("--target", {"default": "macos"}), ("--no-build", {"action": "store_true"})],
        "test": [("--scenario", {})],
        "capture": [("--scenario", {}), ("--output", {}), ("--target", {"default": "macos"})],
        "package": [("--target", {"default": "macos"}), ("--output", {})],
    }
    for name, arguments in backend_specs.items():
        command = commands.add_parser(name, help=f"{name} through the installed Vellum SDK backend")
        command.add_argument("--project")
        for argument, options in arguments:
            command.add_argument(argument, **options)
    return root


def forwarded_arguments(args: argparse.Namespace) -> list[str]:
    ignored = {"json", "command", "project"}
    forwarded: list[str] = []
    for key, value in vars(args).items():
        if key in ignored or value is None or value is False:
            continue
        option = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            forwarded.append(option)
        elif key == "source" and args.command == "import":
            forwarded.append(str(value))
        else:
            forwarded.extend([option, str(value)])
    return forwarded


def main(argv: Iterable[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    json_output = "--json" in raw
    if json_output:
        raw.remove("--json")
        raw.insert(0, "--json")
    command = next((item for item in raw if not item.startswith("-")), "unknown")
    try:
        args = parser().parse_args(raw)
        if args.command == "create":
            payload = create_project(args)
        elif args.command == "doctor":
            payload = doctor(args)
        else:
            payload = backend_command(args, forwarded_arguments(args))
        emit(payload, json_output=args.json)
        if payload["ok"]:
            return 0
        return EXIT_PROJECT if args.command == "doctor" else EXIT_BACKEND
    except CliFailure as error:
        emit(result(command, ok=False, status=error.status, message=str(error), diagnostics=error.diagnostics), json_output=json_output)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
