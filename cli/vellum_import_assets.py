"""Standalone JSX and annotated SVG import paths."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET


class AssetImportError(ValueError):
    pass


def _number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _regular(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise AssetImportError(f"{label} must be a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise AssetImportError(f"cannot read {label} {path}: {error.strerror or error}") from error


def _decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AssetImportError(f"{label} must be valid UTF-8: {error}") from error


def _write(path: Path, data: bytes) -> None:
    # lstat-based checks so dangling symlinks and symlinked ancestors are refused.
    # Top-level OS aliases (/var, /tmp on macOS) are exempt.
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        if len(candidate.parts) > 2 and candidate.is_symlink():
            raise AssetImportError(f"output must not be or sit under a symlink: {candidate}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as error:
        raise AssetImportError(f"cannot write output {path}: {error.strerror or error}") from error


def import_jsx(source_path: Path, *, emit: str, mode: str, output: Path | None,
               source_out: Path | None) -> dict:
    source = _regular(source_path, "JSX source")
    if mode not in {"live", "baked"}:
        raise AssetImportError("--mode must be live or baked")
    if emit not in {"js", "ir-json", "materialized-runtime"}:
        raise AssetImportError("--emit must be js, ir-json, or materialized-runtime")
    if mode == "baked" and emit == "js":
        raise AssetImportError("--mode baked requires --emit ir-json or --emit materialized-runtime")
    if emit == "materialized-runtime":
        raise AssetImportError(
            "--emit materialized-runtime is not implemented by the standalone importer; "
            "use --emit js or --emit ir-json"
        )
    if output is None:
        raise AssetImportError("--out is required for JSX output")
    if emit == "js":
        _decode(source, "JSX source")
        _write(output, source)
    else:
        payload = {
            "schema": "vellum.jsx-import.v1", "emit": emit, "mode": mode,
            "sourceSha256": hashlib.sha256(source).hexdigest(),
            "source": _decode(source, "JSX source"),
        }
        _write(output, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return {"mode": mode, "emit": emit, "output": str(output), "sourceSha256": hashlib.sha256(source).hexdigest()}


def validate_preconverted_svg(svg_path: Path, manifest_path: Path) -> dict:
    svg = _regular(svg_path, "SVG source")
    manifest_bytes = _regular(manifest_path, "control-map sidecar")
    try:
        manifest = json.loads(_decode(manifest_bytes, "control-map sidecar"))
    except json.JSONDecodeError as error:
        raise AssetImportError(f"invalid control-map JSON: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("provenance"), dict) or not manifest["provenance"].get("source"):
        raise AssetImportError("sidecar requires provenance.source")
    digest = manifest.get("svg_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise AssetImportError("svg_sha256 must be a 64-character hex digest")
    if digest.lower() != hashlib.sha256(svg).hexdigest():
        raise AssetImportError("svg_sha256 does not match SVG bytes")
    canvas = manifest.get("canvas")
    if not isinstance(canvas, dict) or not all(_number(canvas.get(key)) and canvas[key] > 0 for key in ("width", "height")):
        raise AssetImportError("canvas width and height must be positive")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        raise AssetImportError("SVG bytes must contain an <svg> root element") from error
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise AssetImportError("SVG bytes must contain an <svg> root element")
    ids = {}
    for element in root.iter():
        identity = element.attrib.get("id")
        if identity:
            if identity in ids:
                raise AssetImportError(f"SVG contains duplicate id '{identity}'")
            ids[identity] = element
    elements = manifest.get("elements")
    if not isinstance(elements, list):
        raise AssetImportError("sidecar requires elements array")
    selectors = set()
    width, height = float(canvas["width"]), float(canvas["height"])
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise AssetImportError(f"element {index} must be an object")
        selector = element.get("selector", "")
        if not isinstance(selector, str) or not selector.startswith("#") or len(selector) < 2:
            raise AssetImportError(f"element {index} selector must be an SVG #id")
        identity = selector[1:]
        if identity in selectors:
            raise AssetImportError(f"duplicate control selector '{selector}'")
        selectors.add(identity)
        if identity not in ids:
            raise AssetImportError(f"control selector '{selector}' is absent from SVG")
        geometry = element.get("geometry")
        if not isinstance(geometry, dict):
            raise AssetImportError(f"element {index} requires a geometry object")
        kind = element.get("kind", "")
        required = ("cx", "cy", "hit_radius") if kind == "knob" else ("x", "y", "w", "h")
        if any(not _number(geometry.get(key)) for key in required):
            raise AssetImportError(f"element {index} geometry is incomplete")
        if kind == "knob":
            if geometry["hit_radius"] <= 0 or geometry["cx"] - geometry["hit_radius"] < 0 or geometry["cy"] - geometry["hit_radius"] < 0 or geometry["cx"] + geometry["hit_radius"] > width or geometry["cy"] + geometry["hit_radius"] > height:
                raise AssetImportError(f"element {index} control center is outside canvas")
        elif geometry["x"] < 0 or geometry["y"] < 0 or geometry["w"] <= 0 or geometry["h"] <= 0 or geometry["x"] + geometry["w"] > width or geometry["y"] + geometry["h"] > height:
            raise AssetImportError(f"element {index} geometry is outside canvas")
    return {"source": "preconverted-svg", "validated": True, "elements": len(elements)}
