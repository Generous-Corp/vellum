#!/usr/bin/env python3
"""Installed exact-pin static web application backend for Vellum projects."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Any, Iterable
from urllib.parse import quote, unquote_to_bytes

from vellum_manifest import (
    LOCK_NAME, LOCK_SCHEMA, ManifestError, imported_materialized_design,
    load_app_manifest, load_components_manifest,
)
from vellum_scenario import (
    ScenarioValidationError,
    validate_scenario_document as validate_shared_scenario_document,
)
from vellum_cdp import CdpAdmission
from vellum_cdp_client import CdpClient, CdpClientError
from vellum_interaction import InteractionPlanError, validate_interaction_plan
from vellum_browser import browser_version, configured_provenance


RESULT_SCHEMA = "vellum.backend.result.v1"
SUPPORTED_TARGET = "web"
WEB_COMMANDS = {"build", "run", "test", "package"}
PRIVATE_VELLUM_INCLUDE = re.compile(
    r'^\s*#\s*include\s*[<"](vellum/[^>"]+)[>"]', re.MULTILINE,
)
DATA_URL_RE = re.compile(r"data:[^)\s\"'<>]+,[^)\s\"'<>]+", re.IGNORECASE)
DATA_URL_MIME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
DATA_URL_EXTENSIONS = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
    "image/svg+xml": "svg", "image/webp": "webp", "font/ttf": "ttf",
    "font/otf": "otf",
}
MAX_CAPTURE_NODES = 8192
MAX_CAPTURE_ASSETS = 128
MAX_CAPTURE_ASSET_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_ATTRIBUTE_BYTES = 64 * 1024
CAPTURE_STYLE_NAMES = (
    "display", "visibility", "color", "font-size", "background-image",
    "list-style-image", "content",
)


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
    try:
        components = load_components_manifest(
            project, manifest["native"]["components_manifest"]
        )
    except ManifestError as error:
        raise BackendFailure(str(error), status="invalid_component_manifest") from error
    return {
        "root": project, "lock": lock, "manifest": manifest, "entry": entry,
        "slug": slug, "components": components,
    }


def run_checked(
    arguments: list[str], *, cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
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


def discover_emxx() -> Path:
    candidates = [
        Path(os.environ["EMSDK"]) / "upstream/emscripten/em++"
        if os.environ.get("EMSDK") else None,
        Path.home() / "emsdk/upstream/emscripten/em++",
        Path(shutil.which("em++")) if shutil.which("em++") else None,
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise BackendFailure(
        "em++ is required for declared web Wasm custom components; activate emsdk",
        status="prerequisite_missing", exit_code=4,
    )


def emxx_command(emxx: Path) -> list[str]:
    driver = emxx.with_name("em++.py")
    if not driver.is_file():
        return [str(emxx)]
    candidates = [
        Path(sys.executable), Path("/opt/homebrew/bin/python3"),
        Path("/usr/local/bin/python3"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        completed = subprocess.run(
            [str(candidate), "-c", "import sys; print(int(sys.version_info >= (3, 10)))"],
            text=True, capture_output=True, check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip() == "1":
            return [str(candidate), str(driver)]
    raise BackendFailure(
        "Emscripten requires Python 3.10 or newer",
        status="prerequisite_missing", exit_code=4,
    )


def validate_component_source(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BackendFailure(
            f"Cannot read custom component source {path}: {error}",
            status="invalid_component_source",
        ) from error
    if len(content.encode("utf-8")) > 4 * 1024 * 1024:
        raise BackendFailure(
            "Custom component source exceeds 4 MiB",
            status="invalid_component_source",
        )
    framework_includes = PRIVATE_VELLUM_INCLUDE.findall(content)
    forbidden = sorted(set(framework_includes) - {"vellum/components/abi.h"})
    if forbidden:
        raise BackendFailure(
            "Web custom components may use only the public "
            "vellum/components/abi.h framework header; "
            f"forbidden includes: {forbidden}",
            status="private_component_api",
        )


def build_component_modules(
    context: dict[str, Any], sdk: Path, destination: Path,
) -> list[dict[str, str]]:
    declarations = context["components"]
    output: list[dict[str, str]] = []
    wasm_declarations = [item for item in declarations if item["web"] == "wasm"]
    if wasm_declarations:
        metadata = load_json(sdk / "metadata.json", "vellum.sdk-artifact.v1", "sdk_metadata")
        if metadata.get("capabilities", {}).get("custom_components") is not True:
            raise BackendFailure(
                "Installed SDK does not provide the custom component ABI",
                status="capability_unavailable", exit_code=4,
            )
        include_root = sdk / "sdk/include"
        abi = include_root / "vellum/components/abi.h"
        adapter = sdk / "web/browser_component_adapter.cpp"
        if not abi.is_file() or not adapter.is_file():
            raise BackendFailure(
                "Installed SDK is missing browser custom-component support",
                status="invalid_sdk",
            )
        emxx = discover_emxx()
        exported = (
            "['_vellum_component_web_start','_vellum_component_web_render',"
            "'_vellum_component_web_command_count','_vellum_component_web_command_kind',"
            "'_vellum_component_web_command_suffix','_vellum_component_web_command_number',"
            "'_vellum_component_web_command_text','_vellum_component_web_error']"
        )
        for declaration in wasm_declarations:
            source = safe_relative(
                context["root"], declaration["wasm_source"],
                f"custom component {declaration['id']} Wasm source",
            )
            validate_component_source(source)
            javascript_name = f"vellum_component_{declaration['id']}.js"
            javascript = destination / javascript_name
            run_checked([
                *emxx_command(emxx), "-std=c++20", "-O2", "-sWASM=1",
                "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sENVIRONMENT=web,node",
                "-sFILESYSTEM=0", "-sASSERTIONS=1",
                f"-sEXPORTED_FUNCTIONS={exported}",
                "-sEXPORTED_RUNTIME_METHODS=['cwrap']",
                "-I", str(include_root), str(adapter), str(source),
                "-o", str(javascript),
            ], cwd=context["root"])
            wasm = javascript.with_suffix(".wasm")
            if not javascript.is_file() or not wasm.is_file():
                raise BackendFailure(
                    f"Emscripten omitted the component payload for {declaration['id']}",
                    status="tool_failed",
                )
            output.append({
                "id": declaration["id"], "web": "wasm",
                "module": javascript.name, "wasm": wasm.name,
            })
    for declaration in declarations:
        if declaration["web"] == "fallback":
            output.append({"id": declaration["id"], "web": "fallback"})
    return sorted(output, key=lambda item: item["id"])


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


def contains_sdk_install_path(payload: bytes, install_prefix: Path) -> bool:
    markers = {
        str(install_prefix).encode(),
        str(install_prefix.resolve()).encode(),
        b"/vellum-installs/",
        b"\\vellum-installs\\",
        # Source maps percent-encode a source that resolves outside both the
        # project and the SDK package, so the separators disappear and the
        # plain markers above cannot see the path.
        b"%2Fvellum-installs%2F",
        b"%2fvellum-installs%2f",
    }
    return any(marker in payload for marker in markers)


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
    run_checked(
        command, cwd=project,
        env={**os.environ, "VELLUM_BUILD_FORMAT": "esm"},
    )
    source_map = bundle.with_suffix(f"{bundle.suffix}.map")
    if not source_map.is_file():
        raise BackendFailure(
            "Installed UI bundler omitted the required application source map",
            status="tool_failed",
        )
    for name in (
        "vellum_web_core.js", "vellum_web_core.wasm", "vellum_host.js",
        "text_semantics.js", "style.css",
    ):
        shutil.copy2(sdk / "web" / name, staging / name)
    components = build_component_modules(context, sdk, staging)
    (staging / "vellum_components.json").write_text(json.dumps({
        "schema": "vellum.web-components.v1",
        "components": components,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    index = (sdk / "web/index.html").read_text(encoding="utf-8").replace(
        "{{APP_NAME}}", html.escape(context["manifest"]["app"]["name"], quote=True)
    )
    (staging / "index.html").write_text(index, encoding="utf-8")
    detector = sdk / "web/check_wasm_no_engine.py"
    run_checked([sys.executable, str(detector), str(staging / "vellum_web_core.wasm")])
    run_checked([sys.executable, str(detector), "--negative-control"])
    install_prefix = sdk.parent.parent.parent
    for path in staging.iterdir():
        if path.is_file() and contains_sdk_install_path(
            path.read_bytes(), install_prefix,
        ):
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
    return {
        "root": destination, "bundle": bundle.name,
        "files": sorted(path.name for path in destination.iterdir()),
        "components": components,
    }


def ensure_build(context: dict[str, Any], sdk: Path, no_build: bool = False) -> dict[str, Any]:
    root = context["root"] / ".vellum/build/web"
    if no_build:
        if not (root / "build-manifest.json").is_file():
            raise BackendFailure("No prior web build exists", status="build_missing")
        component_manifest = load_json(
            root / "vellum_components.json",
            "vellum.web-components.v1",
            "web_components",
        )
        components = component_manifest.get("components")
        if not isinstance(components, list):
            raise BackendFailure(
                "Built web component manifest is malformed",
                status="build_missing",
            )
        return {
            "root": root, "bundle": "app.js",
            "files": sorted(path.name for path in root.iterdir()),
            "components": components,
        }
    return build_app(context, sdk)


def chrome_path() -> str:
    # A pinned browser is named through the environment, the same way the
    # web/tests harnesses accept it. Reject an override that does not resolve
    # rather than falling back to an installed browser: silently running a
    # different build would defeat the pin the caller asked for.
    override = os.environ.get("VELLUM_CHROME_PATH") or None
    if override is not None:
        candidate = Path(override)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise BackendFailure(
                f"VELLUM_CHROME_PATH does not name an executable browser: {override}",
                status="prerequisite_missing", exit_code=4,
            )
        if os.environ.get("VELLUM_REQUIRE_CHROME_PROVENANCE") == "1":
            valid, detail = configured_provenance(candidate)
            if not valid:
                raise BackendFailure(
                    f"Pinned Chrome lacks valid provenance: {detail}",
                    status="prerequisite_missing", exit_code=4,
                )
        return str(candidate)
    chrome = shutil.which("google-chrome")
    application = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome and application.is_file():
        chrome = str(application)
    if not chrome:
        raise BackendFailure("Google Chrome is required for web scenarios", status="prerequisite_missing", exit_code=4)
    if os.environ.get("VELLUM_REQUIRE_CHROME_PROVENANCE") == "1":
        valid, detail = configured_provenance(Path(chrome))
        if not valid:
            raise BackendFailure(
                f"Chrome lacks valid provenance: {detail}",
                status="prerequisite_missing", exit_code=4,
            )
    return chrome


def validate_scenario_evidence(
    evidence: dict[str, Any], *, expected_wasm_ids: list[str] | None = None
) -> None:
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
    inputs = evidence.get("inputs")
    keys = evidence.get("keys")
    components = evidence.get("components")
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
        or not isinstance(inputs, list)
        or not all(isinstance(item, dict) and item.get("executed") is True for item in inputs)
        or not isinstance(keys, list)
        or not all(isinstance(item, dict) and item.get("executed") is True for item in keys)
        or not isinstance(components, list)
    ):
        raise BackendFailure(
            f"Browser scenario proof failed: {evidence}",
            status="test_failed",
        )
    expected = sorted(expected_wasm_ids or [])
    observed: list[str] = []
    for item in components:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or item.get("loaded") is not True
            or not isinstance(item.get("renders"), int)
            or isinstance(item.get("renders"), bool)
            or item["renders"] < 0
            or not isinstance(item.get("commands"), int)
            or isinstance(item.get("commands"), bool)
            or item["commands"] < 0
        ):
            raise BackendFailure(
                f"Browser component proof failed: {components}",
                status="test_failed",
            )
        observed.append(item["id"])
    if sorted(observed) != expected or len(observed) != len(set(observed)):
        raise BackendFailure(
            f"Browser component inventory differs: expected={expected} observed={observed}",
            status="test_failed",
        )


def _snapshot_string(snapshot: dict[str, Any], value: object, label: str) -> str:
    strings = snapshot.get("strings")
    if not isinstance(strings, list):
        raise BackendFailure("DOMSnapshot omitted its string table", status="test_failed")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(strings):
        raise BackendFailure(f"DOMSnapshot {label} has an invalid string index", status="test_failed")
    text = strings[value]
    if not isinstance(text, str) or "\0" in text or len(text.encode("utf-8")) > MAX_CAPTURE_ATTRIBUTE_BYTES:
        raise BackendFailure(f"DOMSnapshot {label} contains an invalid bounded string", status="test_failed")
    return text


def _decode_capture_data_url(value: str) -> tuple[str, bytes]:
    if not value.startswith("data:"):
        raise ValueError("not a data URL")
    header, payload = value[5:].split(",", 1)
    parts = header.split(";")
    mime = parts[0].lower()
    if not DATA_URL_MIME_RE.fullmatch(mime):
        raise ValueError("data URL has an invalid MIME type")
    is_base64 = any(part.lower() == "base64" for part in parts[1:])
    if is_base64:
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("data URL has malformed base64") from error
    else:
        try:
            decoded = unquote_to_bytes(payload)
        except (ValueError, UnicodeError) as error:
            raise ValueError("data URL has malformed percent encoding") from error
    if len(decoded) > MAX_CAPTURE_ASSET_BYTES:
        raise ValueError("data URL payload is oversized")
    return mime, decoded


def _localize_snapshot_data_urls(snapshot: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], int]:
    """Extract only bounded data URLs from the browser-owned string table."""
    strings = snapshot.get("strings")
    if not isinstance(strings, list) or len(strings) > MAX_CAPTURE_NODES * 16:
        raise BackendFailure("DOMSnapshot string table is missing or oversized", status="test_failed")
    records: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    observed_urls = 0
    for text in strings:
        if not isinstance(text, str):
            raise BackendFailure("DOMSnapshot string table contains a non-string", status="test_failed")
        for match in DATA_URL_RE.finditer(text):
            observed_urls += 1
            if observed_urls > MAX_CAPTURE_ASSETS * 4:
                raise BackendFailure("browser capture contains too many data URLs", status="test_failed")
            raw = match.group(0)
            try:
                mime, payload = _decode_capture_data_url(raw)
            except ValueError as error:
                raise BackendFailure(str(error), status="test_failed") from error
            digest = hashlib.sha256(payload).hexdigest()
            content_hash = f"sha256:{digest}"
            if content_hash in records:
                continue
            total_bytes += len(payload)
            if total_bytes > MAX_CAPTURE_ASSET_BYTES:
                raise BackendFailure("localized browser assets exceed the bounded capture budget", status="test_failed")
            extension = DATA_URL_EXTENSIONS.get(mime, "bin")
            uri = f"assets/{digest}.{extension}"
            records[content_hash] = {
                "asset": {
                    "id": f"data-{digest[:12]}",
                    "contentHash": content_hash,
                    "uri": uri,
                    "mimeType": mime,
                    "byteLength": len(payload),
                },
                "bytes": base64.b64encode(payload).decode("ascii"),
                "raw": raw,
            }
    return records, observed_urls


def _replace_data_urls(value: str, records: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    references: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            _, payload = _decode_capture_data_url(raw)
        except ValueError as error:
            raise BackendFailure(str(error), status="test_failed") from error
        content_hash = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        record = records.get(content_hash)
        if record is None:
            raise BackendFailure("DOMSnapshot data URL was not localized", status="test_failed")
        uri = record["asset"]["uri"]
        references.append(uri)
        return uri

    return DATA_URL_RE.sub(replace, value), references


def _snapshot_node_value(snapshot: dict[str, Any], nodes: dict[str, Any], key: str, index: int) -> str:
    values = nodes.get(key)
    if not isinstance(values, list) or index >= len(values):
        return ""
    value = values[index]
    return _snapshot_string(snapshot, value, f"{key}[{index}]") if value is not None else ""


def lower_dom_snapshot(
    snapshot: dict[str, Any], *, settled_snapshot: dict[str, Any], screenshot: dict[str, Any],
    interaction_evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Lower Chrome's bounded DOMSnapshot into the browser capture source model."""
    documents = snapshot.get("documents")
    if not isinstance(documents, list) or len(documents) != 1 or not isinstance(documents[0], dict):
        raise BackendFailure("DOMSnapshot must contain exactly one document", status="test_failed")
    document = documents[0]
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        raise BackendFailure("DOMSnapshot document omitted nodes", status="test_failed")
    node_types = nodes.get("nodeType")
    parents = nodes.get("parentIndex")
    if not isinstance(node_types, list) or not isinstance(parents, list) or len(node_types) != len(parents):
        raise BackendFailure("DOMSnapshot node arrays are malformed", status="test_failed")
    if not node_types or len(node_types) > MAX_CAPTURE_NODES:
        raise BackendFailure("DOMSnapshot node count is outside the bounded range", status="test_failed")
    records, observed_urls = _localize_snapshot_data_urls(snapshot)
    source_nodes: list[dict[str, Any] | None] = [None] * len(node_types)
    included: set[int] = set()
    for index, node_type in enumerate(node_types):
        if node_type not in {1, 3, 9, 11}:
            continue
        name = _snapshot_node_value(snapshot, nodes, "nodeName", index)
        value = _snapshot_node_value(snapshot, nodes, "nodeValue", index)
        properties: dict[str, Any] = {"domNodeIndex": index}
        if node_type == 1:
            properties["tagName"] = name.lower()
            attributes = nodes.get("attributes")
            attr_values = attributes[index] if isinstance(attributes, list) and index < len(attributes) else []
            if not isinstance(attr_values, list) or len(attr_values) % 2:
                raise BackendFailure("DOMSnapshot attributes are malformed", status="test_failed")
            attr_map: dict[str, str] = {}
            refs: list[str] = []
            for pair in range(0, len(attr_values), 2):
                key = _snapshot_string(snapshot, attr_values[pair], f"attribute name[{index}]")
                attr = _snapshot_string(snapshot, attr_values[pair + 1], f"attribute value[{index}]")
                replaced, found = _replace_data_urls(attr, records)
                attr_map[key] = replaced
                refs.extend(found)
            properties["attributes"] = attr_map
            if refs:
                properties["assetRefs"] = sorted(set(refs))
        elif node_type == 3 and value:
            properties["domNodeIndex"] = index
        raw: dict[str, Any] = {
            "sourceId": f"dom-{index}",
            "kind": "text" if node_type == 3 else "view",
            "name": name.lower() if name else ("Document" if node_type == 9 else "node"),
            "properties": properties,
            "children": [],
        }
        if node_type == 3 and value:
            raw["text"] = value[:MAX_CAPTURE_ATTRIBUTE_BYTES]
        if node_type == 1:
            role = properties["attributes"].get("role") or properties["attributes"].get("aria-role")
            semantic_id = properties["attributes"].get("data-vellum-id")
            if isinstance(role, str) and role:
                raw["role"] = role[:256]
            if isinstance(semantic_id, str) and semantic_id:
                raw["semanticId"] = semantic_id[:256]
                # An explicit application identity is more durable than the
                # transient DOM index and therefore takes the canonical
                # semantic-identity path.
                raw.pop("sourceId", None)
            else:
                raw["sourceId"] = f"dom-{index}"
        source_nodes[index] = raw
        included.add(index)
    layout = document.get("layout")
    if isinstance(layout, dict):
        layout_indices = layout.get("nodeIndex")
        layout_styles = layout.get("styles")
        if layout_indices is not None or layout_styles is not None:
            if not isinstance(layout_indices, list) or not isinstance(layout_styles, list) or len(layout_indices) != len(layout_styles):
                raise BackendFailure("DOMSnapshot layout arrays are malformed", status="test_failed")
            for row_index, node_index in enumerate(layout_indices):
                if not isinstance(node_index, int) or node_index not in included:
                    raise BackendFailure("DOMSnapshot layout references an invalid node", status="test_failed")
                row = layout_styles[row_index]
                if not isinstance(row, list) or len(row) > len(CAPTURE_STYLE_NAMES):
                    raise BackendFailure("DOMSnapshot computed styles are malformed", status="test_failed")
                computed: dict[str, str] = {}
                refs: list[str] = []
                for style_index, string_index in enumerate(row):
                    value = _snapshot_string(snapshot, string_index, f"computed style[{node_index}]")
                    replaced, found = _replace_data_urls(value, records)
                    computed[CAPTURE_STYLE_NAMES[style_index]] = replaced
                    refs.extend(found)
                source_nodes[node_index]["properties"]["computedStyles"] = computed  # type: ignore[index]
                if refs:
                    existing = source_nodes[node_index]["properties"].get("assetRefs", [])  # type: ignore[index]
                    source_nodes[node_index]["properties"]["assetRefs"] = sorted(set(existing + refs))  # type: ignore[index]
    if not included:
        raise BackendFailure("DOMSnapshot contained no lowerable nodes", status="test_failed")
    root_index = next((index for index in sorted(included) if node_types[index] == 9), min(included))
    for index in sorted(included):
        if index == root_index:
            continue
        parent = parents[index]
        while isinstance(parent, int) and parent >= 0 and parent not in included:
            parent = parents[parent] if parent < len(parents) else -1
        if not isinstance(parent, int) or parent < 0 or parent == index:
            parent = root_index
        source_nodes[parent]["children"].append(source_nodes[index])  # type: ignore[index]
    assets = [record["asset"] for record in records.values()]
    assets.sort(key=lambda item: item["id"])
    evidence = {
        "schema": "vellum.browser-capture-evidence.v1",
        "interactionEvidence": interaction_evidence or [],
        "domNodeCount": len(included),
        "observedDataUrls": observed_urls,
        "localizedAssets": [
            {**record["asset"], "data": record["bytes"]}
            for record in sorted(records.values(), key=lambda item: item["asset"]["id"])
        ],
        "settledSnapshot": settled_snapshot,
        "screenshot": screenshot,
    }
    root = source_nodes[root_index]
    root["properties"]["captureEvidence"] = evidence  # type: ignore[index]
    return root, assets, evidence


def run_chrome_scenario(
    url: str,
    received: Any,
    *,
    chrome: str | None = None,
    profile_factory: Any = tempfile.TemporaryDirectory,
    process_factory: Any = subprocess.Popen,
) -> None:
    with profile_factory(prefix="vellum-web-chrome-") as profile:
        if Path(profile).is_dir():
            os.chmod(profile, 0o700)
        with CdpAdmission(str(Path(profile) / "vellum-cdp.sock")) as cdp:
            process = process_factory([
                chrome or chrome_path(), "--headless=new", "--disable-gpu-sandbox",
                "--no-first-run", "--disable-background-networking",
                *cdp.chrome_arguments(),
                f"--user-data-dir={profile}", url,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                start_new_session=os.name == "posix")
            process_group = (
                process.pid
                if os.name == "posix" and isinstance(getattr(process, "pid", None), int)
                else None
            )
            try:
                if not received.wait(20):
                    raise BackendFailure(
                        "Browser scenario proof timed out",
                        status="test_failed",
                    )
            finally:
                stop_browser(process, process_group=process_group)


def run_chrome_interaction_capture(
    build: Path, plan: dict[str, Any], *, chrome: str | None = None,
    profile_factory: Any = tempfile.TemporaryDirectory,
    process_factory: Any = subprocess.Popen, entry: str = "index.html",
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a bounded CDP plan and retain its live capture envelope."""
    entry_path = safe_relative(build, entry, "capture entry")
    if not entry_path.is_file() or entry_path.is_symlink():
        raise BackendFailure("Capture entry is missing or not a regular file", status="invalid_project")
    entry_name = entry_path.relative_to(build.resolve()).as_posix()
    if source_metadata is not None:
        if not isinstance(source_metadata, dict):
            raise BackendFailure("Capture source metadata must be an object", status="invalid_project")
        unknown_metadata = set(source_metadata) - {
            "producer", "fingerprint", "preflightSchema", "dependencies",
        }
        if unknown_metadata:
            raise BackendFailure("Capture source metadata contains unknown fields", status="invalid_project")
    browser_executable = chrome or chrome_path()
    exact_browser_version = browser_version(Path(browser_executable))
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 lambda *args, **kwargs: SimpleHTTPRequestHandler(
                                     *args, directory=str(build), **kwargs))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with profile_factory(prefix="vellum-web-capture-") as profile:
            if Path(profile).is_dir():
                os.chmod(profile, 0o700)
            with CdpAdmission(str(Path(profile) / "vellum-cdp.sock")) as admission:
                process = process_factory([
                    browser_executable, "--headless=new", "--disable-gpu-sandbox",
                    "--no-first-run", "--disable-background-networking",
                    *admission.chrome_arguments(),
                    "--window-size=1280,720",
                    f"--user-data-dir={profile}",
                    f"http://127.0.0.1:{server.server_port}/{quote(entry_name, safe='/')}",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                    start_new_session=os.name == "posix")
                process_group = (
                    process.pid if os.name == "posix" and isinstance(getattr(process, "pid", None), int)
                    else None
                )
                client: CdpClient | None = None
                discovery: dict[str, Any] = {}
                try:
                    deadline = time.monotonic() + 20.0
                    last_error: Exception | None = None
                    while time.monotonic() < deadline:
                        candidate = CdpClient(admission.endpoint, timeout=2.0)
                        try:
                            discovery = candidate.discover()
                            candidate.connect()
                            client = candidate
                            break
                        except (CdpClientError, OSError) as error:
                            last_error = error
                            candidate.close()
                            time.sleep(0.05)
                    if client is None:
                        raise BackendFailure(
                            f"Browser CDP did not become ready: {last_error}",
                            status="test_failed",
                    )
                    client.wait_for_dom()
                    evidence = client.execute_interaction_plan(plan)
                    settled_snapshot = client.settle_idle()
                    screenshot = client.capture_screenshot()
                    viewport = client.viewport()
                    root, assets, capture_evidence = lower_dom_snapshot(
                        settled_snapshot,
                        settled_snapshot=settled_snapshot,
                        screenshot=screenshot,
                        interaction_evidence=evidence,
                    )
                    browser = discovery.get("Browser")
                    if not isinstance(browser, str) or not browser:
                        raise BackendFailure("Browser CDP discovery omitted exact browser identity", status="test_failed")
                    capture_id = hashlib.sha256(json.dumps({
                        "plan": plan,
                        "browser": browser,
                        "browserVersion": exact_browser_version,
                        "viewport": viewport,
                        "evidence": evidence,
                        "settledSnapshot": settled_snapshot,
                        "screenshot": screenshot,
                        "root": root,
                        "assets": assets,
                        "sourceMetadata": source_metadata or {},
                    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
                    root["properties"]["captureEvidence"]["captureId"] = capture_id
                    source = {
                        "url": f"http://127.0.0.1:{server.server_port}/{quote(entry_name, safe='/')}",
                        "browser": browser,
                        "browserVersion": exact_browser_version,
                        "plan": plan["name"],
                    }
                    if source_metadata:
                        source.update(source_metadata)
                    return {
                        "schema": "vellum.browser-capture-envelope.v1",
                        "captureId": capture_id,
                        "source": source,
                        "viewport": viewport,
                        "root": root,
                        "assets": assets, "diagnostics": [],
                    }
                finally:
                    if client is not None:
                        client.close()
                    stop_browser(process, process_group=process_group)
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def run_html_interaction_capture(
    source: Path, plan: dict[str, Any], *, chrome: str | None = None,
    profile_factory: Any = tempfile.TemporaryDirectory,
    process_factory: Any = subprocess.Popen,
) -> dict[str, Any]:
    """Stage and capture a raw HTML source through the isolated browser lane."""
    from vellum_html_source import stage_html_source

    with tempfile.TemporaryDirectory(prefix="vellum-html-source-") as temporary:
        staging = Path(temporary) / "tree"
        receipt = stage_html_source(source, staging)
        metadata = {
            "producer": receipt["producer"],
            "fingerprint": receipt["fingerprint"],
            "preflightSchema": receipt["schema"],
            "dependencies": receipt["dependencies"],
        }
        return run_chrome_interaction_capture(
            staging, plan, chrome=chrome, profile_factory=profile_factory,
            process_factory=process_factory, entry=str(receipt["entry"]),
            source_metadata=metadata,
        )


def stop_browser(
    process: subprocess.Popen[str], *, process_group: int | None = None,
    timeout: int = 5,
) -> None:
    """Stop the browser launcher and any helper retaining inherited resources."""
    if process_group is None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout)
        return

    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process_group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if process.poll() is None:
        process.wait(timeout)


def run_scenario(
    build: Path, scenario: Path, *, expected_wasm_ids: list[str] | None = None
) -> dict[str, Any]:
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
    validate_scenario_evidence(evidence, expected_wasm_ids=expected_wasm_ids)
    return evidence


def validate_scenario_document(scenario: dict[str, Any]) -> None:
    try:
        validate_shared_scenario_document(scenario)
    except ScenarioValidationError as error:
        status = (
            "unsupported_scenario_action"
            if str(error).startswith("Unsupported scenario action:")
            else "invalid_scenario"
        )
        raise BackendFailure(
            str(error), status=status
        ) from error


def scenario_path(context: dict[str, Any], value: str | None) -> Path:
    relative = value or "tests/scenarios/smoke.json"
    if not relative.endswith(".json") and "/" not in relative:
        relative = f"tests/scenarios/{relative}.json"
    path = safe_relative(context["root"], relative, "scenario")
    try:
        scenario = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackendFailure(
            f"Cannot read scenario at {path}: {error}",
            status="invalid_scenario",
        ) from error
    if not isinstance(scenario, dict):
        raise BackendFailure("Scenario must be an object", status="invalid_scenario")
    validate_scenario_document(scenario)
    return path


def interaction_plan_path(context: dict[str, Any], value: str | None) -> dict[str, Any]:
    path = safe_relative(context["root"], value, "interaction plan")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        return validate_interaction_plan(plan)
    except (OSError, json.JSONDecodeError, InteractionPlanError) as error:
        raise BackendFailure(f"Cannot read interaction plan at {path}: {error}", status="invalid_interaction_plan") from error


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
        if getattr(args, "interaction_plan", None):
            if args.scenario:
                raise BackendFailure("--interaction-plan cannot be combined with --scenario", status="invalid_arguments", exit_code=2)
            envelope = run_chrome_interaction_capture(
                built["root"], interaction_plan_path(context, args.interaction_plan),
            )
            return result(command, ok=True, status="capture_passed", message="Browser interaction capture passed",
                          data={"target": "web", "build": str(built["root"]), "capture": envelope})
        expected_wasm_ids = [
            item["id"] for item in built.get("components", [])
            if isinstance(item, dict) and item.get("web") == "wasm"
        ]
        evidence = run_scenario(
            built["root"], scenario_path(context, args.scenario),
            expected_wasm_ids=expected_wasm_ids,
        )
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
    if command == "test": value.add_argument("--interaction-plan")
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
