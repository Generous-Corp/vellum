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

from vellum_manifest import (
    APP_MANIFEST_NAME, LOCK_NAME, LOCK_SCHEMA, ManifestError,
    load_app_manifest, load_components_manifest,
)


CLI_VERSION = "0.1.0-dev"
FRAMEWORK_VERSION = "0.1.0"
UI_VERSION = "0.1.0-experimental.0"
CLI_API_VERSION = 1
RESULT_SCHEMA = "vellum.cli.result.v1"
SDK_METADATA_SCHEMA = "vellum.sdk-artifact.v1"
INSTALL_MANIFEST_SCHEMA = "vellum.sdk-install.v1"
BACKEND_NAMES = ("vellum-backend.cmd", "vellum-backend.exe") if os.name == "nt" else ("vellum-backend",)
BACKEND_COMMANDS = ("import", "reimport", "build", "run", "test", "capture", "package")
EXIT_USAGE = 2
EXIT_PROJECT = 3
EXIT_UNAVAILABLE = 4
EXIT_BACKEND = 5
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


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


def unverified_artifact_identity() -> dict[str, Any]:
    return {
        "verified": False,
        "sha256": None,
        "target": "local-development",
        "sourceCommit": None,
    }


def artifact_identity(install_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "verified": install_manifest["verified"],
        "sha256": install_manifest["artifact_sha256"],
        "target": install_manifest["target"],
        "sourceCommit": install_manifest["source_commit"],
    }


def _load_json_file(path: Path, status: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliFailure(f"Cannot read {path}: {error}", status=status, exit_code=EXIT_PROJECT) from error
    if not isinstance(value, dict):
        raise CliFailure(f"Expected a JSON object in {path}.", status=status, exit_code=EXIT_PROJECT)
    return value


def validate_js_dependency_lock(root: Path, lock: dict[str, Any]) -> None:
    package_path = root / "package.json"
    package_lock_path = root / "package-lock.json"
    package = _load_json_file(package_path, "invalid_package_manifest")
    package_lock = _load_json_file(package_lock_path, "invalid_package_lock")
    pinned = lock.get("framework", {}).get("jsPackages")
    if pinned != {"@vellum/ui": UI_VERSION}:
        raise CliFailure(
            f"{LOCK_NAME} must pin exactly @vellum/ui {UI_VERSION}.",
            status="invalid_project_lock", exit_code=EXIT_PROJECT,
        )
    dependency = "file:.vellum/packages/vellum-ui"
    packages = package_lock.get("packages")
    dependencies = package.get("dependencies")
    root_lock = packages.get("") if isinstance(packages, dict) else None
    if (
        not isinstance(dependencies, dict)
        or dependencies.get("@vellum/ui") != dependency
        or any(not isinstance(name, str) or not name or not isinstance(version, str) or not version
               for name, version in dependencies.items())
        or package_lock.get("lockfileVersion") != 3
        or not isinstance(packages, dict)
        or not isinstance(root_lock, dict)
        or root_lock.get("name") != package.get("name")
        or root_lock.get("version") != package.get("version")
        or root_lock.get("dependencies") != dependencies
        or packages.get(".vellum/packages/vellum-ui", {}).get("version") != pinned["@vellum/ui"]
        or packages.get("node_modules/@vellum/ui") != {
            "resolved": ".vellum/packages/vellum-ui", "link": True,
        }
    ):
        raise CliFailure(
            "package.json and package-lock.json must agree and retain the exact project-locked @vellum/ui SDK package.",
            status="invalid_package_lock", exit_code=EXIT_PROJECT,
        )


def project_ui_package(root: Path) -> Path:
    return root / ".vellum/packages/vellum-ui"


def _project_ui_payload(source: Path) -> dict[str, bytes]:
    package = _load_json_file(source / "package.json", "invalid_sdk_artifact")
    if package.get("name") != "@vellum/ui" or package.get("version") != UI_VERSION:
        raise CliFailure(
            "installed SDK @vellum/ui identity does not match the project lock",
            status="invalid_sdk_artifact", exit_code=EXIT_PROJECT,
        )
    runtime_package = {
        key: package[key]
        for key in ("name", "version", "description", "type", "license", "engines", "exports", "dependencies")
        if key in package
    }
    payload = {
        "package.json": (json.dumps(runtime_package, indent=2, sort_keys=True) + "\n").encode(),
    }
    for relative in (Path("LICENSE.md"), Path("README.md")):
        path = source / relative
        if path.is_file():
            payload[relative.as_posix()] = path.read_bytes()
    for path in sorted((source / "src").rglob("*")):
        if path.is_file():
            payload[path.relative_to(source).as_posix()] = path.read_bytes()
    if not any(name.startswith("src/") for name in payload):
        raise CliFailure("installed SDK @vellum/ui has no runtime sources", status="invalid_sdk_artifact", exit_code=EXIT_PROJECT)
    return payload


def ensure_project_ui_package(
    root: Path, sdk: tuple[Path, dict[str, Any], dict[str, Any]] | None, *, fix: bool,
) -> tuple[bool, str, bool]:
    destination = project_ui_package(root)
    source = sdk[0] / "ui" if sdk else None
    expected = _project_ui_payload(source) if source is not None and (source / "package.json").is_file() else None
    if destination.is_dir():
        actual_names = {
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*") if path.is_file()
        }
        available = expected is not None and actual_names == set(expected) and all(
            (destination / name).read_bytes() == content for name, content in expected.items()
        )
        return available, str(destination) if available else "project-local @vellum/ui differs from the exact installed SDK projection", False
    if not fix:
        return False, "run vellum doctor --fix to materialize the project-locked UI package", False
    if source is None or not (source / "package.json").is_file():
        return False, "installed SDK does not contain @vellum/ui", False
    if expected is None:
        return False, "installed SDK does not contain a valid @vellum/ui runtime package", False
    destination.mkdir(parents=True)
    for name, content in expected.items():
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    return True, str(destination), True


def install_project_js_dependencies(root: Path) -> tuple[bool, str]:
    source = project_ui_package(root)
    if not source.is_dir():
        return False, "the exact project-local @vellum/ui package is missing"
    destination = root / "node_modules/@vellum/ui"
    try:
        if (root / "node_modules").exists():
            shutil.rmtree(root / "node_modules")
        destination.parent.mkdir(parents=True)
        shutil.copytree(source, destination)
    except OSError as error:
        return False, f"could not materialize exact project JavaScript dependencies: {error}"
    return True, "materialized exact project-locked @vellum/ui without external npm"


def create_project(args: argparse.Namespace) -> dict[str, Any]:
    if args.run and args.no_verify:
        raise CliFailure("--run cannot be combined with --no-verify.", status="invalid_arguments", exit_code=EXIT_USAGE)
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
    sdk = load_sdk_metadata()
    import_request: tuple[str, Path, str] | None = None
    if args.import_source:
        source_type, source_value = args.import_source
        if source_type not in {"figma", "design-ir"}:
            raise CliFailure(
                f"Unsupported create source type: {source_type}",
                status="unsupported_source_type", exit_code=EXIT_USAGE,
            )
        source_path = Path(source_value).expanduser().resolve()
        if not source_path.is_file():
            raise CliFailure(
                f"Import source does not exist: {source_path}",
                status="source_not_found", exit_code=EXIT_PROJECT,
            )
        if sdk is None or sdk[1]["capabilities"]["commands"].get("import") is not True:
            raise CliFailure(
                "create --from requires an installed SDK with the import capability.",
                status="capability_unavailable", exit_code=EXIT_UNAVAILABLE,
            )
        import_request = (source_type, source_path, args.source_key)
    target_capabilities = sdk[1]["capabilities"].get("targets", {}) if sdk else {}
    runnable_target = next((target for target in ("macos", "web") if all(
        target_capabilities.get(target, {}).get("commands", {}).get(name)
        for name in ("build", "run", "test")
    )), None)
    if args.run and runnable_target is None:
        raise CliFailure(
            "--run requires one installed target with build, test, and run capabilities.",
            status="capability_unavailable",
            exit_code=EXIT_UNAVAILABLE,
        )

    sdk = load_sdk_metadata()
    framework_version = sdk[1]["framework_version"] if sdk else FRAMEWORK_VERSION
    installed_identity = artifact_identity(sdk[2]) if sdk else unverified_artifact_identity()
    project_id = hashlib.sha256(f"vellum-project-v1:{slug}".encode()).hexdigest()[:24]
    replacements = {
        "PROJECT_NAME": args.name.strip(),
        "PROJECT_NAME_JSON": json.dumps(args.name.strip(), ensure_ascii=False)[1:-1],
        "PROJECT_SLUG": slug,
        "PROJECT_ID": project_id,
        "CLI_VERSION": CLI_VERSION,
        "FRAMEWORK_VERSION": framework_version,
        "UI_VERSION": UI_VERSION,
        "ARTIFACT_IDENTITY_JSON": json.dumps(
            installed_identity, sort_keys=True, separators=(",", ":")
        ),
    }
    destination.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for template in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = template.relative_to(source)
        output_relative = Path(str(relative).removesuffix(".template"))
        render_template(template, destination / output_relative, replacements)
        created.append(output_relative.as_posix())

    # The exact JS package is a project-local view of immutable installed SDK
    # bytes. It is generated state, never an editable framework copy.
    setup_commands: list[dict[str, Any]] = []
    if sdk is not None and (sdk[0] / "ui/package.json").is_file():
        available, detail, _ = ensure_project_ui_package(destination, sdk, fix=True)
        if not available:
            raise CliFailure(detail, status="invalid_sdk_artifact", exit_code=EXIT_PROJECT)
        npm_ok, npm_detail = install_project_js_dependencies(destination)
        setup_commands.append({"command": "js-dependencies", "status": "passed" if npm_ok else "failed"})
        if not npm_ok:
            return result(
                "create", ok=False, status="create_validation_failed",
                message="Created the project, but exact JavaScript dependency installation failed.",
                data={
                    "project_root": str(destination), "project_id": project_id,
                    "files": created, "validation": {"status": "failed", "commands": setup_commands},
                },
                diagnostics=[{"level": "error", "code": "npm_ci_failed", "message": npm_detail}],
            )

    lock = json.loads((destination / LOCK_NAME).read_text(encoding="utf-8"))
    if sdk is not None:
        validate_project_sdk(lock, sdk)
    if import_request is not None:
        source_type, source_path, source_key = import_request
        backend_payload, return_code = invoke_backend(
            "import", destination, lock,
            [str(source_path), "--source-type", source_type, "--as", source_key],
        )
        setup_commands.append({"command": "import", "status": backend_payload.get("status")})
        if return_code != 0 or not backend_payload.get("ok"):
            return result(
                "create", ok=False, status="create_import_failed",
                message="Created the project, but initial design import failed.",
                data={
                    "project_root": str(destination), "project_id": project_id,
                    "files": created, "validation": {"status": "failed", "commands": setup_commands},
                    "backend": backend_payload,
                },
                diagnostics=backend_payload.get("diagnostics", []),
            )

    validation: dict[str, Any] = {
        "status": "not_available",
        "commands": setup_commands,
        "detail": "No installed application target capability was available; scaffold only.",
    }
    if not args.no_verify and sdk is not None:
        validation_target = runnable_target
        if validation_target is not None:
            completed_commands: list[dict[str, Any]] = list(setup_commands)
            for command, forwarded in (
                ("build", ["--target", validation_target]),
                ("test", ["--scenario", "smoke", "--target", validation_target]),
            ):
                backend_payload, return_code = invoke_backend(
                    command, destination, lock, forwarded
                )
                completed_commands.append({"command": command, "status": backend_payload.get("status")})
                if return_code != 0 or not backend_payload.get("ok"):
                    return result(
                        "create",
                        ok=False,
                        status="create_validation_failed",
                        message=f"Created the project, but {validation_target} '{command}' validation failed.",
                        data={
                            "project_root": str(destination),
                            "project_id": project_id,
                            "files": created,
                            "validation": {"status": "failed", "commands": completed_commands},
                            "backend": backend_payload,
                        },
                        diagnostics=backend_payload.get("diagnostics", []),
                    )
            validation = {"status": "passed", "commands": completed_commands}
            if args.run:
                backend_payload, return_code = invoke_backend(
                    "run", destination, lock, ["--target", validation_target, "--no-build", "--no-window"]
                )
                validation["commands"].append({"command": "run", "status": backend_payload.get("status")})
                if return_code != 0 or not backend_payload.get("ok"):
                    return result(
                        "create", ok=False, status="create_run_failed",
                        message="Created and validated the project, but launch failed.",
                        data={"project_root": str(destination), "validation": validation, "backend": backend_payload},
                        diagnostics=backend_payload.get("diagnostics", []),
                    )
        elif args.run:
            raise CliFailure(
                "--run requires an installed target with build and run capabilities.",
                status="capability_unavailable",
                exit_code=EXIT_UNAVAILABLE,
            )
    return result(
        "create",
        ok=True,
        status="created",
        message=f"Created {args.name.strip()} at {destination}",
        data={
            "project_root": str(destination),
            "project_id": project_id,
            "template": args.template,
            "artifact": installed_identity,
            "files": created,
            "validation": validation,
            "next_steps": [
                f"cd {destination}", "vellum doctor",
                f"vellum build --target {runnable_target}" if runnable_target else "vellum build",
            ],
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
    artifact = lock.get("framework", {}).get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "verified", "sha256", "target", "sourceCommit",
    }:
        raise CliFailure(
            f"Project lock has no valid artifact identity in {lock_path}.",
            status="invalid_project_lock",
            exit_code=EXIT_PROJECT,
        )
    verified = artifact.get("verified")
    sha = artifact.get("sha256")
    target = artifact.get("target")
    source_commit = artifact.get("sourceCommit")
    if (
        not isinstance(verified, bool) or not isinstance(target, str) or not target or
        (verified and (
            not isinstance(sha, str) or not SHA256_RE.fullmatch(sha) or
            not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit)
        )) or
        (not verified and (sha is not None or source_commit is not None))
    ):
        raise CliFailure(
            f"Project lock artifact identity is malformed in {lock_path}.",
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
    try:
        load_app_manifest(root)
    except ManifestError as error:
        raise CliFailure(str(error), status="invalid_app_manifest", exit_code=EXIT_PROJECT) from error
    validate_js_dependency_lock(root, lock)
    return root, lock


def locate_backend() -> Path | None:
    override = os.environ.get("VELLUM_BACKEND")
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if candidate.is_file() and os.access(candidate, os.X_OK) else None
    sdk_root = os.environ.get("VELLUM_SDK_ROOT")
    if sdk_root:
        for name in BACKEND_NAMES:
            candidate = Path(sdk_root).expanduser() / "bin" / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
    for name in BACKEND_NAMES:
        located = shutil.which(name)
        if located:
            return Path(located).resolve()
    return None


def load_install_manifest(sdk_root: Path) -> dict[str, Any]:
    path = sdk_root / "install-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliFailure(
            f"Cannot read SDK install manifest at {path}: {error}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        ) from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema", "verified", "artifact", "artifact_sha256", "framework_version",
        "target", "source_commit",
    } or manifest.get("schema") != INSTALL_MANIFEST_SCHEMA:
        raise CliFailure(
            f"SDK install manifest is incompatible: {path}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        )
    verified = manifest.get("verified")
    if (
        not isinstance(verified, bool) or
        not isinstance(manifest.get("framework_version"), str) or
        not isinstance(manifest.get("target"), str) or not manifest["target"] or
        (verified and (
            not isinstance(manifest.get("artifact"), str) or not manifest["artifact"] or
            not isinstance(manifest.get("artifact_sha256"), str) or
            not SHA256_RE.fullmatch(manifest["artifact_sha256"]) or
            not isinstance(manifest.get("source_commit"), str) or
            not COMMIT_RE.fullmatch(manifest["source_commit"])
        )) or
        (not verified and any(manifest.get(name) is not None for name in (
            "artifact", "artifact_sha256", "source_commit",
        )))
    ):
        raise CliFailure(
            f"SDK install manifest identity is malformed: {path}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        )
    return manifest


def load_sdk_metadata() -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
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
        or not isinstance(metadata.get("capabilities", {}).get("custom_components"), bool)
        or not isinstance(metadata.get("capabilities", {}).get("commands"), dict)
        or set(metadata["capabilities"]["commands"]) != set(BACKEND_COMMANDS)
        or not all(isinstance(value, bool) for value in metadata["capabilities"]["commands"].values())
    ):
        raise CliFailure(
            f"SDK artifact metadata is incompatible: {metadata_path}",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        )
    install_manifest = load_install_manifest(sdk_root)
    if (
        install_manifest["framework_version"] != metadata["framework_version"] or
        install_manifest["target"] != metadata.get("target") or
        install_manifest["source_commit"] != metadata.get("source_commit")
    ):
        raise CliFailure(
            f"SDK metadata and install identity disagree at {sdk_root}.",
            status="invalid_sdk_artifact",
            exit_code=EXIT_PROJECT,
        )
    return sdk_root, metadata, install_manifest


def validate_project_sdk(
    lock: dict[str, Any], sdk: tuple[Path, dict[str, Any], dict[str, Any]] | None,
) -> None:
    if sdk is None:
        return
    sdk_root, metadata, install_manifest = sdk
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
                "message": f"Install the exact framework version pinned by {LOCK_NAME}; do not edit the lock to bypass compatibility.",
            }],
        )
    locked_artifact = lock["framework"]["artifact"]
    installed_artifact = artifact_identity(install_manifest)
    if locked_artifact != installed_artifact:
        raise CliFailure(
            "Project artifact pin does not match the installed Vellum SDK artifact.",
            status="sdk_artifact_mismatch",
            exit_code=EXIT_PROJECT,
            diagnostics=[{
                "level": "error",
                "code": "artifact_pin_mismatch",
                "message": f"Install the exact artifact SHA pinned by {LOCK_NAME}; local-development locks remain explicitly unverified.",
            }],
        )


def check_item(name: str, *, required: bool, available: bool, detail: str, fix: str | None = None) -> dict[str, Any]:
    return {"name": name, "required": required, "available": available, "detail": detail, "fix": fix}


def node_version(executable: str | None = None) -> tuple[bool, str]:
    executable = executable or shutil.which("node")
    if not executable:
        return False, "not found"
    try:
        completed = subprocess.run([executable, "--version"], text=True, capture_output=True, check=False)
    except OSError as error:
        return False, str(error)
    rendered = completed.stdout.strip() or completed.stderr.strip() or "unknown"
    match = re.fullmatch(r"v?(\d+)(?:\.\d+){0,2}", rendered)
    return bool(completed.returncode == 0 and match and int(match.group(1)) >= 20), rendered


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
    sdk: tuple[Path, dict[str, Any], dict[str, Any]] | None = None
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
    command_capabilities = sdk[1]["capabilities"]["commands"] if sdk else {}
    component_count = 0
    components_valid = True
    component_detail = "not in a Vellum project"
    if project_root:
        try:
            app_manifest = load_app_manifest(project_root)
            components = load_components_manifest(
                project_root, app_manifest["native"]["components_manifest"]
            )
            component_count = len(components)
            component_detail = f"{component_count} declared"
        except ManifestError as error:
            components_valid = False
            component_detail = str(error)
            diagnostics.append({
                "level": "error", "code": "invalid_component_manifest",
                "message": str(error),
            })
    ui_available = False
    ui_detail = "not in a Vellum project"
    if project_root:
        try:
            ui_available, ui_detail, ui_created = ensure_project_ui_package(
                project_root, sdk, fix=bool(args.fix and sdk)
            )
            if ui_created:
                created.append(".vellum/packages/vellum-ui")
        except CliFailure as error:
            ui_detail = str(error)
            diagnostics.append({"level": "error", "code": error.status, "message": str(error)})
    embedded_node_path = next((path for path in (
        sdk[0] / "node/bin/node", sdk[0] / "node/bin/node.exe",
    ) if path.is_file()), None) if sdk else None
    embedded_node = str(embedded_node_path) if embedded_node_path else None
    node_available, node_detail = node_version(embedded_node)
    if embedded_node:
        node_detail = f"{node_detail} (SDK-local {embedded_node})"
    import_required = bool(command_capabilities.get("import") or command_capabilities.get("reimport"))
    web_commands = sdk[1]["capabilities"].get("targets", {}).get("web", {}).get("commands", {}) if sdk else {}
    web_required = any(web_commands.values())
    checks = [
        check_item("python", required=True, available=sys.version_info >= (3, 9), detail=sys.version.split()[0]),
        check_item("project-lock", required=bool(args.project), available=lock_valid, detail=str(project_root) if project_root else "not in a Vellum project"),
        check_item("git", required=False, available=bool(shutil.which("git")), detail=shutil.which("git") or "not found"),
        check_item("cmake", required=False, available=bool(shutil.which("cmake")), detail=shutil.which("cmake") or "not found", fix="Install CMake for native builds."),
        check_item("ninja", required=False, available=bool(shutil.which("ninja")), detail=shutil.which("ninja") or "not found", fix="Install Ninja for native builds."),
        check_item("node", required=import_required or web_required, available=node_available, detail=node_detail, fix="Install an SDK with its exact Node runtime or provide Node.js 20+."),
        check_item("chrome", required=web_required, available=bool(shutil.which("google-chrome") or Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").is_file()), detail=shutil.which("google-chrome") or ("/Applications/Google Chrome.app" if Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").is_file() else "not found"), fix="Install Chrome to execute web scenarios."),
        check_item("sdk-artifact", required=sdk_configured, available=sdk is not None, detail=sdk_detail, fix="Install a checksummed SDK artifact for native CMake consumption."),
        check_item("project-sdk-compatibility", required=sdk_configured and project_lock is not None, available=sdk_error is None and (sdk is not None or not sdk_configured), detail="exact framework pin matched" if sdk and not sdk_error and project_lock else sdk_detail),
        check_item("project-ui-package", required=bool(project_root and sdk and (sdk[0] / "ui/package.json").is_file()), available=ui_available, detail=ui_detail, fix="Run vellum doctor --fix with the exact project-locked SDK installed."),
        check_item("backend-dispatcher", required=bool(command_capabilities), available=backend is not None, detail=str(backend) if backend else "not installed in this extraction milestone", fix="Set VELLUM_SDK_ROOT or VELLUM_BACKEND when a backend artifact is available."),
        check_item("import-backend", required=import_required, available=bool(backend and command_capabilities.get("import") and command_capabilities.get("reimport")), detail="import and reimport available" if command_capabilities.get("import") and command_capabilities.get("reimport") else "unavailable"),
        check_item(
            "custom-components", required=component_count > 0 or not components_valid,
            available=components_valid and bool(
                component_count == 0 or
                (sdk and sdk[1]["capabilities"].get("custom_components") is True)
            ),
            detail=component_detail if component_count == 0 else (
                f"{component_count} declared; installed ABI available"
                if sdk and sdk[1]["capabilities"].get("custom_components") is True
                else f"{component_count} declared; installed ABI unavailable"
            ),
            fix="Install the exact GPU SDK artifact that provides the component ABI.",
        ),
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
    sdk = load_sdk_metadata()
    validate_project_sdk(lock, sdk)
    target = getattr(args, "target", None)
    available = sdk[1]["capabilities"]["commands"].get(args.command) if sdk is not None else None
    if sdk is not None and target is not None:
        available = sdk[1]["capabilities"].get("targets", {}).get(target, {}).get(
            "commands", {}
        ).get(args.command)
    if sdk is not None and available is not True:
        raise CliFailure(
            f"The installed SDK does not provide '{args.command}' for target '{target or 'default'}'.",
            status="capability_unavailable",
            exit_code=EXIT_UNAVAILABLE,
        )
    backend_payload, return_code = invoke_backend(args.command, root, lock, forwarded)
    return result(
        args.command,
        ok=return_code == 0 and bool(backend_payload.get("ok")),
        status=str(backend_payload.get("status", "backend_failed")),
        message=str(backend_payload.get("message", f"Backend completed '{args.command}'.")),
        data={"project_root": str(root), "project_id": lock["project"]["id"], "backend": backend_payload},
        diagnostics=backend_payload.get("diagnostics", []),
    )


def invoke_backend(
    command: str, root: Path, lock: dict[str, Any], forwarded: list[str]
) -> tuple[dict[str, Any], int]:
    backend = locate_backend()
    if backend is None:
        raise CliFailure(
            f"'{command}' needs the Vellum SDK backend, which is not installed in this extraction milestone.",
            status="capability_unavailable",
            exit_code=EXIT_UNAVAILABLE,
            diagnostics=[{
                "level": "info",
                "code": "backend_missing",
                "message": "Set VELLUM_SDK_ROOT or VELLUM_BACKEND after installing a compatible SDK artifact.",
            }],
        )
    invocation = [
        str(backend), command,
        "--project", str(root),
        "--json",
        "--framework-version", lock["framework"]["version"],
        "--cli-api", str(CLI_API_VERSION),
        *forwarded,
    ]
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
            f"Backend returned invalid JSON for '{command}'.",
            status="backend_protocol_error",
            exit_code=EXIT_BACKEND,
            diagnostics=[{"level": "error", "code": "invalid_backend_json", "message": completed.stderr.strip() or str(error)}],
        ) from error
    if not isinstance(backend_payload, dict):
        raise CliFailure("Backend response must be a JSON object.", status="backend_protocol_error", exit_code=EXIT_BACKEND)
    backend_diagnostics = backend_payload.get("diagnostics", [])
    if not isinstance(backend_diagnostics, list) or not all(isinstance(item, dict) for item in backend_diagnostics):
        raise CliFailure("Backend diagnostics must be an array of objects.", status="backend_protocol_error", exit_code=EXIT_BACKEND)
    if backend_payload.get("schema") not in {None, "vellum.backend.result.v1"}:
        raise CliFailure("Backend response has an unsupported schema.", status="backend_protocol_error", exit_code=EXIT_BACKEND)
    return backend_payload, completed.returncode


def parser() -> argparse.ArgumentParser:
    root = CliArgumentParser(prog="vellum", description="Build GPU-rendered applications without a browser UI runtime.")
    root.add_argument("--json", action="store_true", help="emit one stable JSON result object")
    root.add_argument("--version", action="version", version=f"vellum {CLI_VERSION}")
    commands = root.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a deterministic Vellum project")
    create.add_argument("name")
    create.add_argument("--directory", "-d")
    create.add_argument("--template", default="basic")
    create.add_argument(
        "--from", dest="import_source", nargs=2, metavar=("SOURCE_TYPE", "SOURCE"),
        choices=None,
        help="create from an initial source: --from figma FILE or --from design-ir FILE",
    )
    create.add_argument("--as", dest="source_key", default="main", help="permanent source key for --from")
    create.add_argument("--no-verify", action="store_true", help="scaffold without installed native build/test proof")
    create.add_argument("--run", action="store_true", help="launch after the default build/test proof")

    doctor_parser = commands.add_parser("doctor", help="inspect authoring and SDK prerequisites")
    doctor_parser.add_argument("--fix", action="store_true", help="create safe project-local cache/state directories")
    doctor_parser.add_argument("--project")

    backend_specs = {
        "import": [
            ("source", {}),
            ("--source-type", {"choices": ["figma", "design-ir"], "default": "figma"}),
            ("--as", {"dest": "source_key", "default": "main"}),
        ],
        "reimport": [
            ("--source", {"required": True}),
            ("--as", {"dest": "source_key", "default": "main"}),
        ],
        "build": [("--target", {"default": "macos"})],
        "run": [
            ("--target", {"default": "macos"}),
            ("--no-build", {"action": "store_true"}),
            ("--self-test", {"action": "store_true"}),
            ("--no-window", {"action": "store_true"}),
        ],
        "test": [("--scenario", {}), ("--target", {"default": "macos"})],
        "capture": [
            ("--scenario", {}), ("--matrix", {}), ("--montage", {"action": "store_true"}),
            ("--output", {}), ("--target", {"default": "macos"}),
        ],
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
        if payload["status"] == "capability_unavailable":
            return EXIT_UNAVAILABLE
        return EXIT_PROJECT if args.command == "doctor" else EXIT_BACKEND
    except CliFailure as error:
        emit(result(command, ok=False, status=error.status, message=str(error), diagnostics=error.diagnostics), json_output=json_output)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
