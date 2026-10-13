#!/usr/bin/env python3
"""Verify Pulp-to-Vellum mirror export receipts (Decision 0010).

An export projects the closure manifests of one exact Pulp commit onto
`refs/heads/mirror/pulp-framework` with every path renamed under `mirror/`.
The landing merges that projected commit into Vellum and records its receipt,
closures, and commit map under `provenance/mirror/`. This verifier proves:

- the newest receipt's mirror tree is exactly `HEAD:mirror` (tree equality);
- every receipt agrees with its projected commit, closures, commit map, and
  blob-origin records;
- every closure passes the Vellum link-closure policy; and, with a Pulp
  checkout, every mirrored blob is the Pulp blob at the exported commit.

The executable form of the shared receipt specification; field rules here
win over prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


RECEIPT_SCHEMA = "pulp.vellum.export-receipt.v2"
CLOSURE_SCHEMA = "pulp.vellum.closure.v1"
INDEX_SCHEMA = "vellum.mirror-receipt-index.v1"
POLICY_SCHEMA = "vellum.mirror-link-policy.v1"
MIRROR_DIR = "mirror"
MIRROR_REF = "refs/heads/mirror/pulp-framework"
PULP_REPOSITORY = "Generous-Corp/pulp"
INDEX_PATH = "provenance/mirror/receipts/index.json"
POLICY_PATH = "provenance/mirror/link-policy.json"
ORIGINS_PATH = "provenance/mirror/blob-origins.json"
RECEIPT_DIR = "provenance/mirror/receipts/"
CLOSURE_DIR = "provenance/mirror/closures/"
COMMIT_MAP_DIR = "provenance/mirror/commit-maps/"
RECEIPT_KEYS = {
    "schema", "export_id", "pulp_repository", "pulp_sha", "mirror_ref",
    "mirror_sha", "mirror_tree", "rename", "commit_map", "units", "files",
    "generated_inputs", "tool", "created_at",
}
UNIT_KEYS = {"name", "closure_manifest", "closure_sha256", "oracle_class", "configure"}
FILE_KEYS = {"mirror_path", "pulp_path", "mode", "blob"}
GENERATED_KEYS = {"path", "sha256", "producer_paths"}
GENERATED_OPTIONAL_KEYS = {"producer_dependency"}
EXTERNAL_CACHE_PATH = re.compile(r"^@[A-Za-z0-9_.-]+/")
EXTERNAL_REPOSITORY = re.compile(r"^https://(?:github\.com|gitlab\.com|codeberg\.org|skia\.googlesource\.com|dawn\.googlesource\.com)/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*(?:\.git)?/?$")
EMBEDDED_PIN = re.compile(r"(?:^|[-_/])([0-9a-f]{40})(?=$|[-_/])", re.IGNORECASE)
POLICY_KEYS = {
    "schema", "allowed_external_targets", "forbidden_target_pattern",
    "forbidden_path_prefixes", "forbidden_definition_pattern", "approved_unmirrored_edges",
}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PATCH_SET_KEYS = {"pulp_path", "pulp_blob", "key"}
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
FILE_MODES = {"100644", "100755", "120000"}
REPLAY_METHOD = "export the pinned commit with `git archive <pin>`; in a CMake -P script, include the patch-set file whose git blob is patch_set_blob and call each name in functions, in order, with the exported tree's root; copy_tree is the digest of the result"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
    ).strip()


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def git_try(root: Path, *args: str) -> str | None:
    try:
        return git(root, *args)
    except subprocess.CalledProcessError:
        return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json(path: Path) -> Any:
    def no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)


def safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    parts = PurePosixPath(value).parts
    return ".." not in parts and "." not in parts and "\\" not in value


def validate_generated_input(value: Any, label: str) -> list[str]:
    """Validate one v2 generated-input object before reading its fields."""
    if not isinstance(value, dict):
        return [f"{label} must be an object; legacy string entries are rejected"]
    if set(value) not in (GENERATED_KEYS, GENERATED_KEYS | GENERATED_OPTIONAL_KEYS):
        return [f"{label} must contain path, sha256, producer_paths, and optional producer_dependency"]
    errors: list[str] = []
    path = value.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("@build/")
        or not safe_relative(path)
        or Path(path).is_absolute()
        or path.startswith("~")
    ):
        errors.append(f"{label}.path must be a relative @build/ path")
    if not SHA256.fullmatch(str(value.get("sha256"))):
        errors.append(f"{label}.sha256 must be a SHA-256")
    producers = value.get("producer_paths")
    if not isinstance(producers, list) or (not producers and "producer_dependency" not in value):
        errors.append(f"{label}.producer_paths must be a non-empty list unless producer_dependency is present")
    else:
        for index, producer in enumerate(producers):
            if (
                not isinstance(producer, str)
                or not safe_relative(producer)
                or Path(producer).is_absolute()
                or producer.startswith("~")
            ):
                errors.append(f"{label}.producer_paths[{index}] must be repository-relative")
    if "producer_dependency" in value:
        dependency = value["producer_dependency"]
        if not isinstance(dependency, dict) or set(dependency) != {"name", "source", "pin"}:
            errors.append(f"{label}.producer_dependency must contain name, source, and pin")
        else:
            if not isinstance(dependency["name"], str) or not dependency["name"]:
                errors.append(f"{label}.producer_dependency.name must be non-empty")
            if not EXTERNAL_REPOSITORY.fullmatch(str(dependency["source"])):
                errors.append(f"{label}.producer_dependency.source must be a known repository URL")
            if not SHA40.fullmatch(str(dependency["pin"])):
                errors.append(f"{label}.producer_dependency.pin must be a 40-hex commit SHA")
    return errors


def configure_violations(closure: dict[str, Any], label: str, *, is_canvas: bool = False) -> list[str]:
    configure = closure.get("configure")
    if not isinstance(configure, dict):
        return [f"{label}.configure must be recorded"]
    if set(configure) - {"options", "options_sha256", "identity", "label"}:
        return [f"{label}.configure has unexpected keys"]
    options = configure.get("options")
    identity = configure.get("identity")
    if not isinstance(options, dict):
        return [f"{label}.configure.options must be an object"]
    if not isinstance(identity, dict) or set(identity) != {"gpu", "skia", "text_shaping", "build_type", "system", "processor"}:
        return [f"{label}.configure.identity must contain gpu, skia, text_shaping, build_type, system, and processor"]
    errors: list[str] = []
    for option in ("CMAKE_BUILD_TYPE", "CMAKE_SYSTEM_NAME", "CMAKE_SYSTEM_PROCESSOR"):
        if option not in options or not isinstance(options[option], str) or not options[option]:
            errors.append(f"{label}.configure.options must include non-empty {option}")
    if not SHA256.fullmatch(str(configure.get("options_sha256"))):
        errors.append(f"{label}.configure.options_sha256 must be a SHA-256")
    elif hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest() != configure["options_sha256"]:
        errors.append(f"{label}.configure.options_sha256 does not match options")
    for field in ("gpu", "skia", "text_shaping"):
        if not isinstance(identity[field], bool):
            errors.append(f"{label}.configure.identity.{field} must be boolean")
    for field in ("build_type", "system", "processor"):
        if not isinstance(identity[field], str) or not identity[field]:
            errors.append(f"{label}.configure.identity.{field} must be non-empty")
    option_map = {"gpu": "PULP_ENABLE_GPU", "skia": "PULP_HAS_SKIA", "text_shaping": "PULP_TEXT_SHAPING"}
    for field, option in option_map.items():
        expected = str(options.get(option, "OFF")).strip().upper() in {"1", "ON", "TRUE", "YES", "Y"}
        if bool(identity[field]) != expected:
            errors.append(f"{label}.configure.identity.{field} disagrees with {option}")
    if "CMAKE_BUILD_TYPE" in options and identity["build_type"] != options["CMAKE_BUILD_TYPE"]:
        errors.append(f"{label}.configure.identity.build_type disagrees with CMAKE_BUILD_TYPE")
    if identity["build_type"] != closure.get("configuration"):
        errors.append(f"{label}.configure.identity.build_type disagrees with configuration")
    if identity["system"] != options.get("CMAKE_SYSTEM_NAME"):
        errors.append(f"{label}.configure.identity.system disagrees with CMAKE_SYSTEM_NAME")
    if identity["processor"] != options.get("CMAKE_SYSTEM_PROCESSOR"):
        errors.append(f"{label}.configure.identity.processor disagrees with CMAKE_SYSTEM_PROCESSOR")
    if "label" in configure and configure["label"] != "cpu-only":
        errors.append(f"{label}.configure.label must be cpu-only when present")
    if is_canvas:
        if configure.get("label") == "cpu-only":
            errors.append(f"{label}: canvas closure labelled cpu-only is refused for G2e")
        if not identity["gpu"] or not identity["skia"] or not identity["text_shaping"]:
            errors.append(f"{label}: canvas closure requires GPU, Skia, and text shaping configure")
        canvas_targets = [t for t in closure.get("targets", []) if isinstance(t, dict) and t.get("name") == "pulp-canvas"]
        definitions = {d for target in canvas_targets for d in target.get("compile_definitions", [])}
        for definition in ("PULP_HAS_SKIA=1", "PULP_HAS_TEXT_SHAPING=1"):
            if definition not in definitions:
                errors.append(f"{label}: canvas compile definitions must include {definition}")
    return errors


def host_path_violations(value: Any, label: str) -> list[str]:
    """Reject host-absolute strings anywhere in a stored manifest/receipt."""
    errors: list[str] = []
    if isinstance(value, str):
        windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", value))
        skia_match = re.search(r"@external/skia/[0-9a-f]{40}/[^;\"']*?/include/private/", value, re.IGNORECASE)
        host_scan_value = value
        if skia_match:
            host_scan_value = value[:skia_match.start()] + value[skia_match.end():]
        embedded_unix = any(
            (
                re.search(rf"(?:^|//){root}(?:/|$)", host_scan_value, re.IGNORECASE)
                or re.search(rf"(?:^|[=:\s,(;\"'])/{root}(?:/|$)", host_scan_value, re.IGNORECASE)
                or re.search(rf"(?:^|[-=:\s;\"'])-[Ii](?:system)?/{root}(?:/|$)", host_scan_value, re.IGNORECASE)
            )
            for root in ("Users", "Volumes", "home", "private")
        )
        embedded_windows = bool(re.search(r"(?i)(?:^|[^A-Za-z0-9])[A-Z]:[\\/]", value))
        unc = bool(re.search(r"\\\\[^\\/\s]+[\\/]", value))
        file_url = "file://" in value.lower()
        if Path(value).is_absolute() or value.startswith("~") or windows_absolute or embedded_unix or embedded_windows or unc or file_url:
            errors.append(f"{label} contains an absolute host path: {value}")
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                errors.extend(host_path_violations(key, f"{label}.<key>"))
            errors.extend(host_path_violations(child, f"{label}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(host_path_violations(child, f"{label}[{index}]"))
    return errors


def patch_provenance_violations(
    row: dict[str, Any], label: str, *, pulp_root: Path | None = None, pulp_sha: str | None = None
) -> list[str]:
    errors: list[str] = []
    path = row.get("path")
    patched = row.get("patched") is True
    if not SHA256.fullmatch(str(row.get("sha256", ""))):
        errors.append(f"{label}.sha256 must be a SHA-256 content digest")
    if not patched:
        if any(key in row for key in (
            "patch", "patched", "patch_set", "base_modification", "copy_tree", "patch_recipe"
        )):
            errors.append(f"{label} patch fields require patched=true")
        return errors
    if not isinstance(path, str) or not path.startswith("@external/"):
        errors.append(f"{label}.path must be @external for patched inputs")
    patch = row.get("patch")
    if not SHA256.fullmatch(str(patch)):
        errors.append(f"{label}.patch must be a SHA-256 when patched")
    patch_set = row.get("patch_set")
    if not isinstance(patch_set, dict) or set(patch_set) != PATCH_SET_KEYS:
        errors.append(f"{label}.patch_set must contain pulp_path, pulp_blob, and key")
        return errors
    source_path, source_blob, key = (patch_set.get(k) for k in ("pulp_path", "pulp_blob", "key"))
    if not isinstance(source_path, str) or not safe_relative(source_path):
        errors.append(f"{label}.patch_set.pulp_path must be repository-relative")
    if not isinstance(source_blob, str) or not SHA40.fullmatch(source_blob):
        errors.append(f"{label}.patch_set.pulp_blob must be a Git blob SHA")
    if not isinstance(key, str) or not SHA256.fullmatch(key):
        errors.append(f"{label}.patch_set.key must be a SHA-256")
    base_modification = row.get("base_modification")
    copy_tree = row.get("copy_tree")
    if base_modification != "":
        errors.append(f"{label}.base_modification must be empty")
    if not SHA256.fullmatch(str(copy_tree)):
        errors.append(f"{label}.copy_tree must be a SHA-256")
    patch_recipe = row.get("patch_recipe")
    expected_formula = "sha256(utf8('patch-set:' + patch_set.key + '\\nbase-modification:' + base_modification + '\\ncopy-tree:' + copy_tree + '\\n'))"
    if not isinstance(patch_recipe, dict) or set(patch_recipe) != {"id", "formula", "replay"}:
        errors.append(f"{label}.patch_recipe must contain id, formula, and replay")
    elif patch_recipe.get("id") != "pulp.vellum.patch-id.v1":
        errors.append(f"{label}.patch_recipe.id is not pulp.vellum.patch-id.v1")
    elif patch_recipe.get("formula") != expected_formula:
        errors.append(f"{label}.patch_recipe.formula is not the supported recipe")
    replay = patch_recipe.get("replay") if isinstance(patch_recipe, dict) else None
    replay_functions = replay.get("functions") if isinstance(replay, dict) else None
    if not isinstance(replay, dict) or set(replay) != {"method", "patch_set_blob", "functions"}:
        errors.append(f"{label}.patch_recipe.replay must contain method, patch_set_blob, and functions")
    elif replay.get("method") != REPLAY_METHOD:
        errors.append(f"{label}.patch_recipe.replay.method is not the supported replay")
    if not isinstance(replay_functions, list) or not replay_functions or not all(isinstance(name, str) and name.startswith("pulp_patch_") for name in replay_functions):
        errors.append(f"{label}.patch_recipe.replay.functions must be non-empty pulp_patch_* names")
    expected_recipe = f"patch-set:{key}\nbase-modification:{base_modification}\ncopy-tree:{copy_tree}\n"
    if SHA256.fullmatch(str(patch)) and hashlib.sha256(expected_recipe.encode()).hexdigest() != patch:
        errors.append(f"{label}.patch does not recompute from patch-set provenance")
    if isinstance(replay, dict) and replay.get("patch_set_blob") != source_blob:
        errors.append(f"{label}.patch_recipe.replay.patch_set_blob must equal patch_set.pulp_blob")
    if pulp_root is not None and isinstance(pulp_sha, str) and isinstance(source_path, str):
        blob = git_try(pulp_root, "rev-parse", f"{pulp_sha}:{source_path}")
        if blob != source_blob:
            errors.append(f"{label}.patch_set.pulp_blob does not match Pulp {pulp_sha}:{source_path}")
        content = subprocess.run(
            ["git", "show", f"{pulp_sha}:{source_path}"], cwd=pulp_root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if content.returncode != 0 or hashlib.sha256(content.stdout).hexdigest() != key:
            errors.append(f"{label}.patch_set.key does not match the Pulp patch-set blob")
        if isinstance(replay, dict) and isinstance(replay_functions, list) and all(isinstance(name, str) and name for name in replay_functions):
            positions = []
            text = content.stdout.decode("utf-8", errors="replace") if content.returncode == 0 else ""
            for name in replay_functions:
                match = re.search(rf"(?m)^function\s*\(\s*{re.escape(name)}\b", text)
                if match is None:
                    errors.append(f"{label}.patch_recipe.replay function {name} is absent from the patch-set blob")
                else:
                    positions.append(match.start())
            if positions != sorted(positions) or len(set(positions)) != len(positions):
                errors.append(f"{label}.patch_recipe.replay order does not match the patch-set blob")
    return errors


def validate_external_inputs(
    value: Any, label: str, *, pulp_root: Path | None = None, pulp_sha: str | None = None
) -> list[str]:
    """Require cache-relative external-input identities in v2 closures."""
    if not isinstance(value, list):
        return [f"{label} must be a list"]
    errors: list[str] = []
    for index, row in enumerate(value):
        where = f"{label}[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{where} must be an object")
            continue
        path = row.get("path")
        if not isinstance(path, str) or not safe_relative(path) or not EXTERNAL_CACHE_PATH.match(path):
            errors.append(f"{where}.path must be a cache-relative identity such as @fetchcontent/name/path")
        source = row.get("source")
        if not isinstance(source, str) or source == "build-tree" or not EXTERNAL_REPOSITORY.fullmatch(source):
            errors.append(f"{where}.source must be a known repository URL")
        pin = row.get("pin")
        if not isinstance(pin, str) or not SHA40.fullmatch(pin):
            errors.append(f"{where}.pin must be a 40-hex commit SHA")
        errors.extend(patch_provenance_violations(row, where, pulp_root=pulp_root, pulp_sha=pulp_sha))
        patch = row.get("patch")
        embedded = EMBEDDED_PIN.search(path) if isinstance(path, str) else None
        if embedded and (not isinstance(pin, str) or embedded.group(1).lower() != pin.lower()):
            errors.append(f"{where}.path embedded pin must equal pin")
        if isinstance(path, str) and path.startswith("@external/"):
            segments = path.split("/")
            positional = segments[2] if len(segments) > 2 else ""
            expected = pin
            if row.get("patched") is True and isinstance(patch, str) and SHA256.fullmatch(patch):
                expected = f"{pin}+patch-{patch[:16]}"
            if positional != expected:
                errors.append(
                    f"{where}.path marker must equal pin+patch-<patch[:16]>"
                    if row.get("patched") is True
                    else f"{where}.path @external pin segment must equal pin"
                )
    return errors


def validate_closure_manifest(
    closure: dict[str, Any], label: str, *, pulp_root: Path | None = None, pulp_sha: str | None = None,
    is_canvas: bool = False,
) -> list[str]:
    """Validate path-bearing closure fields before link-closure comparison."""
    errors = host_path_violations(closure, label)
    errors.extend(configure_violations(closure, label, is_canvas=is_canvas))
    errors.extend(validate_external_inputs(
        closure.get("external_inputs", []), f"{label}.external_inputs",
        pulp_root=pulp_root, pulp_sha=pulp_sha,
    ))
    for key in ("files", "resources", "public_include_dirs"):
        values = closure.get(key, [])
        if not isinstance(values, list):
            errors.append(f"{label}.{key} must be a list")
            continue
        for index, path in enumerate(values):
            if not safe_relative(path):
                errors.append(f"{label}.{key}[{index}] must be repository/cache-relative")
    targets = closure.get("targets", [])
    if not isinstance(targets, list):
        errors.append(f"{label}.targets must be a list")
        targets = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            errors.append(f"{label}.targets[{index}] must be an object")
            continue
        for key in ("sources", "include_dirs"):
            values = target.get(key, [])
            if not isinstance(values, list):
                errors.append(f"{label}.targets[{index}].{key} must be a list")
                continue
            for path_index, path in enumerate(values):
                if not safe_relative(path):
                    errors.append(f"{label}.targets[{index}].{key}[{path_index}] must be repository/cache-relative")
    if "unmirrored" in closure:
        errors.append(f"{label}.unmirrored is not an accepted closure field")
    return errors


def tree_entries(root: Path, commit: str) -> list[dict[str, str]] | None:
    output = git_try(root, "ls-tree", "-r", "--full-tree", commit)
    if output is None:
        return None
    entries = []
    for line in output.splitlines():
        metadata, path = line.split("\t", 1)
        mode, kind, blob = metadata.split(" ")
        entries.append({"mirror_path": path, "mode": mode, "blob": blob, "kind": kind})
    return entries


def validate_receipt_shape(receipt: Any, label: str) -> list[str]:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        return [f"{label}: receipt keys must be exactly {sorted(RECEIPT_KEYS)}"]
    errors: list[str] = []
    errors.extend(host_path_violations(receipt, label))
    expected = {
        "schema": RECEIPT_SCHEMA,
        "pulp_repository": PULP_REPOSITORY,
        "mirror_ref": MIRROR_REF,
        "rename": {"from_prefix": "", "to_prefix": "mirror/"},
    }
    for key, value in expected.items():
        if receipt[key] != value:
            errors.append(f"{label}: {key} must be {value!r}")
    for key in ("pulp_sha", "mirror_sha", "mirror_tree"):
        if not isinstance(receipt[key], str) or not SHA40.fullmatch(receipt[key]):
            errors.append(f"{label}: {key} is not a full object ID")
    pulp_sha = receipt["pulp_sha"] if isinstance(receipt["pulp_sha"], str) else ""
    export_id = receipt["export_id"]
    if not isinstance(export_id, str) or not re.fullmatch(
        r"\d{8}-" + re.escape(pulp_sha[:12]) + r"\Z", export_id
    ) or len(pulp_sha) != 40:
        errors.append(f"{label}: export_id must be YYYYMMDD-<pulp_sha[:12]>")
        export_id = ""
    if not isinstance(receipt["created_at"], str) or not UTC.fullmatch(receipt["created_at"]):
        errors.append(f"{label}: created_at must be a UTC timestamp")
    commit_map = receipt["commit_map"]
    if (
        not isinstance(commit_map, dict)
        or set(commit_map) != {"path", "sha256"}
        or commit_map.get("path") != f"{COMMIT_MAP_DIR}{export_id}.txt"
        or not SHA256.fullmatch(str(commit_map.get("sha256")))
    ):
        errors.append(f"{label}: commit_map must name {COMMIT_MAP_DIR}<export_id>.txt and its sha256")
    tool = receipt["tool"]
    if (
        not isinstance(tool, dict)
        or set(tool) != {"path", "pulp_blob"}
        or tool.get("path") != "tools/scripts/vellum_closure.py"
        or not SHA40.fullmatch(str(tool.get("pulp_blob")))
    ):
        errors.append(f"{label}: tool must name tools/scripts/vellum_closure.py and its blob")
    units = receipt["units"]
    if not isinstance(units, list) or not units:
        errors.append(f"{label}: units must be a non-empty list")
    else:
        names: set[str] = set()
        for index, unit in enumerate(units):
            where = f"{label}: units[{index}]"
            if not isinstance(unit, dict) or set(unit) != UNIT_KEYS:
                errors.append(f"{where} has unexpected keys")
                continue
            unit_configure = unit["configure"]
            if not isinstance(unit_configure, dict) or set(unit_configure) != {"identity", "options_sha256", "label"}:
                errors.append(f"{where} configure must contain identity, options_sha256, and label")
            elif not isinstance(unit_configure["identity"], dict) or set(unit_configure["identity"]) != {"gpu", "skia", "text_shaping", "build_type", "system", "processor"}:
                errors.append(f"{where} configure identity is malformed")
            if not isinstance(unit["name"], str) or not unit["name"] or unit["name"] in names:
                errors.append(f"{where} name is empty or duplicated")
            names.add(str(unit["name"]))
            if unit["oracle_class"] not in {"identity", "divergent"}:
                errors.append(f"{where} oracle_class must be identity or divergent")
            manifest = unit["closure_manifest"]
            if (
                not safe_relative(manifest)
                or not manifest.startswith(f"{CLOSURE_DIR}{export_id}/")
                or not manifest.endswith(".json")
            ):
                errors.append(f"{where} closure_manifest must live under {CLOSURE_DIR}<export_id>/")
            if not SHA256.fullmatch(str(unit["closure_sha256"])):
                errors.append(f"{where} closure_sha256 is not a SHA-256")
    files = receipt["files"]
    if not isinstance(files, list) or not files:
        errors.append(f"{label}: files must be a non-empty list")
    else:
        previous = ""
        for index, row in enumerate(files):
            where = f"{label}: files[{index}]"
            if not isinstance(row, dict) or set(row) != FILE_KEYS:
                errors.append(f"{where} has unexpected keys")
                continue
            if not safe_relative(row["pulp_path"]):
                errors.append(f"{where} pulp_path is not repository-relative")
            if row["mirror_path"] != f"mirror/{row['pulp_path']}":
                errors.append(f"{where} mirror_path must be mirror/<pulp_path>")
            if row["mode"] not in FILE_MODES:
                errors.append(f"{where} mode is not a file mode")
            if not SHA40.fullmatch(str(row["blob"])):
                errors.append(f"{where} blob is not a full object ID")
            if str(row["mirror_path"]) <= previous:
                errors.append(f"{where} files must be sorted and unique by mirror_path")
            previous = str(row["mirror_path"])
    generated = receipt["generated_inputs"]
    if not isinstance(generated, list):
        errors.append(f"{label}: generated_inputs must be a list")
    else:
        for index, row in enumerate(generated):
            where = f"{label}: generated_inputs[{index}]"
            errors.extend(validate_generated_input(row, where))
    return errors


def load_policy(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = root / POLICY_PATH
    try:
        policy = strict_json(path)
    except (OSError, ValueError) as exc:
        return None, [f"{POLICY_PATH}: unreadable: {exc}"]
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        return None, [f"{POLICY_PATH}: keys must be exactly {sorted(POLICY_KEYS)}"]
    errors = []
    if policy["schema"] != POLICY_SCHEMA:
        errors.append(f"{POLICY_PATH}: schema must be {POLICY_SCHEMA}")
    for key in ("forbidden_target_pattern", "forbidden_definition_pattern"):
        try:
            re.compile(policy[key])
        except (re.error, TypeError):
            errors.append(f"{POLICY_PATH}: {key} is not a regular expression")
    for key in ("allowed_external_targets", "forbidden_path_prefixes"):
        if not isinstance(policy[key], list) or not all(isinstance(x, str) for x in policy[key]):
            errors.append(f"{POLICY_PATH}: {key} must be a list of strings")
    if any(is_pulp_target(x) for x in policy.get("allowed_external_targets", [])):
        errors.append(f"{POLICY_PATH}: allowed_external_targets cannot contain Pulp targets")
    exceptions = policy.get("approved_unmirrored_edges")
    required = {"id", "source", "target", "platforms", "owner", "approver", "exit_criterion"}
    if not isinstance(exceptions, list):
        errors.append(f"{POLICY_PATH}: approved_unmirrored_edges must be a list")
    else:
        for index, entry in enumerate(exceptions):
            where = f"{POLICY_PATH}: approved_unmirrored_edges[{index}]"
            if not isinstance(entry, dict) or set(entry) != required:
                errors.append(f"{where} must contain id, source, target, platforms, owner, approver, and exit_criterion")
                continue
            if not all(isinstance(entry[k], str) and entry[k] for k in ("id", "source", "target", "owner", "approver", "exit_criterion")):
                errors.append(f"{where} string fields must be non-empty")
            if not isinstance(entry["platforms"], list) or not entry["platforms"] or not all(isinstance(p, str) and p for p in entry["platforms"]):
                errors.append(f"{where}.platforms must be a non-empty list")
            if not re.fullmatch(r"team-lead \d{4}-\d{2}-\d{2}", entry["approver"]):
                errors.append(f"{where}.approver must be team-lead YYYY-MM-DD")
    return (policy if not errors else None), errors


def canonical_target(name: str) -> str:
    return "pulp-" + name.removeprefix("pulp::") if name.startswith("pulp::") else name


def is_pulp_target(name: str) -> bool:
    return canonical_target(name).startswith(("pulp-", "pulp_"))


def approved_unmirrored_edge(policy: dict[str, Any], source: str, target: str, system: Any, used: set[str] | None = None) -> bool:
    """Match one exact Pulp edge to a current Vellum-owned exception."""
    for entry in policy.get("approved_unmirrored_edges", []):
        if entry.get("source") != source or entry.get("target") != target:
            continue
        if not isinstance(system, str) or system not in entry.get("platforms", []):
            continue
        if entry.get("owner") and entry.get("approver"):
            if used is not None:
                used.add(str(entry.get("id")))
            return True
    return False


def link_closure_findings(
    closures: dict[str, dict[str, Any]], policy: dict[str, Any], *, enforce_stale: bool = False
) -> list[str]:
    """Re-check machine-generated closures against the Vellum-owned policy."""
    findings: list[str] = []
    forbidden_target = re.compile(policy["forbidden_target_pattern"], re.IGNORECASE)
    forbidden_define = re.compile(policy["forbidden_definition_pattern"])
    allowed_external = set(policy["allowed_external_targets"])
    used_exceptions: set[str] = set()
    exported_targets = {
        canonical_target(str(target.get("name", "")))
        for closure in closures.values()
        for target in closure.get("targets", [])
        if isinstance(target, dict)
    }
    for unit, closure in sorted(closures.items()):
        if closure.get("missing_targets"):
            findings.append(f"{unit}: missing targets {closure['missing_targets']}")
        system = ((closure.get("configure") or {}).get("identity") or {}).get("system")
        direct_links = {
            canonical_target(str(link))
            for target in closure.get("targets", []) if isinstance(target, dict)
            for link in target.get("link_targets", [])
        }
        for link in closure.get("transitive_link_targets", []):
            canonical = canonical_target(str(link))
            if canonical in direct_links:
                continue
            if is_pulp_target(canonical) and canonical not in exported_targets:
                findings.append(f"{unit}: transitive unmirrored target {canonical} lacks an exact source edge exception")
            elif canonical not in exported_targets and canonical not in allowed_external:
                if forbidden_target.search(canonical):
                    findings.append(f"{unit}: forbidden link target {canonical}")
                else:
                    findings.append(f"{unit}: link target {canonical} is neither exported nor allowed")
        for target in closure.get("targets", []):
            if not isinstance(target, dict):
                findings.append(f"{unit}: targets entry must be an object")
                continue
        for target in closure.get("targets", []):
            if not isinstance(target, dict):
                continue
            source = canonical_target(str(target.get("name", "")))
            for link in target.get("link_targets", []):
                canonical = canonical_target(str(link))
                if canonical in exported_targets:
                    continue
                if is_pulp_target(canonical) and approved_unmirrored_edge(policy, source, canonical, system, used_exceptions):
                    continue
                if forbidden_target.search(canonical):
                    findings.append(f"{unit}: forbidden link target {canonical}")
                elif is_pulp_target(canonical):
                    findings.append(f"{unit}: unmirrored edge {source}->{canonical} lacks a current approved exception")
                elif canonical not in allowed_external:
                    findings.append(f"{unit}: link target {canonical} is neither exported nor allowed")
        for path in closure.get("files", []):
            if any(path.startswith(prefix) for prefix in policy["forbidden_path_prefixes"]):
                findings.append(f"{unit}: closure file under a forbidden Pulp root: {path}")
        for definition in closure.get("compile_definitions", []):
            if forbidden_define.search(definition):
                findings.append(f"{unit}: forbidden compile definition {definition}")
    if enforce_stale:
        for entry in policy.get("approved_unmirrored_edges", []):
            if canonical_target(str(entry.get("source", ""))) in exported_targets and entry.get("id") not in used_exceptions:
                findings.append(f"stale approved unmirrored exception {entry.get('id')}")
    return findings


def verify_receipt(
    root: Path, receipt_path: str, receipt: dict[str, Any], policy: dict[str, Any] | None,
    origins: list[dict[str, Any]], *, newest: bool, head: str, pulp_root: Path | None = None,
) -> list[str]:
    label = receipt_path
    errors = validate_receipt_shape(receipt, label)
    if errors:
        return errors
    export_id = receipt["export_id"]
    if receipt_path != f"{RECEIPT_DIR}{export_id}.json":
        errors.append(f"{label}: file name must be {RECEIPT_DIR}<export_id>.json")
    mirror_sha = receipt["mirror_sha"]
    if not git_ok(root, "cat-file", "-e", f"{mirror_sha}^{{commit}}"):
        return errors + [f"{label}: mirror_sha {mirror_sha} is not a commit in this repository"]
    if not git_ok(root, "merge-base", "--is-ancestor", mirror_sha, head):
        errors.append(f"{label}: mirror_sha is not an ancestor of HEAD")
    top = git_try(root, "ls-tree", "--name-only", mirror_sha) or ""
    if top.splitlines() != [MIRROR_DIR]:
        errors.append(f"{label}: mirror_sha root tree must contain only mirror/")
    if git_try(root, "rev-parse", f"{mirror_sha}:{MIRROR_DIR}") != receipt["mirror_tree"]:
        errors.append(f"{label}: mirror_tree differs from {mirror_sha}:mirror")
    if newest and git_try(root, "rev-parse", f"{head}:{MIRROR_DIR}") != receipt["mirror_tree"]:
        errors.append(f"{label}: HEAD:mirror is not the newest receipt's mirror tree")
    entries = tree_entries(root, mirror_sha) or []
    projected = [
        {"mirror_path": e["mirror_path"], "mode": e["mode"], "blob": e["blob"]}
        for e in entries
        if e["kind"] == "blob"
    ]
    if any(e["kind"] != "blob" for e in entries):
        errors.append(f"{label}: mirror_sha contains non-blob entries (submodules)")
    recorded = [
        {"mirror_path": f["mirror_path"], "mode": f["mode"], "blob": f["blob"]}
        for f in receipt["files"]
    ]
    if projected != recorded:
        errors.append(f"{label}: files differ from the projected tree of mirror_sha")

    commit_map = root / receipt["commit_map"]["path"]
    if not commit_map.is_file() or sha256_file(commit_map) != receipt["commit_map"]["sha256"]:
        errors.append(f"{label}: commit map is missing or its sha256 differs")
    elif f"{receipt['pulp_sha']} {mirror_sha}" not in commit_map.read_text(encoding="utf-8").splitlines():
        errors.append(f"{label}: commit map lacks the row '<pulp_sha> <mirror_sha>'")

    closures: dict[str, dict[str, Any]] = {}
    for unit in receipt["units"]:
        path = root / unit["closure_manifest"]
        if not path.is_file() or sha256_file(path) != unit["closure_sha256"]:
            errors.append(f"{label}: closure {unit['closure_manifest']} is missing or differs")
            continue
        try:
            closure = strict_json(path)
        except ValueError as exc:
            errors.append(f"{label}: closure {unit['closure_manifest']} unreadable: {exc}")
            continue
        if (
            not isinstance(closure, dict)
            or closure.get("schema") != CLOSURE_SCHEMA
            or closure.get("source_root") is not None
            or closure.get("build_dir") is not None
            or not isinstance(closure.get("files"), list)
        ):
            errors.append(
                f"{label}: closure {unit['name']} must be {CLOSURE_SCHEMA} with null source_root/build_dir"
            )
            continue
        errors.extend(validate_closure_manifest(
            closure, f"{label}: closure {unit['name']}",
            pulp_root=pulp_root, pulp_sha=receipt["pulp_sha"],
            is_canvas=unit["name"] == "canvas",
        ))
        if unit["name"] != "canvas" and any(
            isinstance(target, dict) and target.get("name") == "pulp-canvas"
            for target in closure.get("targets", [])
        ):
            errors.append(f"{label}: pulp-canvas may only appear in the canvas D3 unit")
        expected_configure = {
            "identity": closure.get("configure", {}).get("identity"),
            "options_sha256": closure.get("configure", {}).get("options_sha256"),
            "label": closure.get("configure", {}).get("label", ""),
        }
        if unit["configure"] != expected_configure:
            errors.append(f"{label}: unit {unit['name']} configure differs from its closure configure")
        closure_generated_inputs = closure.get("generated_inputs", [])
        for index, generated_input in enumerate(
            closure_generated_inputs if isinstance(closure_generated_inputs, list) else [closure_generated_inputs]
        ):
            errors.extend(
                validate_generated_input(
                    generated_input,
                    f"{label}: closure {unit['name']} generated_inputs[{index}]",
                )
            )
        closures[unit["name"]] = closure
    if len(closures) == len(receipt["units"]):
        closure_paths = {
            p for c in closures.values() for p in c["files"] if not p.startswith("@build/")
        }
        if closure_paths != {f["pulp_path"] for f in receipt["files"]}:
            errors.append(f"{label}: closure files differ from the exported file set")
        generated = {
            p for c in closures.values() for p in c["files"] if p.startswith("@build/")
        } | {
            generated_input["path"]
            for c in closures.values()
            for generated_input in c.get("generated_inputs", [])
            if isinstance(generated_input, dict) and isinstance(generated_input.get("path"), str)
        }
        if generated != {g["path"] for g in receipt["generated_inputs"]}:
            errors.append(f"{label}: generated_inputs differ from the closures' @build/ inputs")
        exported = {f["pulp_path"] for f in receipt["files"]}
        for row in receipt["generated_inputs"]:
            missing = [p for p in row["producer_paths"] if p not in exported]
            if missing:
                errors.append(f"{label}: generated input {row['path']} producers not exported: {missing}")
        if policy is not None:
            errors.extend(f"{label}: {f}" for f in link_closure_findings(closures, policy, enforce_stale=True))

    recorded_origins = {
        (o.get("mirror_path"), o.get("git_blob_sha"))
        for o in origins
        if o.get("pulp_commit") == receipt["pulp_sha"] and o.get("export_receipt") == receipt_path
    }
    missing_origins = [
        f["mirror_path"] for f in receipt["files"]
        if (f["mirror_path"], f["blob"]) not in recorded_origins
    ]
    if missing_origins:
        errors.append(
            f"{label}: {len(missing_origins)} files lack a blob-origin record for this receipt"
        )
    return errors


def verify_receipt_against_pulp(pulp_root: Path, receipt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pulp_sha = receipt["pulp_sha"]
    if not git_ok(pulp_root, "cat-file", "-e", f"{pulp_sha}^{{commit}}"):
        return [f"Pulp commit {pulp_sha} is not available in {pulp_root}"]
    main = git_try(pulp_root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    if main is None:
        return ["Pulp checkout has no refs/remotes/origin/main"]
    if main is None or not git_ok(pulp_root, "merge-base", "--is-ancestor", pulp_sha, main):
        errors.append(f"Pulp commit {pulp_sha} is not reachable from Pulp main")
    listing = git_try(pulp_root, "ls-tree", "-r", "--full-tree", pulp_sha) or ""
    origin: dict[str, tuple[str, str]] = {}
    for line in listing.splitlines():
        metadata, path = line.split("\t", 1)
        mode, _kind, blob = metadata.split(" ")
        origin[path] = (mode, blob)
    for row in receipt["files"]:
        if origin.get(row["pulp_path"]) != (row["mode"], row["blob"]):
            errors.append(f"{row['mirror_path']} is not Pulp {pulp_sha}:{row['pulp_path']}")
    tool = receipt["tool"]
    if origin.get(tool["path"], ("", ""))[1] != tool["pulp_blob"]:
        errors.append(f"tool blob differs from Pulp {pulp_sha}:{tool['path']}")
    return errors


def landed_receipts(root: Path) -> list[str]:
    """Receipt paths in the index, or none when the index is unreadable."""
    try:
        index = strict_json(root / INDEX_PATH)
    except (OSError, ValueError):
        return []
    receipts = index.get("receipts") if isinstance(index, dict) else None
    return [r for r in receipts if isinstance(r, str)] if isinstance(receipts, list) else []


def verify(root: Path, pulp_root: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    head = git(root, "rev-parse", "HEAD")
    try:
        index = strict_json(root / INDEX_PATH)
    except (OSError, ValueError) as exc:
        return {"status": "fail", "receipts": [], "errors": [f"{INDEX_PATH}: unreadable: {exc}"]}
    if (
        not isinstance(index, dict)
        or set(index) != {"schema", "receipts"}
        or index["schema"] != INDEX_SCHEMA
        or not isinstance(index["receipts"], list)
        or not all(isinstance(x, str) for x in index["receipts"])
        or len(set(index["receipts"])) != len(index["receipts"])
    ):
        return {"status": "fail", "receipts": [], "errors": [f"{INDEX_PATH}: malformed index"]}
    receipts: list[str] = index["receipts"]
    policy, policy_errors = load_policy(root)
    errors.extend(policy_errors)
    try:
        origins_doc = strict_json(root / ORIGINS_PATH)
        origins = origins_doc.get("records", []) if isinstance(origins_doc, dict) else []
    except (OSError, ValueError) as exc:
        errors.append(f"{ORIGINS_PATH}: unreadable: {exc}")
        origins = []
    stored = sorted(
        path.relative_to(root).as_posix()
        for path in (root / RECEIPT_DIR).glob("*.json")
        if path.name != "index.json"
    )
    if stored != sorted(receipts):
        errors.append("receipt files on disk differ from the receipt index")
    has_mirror = git_ok(root, "cat-file", "-e", f"{head}:{MIRROR_DIR}")
    if not receipts and has_mirror:
        errors.append("mirror/ exists at HEAD without an export receipt")
    pulp_checked = False
    for position, receipt_path in enumerate(receipts):
        try:
            receipt = strict_json(root / receipt_path)
        except (OSError, ValueError) as exc:
            errors.append(f"{receipt_path}: unreadable: {exc}")
            continue
        newest = position == len(receipts) - 1
        receipt_errors = verify_receipt(
            root, receipt_path, receipt, policy, origins, newest=newest, head=head,
            pulp_root=pulp_root,
        )
        errors.extend(receipt_errors)
        if pulp_root is not None and not receipt_errors:
            errors.extend(f"{receipt_path}: {e}" for e in verify_receipt_against_pulp(pulp_root, receipt))
            pulp_checked = True
    return {
        "status": "pass" if not errors else "fail",
        "receipts": receipts,
        "pulp_identity_checked": pulp_checked,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--pulp-root", type=Path)
    parser.add_argument("--require-pulp-identity", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.root.resolve(), args.pulp_root.resolve() if args.pulp_root else None)
    if args.require_pulp_identity and report["receipts"] and not report["pulp_identity_checked"]:
        report["status"] = "fail"
        report["errors"].append("receipts exist but Pulp identity was not proved")
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
