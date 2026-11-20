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
artifact it uploaded. The record carries a consistency digest over that
artifact digest and the complete manifest set; the provenance lane separately
downloads the artifact and checks its bytes. This module reads and checks a
record; it runs no git. The mirror
build (tools/mirror/mirror_package.py) plans a platform only from a record
that passes, and the provenance lane checks every record.

macOS needs no record: the receipt's closures are its closures.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
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


def verify_export_artifact(key: str, record: dict[str, Any], archive: bytes) -> None:
    """Prove a downloaded Pulp closure artifact is the one named by ``record``.

    The record is Vellum-controlled, so its hashes are not evidence by
    themselves. The workflow artifact is the authority: its bytes, export
    metadata, and closure manifests must all agree with the record before the
    provenance lane accepts the platform.
    """
    export = record["export"]
    if hashlib.sha256(archive).hexdigest() != export["artifact_sha256"]:
        raise RecordError(f"{ROOT}/{key}/record.json: downloaded Pulp artifact digest differs from the record")
    try:
        handle = zipfile.ZipFile(io.BytesIO(archive))
    except (OSError, zipfile.BadZipFile) as exc:
        raise RecordError(f"{ROOT}/{key}/record.json: downloaded Pulp artifact is not a ZIP: {exc}") from exc
    with handle:
        members = handle.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise RecordError(f"{ROOT}/{key}/record.json: downloaded Pulp artifact has duplicate members")
        expected = {"export.json", *(f"{unit['name']}.json" for unit in record["units"])}
        if set(names) != expected:
            raise RecordError(f"{ROOT}/{key}/record.json: downloaded Pulp artifact members {sorted(names)!r} "
                              f"do not equal {sorted(expected)!r}")
        if any(not name or name.startswith(("/", "\\")) or ".." in Path(name).parts
               for name in names):
            raise RecordError(f"{ROOT}/{key}/record.json: downloaded Pulp artifact contains an unsafe member")
        try:
            export_meta = json.loads(handle.read("export.json"))
        except (UnicodeDecodeError, ValueError, KeyError) as exc:
            raise RecordError(f"{ROOT}/{key}/record.json: downloaded export.json is unreadable: {exc}") from exc
        expected_meta = {
            "platform": key,
            "pulp_sha": record["pulp_sha"],
            "runner_image": record["runner_image"],
            "renderer_asset_name": record["renderer_asset"]["name"],
        }
        if not isinstance(export_meta, dict) or any(export_meta.get(name) != value
                                                    for name, value in expected_meta.items()):
            raise RecordError(f"{ROOT}/{key}/record.json: downloaded export.json does not match the record")
        units = []
        downloaded_units = []
        for unit in record["units"]:
            name = f"{unit['name']}.json"
            data = handle.read(name)
            digest = hashlib.sha256(data).hexdigest()
            if digest != unit["closure_sha256"]:
                raise RecordError(f"{ROOT}/{key}/record.json: downloaded {name} differs from the record")
            try:
                json.loads(data)
            except (UnicodeDecodeError, ValueError) as exc:
                raise RecordError(f"{ROOT}/{key}/record.json: downloaded {name} is not JSON: {exc}") from exc
            units.append(unit)
            downloaded_units.append({"name": unit["name"], "closure_sha256": digest})
        downloaded_binding = manifest_binding_sha256(export["artifact_sha256"], downloaded_units)
        if export["manifest_sha256"] != downloaded_binding:
            raise RecordError(f"{ROOT}/{key}/record.json: downloaded manifests do not match the export binding")


def manifest_binding_sha256(artifact_sha256: str, units: list[dict[str, Any]]) -> str:
    """Compute the record's consistency digest for an artifact and manifests.

    This value is not trusted evidence by itself: the provenance lane verifies
    the downloaded artifact bytes and re-derives each manifest digest.
    """
    listing = "".join(
        f"{unit['name']} {unit['closure_sha256']}\n"
        for unit in sorted(units, key=lambda item: item["name"])
    )
    return hashlib.sha256(f"artifact-sha256:{artifact_sha256}\n{listing}".encode("utf-8")).hexdigest()


def options_sha256(options: dict[str, Any]) -> str:
    """Pulp's configure_options_sha256: the digest of the options as sorted JSON."""
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()


def platform_of(system: str, processor: str, apple_sdk: str = "") -> str:
    """The platform key a configure's target system and processor name, or refuse."""
    if system == "iOS":
        family = _apple_sdk_family(apple_sdk)
        key = {"iphoneos": "ios-device-arm64", "iphonesimulator": "ios-simulator-arm64"}.get(family)
        if key is None:
            raise RecordError(f"CMAKE_OSX_SYSROOT {apple_sdk!r} is not an iphoneos or iphonesimulator SDK")
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
        sdk = _apple_sdk_family(options.get("CMAKE_OSX_SYSROOT"))
        wanted = "iphonesimulator" if key == "ios-simulator-arm64" else "iphoneos"
        if sdk != wanted:
            raise RecordError(f"{at}: CMAKE_OSX_SYSROOT {options.get('CMAKE_OSX_SYSROOT')!r} "
                              f"is not the {wanted} SDK that {key} needs")


def _apple_sdk_family(value: Any) -> str:
    """The SDK family of a CMAKE_OSX_SYSROOT: 'iphoneos', 'iphonesimulator', 'macosx', ...

    The value is an SDK name (iphoneos, iphoneos17.5) or a path ending in
    <Family><version>.sdk; anything else has no family.
    """
    import re
    if not isinstance(value, str) or not value:
        return ""
    base = value.rstrip("/").rsplit("/", 1)[-1].lower()
    if base.endswith(".sdk"):
        base = base[:-4]
    match = re.fullmatch(r"([a-z]+)(?:[0-9]+(?:\.[0-9]+)*)?", base)
    return match.group(1) if match else ""


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def safe_relative_path(value: Any) -> bool:
    """Whether a path is relative and cannot leave its root on any platform.

    Checked with POSIX and Windows semantics alike, so a path that is harmless
    on one host cannot escape on another: no backslash or NUL, no leading '/'
    or '~', no drive or UNC prefix, and no empty, '.' or '..' segment.
    """
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        return False
    if value.startswith(('/', '~')):
        return False
    from pathlib import PurePosixPath, PureWindowsPath
    for flavour in (PurePosixPath, PureWindowsPath):
        pure = flavour(value)
        if pure.is_absolute() or getattr(pure, 'drive', '') or getattr(pure, 'root', ''):
            return False
    parts = value.split('/')
    return all(part not in {'', '.', '..'} and ':' not in part for part in parts)


def contained_under(root: Path, candidate: Path) -> bool:
    """Whether candidate, with symlinks resolved, sits inside root."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _source_strings(value: Any) -> bool:
    return isinstance(value, list) and all(safe_relative_path(item) for item in value)


def _rows(value: Any) -> bool:
    """A list of objects, each with a string path when it names one."""
    return isinstance(value, list) and all(
        isinstance(row, dict) and isinstance(row.get("path", ""), str) for row in value)


# The graph fields the mirror planner reads from a closure, and the shape each must have.
CLOSURE_LISTS = {"external_inputs": (_rows, "a list of objects with string paths"),
                 "generated_inputs": (_rows, "a list of objects with string paths"),
                 "compile_definitions": (_strings, "a list of strings")}
TARGET_LISTS = {"sources": _source_strings, "include_dirs": _strings, "link_targets": _strings,
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
                shape = "a list of strings with safe relative paths" if field == "sources" else "a list of strings"
                raise RecordError(f"{at}: {name} {field} must be {shape}")
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
    if not isinstance(export, dict):
        raise RecordError(f"{where}: export names the Pulp run, artifact sha256 and manifest binding sha256")
    if "run_url" not in export or "artifact_sha256" not in export:
        raise RecordError(f"{where}: export names the Pulp run, artifact sha256 and manifest binding sha256")
    if not isinstance(export["run_url"], str) or not EXPORT_RUN.match(export["run_url"]):
        raise RecordError(f"{where}: export.run_url {export['run_url']!r} is not a "
                          "https://github.com/Generous-Corp/pulp/actions/runs/<id> URL")
    if not _hex(SHA256, export["artifact_sha256"]):
        raise RecordError(f"{where}: export.artifact_sha256 is not a lowercase SHA-256")
    if set(export) != {"run_url", "artifact_sha256", "manifest_sha256"}:
        raise RecordError(f"{where}: export names the Pulp run, artifact sha256 and manifest binding sha256")
    if not _hex(SHA256, export["manifest_sha256"]):
        raise RecordError(f"{where}: export.manifest_sha256 is not a lowercase SHA-256")
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
    if export["manifest_sha256"] != manifest_binding_sha256(export["artifact_sha256"], units):
        raise RecordError(f"{where}: export.manifest_sha256 does not bind the artifact to the recorded manifests")
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
