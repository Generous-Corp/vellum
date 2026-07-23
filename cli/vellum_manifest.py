#!/usr/bin/env python3
"""Strict, dependency-free reader for Vellum's small application manifest."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


APP_MANIFEST_NAME = "app.toml"
LOCK_NAME = "framework.lock"
LOCK_SCHEMA = "vellum.project-lock.v1"
SUPPORTED_DESKTOP_TARGET = "macos"
COMPONENT_MANIFEST_SCHEMA = "vellum.components.v1"
IMPORT_LOCK_SCHEMA = "vellum.design-import-lock.v1"
_COMPONENT_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")


class ManifestError(ValueError):
    pass


_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_IDENTIFIER = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")


def _without_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {'"', "'"}:
            quote = None if quote == character else character if quote is None else quote
            continue
        if character == "#" and quote is None:
            return line[:index]
    if quote is not None:
        raise ManifestError("unterminated string")
    return line


def _split_array(value: str) -> list[str]:
    body = value[1:-1].strip()
    if not body:
        return []
    items: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(body):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {'"', "'"}:
            quote = None if quote == character else character if quote is None else quote
            continue
        if character == "," and quote is None:
            items.append(body[start:index].strip())
            start = index + 1
    if quote is not None:
        raise ManifestError("unterminated array string")
    items.append(body[start:].strip())
    if any(not item for item in items):
        raise ManifestError("empty array item")
    return items


def _value(text: str) -> Any:
    text = text.strip()
    if text == "true":
        return True
    if text == "false":
        return False
    if text.startswith('"') and text.endswith('"'):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ManifestError(f"invalid string: {error.msg}") from error
        if not isinstance(value, str):
            raise ManifestError("manifest strings must decode to text")
        return value
    if text.startswith("'") and text.endswith("'") and "'" not in text[1:-1]:
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        values = [_value(item) for item in _split_array(text)]
        if any(not isinstance(item, str) for item in values):
            raise ManifestError("only string arrays are supported")
        return values
    raise ManifestError(f"unsupported value {text!r}; use a string, boolean, or string array")


def parse_app_toml(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    document: dict[str, dict[str, Any]] = {}
    section: str | None = None
    for number, original in enumerate(lines, start=1):
        try:
            line = _without_comment(original).strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                name = line[1:-1].strip()
                if not name or any(not _KEY.fullmatch(part) for part in name.split(".")):
                    raise ManifestError(f"invalid table name {name!r}")
                if name in document:
                    raise ManifestError(f"duplicate table [{name}]")
                document[name] = {}
                section = name
                continue
            if section is None or "=" not in line:
                raise ManifestError("key/value appears before a table or lacks '='")
            key, raw = (part.strip() for part in line.split("=", 1))
            if not _KEY.fullmatch(key):
                raise ManifestError(f"invalid key {key!r}")
            if key in document[section]:
                raise ManifestError(f"duplicate key {key!r} in [{section}]")
            document[section][key] = _value(raw)
        except ManifestError as error:
            raise ManifestError(f"{path}:{number}: {error}") from error
    return document


def _exact_keys(value: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ManifestError(f"[{label}] has " + "; ".join(details))


def _project_relative(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ManifestError(f"{label} must be a non-empty project-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{label} must remain inside the project")


def _project_file(project: Path, value: Any, label: str, suffixes: set[str]) -> str:
    _project_relative(value, label)
    relative = Path(value)
    if relative.suffix.lower() not in suffixes:
        raise ManifestError(f"{label} has an unsupported file type")
    root = project.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ManifestError(f"{label} escapes the project") from error
    if not resolved.is_file():
        raise ManifestError(f"{label} does not exist: {value}")
    return relative.as_posix()


def load_components_manifest(project: Path, relative_path: str) -> list[dict[str, Any]]:
    _project_relative(relative_path, "[native].components_manifest")
    root = project.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ManifestError("native components manifest escapes the project") from error
    raw = parse_app_toml(path)
    manifest = raw.get("manifest", {})
    _exact_keys(manifest, {"schema", "components"}, set(), "manifest")
    if manifest["schema"] != COMPONENT_MANIFEST_SCHEMA:
        raise ManifestError(f"[manifest].schema must be {COMPONENT_MANIFEST_SCHEMA}")
    identifiers = manifest["components"]
    if not isinstance(identifiers, list) or len(identifiers) > 32:
        raise ManifestError("[manifest].components must contain at most 32 identifiers")
    if len(set(identifiers)) != len(identifiers) or any(
        not isinstance(identifier, str) or not _COMPONENT_ID.fullmatch(identifier)
        for identifier in identifiers
    ):
        raise ManifestError("[manifest].components contains an invalid or duplicate identifier")
    expected_sections = {"manifest", *(f"component.{identifier}" for identifier in identifiers)}
    unknown_sections = sorted(set(raw) - expected_sections)
    missing_sections = sorted(expected_sections - set(raw))
    if unknown_sections or missing_sections:
        raise ManifestError(
            "component tables differ from the declaration: "
            f"missing={missing_sections} unknown={unknown_sections}"
        )
    output: list[dict[str, Any]] = []
    for identifier in identifiers:
        section_name = f"component.{identifier}"
        component = raw[section_name]
        _exact_keys(component, {"native_source", "web"}, {"wasm_source"}, section_name)
        native_source = _project_file(
            root, component["native_source"], f"[{section_name}].native_source",
            {".c", ".cc", ".cpp", ".cxx"},
        )
        web = component["web"]
        if web not in {"fallback", "wasm"}:
            raise ManifestError(f"[{section_name}].web must be fallback or wasm")
        wasm_source = component.get("wasm_source")
        if web == "wasm":
            wasm_source = _project_file(
                root, wasm_source, f"[{section_name}].wasm_source",
                {".c", ".cc", ".cpp", ".cxx"},
            )
        elif wasm_source is not None:
            raise ManifestError(f"[{section_name}].wasm_source requires web = \"wasm\"")
        output.append({
            "id": identifier,
            "native_source": native_source,
            "web": web,
            "wasm_source": wasm_source,
        })
    return output


def imported_materialized_design(project: Path) -> Path | None:
    """Resolve the one-source v0 generated design without assuming its key."""
    root = project.resolve()
    lock_path = root / "design/import.lock.json"
    if not lock_path.exists():
        return None
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read design import lock: {error}") from error
    sources = lock.get("sources") if isinstance(lock, dict) else None
    if (
        not isinstance(lock, dict) or
        lock.get("schema") != IMPORT_LOCK_SCHEMA or
        not isinstance(sources, dict) or
        len(sources) != 1
    ):
        raise ManifestError("design import lock must contain exactly one v1 source")
    source_key = next(iter(sources))
    if not isinstance(source_key, str) or not _COMPONENT_ID.fullmatch(source_key):
        raise ManifestError("design import lock contains an invalid source key")
    materialized = root / "ui/generated" / f"{source_key}.materialized.json"
    bindings = root / "ui/generated" / f"{source_key}.bindings.json"
    if not materialized.is_file() or not bindings.is_file():
        raise ManifestError(
            f"generated design and bindings are missing for source '{source_key}'"
        )
    return materialized


def load_app_manifest(project: Path) -> dict[str, Any]:
    path = project / APP_MANIFEST_NAME
    raw = parse_app_toml(path)
    allowed_sections = {"app", "runtime", "targets", "capabilities", "native", "packaging", "packaging.overrides"}
    unknown_sections = sorted(set(raw) - allowed_sections)
    if unknown_sections:
        raise ManifestError("unsupported application tables: " + ", ".join(unknown_sections))

    app = raw.get("app", {})
    _exact_keys(app, {"name", "identifier", "version", "entry", "design"}, set(), "app")
    if not isinstance(app["name"], str) or not app["name"].strip() or len(app["name"]) > 100:
        raise ManifestError("[app].name must be 1-100 characters")
    if not isinstance(app["identifier"], str) or not _IDENTIFIER.fullmatch(app["identifier"]):
        raise ManifestError("[app].identifier must be a reverse-DNS identifier")
    if not isinstance(app["version"], str) or not _VERSION.fullmatch(app["version"]):
        raise ManifestError("[app].version must be a semantic version")
    for key in ("entry", "design"):
        _project_relative(app[key], f"[app].{key}")

    runtime = raw.get("runtime", {})
    _exact_keys(runtime, {"profile", "native_js_engine"}, set(), "runtime")
    if runtime != {"profile": "portable", "native_js_engine": "default"}:
        raise ManifestError("the initial SDK requires portable/default runtime settings")

    targets = raw.get("targets", {})
    _exact_keys(targets, {"desktop", "mobile", "web"}, set(), "targets")
    if targets.get("desktop") != [SUPPORTED_DESKTOP_TARGET] or targets.get("mobile") != [] or \
            not isinstance(targets.get("web"), bool):
        raise ManifestError(
            "the initial SDK requires desktop = [\"macos\"], mobile = [], and boolean web"
        )

    capabilities = raw.get("capabilities", {})
    _exact_keys(
        capabilities,
        {"commands", "files", "clipboard", "open_url", "network", "persistence"},
        set(),
        "capabilities",
    )
    versions = {
        "commands": "v1",
        "files": "user-selected-text-v1",
        "clipboard": "text-v1",
        "open_url": "external-v1",
        "persistence": "state-v1",
    }
    for capability, version in versions.items():
        if capabilities[capability] not in {version, "denied", "unsupported"}:
            raise ManifestError(
                f"[capabilities].{capability} must be {version}, denied, or unsupported"
            )
    if capabilities["network"] is not False:
        raise ManifestError("[capabilities].network is unsupported and must remain false")

    native = raw.get("native", {})
    _exact_keys(native, {"components_manifest"}, set(), "native")
    _project_relative(native["components_manifest"], "[native].components_manifest")

    packaging = raw.get("packaging", {})
    _exact_keys(packaging, {"macos_format"}, {"web_format"}, "packaging")
    if packaging["macos_format"] != "app":
        raise ManifestError("the initial SDK packages only macOS app bundles")
    if targets["web"] and packaging.get("web_format") != "static":
        raise ManifestError("web targets require [packaging].web_format = \"static\"")
    if "web_format" in packaging and packaging["web_format"] != "static":
        raise ManifestError("the initial SDK packages web targets only as static bundles")
    overrides = raw.get("packaging.overrides", {})
    _exact_keys(overrides, set(), {"macos"}, "packaging.overrides")
    if "macos" in overrides:
        _project_relative(overrides["macos"], "[packaging.overrides].macos")

    return {
        "app": app,
        "runtime": runtime,
        "targets": targets,
        "capabilities": capabilities,
        "native": native,
        "packaging": packaging,
        "packaging_overrides": overrides,
    }
