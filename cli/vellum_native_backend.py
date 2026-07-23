#!/usr/bin/env python3
"""Installed macOS native application backend for Vellum projects."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable

from vellum_manifest import (
    LOCK_NAME, LOCK_SCHEMA, ManifestError, load_app_manifest, load_components_manifest,
)
from vellum_png import PngError, montage, read_png, write_png


RESULT_SCHEMA = "vellum.backend.result.v1"
SCENARIO_SCHEMA = "vellum.scenario.v1"
CAPTURE_MATRIX_SCHEMA = "vellum.capture-matrix.v1"
SUPPORTED_TARGET = "macos"
MAX_SCENARIO_STEPS = 1000
MAX_SCENARIO_TARGET_BYTES = 1024
MAX_SCENARIO_INPUT_BYTES = 64 * 1024
SUPPORTED_SCENARIO_KEYS = {
    "Enter", "Escape", "Backspace", "Tab", "ArrowUp", "ArrowDown",
    "ArrowLeft", "ArrowRight", "Home", "End", "Delete",
}
PRIVATE_VELLUM_INCLUDE = re.compile(
    r'^\s*#\s*include\s*[<"](vellum/[^>"]+)[>"]', re.MULTILINE,
)


class BackendFailure(RuntimeError):
    def __init__(self, message: str, *, status: str = "backend_error", exit_code: int = 1):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code


class BackendArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise BackendFailure(message, status="invalid_arguments", exit_code=2)


def result(command: str, *, ok: bool, status: str, message: str,
           data: dict[str, Any] | None = None,
           diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "command": command,
        "ok": ok,
        "status": status,
        "message": message,
        "data": data or {},
        "diagnostics": diagnostics or [],
    }


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
    project = project.resolve()
    if not isinstance(value, str) or not value or "\0" in value:
        raise BackendFailure(f"{label} must be a non-empty project-relative path", status="invalid_project")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise BackendFailure(f"{label} must remain inside the project", status="invalid_project")
    resolved = (project / candidate).resolve()
    try:
        resolved.relative_to(project)
    except ValueError as error:
        raise BackendFailure(f"{label} escapes the project", status="invalid_project") from error
    return resolved


def output_path(project: Path, value: str | None, default: str, label: str) -> Path:
    path = safe_relative(project, value or default, label)
    if path == project:
        raise BackendFailure(f"{label} cannot be the project root", status="invalid_arguments", exit_code=2)
    return path


def sdk_root() -> Path:
    configured = os.environ.get("VELLUM_SDK_ROOT")
    if not configured:
        raise BackendFailure("VELLUM_SDK_ROOT is required", status="invalid_sdk")
    root = Path(configured).expanduser().resolve()
    metadata = load_json(root / "metadata.json", "vellum.sdk-artifact.v1", "sdk_metadata")
    if metadata.get("target") != "darwin-arm64" or metadata.get("capabilities", {}).get("gpu_renderer") is not True:
        raise BackendFailure(
            "Installed SDK does not provide the macOS arm64 GPU application backend",
            status="capability_unavailable",
            exit_code=4,
        )
    return root


def project_context(project_argument: str) -> dict[str, Any]:
    project = Path(project_argument).expanduser().resolve()
    if not project.is_dir():
        raise BackendFailure(f"Project directory does not exist: {project}", status="invalid_project")
    lock = load_json(project / LOCK_NAME, LOCK_SCHEMA, "project_lock")
    try:
        manifest = load_app_manifest(project)
    except ManifestError as error:
        raise BackendFailure(str(error), status="invalid_app_manifest") from error
    identity = lock.get("project", {}).get("id")
    slug = lock.get("project", {}).get("slug")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{24}", identity):
        raise BackendFailure("Project lock has an invalid project id", status="invalid_project_lock")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise BackendFailure("Project lock has an invalid project slug", status="invalid_project_lock")
    entry = safe_relative(project, manifest["app"].get("entry"), "project entry")
    if not entry.is_file() or entry.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
        raise BackendFailure(f"Project entry is missing or unsupported: {entry}", status="invalid_project")
    targets = manifest["targets"]
    if SUPPORTED_TARGET not in targets.get("desktop", []):
        raise BackendFailure("Project does not enable the macos target", status="unsupported_target", exit_code=4)
    application_id = manifest["app"].get("identifier")
    display_name = manifest["app"].get("name")
    if not isinstance(application_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", application_id):
        raise BackendFailure("Package application_id is invalid", status="invalid_package")
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
        raise BackendFailure("Package display_name is invalid", status="invalid_package")
    if manifest["packaging"].get("macos_format") != "app":
        raise BackendFailure("This SDK packages only a macOS .app", status="unsupported_target", exit_code=4)
    try:
        components = load_components_manifest(
            project, manifest["native"]["components_manifest"]
        )
    except ManifestError as error:
        raise BackendFailure(str(error), status="invalid_component_manifest") from error
    return {
        "root": project,
        "lock": lock,
        "entry": entry,
        "slug": slug,
        "application_id": application_id,
        "display_name": display_name.strip(),
        "version": manifest["app"]["version"],
        "capabilities": manifest["capabilities"],
        "components": components,
    }


def run_checked(arguments: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise BackendFailure(
            f"Command failed ({completed.returncode}): {' '.join(arguments)}\n{detail}",
            status="tool_failed",
        )
    return completed


def require_target(value: str) -> None:
    if value != SUPPORTED_TARGET:
        raise BackendFailure(
            f"Target '{value}' is unavailable; this SDK supports only macos",
            status="unsupported_target",
            exit_code=4,
        )


def sdk_node(sdk: Path) -> Path:
    for node in (sdk / "node/bin/node", sdk / "node/bin/node.exe"):
        if node.is_file() and os.access(node, os.X_OK):
            return node
    raise BackendFailure("Installed SDK has no exact Node runtime", status="invalid_sdk")


def copy_frameworks(sdk: Path, destination: Path) -> list[str]:
    source = sdk / "sdk/lib"
    names = ["libvellum-authoring.dylib", "libvellum-gpu.dylib"]
    destination.mkdir(parents=True)
    for name in names:
        candidate = source / name
        if not candidate.is_file():
            raise BackendFailure(f"Installed SDK is missing {candidate}", status="invalid_sdk")
        shutil.copy2(candidate, destination / name)
    return names


def validate_component_source(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BackendFailure(
            f"Cannot read custom component source {path}: {error}",
            status="invalid_component_source",
        ) from error
    if len(content.encode("utf-8")) > 4 * 1024 * 1024:
        raise BackendFailure("Custom component source exceeds 4 MiB", status="invalid_component_source")
    framework_includes = PRIVATE_VELLUM_INCLUDE.findall(content)
    forbidden = sorted(set(framework_includes) - {"vellum/components/abi.h"})
    if forbidden:
        raise BackendFailure(
            "Custom components may use only the public vellum/components/abi.h framework header; "
            f"forbidden includes: {forbidden}",
            status="private_component_api",
        )


def component_compiler() -> str:
    xcrun = shutil.which("xcrun")
    if xcrun:
        return run_checked([xcrun, "--find", "clang++"]).stdout.strip()
    compiler = shutil.which("clang++")
    if compiler:
        return compiler
    raise BackendFailure("clang++ is required for native custom components", status="tool_unavailable")


def build_component_modules(
    context: dict[str, Any], sdk: Path, destination: Path,
) -> list[dict[str, str]]:
    declarations = context["components"]
    if not declarations:
        return []
    metadata = load_json(sdk / "metadata.json", "vellum.sdk-artifact.v1", "sdk_metadata")
    if metadata.get("capabilities", {}).get("custom_components") is not True:
        raise BackendFailure(
            "Installed SDK does not provide the custom component ABI",
            status="capability_unavailable", exit_code=4,
        )
    sdk_include_root = sdk / "sdk/include"
    abi = sdk_include_root / "vellum/components/abi.h"
    if not abi.is_file():
        raise BackendFailure("Installed SDK is missing the custom component ABI header", status="invalid_sdk")
    include_root = context["root"] / ".vellum/build/component-abi/include"
    projected_abi = include_root / "vellum/components/abi.h"
    projected_abi.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(abi, projected_abi)
    compiler = component_compiler()
    destination.mkdir(parents=True)
    modules: list[dict[str, str]] = []
    for declaration in declarations:
        source = safe_relative(
            context["root"], declaration["native_source"],
            f"custom component {declaration['id']} native source",
        )
        validate_component_source(source)
        output = destination / f"{declaration['id']}.dylib"
        run_checked([
            compiler, "-std=c++20", "-dynamiclib", "-fvisibility=hidden",
            "-fvisibility-inlines-hidden", "-DVELLUM_COMPONENT_BUILD=1",
            "-I", str(include_root), str(source), "-o", str(output),
            "-Wl,-undefined,error",
        ], cwd=context["root"])
        modules.append({"id": declaration["id"], "path": str(output)})
    return modules


def build_app(context: dict[str, Any], sdk: Path) -> dict[str, Any]:
    project: Path = context["root"]
    build_root = project / ".vellum/build/macos"
    app = build_root / f"{context['slug']}.app"
    staging = build_root / f".{context['slug']}.app.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    contents = staging / "Contents"
    executable_dir = contents / "MacOS"
    resources = contents / "Resources"
    frameworks = contents / "Frameworks"
    component_directory = contents / "PlugIns/VellumComponents"
    executable_dir.mkdir(parents=True)
    resources.mkdir(parents=True)
    host = sdk / "sdk/bin/vellum-app-host"
    bundler = sdk / "ui/scripts/build-project.mjs"
    if not host.is_file() or not os.access(host, os.X_OK):
        raise BackendFailure("Installed SDK has no executable vellum-app-host", status="invalid_sdk")
    if not bundler.is_file():
        raise BackendFailure("Installed SDK has no @vellum/ui project bundler", status="invalid_sdk")
    bundle = resources / "app.js"
    bundle_command = [str(sdk_node(sdk)), str(bundler), str(context["entry"]), str(bundle)]
    imported_design = project / "ui/generated/main.materialized.json"
    if imported_design.is_file():
        bundle_command.append(str(imported_design))
    run_checked(bundle_command)
    executable = executable_dir / context["slug"]
    shutil.copy2(host, executable)
    executable.chmod(0o755)
    framework_names = copy_frameworks(sdk, frameworks)
    component_modules = build_component_modules(context, sdk, component_directory)
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": context["display_name"],
        "CFBundleExecutable": context["slug"],
        "CFBundleIdentifier": context["application_id"],
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": context["display_name"],
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": context["version"],
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
        "VellumPersistence": context["capabilities"]["persistence"],
    }
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(info, output, sort_keys=True)
    if app.exists():
        shutil.rmtree(app)
    os.replace(staging, app)
    installed_components = [
        {"id": item["id"], "path": str(app / "Contents/PlugIns/VellumComponents" / f"{item['id']}.dylib")}
        for item in component_modules
    ]
    return {
        "app": app,
        "bundle": app / "Contents/Resources/app.js",
        "executable": app / f"Contents/MacOS/{context['slug']}",
        "frameworks": framework_names,
        "components": installed_components,
    }


def ensure_app(context: dict[str, Any], sdk: Path, no_build: bool = False) -> dict[str, Any]:
    app = context["root"] / ".vellum/build/macos" / f"{context['slug']}.app"
    if no_build:
        executable = app / f"Contents/MacOS/{context['slug']}"
        bundle = app / "Contents/Resources/app.js"
        if not executable.is_file() or not bundle.is_file():
            raise BackendFailure("No built app exists; run vellum build first", status="build_missing")
        components = [{
            "id": item["id"],
            "path": str(app / "Contents/PlugIns/VellumComponents" / f"{item['id']}.dylib"),
        } for item in context["components"]]
        if any(not Path(item["path"]).is_file() for item in components):
            raise BackendFailure("Built app is missing a declared custom component module", status="build_missing")
        return {
            "app": app, "bundle": bundle, "executable": executable,
            "frameworks": [], "components": components,
        }
    return build_app(context, sdk)


def scenario_arguments(context: dict[str, Any], scenario_value: str | None) -> tuple[list[str], str]:
    project: Path = context["root"]
    name = scenario_value or "smoke"
    relative = name if name.endswith(".json") or "/" in name else f"tests/scenarios/{name}.json"
    scenario_path = safe_relative(project, relative, "scenario")
    scenario = load_json(scenario_path, SCENARIO_SCHEMA, "scenario")
    if set(scenario) - {"schema", "name", "viewport", "steps"}:
        raise BackendFailure("Scenario contains unknown fields", status="invalid_scenario")
    def bounded_text(value: object, limit: int) -> bool:
        if not isinstance(value, str) or not value or "\0" in value:
            return False
        try:
            return len(value.encode("utf-8")) <= limit
        except UnicodeEncodeError:
            return False

    scenario_name = scenario.get("name", name)
    if not bounded_text(scenario_name, 256):
        raise BackendFailure("Scenario name is invalid", status="invalid_scenario")
    arguments: list[str] = []
    viewport = scenario.get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"width", "height"}:
        raise BackendFailure("Scenario viewport is required", status="invalid_scenario")
    for field, option in (("width", "--expect-width"), ("height", "--expect-height")):
        value = viewport.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > 16384:
            raise BackendFailure(f"Scenario viewport {field} is invalid", status="invalid_scenario")
        arguments.extend([option, str(value)])
    steps = scenario.get("steps")
    if not isinstance(steps, list) or len(steps) > MAX_SCENARIO_STEPS:
        raise BackendFailure("Scenario steps must be an array of at most 1000 actions", status="invalid_scenario")

    def target_at(step: dict[str, Any], index: int, allowed: set[str]) -> str:
        if set(step) not in ({"action", "target"}, {"action", "id"}) or not set(step) <= allowed:
            raise BackendFailure(f"Scenario step {index} has unexpected fields", status="invalid_scenario")
        target = step.get("target") or step.get("id")
        if not bounded_text(target, MAX_SCENARIO_TARGET_BYTES):
            raise BackendFailure(f"Scenario step {index} has an invalid target", status="invalid_scenario")
        return target

    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise BackendFailure(f"Scenario step {index} is invalid", status="invalid_scenario")
        action = step["action"]
        if action == "wait-for-idle":
            if set(step) != {"action"}:
                raise BackendFailure(f"Scenario step {index} has unexpected fields", status="invalid_scenario")
            continue
        if action == "capture":
            if set(step) not in ({"action"}, {"action", "name"}) or \
                    ("name" in step and (not isinstance(step["name"], str) or not step["name"])):
                raise BackendFailure(f"Scenario step {index} has invalid capture fields", status="invalid_scenario")
            continue
        if action in {"press", "click"}:
            target = target_at(step, index, {"action", "target", "id"})
            arguments.extend(["--press", target])
            continue
        if action == "input":
            if set(step) != {"action", "target", "text"}:
                raise BackendFailure(f"Scenario step {index} has invalid input fields", status="invalid_scenario")
            target = step["target"]
            text = step["text"]
            if not bounded_text(target, MAX_SCENARIO_TARGET_BYTES) or \
                    not isinstance(text, str) or "\0" in text:
                raise BackendFailure(f"Scenario step {index} has an invalid input payload", status="invalid_scenario")
            try:
                input_size = len(text.encode("utf-8"))
            except UnicodeEncodeError:
                input_size = MAX_SCENARIO_INPUT_BYTES + 1
            if input_size > MAX_SCENARIO_INPUT_BYTES:
                raise BackendFailure(f"Scenario step {index} has an invalid input payload", status="invalid_scenario")
            arguments.extend(["--input", target, text])
            continue
        if action == "key":
            if set(step) != {"action", "target", "key"}:
                raise BackendFailure(f"Scenario step {index} has invalid key fields", status="invalid_scenario")
            target = step["target"]
            key = step["key"]
            if not bounded_text(target, MAX_SCENARIO_TARGET_BYTES) or \
                    not isinstance(key, str) or key not in SUPPORTED_SCENARIO_KEYS:
                raise BackendFailure(f"Scenario step {index} has an invalid key payload", status="invalid_scenario")
            arguments.extend(["--key", target, key])
            continue
        raise BackendFailure(f"Unsupported scenario action: {action}", status="unsupported_scenario_action")
    return arguments, scenario_name


def parse_hex_color(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", value):
        raise BackendFailure("Capture matrix background must be #RRGGBB or #RRGGBBAA", status="invalid_capture_matrix")
    encoded = value[1:]
    channels = tuple(int(encoded[index:index + 2], 16) for index in range(0, len(encoded), 2))
    return (*channels, 255) if len(channels) == 3 else channels  # type: ignore[return-value]


def capture_matrix(context: dict[str, Any], matrix_value: str) -> dict[str, Any]:
    path = safe_relative(context["root"], matrix_value, "capture matrix")
    matrix = load_json(path, CAPTURE_MATRIX_SCHEMA, "capture_matrix")
    if set(matrix) - {"schema", "captures", "columns", "gap", "background"}:
        raise BackendFailure("Capture matrix contains unknown fields", status="invalid_capture_matrix")
    captures = matrix.get("captures")
    if not isinstance(captures, list) or not 1 <= len(captures) <= 100:
        raise BackendFailure("Capture matrix must contain 1-100 captures", status="invalid_capture_matrix")
    normalized: list[dict[str, str]] = []
    names: set[str] = set()
    for index, item in enumerate(captures):
        if not isinstance(item, dict) or set(item) != {"name", "scenario"}:
            raise BackendFailure(f"Capture matrix item {index} is malformed", status="invalid_capture_matrix")
        name = item.get("name")
        scenario = item.get("scenario")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]{0,63})", name):
            raise BackendFailure(f"Capture matrix item {index} has an invalid name", status="invalid_capture_matrix")
        if name in names:
            raise BackendFailure(f"Capture matrix name is duplicated: {name}", status="invalid_capture_matrix")
        if not isinstance(scenario, str) or not scenario or "\0" in scenario:
            raise BackendFailure(f"Capture matrix item {index} has an invalid scenario", status="invalid_capture_matrix")
        names.add(name)
        normalized.append({"name": name, "scenario": scenario})
    columns = matrix.get("columns")
    gap = matrix.get("gap", 16)
    if columns is not None and (isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= len(normalized)):
        raise BackendFailure("Capture matrix columns are invalid", status="invalid_capture_matrix")
    if isinstance(gap, bool) or not isinstance(gap, int) or not 0 <= gap <= 1024:
        raise BackendFailure("Capture matrix gap is invalid", status="invalid_capture_matrix")
    background = parse_hex_color(matrix.get("background", "#181A20"))
    return {
        "path": path, "captures": normalized, "columns": columns,
        "gap": gap, "background": background,
    }


def execute_host(app: dict[str, Any], arguments: list[str]) -> subprocess.CompletedProcess[str]:
    components = [
        argument
        for item in app.get("components", [])
        for argument in ("--component", f"{item['id']}={item['path']}")
    ]
    return run_checked([str(app["executable"]), "--self-test", *components, *arguments])


def command_result(command: str, args: argparse.Namespace, context: dict[str, Any], sdk: Path) -> dict[str, Any]:
    require_target(getattr(args, "target", SUPPORTED_TARGET))
    if command == "build":
        app = build_app(context, sdk)
        return result(command, ok=True, status="built", message=f"Built {app['app']}", data={
            "target": SUPPORTED_TARGET,
            "app": str(app["app"]),
            "bundle": str(app["bundle"]),
            "frameworks": app["frameworks"],
            "components": [item["id"] for item in app["components"]],
        })
    if command == "run":
        app = ensure_app(context, sdk, args.no_build)
        if args.self_test or args.no_window:
            completed = execute_host(app, [])
            return result(command, ok=True, status="self_test_passed", message="Native app self-test passed", data={
                "target": SUPPORTED_TARGET, "app": str(app["app"]), "host_output": completed.stdout.strip(),
            })
        run_checked(["open", str(app["app"])])
        return result(command, ok=True, status="launched", message=f"Launched {app['app']}", data={
            "target": SUPPORTED_TARGET, "app": str(app["app"]),
        })
    if command == "test":
        app = ensure_app(context, sdk)
        scenario_args, scenario_name = scenario_arguments(context, args.scenario)
        completed = execute_host(app, scenario_args)
        return result(command, ok=True, status="tests_passed", message=f"Scenario '{scenario_name}' passed", data={
            "target": SUPPORTED_TARGET, "scenario": scenario_name,
            "app": str(app["app"]), "host_output": completed.stdout.strip(),
        })
    if command == "capture":
        app = ensure_app(context, sdk)
        if args.matrix:
            if args.scenario:
                raise BackendFailure("--matrix cannot be combined with --scenario", status="invalid_arguments", exit_code=2)
            matrix = capture_matrix(context, args.matrix)
            if args.montage:
                montage_output = output_path(
                    context["root"], args.output, "artifacts/montage.png", "montage output"
                )
                if montage_output.suffix.lower() != ".png":
                    raise BackendFailure("Montage output must end in .png", status="invalid_arguments", exit_code=2)
                capture_directory = montage_output.parent / f"{montage_output.stem}-captures"
            else:
                capture_directory = output_path(
                    context["root"], args.output, "artifacts/captures", "capture output"
                )
                if capture_directory.suffix.lower() == ".png":
                    raise BackendFailure("Matrix output without --montage must be a directory", status="invalid_arguments", exit_code=2)
                montage_output = None
            results: list[dict[str, Any]] = []
            images = []
            capture_directory.mkdir(parents=True, exist_ok=True)
            for item in matrix["captures"]:
                scenario_args, scenario_name = scenario_arguments(context, item["scenario"])
                output = capture_directory / f"{item['name']}.png"
                output.unlink(missing_ok=True)
                completed = execute_host(app, [*scenario_args, "--capture", str(output)])
                if not output.is_file():
                    raise BackendFailure("Native host did not produce a matrix capture", status="capture_failed")
                try:
                    image = read_png(output)
                except PngError as error:
                    raise BackendFailure(f"Matrix capture is not a supported PNG: {error}", status="capture_failed") from error
                images.append(image)
                results.append({
                    "name": item["name"], "scenario": scenario_name,
                    "output": str(output), "width": image.width, "height": image.height,
                    "bytes": output.stat().st_size, "host_output": completed.stdout.strip(),
                })
            montage_data = None
            if montage_output is not None:
                try:
                    composed = montage(
                        images, columns=matrix["columns"], gap=matrix["gap"],
                        background=matrix["background"],
                    )
                    write_png(montage_output, composed)
                except PngError as error:
                    raise BackendFailure(f"Could not compose capture montage: {error}", status="capture_failed") from error
                montage_data = {
                    "output": str(montage_output), "width": composed.width,
                    "height": composed.height, "bytes": montage_output.stat().st_size,
                }
            return result(command, ok=True, status="captured", message=f"Captured {len(results)} matrix views", data={
                "target": SUPPORTED_TARGET, "matrix": str(matrix["path"]),
                "captures": results, "montage": montage_data,
            })
        if args.montage:
            raise BackendFailure("--montage requires --matrix", status="invalid_arguments", exit_code=2)
        scenario_args, scenario_name = scenario_arguments(context, args.scenario)
        output = output_path(context["root"], args.output, f"artifacts/{scenario_name}.png", "capture output")
        if output.suffix.lower() != ".png":
            raise BackendFailure("Capture output must end in .png", status="invalid_arguments", exit_code=2)
        completed = execute_host(app, [*scenario_args, "--capture", str(output)])
        if not output.is_file():
            raise BackendFailure("Native host did not produce the requested capture", status="capture_failed")
        return result(command, ok=True, status="captured", message=f"Captured {output}", data={
            "target": SUPPORTED_TARGET, "scenario": scenario_name,
            "output": str(output), "bytes": output.stat().st_size,
            "host_output": completed.stdout.strip(),
        })
    if command == "package":
        app = ensure_app(context, sdk)
        output_dir = output_path(context["root"], args.output, "dist", "package output")
        output_dir.mkdir(parents=True, exist_ok=True)
        packaged = output_dir / app["app"].name
        if packaged.exists():
            raise BackendFailure(f"Package destination already exists: {packaged}", status="destination_not_empty")
        shutil.copytree(app["app"], packaged)
        if shutil.which("codesign"):
            run_checked(["codesign", "--force", "--deep", "--sign", "-", str(packaged)])
        return result(command, ok=True, status="packaged", message=f"Packaged {packaged}", data={
            "target": SUPPORTED_TARGET, "format": "app", "output": str(packaged),
        })
    raise BackendFailure(f"Unsupported native command: {command}", status="unsupported_command", exit_code=2)


def parser(command: str) -> argparse.ArgumentParser:
    value = BackendArgumentParser(add_help=False)
    value.add_argument("--project", required=True)
    value.add_argument("--json", action="store_true", required=True)
    if command in {"build", "run", "test", "capture", "package"}:
        value.add_argument("--target", default=SUPPORTED_TARGET)
    if command == "run":
        value.add_argument("--no-build", action="store_true")
        value.add_argument("--self-test", action="store_true")
        value.add_argument("--no-window", action="store_true")
    if command in {"test", "capture"}:
        value.add_argument("--scenario")
    if command in {"capture", "package"}:
        value.add_argument("--output")
    if command == "capture":
        value.add_argument("--matrix")
        value.add_argument("--montage", action="store_true")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    command = raw.pop(0) if raw else ""
    try:
        if command not in {"build", "run", "test", "capture", "package"}:
            raise BackendFailure(f"Unsupported native command: {command}", status="unsupported_command", exit_code=2)
        args = parser(command).parse_args(raw)
        context = project_context(args.project)
        payload = command_result(command, args, context, sdk_root())
        emit(payload)
        return 0
    except BackendFailure as error:
        emit(result(command or "unknown", ok=False, status=error.status, message=str(error)))
        return error.exit_code
    except (OSError, ValueError) as error:
        emit(result(command or "unknown", ok=False, status="backend_error", message=str(error)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
