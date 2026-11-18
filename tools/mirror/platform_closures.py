#!/usr/bin/env python3
"""Platform closure records: what each platform other than macOS compiles.

The export receipt's closures describe one configuration, Darwin arm64 with
Skia. Every other platform's build graph comes from Pulp's own configure for
that platform, written by Pulp's exporter (`tools/scripts/vellum_closure.py
manifest`) as one closure manifest per unit, with no file content: the files
framework/ compiles still come only from the transfer record and the ports
ledger. A platform's record pins those manifests:

    provenance/framework/platforms/index.json             the platforms with a record
    provenance/framework/platforms/<key>/record.json      the record
    provenance/framework/platforms/<key>/<unit>.json      one closure manifest per unit

The record names the Pulp commit and the exporter blob that wrote the
manifests, the configure (identity and options digest), the runner image the
manifests were produced on, the renderer asset they link, each manifest's
digest, and the Pulp workflow run that exported them with the digest of the
artifact it uploaded. This module reads and checks a record; it runs no git. The mirror
build (tools/mirror/mirror_package.py) plans a platform only from a record
that passes, and the provenance lane checks every record.

macOS needs no record: the receipt's closures are its closures.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = "provenance/framework/platforms"
INDEX = f"{ROOT}/index.json"
INDEX_SCHEMA = "vellum.framework-platform-index.v1"
RECORD_SCHEMA = "vellum.framework-platform-closure.v1"
CLOSURE_SCHEMAS = ("pulp.vellum.closure.v1",)
RECORD_KEYS = {"schema", "platform", "pulp_repository", "pulp_sha", "tool", "configure", "runner_image",
               "renderer_asset", "units", "export"}
# The Pulp workflow run that exported the manifests, and the digest of the
# artifact it uploaded: the audit trail from a record to the run that made it.
EXPORT_RUN = re.compile(r"https://github\.com/Generous-Corp/pulp/actions/runs/[1-9][0-9]*\Z")
PULP_REPOSITORY = "Generous-Corp/pulp"
EXPORTER = "tools/scripts/vellum_closure.py"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
UNIT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
# The platform the export receipt's closures describe; it has no record.
RECEIPT_PLATFORM = "macos-arm64"
# Each platform key and the configure identity its closures must carry
# (CMake's CMAKE_SYSTEM_NAME and CMAKE_SYSTEM_PROCESSOR, as Pulp's exporter
# records them).
IDENTITIES = {
    "macos-arm64": ("Darwin", "arm64"),
    "linux-x64": ("Linux", "x86_64"),
    "linux-arm64": ("Linux", "aarch64"),
    "windows-x64": ("Windows", "AMD64"),
    "windows-arm64": ("Windows", "ARM64"),
    "ios-device-arm64": ("iOS", "arm64"),
    "ios-simulator-arm64": ("iOS", "arm64"),
    "android-arm64-v8a": ("Android", "aarch64"),
    "android-x86_64": ("Android", "x86_64"),
    "wasm32": ("Emscripten", "wasm32"),
}


class RecordError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def options_sha256(options: dict[str, Any]) -> str:
    """Pulp's configure_options_sha256: the digest of the options as sorted JSON."""
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()


def platform_of(system: str, processor: str, apple_sdk: str = "") -> str:
    """The platform key a configure's target system and processor name, or refuse."""
    if system == "iOS":
        key = "ios-simulator-arm64" if "simulator" in apple_sdk.lower() else "ios-device-arm64"
        if processor != "arm64":
            raise RecordError(f"no platform key for iOS on {processor}")
        return key
    matches = [key for key, identity in IDENTITIES.items() if identity == (system, processor)
               and not key.startswith("ios-")]
    if len(matches) != 1:
        raise RecordError(f"no platform key for {system} {processor}: the mirror builds only macOS "
                          "and the platforms with a verified closure record")
    return matches[0]


def _load_json(repo: Path, relative: str, what: str) -> Any:
    path = repo / relative
    if path.is_symlink() or not path.is_file():
        raise RecordError(f"{relative}: {what} is missing or not a regular file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RecordError(f"{relative}: unreadable: {exc}") from exc


def index(repo: Path) -> dict[str, str]:
    """Platform key -> record path; a missing index lists no platform."""
    if not (repo / INDEX).exists() and not (repo / INDEX).is_symlink():
        return {}
    document = _load_json(repo, INDEX, "the platform index")
    if not isinstance(document, dict) or set(document) != {"schema", "platforms"} \
            or document["schema"] != INDEX_SCHEMA or not isinstance(document["platforms"], dict):
        raise RecordError(f"{INDEX}: must be an object with schema {INDEX_SCHEMA} and a platforms object")
    out = {}
    for key, relative in sorted(document["platforms"].items()):
        if key not in IDENTITIES or key == RECEIPT_PLATFORM:
            raise RecordError(f"{INDEX}: {key!r} is not a platform that takes a record")
        if relative != f"{ROOT}/{key}/record.json":
            raise RecordError(f"{INDEX}: the record of {key} is {ROOT}/{key}/record.json, not {relative!r}")
        out[key] = relative
    return out


# CMake's true constants; the closures record cache values such as ON and 1.
CMAKE_TRUE = {"ON", "1", "TRUE", "YES", "Y"}
# The configure options each claim of the identity must be backed by, and the
# value that backs it. A missing option is a mismatch.
IDENTITY_OPTIONS = {"build_type": "CMAKE_BUILD_TYPE", "gpu": "PULP_ENABLE_GPU", "skia": "PULP_HAS_SKIA"}


def _check_options_claims(at: str, options: dict[str, Any]) -> None:
    """The recorded options must configure the Release GPU Skia build the identity claims."""
    build = options.get(IDENTITY_OPTIONS["build_type"])
    if build != "Release":
        raise RecordError(f"{at}: {IDENTITY_OPTIONS['build_type']} is {build!r}, but the record claims Release")
    for claim in ("gpu", "skia"):
        name = IDENTITY_OPTIONS[claim]
        value = options.get(name)
        if not isinstance(value, str) or value.upper() not in CMAKE_TRUE:
            raise RecordError(f"{at}: {name} is {value!r}, but the record claims {claim}")


def _check_options_platform(at: str, key: str, options: dict[str, Any]) -> None:
    """The recorded options must configure the platform the index files the record under."""
    system, processor = IDENTITIES[key]
    if options.get("CMAKE_SYSTEM_NAME") != system:
        raise RecordError(f"{at}: configure options target {options.get('CMAKE_SYSTEM_NAME')!r}, "
                          f"but {key} is {system}")
    if options.get("CMAKE_SYSTEM_PROCESSOR") != processor:
        raise RecordError(f"{at}: configure options target processor {options.get('CMAKE_SYSTEM_PROCESSOR')!r}, "
                          f"but {key} is {processor}")
    if system == "iOS":
        sdk = str(options.get("CMAKE_OSX_SYSROOT", "")).lower()
        simulator = "simulator" in sdk
        if not sdk or simulator != (key == "ios-simulator-arm64"):
            raise RecordError(f"{at}: CMAKE_OSX_SYSROOT {options.get('CMAKE_OSX_SYSROOT')!r} "
                              f"is not the {key} SDK")


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _rows(value: Any) -> bool:
    """A list of objects, each with a string path when it names one."""
    return isinstance(value, list) and all(
        isinstance(row, dict) and isinstance(row.get("path", ""), str) for row in value)


# The graph fields the mirror planner reads from a closure, and the shape each must have.
CLOSURE_LISTS = {"external_inputs": (_rows, "a list of objects with string paths"),
                 "generated_inputs": (_rows, "a list of objects with string paths"),
                 "compile_definitions": (_strings, "a list of strings")}
TARGET_LISTS = {"sources": _strings, "include_dirs": _strings, "link_targets": _strings,
                "compile_definitions": _strings}


def _check_graph_shape(at: str, closure: dict[str, Any]) -> None:
    """Every graph field the planner reads has the shape it reads it as, so a
    malformed manifest fails here rather than deep inside planning."""
    for field, (check, shape) in CLOSURE_LISTS.items():
        if field in closure and not check(closure[field]):
            raise RecordError(f"{at}: {field} must be {shape}")
    targets = closure.get("targets", [])
    if not isinstance(targets, list):
        raise RecordError(f"{at}: targets must be a list")
    names: set[str] = set()
    for position, target in enumerate(targets):
        if not isinstance(target, dict):
            raise RecordError(f"{at}: targets[{position}] is not an object")
        name = target.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise RecordError(f"{at}: targets[{position}] name {name!r} is missing, malformed or repeated")
        names.add(name)
        if not isinstance(target.get("type"), str):
            raise RecordError(f"{at}: {name} type must be a string")
        for field, check in TARGET_LISTS.items():
            if field in target and not check(target[field]):
                raise RecordError(f"{at}: {name} {field} must be a list of strings")
        rows = target.get("external_link_inputs", [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise RecordError(f"{at}: {name} external_link_inputs must be a list of objects")


def _hex(pattern: re.Pattern[str], value: Any) -> bool:
    """True when value is a string the hash pattern matches.

    JSON numbers are refused before matching: a 40-digit integer would
    otherwise stringify into a valid-looking commit id and reach git argv.
    """
    return isinstance(value, str) and pattern.match(value) is not None

def load_record(repo: Path, key: str) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """(record, [(unit, closure)]) for a platform the index lists, every pin checked."""
    listed = index(repo)
    if key not in listed:
        raise RecordError(f"no verified closure for platform {key}: {INDEX} lists no record for it")
    relative = listed[key]
    record = _load_json(repo, relative, "the platform record")
    where = relative
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise RecordError(f"{where}: must have exactly {sorted(RECORD_KEYS)}")
    if record["schema"] != RECORD_SCHEMA:
        raise RecordError(f"{where}: schema must be {RECORD_SCHEMA}")
    if record["platform"] != key:
        raise RecordError(f"{where}: names platform {record['platform']!r}, not {key}")
    if record["pulp_repository"] != PULP_REPOSITORY:
        raise RecordError(f"{where}: pulp_repository must be {PULP_REPOSITORY}")
    if not _hex(SHA40, record["pulp_sha"]):
        raise RecordError(f"{where}: pulp_sha is not a full commit id")
    tool = record["tool"]
    if not isinstance(tool, dict) or set(tool) != {"path", "pulp_blob"} or tool["path"] != EXPORTER \
            or not _hex(SHA40, tool["pulp_blob"]):
        raise RecordError(f"{where}: tool names {EXPORTER} and the git blob that wrote the manifests")
    image = record["runner_image"]
    if not isinstance(image, dict) or set(image) != {"name", "version"} \
            or not all(isinstance(image[k], str) and image[k].strip() for k in ("name", "version")):
        raise RecordError(f"{where}: runner_image names the image and version the manifests were produced on")
    export = record["export"]
    if not isinstance(export, dict) or set(export) != {"run_url", "artifact_sha256"}:
        raise RecordError(f"{where}: export names the Pulp run (run_url) and its artifact's sha256 (artifact_sha256)")
    if not isinstance(export["run_url"], str) or not EXPORT_RUN.match(export["run_url"]):
        raise RecordError(f"{where}: export.run_url {export['run_url']!r} is not a "
                          "https://github.com/Generous-Corp/pulp/actions/runs/<id> URL")
    if not _hex(SHA256, export["artifact_sha256"]):
        raise RecordError(f"{where}: export.artifact_sha256 is not a lowercase SHA-256")
    asset = record["renderer_asset"]
    if not isinstance(asset, dict) or set(asset) != {"name", "sha256"} or not isinstance(asset["name"], str) \
            or not _hex(SHA256, asset["sha256"]):
        raise RecordError(f"{where}: renderer_asset names the locked asset and its sha256")
    configure = record["configure"]
    if not isinstance(configure, dict) or set(configure) != {"identity", "options_sha256"} \
            or not isinstance(configure["identity"], dict) or not _hex(SHA256, configure["options_sha256"]):
        raise RecordError(f"{where}: configure carries the identity and the options digest")
    identity = configure["identity"]
    system, processor = IDENTITIES[key]
    if (identity.get("system"), identity.get("processor")) != (system, processor):
        raise RecordError(f"{where}: {key} is built for {system} {processor}, but the record's configure is "
                          f"{identity.get('system')} {identity.get('processor')}")
    if identity.get("build_type") != "Release" or identity.get("skia") is not True or identity.get("gpu") is not True:
        raise RecordError(f"{where}: a platform record describes the Release GPU Skia build")
    units = record["units"]
    if not isinstance(units, list) or not units:
        raise RecordError(f"{where}: units must list one or more closure manifests")
    closures: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for index_, unit in enumerate(units):
        at = f"{where} units[{index_}]"
        if not isinstance(unit, dict) or set(unit) != {"name", "closure_manifest", "closure_sha256"}:
            raise RecordError(f"{at}: must have exactly name, closure_manifest and closure_sha256")
        name = unit["name"]
        if not isinstance(name, str) or not UNIT_NAME.match(name) or name in seen:
            raise RecordError(f"{at}: unit name {name!r} is malformed or repeated")
        seen.add(name)
        manifest = unit["closure_manifest"]
        if manifest != f"{ROOT}/{key}/{name}.json":
            raise RecordError(f"{at}: the manifest of {name} is {ROOT}/{key}/{name}.json, not {manifest!r}")
        path = repo / manifest
        if path.is_symlink() or not path.is_file():
            raise RecordError(f"{at}: {manifest} is missing")
        if sha256_file(path) != unit["closure_sha256"]:
            raise RecordError(f"{at}: {manifest} does not match the record's digest")
        closure = _load_json(repo, manifest, "the closure manifest")
        if not isinstance(closure, dict) or closure.get("schema") not in CLOSURE_SCHEMAS:
            raise RecordError(f"{at}: {manifest} is not a {' or '.join(CLOSURE_SCHEMAS)} manifest")
        recorded = closure.get("configure") or {}
        if not isinstance(recorded, dict):
            raise RecordError(f"{at}: {manifest} configure must be an object")
        if recorded.get("identity") != identity:
            raise RecordError(f"{at}: {manifest} was exported under another configure identity than the record's")
        options = recorded.get("options")
        if not isinstance(options, dict) or options_sha256(options) != configure["options_sha256"]:
            raise RecordError(f"{at}: {manifest}'s configure options do not match the record's digest")
        _check_options_platform(at, key, options)
        _check_options_claims(at, options)
        _check_graph_shape(f"{at}: {manifest}", closure)
        for target in closure.get("targets", []):
            for row in target.get("external_link_inputs", []):
                if row.get("dependency") == "skia" and row.get("asset_sha256") != asset["sha256"]:
                    raise RecordError(f"{at}: {target.get('name')} links skia asset {row.get('asset_sha256')}, "
                                      f"not the record's {asset['sha256']}")
        closures.append((name, closure))
    return record, closures


def errors(repo: Path) -> list[str]:
    """Every problem with the index and the records it lists; empty when all pass or none exist."""
    try:
        listed = index(repo)
    except RecordError as error:
        return [str(error)]
    found = []
    for key in listed:
        try:
            load_record(repo, key)
        except RecordError as error:
            found.append(str(error))
    # A record directory the index does not list is an orphan the build never reads.
    root = repo / ROOT
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in listed:
                found.append(f"{ROOT}/{child.name}/ is not listed in {INDEX}")
    return found
