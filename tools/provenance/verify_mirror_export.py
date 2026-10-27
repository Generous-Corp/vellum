#!/usr/bin/env python3
"""Verify Pulp-to-Vellum mirror export receipts (Decision 0010).

An export projects the closure manifests of one exact Pulp commit onto
`refs/heads/mirror/pulp-framework` with every path renamed under `mirror/`.
The landing merges that projected commit into Vellum and records its receipt,
closures, and commit map under `provenance/mirror/`. This verifier proves:

- the newest receipt's mirror tree is exactly `HEAD:mirror` (tree equality);
- every receipt agrees with its projected commit, closures, commit map, and
  blob-origin records;
- a receipt whose Pulp commit git-filter-repo pruned (it changed no exported
  path) names the ancestor the mirror commit rewrites (`mirror_source`), and
  the commit map, the shipped ref map and, with a Pulp checkout, Pulp itself
  attest that ancestor;
- receipts landed at the merge base are neither removed nor changed;
- every closure passes the Vellum link-closure policy; and, with a Pulp
  checkout, every mirrored blob is the Pulp blob at the exported commit.

The executable form of the shared receipt specification; field rules here
win over prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
REF_MAP_DIR = "provenance/mirror/ref-maps/"
RECEIPT_KEYS = {
    "schema", "export_id", "pulp_repository", "pulp_sha", "mirror_ref",
    "mirror_sha", "mirror_tree", "rename", "commit_map", "units", "files",
    "generated_inputs", "tool", "created_at",
}
# Present only when git-filter-repo pruned pulp_sha: the export ref then lands
# on the rewrite of mirror_source.sha, its nearest ancestor that changes an
# exported path.
RECEIPT_OPTIONAL_KEYS = {"mirror_source"}
MIRROR_SOURCE_KEYS = {"sha", "ref_map", "proof"}
MIRROR_SOURCE_PROOF_KEYS = {
    "commit_map_rows", "ref_map_row", "source_is_ancestor", "exported_paths_equal",
    "exported_path_count", "exported_blobs_sha256",
}
# git-filter-repo's commit-map value for a commit it pruned.
PRUNED_SHA = "0" * 40
# The ref the exporter rewrites; its ref-map row binds pulp_sha to mirror_sha.
EXPORT_SOURCE_REF = "refs/heads/export-source"
UNIT_KEYS = {"name", "closure_manifest", "closure_sha256", "oracle_class", "configure"}
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
FILE_KEYS = {"mirror_path", "pulp_path", "mode", "blob"}
GENERATED_KEYS = {"path", "sha256", "producer_paths"}
GENERATED_OPTIONAL_KEYS = {"producer_dependency", "normalization"}
# Pulp hashes build_info.hpp with its configure timestamp replaced by this
# placeholder, so the recorded digest reproduces across configures.
GENERATED_NORMALIZATION = {"build_timestamp": "<build-timestamp>"}
# The only generated input Pulp normalizes.
GENERATED_NORMALIZED_PATH = re.compile(r"^@build/core/runtime/generated/(?:[^/]+/)*pulp/runtime/build_info\.hpp$")
EXTERNAL_LINK_KEYS = {"dependency", "source", "pin", "asset_sha256"}
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
    if not GENERATED_KEYS <= set(value) <= GENERATED_KEYS | GENERATED_OPTIONAL_KEYS:
        return [f"{label} must contain path, sha256, producer_paths, and optional producer_dependency or normalization"]
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
    if "normalization" in value and value["normalization"] != GENERATED_NORMALIZATION:
        errors.append(f"{label}.normalization must be {GENERATED_NORMALIZATION}")
    if "normalization" in value and not GENERATED_NORMALIZED_PATH.fullmatch(str(path)):
        errors.append(f"{label}.normalization is only accepted on the generated pulp/runtime/build_info.hpp")
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


# Flags whose argument may be glued to them (`-I/x`, `-isystem/x`), longest first.
PATH_FLAG_PREFIX = re.compile(
    r"\A-(?:idirafter|iframework|imacros|include|isysroot|isystem|iquote|I|L|F|D|U|o)"
)
# Separators between the flags, defines, list items and quoted values a stored
# string can carry.
PATH_TOKEN_SEPARATORS = re.compile(r"[\s;,=()<>\"'`]+")
# The cache roots Pulp's closure exporter writes (vellum_closure.py): the
# configured build tree, pinned external sources and system packages.
CACHE_PREFIX = re.compile(r"\A@(?:build|external|system)/")
# Fixed recipe text whose exact value is checked where it is used.
HOST_PATH_EXEMPT_LITERALS = frozenset({
    REPLAY_METHOD,
    "sha256(utf8('patch-set:' + patch_set.key + '\\nbase-modification:' + base_modification"
    " + '\\ncopy-tree:' + copy_tree + '\\n'))",
})


def path_token_allowed(token: str) -> bool:
    """A token that names a path must be cache-relative (`@cache/...`),
    repository-relative, or an https URL; nothing that resolves on a host."""
    if "\\" in token:
        return False
    if token.startswith("https://"):
        return ".." not in token[len("https://"):].split("/")
    cache = CACHE_PREFIX.match(token)
    body = token[cache.end():] if cache else token
    if not cache and (token.startswith(("/", "~", "@")) or ":" in token.split("/", 1)[0]):
        return False
    segments = body.split("/")
    if segments[-1] == "":
        segments.pop()
    return bool(segments) and all(segment not in {"", ".", ".."} for segment in segments)


def host_path_violations(value: Any, label: str) -> list[str]:
    """Allow only cache- or repository-relative paths anywhere in a stored
    manifest or receipt. Each string is split into its flags, defines and
    list items, and every token that names a path must pass
    path_token_allowed; a host path is refused whatever its root is."""
    errors: list[str] = []
    if isinstance(value, str):
        if value in HOST_PATH_EXEMPT_LITERALS:
            return errors
        for piece in PATH_TOKEN_SEPARATORS.split(value):
            token = PATH_FLAG_PREFIX.sub("", piece, count=1) if piece.startswith("-") else piece
            if ("/" in token or "\\" in token or token.startswith("~")) and not path_token_allowed(token):
                errors.append(f"{label} contains an absolute host path: {value}")
                break
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


def pulp_skia_pin(pulp_root: Path, pulp_sha: str) -> dict[str, Any] | None:
    """Skia's repository, commit and release-asset digests in Pulp's dependency
    manifest at ``pulp_sha``, read the way Pulp's closure export reads them."""
    text = git_try(pulp_root, "show", f"{pulp_sha}:tools/deps/manifest.json")
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    rows = payload.get("dependencies", []) if isinstance(payload, dict) else payload
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("name", "")).lower() != "skia":
            continue
        determinism = row.get("determinism") if isinstance(row.get("determinism"), dict) else {}
        commit = str(determinism.get("skia_commit", ""))
        if not SHA40.fullmatch(commit):
            return None
        assets = {
            str(asset.get("sha256", "")).lower()
            for asset in (determinism.get("release_assets") or {}).values()
            if isinstance(asset, dict) and SHA256.fullmatch(str(asset.get("sha256", "")).lower())
        }
        return {"source": str(row.get("repository")), "pin": commit, "assets": assets}
    return None


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
        links = target.get("external_link_inputs", [])
        if not isinstance(links, list):
            errors.append(f"{label}.targets[{index}].external_link_inputs must be a list")
            links = []
        for link_index, link in enumerate(links):
            where = f"{label}.targets[{index}].external_link_inputs[{link_index}]"
            if not isinstance(link, dict) or set(link) != EXTERNAL_LINK_KEYS:
                errors.append(f"{where} must contain dependency, source, pin, and asset_sha256")
                continue
            if link["dependency"] != "skia" or not EXTERNAL_REPOSITORY.fullmatch(str(link["source"])) \
                    or not SHA40.fullmatch(str(link["pin"])) or not SHA256.fullmatch(str(link["asset_sha256"])):
                errors.append(f"{where} must name the Skia repository, a 40-hex pin, and a SHA-256 asset")
        defines = target.get("compile_definitions", [])
        uses_skia = isinstance(defines, list) and "PULP_HAS_SKIA=1" in defines
        skia_links = [link for link in links if isinstance(link, dict) and link.get("dependency") == "skia"]
        if uses_skia and not skia_links:
            errors.append(f"{label}.targets[{index}] compiles against Skia but records no Skia link input")
        if skia_links and not uses_skia:
            errors.append(f"{label}.targets[{index}] records a Skia link input but does not compile against Skia")
        if len(skia_links) > 1:
            errors.append(f"{label}.targets[{index}] records more than one Skia link input")
        if skia_links and pulp_root is not None and isinstance(pulp_sha, str):
            skia = pulp_skia_pin(pulp_root, pulp_sha)
            if skia is None:
                errors.append(f"{label}.targets[{index}] Skia link input cannot be checked: Pulp {pulp_sha} records no Skia pin")
            else:
                for link in skia_links:
                    if link.get("source") != skia["source"] or link.get("pin") != skia["pin"] \
                            or link.get("asset_sha256") not in skia["assets"]:
                        errors.append(
                            f"{label}.targets[{index}] Skia link input does not match Pulp {pulp_sha}'s manifest pin and assets"
                        )
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
    if not isinstance(receipt, dict) or not RECEIPT_KEYS <= set(receipt) <= RECEIPT_KEYS | RECEIPT_OPTIONAL_KEYS:
        return [
            f"{label}: receipt keys must be exactly {sorted(RECEIPT_KEYS)}, "
            f"plus optionally {sorted(RECEIPT_OPTIONAL_KEYS)}"
        ]
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
            elif not UNIT_NAME.fullmatch(unit["name"]):
                errors.append(f"{where} name must be a plain unit name")
            names.add(str(unit["name"]))
            if not isinstance(unit["oracle_class"], str) or unit["oracle_class"] not in {"identity", "divergent"}:
                errors.append(f"{where} oracle_class must be identity or divergent")
            manifest = unit["closure_manifest"]
            if (
                not safe_relative(manifest)
                or not manifest.startswith(f"{CLOSURE_DIR}{export_id}/")
                or not manifest.endswith(".json")
            ):
                errors.append(f"{where} closure_manifest must live under {CLOSURE_DIR}<export_id>/")
            elif isinstance(unit["name"], str) and manifest != f"{CLOSURE_DIR}{export_id}/{unit['name']}.json":
                # The exporter names each closure after its unit, and per-unit
                # rules (the canvas configure contract) key on the name, so a
                # renamed unit must not keep another unit's closure.
                errors.append(f"{where} closure_manifest must be {CLOSURE_DIR}<export_id>/<name>.json")
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
            if not isinstance(row["mode"], str) or row["mode"] not in FILE_MODES:
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
    if "mirror_source" in receipt:
        errors.extend(validate_mirror_source_shape(receipt, label, export_id))
    return errors


def exported_blobs_digest(files: list[dict[str, Any]]) -> str:
    """sha256 of the `<pulp_path> <mode> <blob>` listing Pulp's exporter hashes."""
    listing = "".join(
        f"{row['pulp_path']} {row['mode']} {row['blob']}\n"
        for row in sorted(files, key=lambda row: row["pulp_path"])
    )
    return hashlib.sha256(listing.encode()).hexdigest()


def validate_mirror_source_shape(receipt: dict[str, Any], label: str, export_id: str) -> list[str]:
    """The recorded claims of a pruned-source receipt, checked against its own fields."""
    where = f"{label}: mirror_source"
    source = receipt["mirror_source"]
    if not isinstance(source, dict) or set(source) != MIRROR_SOURCE_KEYS:
        return [f"{where} must contain exactly {sorted(MIRROR_SOURCE_KEYS)}"]
    errors: list[str] = []
    pulp_sha, mirror_sha = receipt["pulp_sha"], receipt["mirror_sha"]
    sha = source["sha"]
    if not isinstance(sha, str) or not SHA40.fullmatch(sha):
        errors.append(f"{where}.sha is not a full object ID")
    elif sha == pulp_sha:
        errors.append(f"{where}.sha must be a strict ancestor of pulp_sha, not pulp_sha itself")
    ref_map = source["ref_map"]
    if (
        not isinstance(ref_map, dict)
        or set(ref_map) != {"path", "sha256"}
        or ref_map.get("path") != f"{REF_MAP_DIR}{export_id}.txt"
        or not SHA256.fullmatch(str(ref_map.get("sha256")))
    ):
        errors.append(f"{where}.ref_map must name {REF_MAP_DIR}<export_id>.txt and its sha256")
    proof = source["proof"]
    if not isinstance(proof, dict) or set(proof) != MIRROR_SOURCE_PROOF_KEYS:
        return errors + [f"{where}.proof must contain exactly {sorted(MIRROR_SOURCE_PROOF_KEYS)}"]
    if proof["commit_map_rows"] != [f"{pulp_sha} {PRUNED_SHA}", f"{sha} {mirror_sha}"]:
        errors.append(f"{where}.proof.commit_map_rows must be '<pulp_sha> {PRUNED_SHA}' and '<sha> <mirror_sha>'")
    if proof["ref_map_row"] != f"{pulp_sha} {mirror_sha} {EXPORT_SOURCE_REF}":
        errors.append(f"{where}.proof.ref_map_row must be '<pulp_sha> <mirror_sha> {EXPORT_SOURCE_REF}'")
    if proof["source_is_ancestor"] is not True or proof["exported_paths_equal"] is not True:
        errors.append(f"{where}.proof must record source_is_ancestor and exported_paths_equal as true")
    files = receipt["files"]
    if isinstance(files, list) and all(
        isinstance(row, dict) and FILE_KEYS <= set(row) and isinstance(row["pulp_path"], str) for row in files
    ):
        if proof["exported_path_count"] != len(files):
            errors.append(f"{where}.proof.exported_path_count differs from the receipt's file rows")
        if proof["exported_blobs_sha256"] != exported_blobs_digest(files):
            errors.append(f"{where}.proof.exported_blobs_sha256 differs from the receipt's file rows")
    return errors


def map_rows(text: str, width: int) -> set[tuple[str, ...]]:
    """Rows of a git-filter-repo map whose first two fields are object IDs."""
    rows = set()
    for line in text.splitlines():
        fields = tuple(line.split())
        if len(fields) == width and SHA40.fullmatch(fields[0]) and SHA40.fullmatch(fields[1]):
            rows.add(fields)
    return rows


def commit_map_errors(root: Path, receipt: dict[str, Any], label: str) -> list[str]:
    """The commit map (and, for a pruned source, the ref map) attest the export.

    Unpruned: the commit map carries `<pulp_sha> <mirror_sha>`. Pruned: it
    carries `<pulp_sha> 0000...` and `<mirror_source.sha> <mirror_sha>` instead,
    names no other source for mirror_sha, and the shipped ref map moves
    refs/heads/export-source from pulp_sha to mirror_sha. No row is ever
    synthesized, so each claim is git-filter-repo's own record.
    """
    commit_map = root / receipt["commit_map"]["path"]
    if not commit_map.is_file() or sha256_file(commit_map) != receipt["commit_map"]["sha256"]:
        return [f"{label}: commit map is missing or its sha256 differs"]
    rows = map_rows(commit_map.read_text(encoding="utf-8"), 2)
    pulp_sha, mirror_sha = receipt["pulp_sha"], receipt["mirror_sha"]
    source = receipt.get("mirror_source")
    if source is None:
        if (pulp_sha, mirror_sha) not in rows:
            return [f"{label}: commit map lacks the row '<pulp_sha> <mirror_sha>'"]
        return []
    errors: list[str] = []
    if (pulp_sha, mirror_sha) in rows:
        errors.append(f"{label}: commit map maps pulp_sha to mirror_sha, so mirror_source must be absent")
    if (pulp_sha, PRUNED_SHA) not in rows:
        errors.append(f"{label}: commit map lacks the pruned row '<pulp_sha> {PRUNED_SHA}'")
    if {old for old, new in rows if new == mirror_sha} != {source["sha"]}:
        errors.append(f"{label}: commit map does not name mirror_source.sha as the only source of mirror_sha")
    ref_map = root / source["ref_map"]["path"]
    if not ref_map.is_file() or sha256_file(ref_map) != source["ref_map"]["sha256"]:
        errors.append(f"{label}: ref map is missing or its sha256 differs")
    elif (pulp_sha, mirror_sha, EXPORT_SOURCE_REF) not in map_rows(ref_map.read_text(encoding="utf-8"), 3):
        errors.append(f"{label}: ref map lacks the row '<pulp_sha> <mirror_sha> {EXPORT_SOURCE_REF}'")
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

    errors.extend(commit_map_errors(root, receipt, label))

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


D3_UNIT_MAP = "tools/scripts/vellum_d3_units.json"


def d3_unit_targets(units: list[Any], name: str, stack: tuple[str, ...] = ()) -> tuple[set[str], set[str]] | None:
    """(required, optional) CMake targets of one D3 unit, following
    alias_of/carried_by, or None when the map does not name the unit."""
    by_name = {u.get("name"): u for u in units if isinstance(u, dict)}
    entry = by_name.get(name)
    if entry is None or name in stack:
        return None
    other = entry.get("alias_of") or entry.get("carried_by")
    if isinstance(other, str) and other:
        return d3_unit_targets(units, other, (*stack, name))
    listed = entry.get("targets")
    required = {t for t in listed if isinstance(t, str)} if isinstance(listed, list) else set()
    optional_rows = entry.get("optional_targets")
    optional = {
        t if isinstance(t, str) else t.get("name")
        for t in (optional_rows if isinstance(optional_rows, list) else [])
        if isinstance(t, str) or (isinstance(t, dict) and isinstance(t.get("name"), str))
    }
    return required, optional


def d3_unit_binding_errors(root: Path, pulp_root: Path, receipt: dict[str, Any]) -> list[str]:
    """Each unit's closure carries exactly its own D3 targets.

    The D3 unit map at the exported Pulp commit names the CMake targets each
    unit owns. A closure that drops one of its unit's required targets
    without reporting it missing, or carries a target of another unit, is
    refused, so the closure's content (not only its file name) belongs to
    the unit the receipt names."""
    pulp_sha = receipt["pulp_sha"]
    text = git_try(pulp_root, "show", f"{pulp_sha}:{D3_UNIT_MAP}")
    try:
        units = json.loads(text)["units"] if text is not None else None
    except (ValueError, KeyError, TypeError):
        units = None
    if not isinstance(units, list):
        return [f"Pulp {pulp_sha} has no readable {D3_UNIT_MAP} to bind closures to units"]
    errors: list[str] = []
    for unit in receipt["units"]:
        expected = d3_unit_targets(units, unit["name"])
        if expected is None:
            errors.append(f"unit {unit['name']} is not a D3 unit at Pulp {pulp_sha}")
            continue
        required, optional = expected
        try:
            closure = strict_json(root / unit["closure_manifest"])
        except (OSError, ValueError) as exc:
            errors.append(f"closure {unit['closure_manifest']} unreadable: {exc}")
            continue
        targets = closure.get("targets") if isinstance(closure, dict) else None
        if not isinstance(targets, list):
            errors.append(f"unit {unit['name']} closure has no target list")
            continue
        names = set()
        for target in targets:
            name = target.get("name") if isinstance(target, dict) else None
            if isinstance(name, str):
                names.add(name)
            else:
                errors.append(f"unit {unit['name']} closure has a target without a string name: {name!r}")
        missing_rows = closure.get("missing_targets") or []
        missing = {t for t in missing_rows if isinstance(t, str)} if isinstance(missing_rows, list) else set()
        for name in sorted(names - required - optional, key=str):
            errors.append(f"unit {unit['name']} closure carries {name}, which D3 assigns to no part of this unit")
        for name in sorted(required - names - missing):
            errors.append(f"unit {unit['name']} closure drops its D3 target {name} without reporting it missing")
    return errors


def verify_receipt_against_pulp(pulp_root: Path, receipt: dict[str, Any],
                                root: Path | None = None) -> list[str]:
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
    source = receipt.get("mirror_source")
    if isinstance(source, dict):
        errors.extend(mirror_source_against_pulp(pulp_root, receipt, source["sha"], root))
    return errors


def commit_identity(repo: Path, commit: str) -> tuple[str, str, bytes] | None:
    """(author, committer, message) of a commit: the fields git-filter-repo keeps.

    The rewrite drops signatures (gpgsig, mergetag) and changes tree and
    parents; the author and committer lines, dates included, and the message
    pass through.
    """
    raw = subprocess.run(["git", "cat-file", "commit", commit], cwd=repo,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if raw.returncode != 0:
        return None
    header, _, message = raw.stdout.partition(b"\n\n")
    fields = {}
    for line in header.split(b"\n"):
        key, _, value = line.partition(b" ")
        if key in (b"author", b"committer"):
            fields[key] = value.decode("utf-8", "replace")
    if set(fields) != {b"author", b"committer"}:
        return None
    return fields[b"author"], fields[b"committer"], message


def mirror_source_against_pulp(pulp_root: Path, receipt: dict[str, Any], source: str,
                               root: Path | None = None) -> list[str]:
    """mirror_source.sha is a strict Pulp ancestor of pulp_sha with every exported file
    unchanged, and the commit the mirror commit was rewritten from.

    The commit map and ref map name the source but are files a lander writes,
    so any commit between the real source and pulp_sha whose exported files
    are unchanged could be named in its place. The mirror commit carries its
    source's author, committer and message through the rewrite, which binds
    it to exactly one Pulp commit.
    """
    pulp_sha = receipt["pulp_sha"]
    if not git_ok(pulp_root, "cat-file", "-e", f"{source}^{{commit}}"):
        return [f"mirror_source {source} is not a commit in {pulp_root}"]
    errors: list[str] = []
    if source == pulp_sha or not git_ok(pulp_root, "merge-base", "--is-ancestor", source, pulp_sha):
        errors.append(f"mirror_source {source} is not a strict ancestor of Pulp {pulp_sha}")
    listing = git_try(pulp_root, "ls-tree", "-r", "--full-tree", source) or ""
    at_source: dict[str, tuple[str, str]] = {}
    for line in listing.splitlines():
        metadata, path = line.split("\t", 1)
        mode, _kind, blob = metadata.split(" ")
        at_source[path] = (mode, blob)
    for row in receipt["files"]:
        if at_source.get(row["pulp_path"]) != (row["mode"], row["blob"]):
            errors.append(f"{row['mirror_path']} differs between mirror_source {source} and Pulp {pulp_sha}")
    if root is None:
        errors.append("mirror_source cannot be bound to the mirror commit without the Vellum repository")
        return errors
    mirrored = commit_identity(root, receipt["mirror_sha"])
    original = commit_identity(pulp_root, source)
    if mirrored is None or original is None or mirrored != original:
        errors.append(
            f"mirror commit {receipt['mirror_sha']} was not rewritten from mirror_source {source}: "
            "its author, committer or message differs")
    return errors


def landed_receipts(root: Path) -> list[str]:
    """Receipt paths in the index, or none when the index is unreadable."""
    try:
        index = strict_json(root / INDEX_PATH)
    except (OSError, ValueError):
        return []
    receipts = index.get("receipts") if isinstance(index, dict) else None
    return [r for r in receipts if isinstance(r, str)] if isinstance(receipts, list) else []


def receipt_history_errors(root: Path, receipts: list[str], stored: list[str]) -> list[str]:
    """Reject removal or mutation of receipts present at the merge base.

    The base is the merge base of HEAD with VELLUM_GIT_BASE, the CI base
    commit. When that names nothing this repository has (a push's all-zero
    `before`), the base is HEAD's first parent, the tip the push replaced; a
    root commit then has no base and fails closed. Without VELLUM_GIT_BASE
    (a local run) the base is the merge base with main or origin/main,
    whichever resolves first, and with neither there is nothing to compare.
    """
    configured = os.environ.get("VELLUM_GIT_BASE")
    if configured:
        base = git_try(root, "merge-base", "HEAD", configured) or git_try(
            root, "rev-parse", "--verify", "--quiet", "HEAD^1")
        if base is None:
            return [f"receipt history base is unavailable: {configured} names no commit and HEAD has no parent"]
    else:
        base = next((found for found in (git_try(root, "merge-base", "HEAD", ref)
                                         for ref in ("main", "origin/main")) if found), None)
        if base is None:
            return []
    base_index_text = git_try(root, "show", f"{base}:{INDEX_PATH}")
    if base_index_text is None:
        return [f"receipt history index is unavailable at {base}"]
    try:
        base_index = json.loads(base_index_text)
    except ValueError as exc:
        return [f"receipt history index is unreadable at {base}: {exc}"]
    base_receipts = base_index.get("receipts") if isinstance(base_index, dict) else None
    if not isinstance(base_receipts, list) or not all(isinstance(path, str) for path in base_receipts):
        return [f"receipt history index is malformed at {base}"]
    errors: list[str] = []
    if receipts[:len(base_receipts)] != base_receipts:
        errors.append("receipt index may only append entries present after the merge base")
    base_stored = {
        entry["mirror_path"]
        for entry in tree_entries(root, base) or []
        if entry["mirror_path"].startswith(RECEIPT_DIR) and entry["mirror_path"] != INDEX_PATH
    }
    if not base_stored <= set(stored):
        errors.append("receipt files present at the merge base may not be removed")
    for path in sorted(base_stored):
        before = subprocess.run(
            ["git", "show", f"{base}:{path}"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        current = root / path
        if before.returncode != 0 or not current.is_file() or current.read_bytes() != before.stdout:
            errors.append(f"receipt file changed from the merge base: {path}")
    return errors


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
    errors.extend(receipt_history_errors(root, receipts, stored))
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
            errors.extend(f"{receipt_path}: {e}" for e in verify_receipt_against_pulp(pulp_root, receipt, root))
            errors.extend(f"{receipt_path}: {e}" for e in d3_unit_binding_errors(root, pulp_root, receipt))
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
