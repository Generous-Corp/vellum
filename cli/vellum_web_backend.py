#!/usr/bin/env python3
"""Installed exact-pin static web application backend for Vellum projects."""

from __future__ import annotations

import argparse
import gzip
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from typing import Any, Iterable

from vellum_manifest import (
    LOCK_NAME, LOCK_SCHEMA, ManifestError, imported_materialized_design,
    load_app_manifest,
)


RESULT_SCHEMA = "vellum.backend.result.v1"
SCENARIO_SCHEMA = "vellum.scenario.v1"
SUPPORTED_TARGET = "web"
WEB_COMMANDS = {"build", "run", "test", "package"}


class BackendFailure(RuntimeError):
    def __init__(self, message: str, *, status: str = "backend_error", exit_code: int = 1):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise BackendFailure(message, status="invalid_arguments", exit_code=2)


def result(command: str, *, ok: bool, status: str, message: str,
           data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"schema": RESULT_SCHEMA, "command": command, "ok": ok, "status": status,
            "message": message, "data": data or {}, "diagnostics": []}


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def load_json(path: Path, schema: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackendFailure(f"Cannot read {label} at {path}: {error}", status=f"invalid_{label}") from error
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise BackendFailure(f"Unsupported {label} schema at {path}", status=f"invalid_{label}")
    return value


def safe_relative(project: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise BackendFailure(f"{label} must be a non-empty project-relative path", status="invalid_project")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise BackendFailure(f"{label} must remain inside the project", status="invalid_project")
    resolved = (project / candidate).resolve()
    try:
        resolved.relative_to(project.resolve())
    except ValueError as error:
        raise BackendFailure(f"{label} escapes the project", status="invalid_project") from error
    return resolved


def sdk_root() -> Path:
    configured = os.environ.get("VELLUM_SDK_ROOT")
    if not configured:
        raise BackendFailure("VELLUM_SDK_ROOT is required", status="invalid_sdk")
    root = Path(configured).expanduser().resolve()
    metadata = load_json(root / "metadata.json", "vellum.sdk-artifact.v1", "sdk_metadata")
    commands = metadata.get("capabilities", {}).get("targets", {}).get("web", {}).get("commands", {})
    if not all(commands.get(name) is True for name in WEB_COMMANDS):
        raise BackendFailure("Installed SDK does not provide the complete web lane",
                             status="capability_unavailable", exit_code=4)
    return root


def project_context(project_value: str) -> dict[str, Any]:
    project = Path(project_value).expanduser().resolve()
    if not project.is_dir():
        raise BackendFailure(f"Project directory does not exist: {project}", status="invalid_project")
    lock = load_json(project / LOCK_NAME, LOCK_SCHEMA, "project_lock")
    try:
        manifest = load_app_manifest(project)
    except ManifestError as error:
        raise BackendFailure(str(error), status="invalid_app_manifest") from error
    if manifest["targets"].get("web") is not True:
        raise BackendFailure("Project does not enable the web target", status="unsupported_target", exit_code=4)
    entry = safe_relative(project, manifest["app"]["entry"], "project entry")
    if not entry.is_file() or entry.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
        raise BackendFailure(f"Project entry is missing or unsupported: {entry}", status="invalid_project")
    slug = lock.get("project", {}).get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise BackendFailure("Project lock has an invalid slug", status="invalid_project_lock")
    return {"root": project, "lock": lock, "manifest": manifest, "entry": entry, "slug": slug}


def run_checked(arguments: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise BackendFailure(
            f"Command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{(completed.stderr or completed.stdout).strip()}", status="tool_failed"
        )
    return completed


def sdk_node(sdk: Path) -> Path:
    for node in (sdk / "node/bin/node", sdk / "node/bin/node.exe"):
        if node.is_file() and os.access(node, os.X_OK):
            return node
    raise BackendFailure("Installed SDK has no exact Node runtime", status="invalid_sdk")


def validate_web_payload(sdk: Path) -> dict[str, Any]:
    web = sdk / "web"
    manifest = load_json(web / "manifest.json", "vellum.web-payload.v1", "web_payload")
    metadata = load_json(sdk / "metadata.json", "vellum.sdk-artifact.v1", "sdk_metadata")
    if manifest.get("source_commit") != metadata.get("source_commit"):
        raise BackendFailure("Installed web payload provenance differs from the SDK", status="invalid_sdk")
    actual_names = {path.name for path in web.iterdir() if path.is_file()}
    if actual_names != set(manifest.get("files", {})) | {"manifest.json"}:
        raise BackendFailure("Installed web payload inventory differs from its manifest", status="invalid_sdk")
    import hashlib
    for name, record in manifest.get("files", {}).items():
        path = web / name
        if not path.is_file() or not isinstance(record, dict) or \
                record.get("size") != path.stat().st_size or \
                record.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            raise BackendFailure(f"Installed web payload does not match its manifest: {name}",
                                 status="invalid_sdk")
    return manifest


def build_app(context: dict[str, Any], sdk: Path) -> dict[str, Any]:
    project: Path = context["root"]
    destination = project / ".vellum/build/web"
    staging = project / ".vellum/build/.web-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    validate_web_payload(sdk)
    bundle = staging / "app.js"
    command = [str(sdk_node(sdk)), str(sdk / "ui/scripts/build-project.mjs"),
               str(context["entry"]), str(bundle)]
    try:
        imported = imported_materialized_design(project)
    except ManifestError as error:
        raise BackendFailure(str(error), status="invalid_imported_design") from error
    if imported is not None:
        command.append(str(imported))
    run_checked(command, cwd=project)
    for name in ("vellum_web_core.js", "vellum_web_core.wasm", "vellum_host.js", "style.css"):
        shutil.copy2(sdk / "web" / name, staging / name)
    index = (sdk / "web/index.html").read_text(encoding="utf-8").replace(
        "{{APP_NAME}}", html.escape(context["manifest"]["app"]["name"], quote=True)
    )
    (staging / "index.html").write_text(index, encoding="utf-8")
    detector = sdk / "web/check_wasm_no_engine.py"
    run_checked([sys.executable, str(detector), str(staging / "vellum_web_core.wasm")])
    run_checked([sys.executable, str(detector), "--negative-control"])
    checkout = str(sdk.parent.parent.parent).encode()
    for path in staging.iterdir():
        if path.is_file() and checkout in path.read_bytes():
            raise BackendFailure(f"Web output leaked an SDK install path: {path.name}", status="non_relocatable_output")
    import hashlib
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(staging.iterdir()) if path.is_file()}
    (staging / "build-manifest.json").write_text(json.dumps({
        "schema": "vellum.web-build.v1", "artifact": context["lock"]["framework"]["artifact"],
        "files": files,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(staging, destination)
    return {"root": destination, "bundle": bundle.name, "files": sorted(path.name for path in destination.iterdir())}


def ensure_build(context: dict[str, Any], sdk: Path, no_build: bool = False) -> dict[str, Any]:
    root = context["root"] / ".vellum/build/web"
    if no_build:
        if not (root / "build-manifest.json").is_file():
            raise BackendFailure("No prior web build exists", status="build_missing")
        return {"root": root, "bundle": "app.js", "files": sorted(path.name for path in root.iterdir())}
    return build_app(context, sdk)


def chrome_path() -> str:
    chrome = shutil.which("google-chrome")
    application = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome and application.is_file():
        chrome = str(application)
    if not chrome:
        raise BackendFailure("Google Chrome is required for web scenarios", status="prerequisite_missing", exit_code=4)
    return chrome


def validate_scenario_evidence(evidence: dict[str, Any]) -> None:
    def valid_frame(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        digest = value.get("digest")
        command_count = value.get("commandCount")
        return (
            isinstance(digest, int)
            and not isinstance(digest, bool)
            and digest >= 0
            and isinstance(command_count, int)
            and not isinstance(command_count, bool)
            and command_count > 0
        )

    presses = evidence.get("presses")
    canvas_bytes = evidence.get("canvasDataBytes")
    if (
        evidence.get("schema") != "vellum.web-proof.v1"
        or evidence.get("bootError")
        or evidence.get("backend") != "wasm-shared-cpp-core+canvas2d-shell"
        or evidence.get("authoringRuntime") != "browser JavaScript"
        or not valid_frame(evidence.get("initial"))
        or not valid_frame(evidence.get("final"))
        or not isinstance(evidence.get("captures"), list)
        or not isinstance(canvas_bytes, int)
        or isinstance(canvas_bytes, bool)
        or canvas_bytes < 1000
        or not isinstance(presses, list)
        or not all(
                isinstance(item, dict) and item.get("changed") is True
                for item in presses
        )
    ):
        raise BackendFailure(
            f"Browser scenario proof failed: {evidence}",
            status="test_failed",
        )


def run_chrome_scenario(
    url: str,
    received: Any,
    *,
    chrome: str | None = None,
    profile_factory: Any = tempfile.TemporaryDirectory,
    process_factory: Any = subprocess.Popen,
) -> None:
    with profile_factory(prefix="vellum-web-chrome-") as profile:
        process = process_factory([
            chrome or chrome_path(), "--headless=new", "--disable-gpu-sandbox",
            "--no-first-run", "--disable-background-networking",
            f"--user-data-dir={profile}", url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            if not received.wait(20):
                raise BackendFailure(
                    "Browser scenario proof timed out",
                    status="test_failed",
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)


def run_scenario(build: Path, scenario: Path) -> dict[str, Any]:
    scenario_copy = build / "__vellum_scenario.json"
    shutil.copy2(scenario, scenario_copy)
    received = threading.Event()
    evidence: dict[str, Any] = {}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(build), **kwargs)
        def log_message(self, format: str, *args: object) -> None:
            pass
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/__vellum_proof":
                self.send_error(404); return
            length = int(self.headers.get("content-length", "0"))
            value = json.loads(self.rfile.read(length))
            if isinstance(value, dict): evidence.update(value)
            self.send_response(204); self.end_headers(); received.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_chrome_scenario(
            f"http://127.0.0.1:{server.server_port}/index.html"
            "?vellum-scenario=/__vellum_scenario.json",
            received,
        )
    finally:
        server.shutdown(); thread.join(5)
        scenario_copy.unlink(missing_ok=True)
    validate_scenario_evidence(evidence)
    return evidence


def scenario_path(context: dict[str, Any], value: str | None) -> Path:
    relative = value or "tests/scenarios/smoke.json"
    if not relative.endswith(".json") and "/" not in relative:
        relative = f"tests/scenarios/{relative}.json"
    path = safe_relative(context["root"], relative, "scenario")
    load_json(path, SCENARIO_SCHEMA, "scenario")
    return path


def write_reproducible_archive(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(source.iterdir()):
                    info = tarfile.TarInfo(path.name); info.size = path.stat().st_size
                    info.mode = 0o644; info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ""
                    with path.open("rb") as handle: archive.addfile(info, handle)


def command_result(command: str, args: argparse.Namespace, context: dict[str, Any], sdk: Path) -> dict[str, Any]:
    if args.target != SUPPORTED_TARGET:
        raise BackendFailure(f"Target '{args.target}' is unavailable in the web backend",
                             status="unsupported_target", exit_code=4)
    if command == "build":
        built = build_app(context, sdk)
        return result(command, ok=True, status="built", message=f"Built {built['root']}",
                      data={"target": "web", **{key: str(value) if isinstance(value, Path) else value for key, value in built.items()}})
    if command == "test" or (command == "run" and args.no_window):
        built = ensure_build(context, sdk, getattr(args, "no_build", False))
        evidence = run_scenario(built["root"], scenario_path(context, args.scenario))
        return result(command, ok=True, status="tests_passed", message="Web scenario passed in Chrome",
                      data={"target": "web", "build": str(built["root"]), "evidence": evidence})
    if command == "run":
        built = ensure_build(context, sdk, args.no_build)
        serve = [sys.executable, "-m", "http.server", "8000", "--directory", str(built["root"])]
        return result(command, ok=True, status="ready_to_serve",
                      message=f"Serve {built['root']} at http://127.0.0.1:8000",
                      data={"target": "web", "root": str(built["root"]), "serve": serve})
    if command == "package":
        built = ensure_build(context, sdk)
        output_dir = safe_relative(context["root"], args.output or "dist", "package output")
        output = output_dir / f"{context['slug']}-web.tar.gz"
        if output.exists():
            raise BackendFailure(f"Package destination already exists: {output}", status="destination_not_empty")
        write_reproducible_archive(built["root"], output)
        return result(command, ok=True, status="packaged", message=f"Packaged {output}",
                      data={"target": "web", "format": "static", "output": str(output)})
    raise BackendFailure(f"Unsupported web command: {command}", status="unsupported_command", exit_code=2)


def parser(command: str) -> argparse.ArgumentParser:
    value = Parser(add_help=False)
    value.add_argument("--project", required=True); value.add_argument("--json", action="store_true", required=True)
    value.add_argument("--target", default="web")
    if command in {"run", "test"}: value.add_argument("--scenario")
    if command == "run":
        value.add_argument("--no-build", action="store_true"); value.add_argument("--no-window", action="store_true")
        value.add_argument("--self-test", action="store_true")
    if command == "package": value.add_argument("--output")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:]); command = raw.pop(0) if raw else ""
    try:
        if command not in WEB_COMMANDS:
            raise BackendFailure(f"Unsupported web command: {command}", status="unsupported_command", exit_code=2)
        args = parser(command).parse_args(raw)
        emit(command_result(command, args, project_context(args.project), sdk_root())); return 0
    except BackendFailure as error:
        emit(result(command or "unknown", ok=False, status=error.status, message=str(error))); return error.exit_code
    except (OSError, ValueError, json.JSONDecodeError) as error:
        emit(result(command or "unknown", ok=False, status="backend_error", message=str(error))); return 1


if __name__ == "__main__":
    raise SystemExit(main())
