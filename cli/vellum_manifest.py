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
SUPPORTED_TARGET = "macos"


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
    if targets != {"desktop": [SUPPORTED_TARGET], "mobile": [], "web": False}:
        raise ManifestError("the initial SDK supports exactly desktop = [\"macos\"], mobile = [], web = false")

    capabilities = raw.get("capabilities", {})
    _exact_keys(capabilities, {"files", "clipboard", "open_url", "network", "persistence"}, set(), "capabilities")
    expected_capabilities = {
        "files": "none", "clipboard": False, "open_url": False,
        "network": False, "persistence": "none",
    }
    if {**capabilities, "persistence": "none"} != expected_capabilities or \
            capabilities["persistence"] not in {"none", "state-v1"}:
        raise ManifestError(
            "the initial host exposes only the optional persistence = \"state-v1\" capability"
        )

    native = raw.get("native", {})
    _exact_keys(native, {"components_manifest"}, set(), "native")
    _project_relative(native["components_manifest"], "[native].components_manifest")

    packaging = raw.get("packaging", {})
    _exact_keys(packaging, {"macos_format"}, set(), "packaging")
    if packaging["macos_format"] != "app":
        raise ManifestError("the initial SDK packages only macOS app bundles")
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
