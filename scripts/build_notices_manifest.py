#!/usr/bin/env python3
"""Generate Vellum's machine-readable third-party notices manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "pulp.notices.v1"
NAMESPACE = "Vellum"


def all_notice_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for match in re.finditer(r"(?m)^##{1,3} (.+?)\s*$", text):
        start = match.start()
        end_match = re.search(r"(?m)^### ", text[match.end():])
        end = match.end() + end_match.start() if end_match else len(text)
        sections[match.group(1).strip()] = text[start:end].rstrip() + "\n"
    return sections


def _feature(row: dict[str, Any]) -> str:
    name = row["name"].lower()
    surface = row.get("surface", "").lower()
    if name in {"v8"}:
        return "VELLUM_ENABLE_V8"
    if name in {"rmlui", "lua", "freetype", "stb_image_write"} or "rmlui" in surface:
        return "VELLUM_ENABLE_RMLUI"
    if name == "perfetto" or "tracing" in surface:
        return "VELLUM_ENABLE_TRACING"
    return ""


def _required_by(row: dict[str, Any], bundled: bool = False) -> list[str]:
    name = row["name"].lower()
    if bundled:
        archive = row.get("parent_archive", "")
        if archive == "libdawn_combined.a":
            return ["VellumGpuRuntime::Dawn", "Vellum::SkiaDawnProvider"]
        return ["VellumGpuRuntime::SkiaLogBridge", "Vellum::SkiaDawnProvider", "Vellum::MirrorCanvas"]
    if name == "v8":
        return ["Vellum::SkiaDawnProvider", "Vellum::MirrorView_script"]
    if name == "perfetto":
        return ["Vellum::MirrorFoundation"]
    if "rmlui" in name or name in {"lua", "freetype", "stb_image_write"}:
        return ["VellumRmlUi::Core", "VellumRmlUi::NativeHost"]
    if "font" in row.get("surface", "") or "font" in name:
        return ["Vellum::Foundation"]
    return ["Vellum::MirrorView_core", "VellumGpuRuntime::SkiaDawnProvider"]


def _heading(name: str, bundled: bool) -> str:
    if bundled and name == "Skia and skcms":
        return name
    aliases = {
        "Skia Graphite and Dawn renderer toolchain": "Skia and skcms",
        "Inter Regular": "Inter",
        "Jost Regular": "Jost",
        "Jost Medium": "Jost",
        "Jost SemiBold": "Jost",
        "Jost Bold": "Jost",
        "Noto Sans JP variable font": "Noto Sans JP",
        "Noto Sans Arabic variable font": "Noto Sans Arabic",
        "esbuild and @esbuild/darwin-arm64": "esbuild",
        "V8": "Optional V8 provider",
        "Perfetto": "Optional native Perfetto tracing",
        "Three.js": "Three.js",
        "FreeType": "FreeType",
        "stb_image_write": "stb_image_write",
    }
    return aliases.get(name, name)


def _license(value: str) -> str:
    text = value.lower()
    if "bsd" in text:
        return "BSD-3-Clause"
    if "apache" in text:
        return "Apache-2.0"
    if "mit" in text:
        return "MIT"
    if "ofl" in text:
        return "OFL-1.1"
    if "f(t)l" in text or text.strip() == "ftl":
        return "FTL"
    if "zlib" in text:
        return "Zlib"
    return value


def _rows(lock: dict[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    result = [(row, False) for row in lock.get("dependencies", [])]
    renderer = lock.get("runtime_toolchain_dependencies", {}).get("renderer", {})
    result.extend((row, True) for row in renderer.get("bundled_components", []))
    return result


def generate(repo: Path, output: Path) -> dict[str, Any]:
    lock = json.loads((repo / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
    sections = all_notice_sections((repo / "NOTICE.md").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    seen_bundled: set[str] = set()
    for row, bundled in _rows(lock):
        name = row["name"]
        if bundled:
            if name in seen_bundled:
                raise ValueError(f"bundled_components row appears more than once: {name}")
            seen_bundled.add(name)
        heading = _heading(name, bundled)
        if heading not in sections:
            raise ValueError(f"NOTICE.md has no section for {name}")
        text = sections[heading].encode("utf-8")
        filename = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + ".txt"
        license_path = output / "licenses" / filename
        license_path.parent.mkdir(parents=True, exist_ok=True)
        license_path.write_bytes(text)
        version = row.get("version") or row.get("commit") or row.get("revision") or "unknown"
        source = row.get("source") or row.get("primary_source") or row.get("url") or ""
        entries.append({
            "name": name,
            "version": version,
            "license": _license(row.get("license", "")),
            "copyright": "",
            "source": source,
            "license_text": f"licenses/{filename}",
            "sha256": hashlib.sha256(text).hexdigest(),
            "required_by": _required_by(row, bundled),
            "feature": _feature(row),
        })
    project = (repo / "CMakeLists.txt").read_text(encoding="utf-8")
    version_match = re.search(r"^project\(Vellum\s*\n\s*VERSION ([0-9]+\.[0-9]+\.[0-9]+)", project, re.M)
    sdk_version = lock.get("sdk_version") or (version_match.group(1) if version_match else "unknown")
    manifest = {
        "schema": SCHEMA,
        "sdk": NAMESPACE,
        "sdk_version": sdk_version,
        "namespaces": [NAMESPACE, NAMESPACE.lower()],
        "entries": entries,
    }
    (output / "notices.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return manifest


def validate_installed_package(manifest_path: Path, lock_path: Path) -> None:
    """Check an installed manifest and every bundled component it represents."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"unexpected notices schema: {manifest.get('schema')!r}")
    entries = manifest.get("entries", [])
    for entry in entries:
        text = manifest_path.parent / entry["license_text"]
        if not text.is_file():
            raise ValueError(f"missing license text for {entry['name']}: {entry['license_text']}")
        if hashlib.sha256(text.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"license text digest mismatch for {entry['name']}")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected = [r["name"] for r in lock.get("runtime_toolchain_dependencies", {}).get("renderer", {}).get("bundled_components", [])]
    names = [e.get("name") for e in entries]
    missing = [name for name in expected if names.count(name) != 1]
    if missing:
        raise ValueError("bundled_components must appear exactly once: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.repo.resolve(), args.output.resolve())
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
