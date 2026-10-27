#!/usr/bin/env python3
"""Plan the Vellum package build of the landed mirror (Decision 0010).

The newest landed export receipt names the mirrored units, their closure
manifests and every mirrored blob. This tool turns that record into a CMake
build plan and refuses to plan anything it cannot prove:

- every file under ``mirror/`` must be exactly a blob the receipt lists, and
  every listed blob must be present (identity with the landed mirror);
- each closure manifest must hash to the digest the receipt records;
- every pinned external input the closures name is fetched at its recorded
  commit, and every file the closures list from it must hash to the recorded
  digest, so a moved tag or a forked upstream cannot slip in;
- every generated input the build needs is produced here or by its mirrored
  producer, and must hash to the closure's recorded digest.

The closures describe the mirrored targets, but not how third-party link
targets are built. ``DEPENDENCIES`` below is the Vellum-owned recipe for each
pinned dependency a closure names; a closure dependency or link target with no
recipe is refused rather than guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_compile_options  # noqa: E402


RECEIPT_SCHEMA = "pulp.vellum.export-receipt.v2"
CLOSURE_SCHEMA = "pulp.vellum.closure.v1"
INDEX_SCHEMA = "vellum.mirror-receipt-index.v1"
PLAN_SCHEMA = "vellum.mirror-build-plan.v1"
INDEX_PATH = "provenance/mirror/receipts/index.json"
MIRROR_DIR = "mirror"
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".m", ".mm"}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BUILD_TIMESTAMP_PLACEHOLDER = "<build-timestamp>"

# Vellum-owned build recipes for the pinned dependencies a closure may name.
# `kind`:
#   cmake   - add_subdirectory() the fetched tree with `options`; it provides
#             the closure link targets listed in `provides`.
#   headers - header-only; nothing is linked.
#   sources - the closure lists compilable files from this tree as inputs of a
#             mirrored target; they are compiled into the target whose include
#             directories name the dependency.
#   archive - the pinned Skia/Dawn release archive Vellum already locks in
#             cmake/VellumSkiaDawn.cmake (identified by its asset digest).
# `patches` names Vellum transformations applied after checkout. Each is
# proved only by the closure: the patched bytes must hash to the recorded
# digest, so a patch that drifts from the export fails the plan.
DEPENDENCIES: dict[str, dict[str, Any]] = {
    "highway": {
        "kind": "cmake",
        "provides": ["hwy"],
        "options": {
            "HWY_ENABLE_EXAMPLES": "OFF",
            "HWY_ENABLE_TESTS": "OFF",
            "HWY_ENABLE_INSTALL": "OFF",
            "HWY_ENABLE_CONTRIB": "OFF",
            "BUILD_TESTING": "OFF",
        },
    },
    "mbed-tls": {
        "kind": "cmake",
        "submodules": True,
        "provides": ["mbedtls", "mbedx509", "mbedcrypto", "everest", "p256m"],
        "options": {
            "ENABLE_PROGRAMS": "OFF",
            "ENABLE_TESTING": "OFF",
            "MBEDTLS_FATAL_WARNINGS": "OFF",
            "DISABLE_PACKAGE_CONFIG_AND_INSTALL": "ON",
        },
    },
    "sheenbidi": {
        "kind": "cmake",
        "provides": ["SheenBidi"],
        "options": {
            "SB_CONFIG_UNITY": "ON",
            "BUILD_GENERATOR": "OFF",
            "BUILD_TESTING": "OFF",
        },
    },
    "choc": {
        "kind": "headers",
        "patches": ["v8-compatibility", "json-positive-exponent"],
    },
    "brotli": {"kind": "sources"},
    "woff2": {"kind": "sources"},
    "skia": {"kind": "archive"},
    "sdl3": {"kind": "cmake", "provides": ["SDL3-static"], "options": {"SDL_SHARED": "OFF", "SDL_STATIC": "ON", "SDL_TEST": "OFF", "CMAKE_DISABLE_PRECOMPILE_HEADERS": "ON"}},
    "yoga": {"kind": "cmake", "source_subdir": "yoga", "provides": ["yogacore"], "options": {}},
    "yaml-cpp": {"kind": "cmake", "provides": ["yaml-cpp"], "options": {"YAML_CPP_BUILD_TESTS": "OFF", "YAML_CPP_BUILD_TOOLS": "OFF", "YAML_CPP_BUILD_CONTRIB": "OFF", "YAML_BUILD_SHARED_LIBS": "OFF", "YAML_CPP_INSTALL": "OFF", "CMAKE_POLICY_VERSION_MINIMUM": "3.5"}},
}

# The build-tree include directories a closure may name, by the generated
# inputs they hold. Anything else under @build/ is refused.
BUILD_INCLUDE_DIRS = {
    "@build/_deps/pulp-mutable-choc-src": "choc",
    "@build/core/runtime/generated/Release": "build-info",
    "@build/core/canvas": "binary-data",
    "@build/core/view": "preludes",
    "@build/_deps/sdl3-build/include-revision": "sdl3-generated",
}
BUILD_INFO_PATH = re.compile(
    r"@build/core/runtime/generated/(?P<config>[^/]+)/pulp/runtime/build_info\.hpp\Z"
)
BINARY_DATA_PATH = re.compile(
    r"@build/core/canvas/(?P<target>[A-Za-z0-9-]+?)_data(?:_(?P<var>[A-Za-z0-9_]+)\.cpp|\.hpp)\Z"
)
PRELUDE_PATH = re.compile(r"@build/core/view/web_compat_preludes_gen\.(?P<suffix>hpp|cpp)\Z")
# Values the build-info template needs that the receipt does not record. Each
# is proved by the closure's digest of the rendered header, so a wrong value
# fails the plan instead of shipping a mislabelled build.
BUILD_INFO_INPUTS = {
    "20261014-34d16417a460": {"version": "0.964.1"},
    "20261025-46cee5e4e6e2": {"version": "1.11.0"},
}

# Pulp's CHOC standards patch, re-expressed: the JSON number parser accepts a
# positive exponent sign. Proved by the closure digest of choc_JSON.h.
CHOC_JSON_HEADER = "choc/text/choc_JSON.h"
CHOC_V8_HEADER = "choc/javascript/choc_javascript_V8.h"
PATCHED_PATHS = {"v8-compatibility": CHOC_V8_HEADER, "json-positive-exponent": CHOC_JSON_HEADER}
CHOC_JSON_BEFORE = (
    "                    hadExponent = true;\n"
    "                    popIf ('-');\n"
    "                    continue;"
)
CHOC_JSON_AFTER = (
    "                    hadExponent = true;\n"
    "                    if (! popIf ('-'))\n"
    "                        popIf ('+');\n"
    "                    continue;"
)


class PlanError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError(f"cannot read {path}: {error}") from error


def newest_receipt(repo: Path) -> tuple[str, dict[str, Any]]:
    index = load_json(repo / INDEX_PATH)
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA:
        raise PlanError(f"{INDEX_PATH} is not a {INDEX_SCHEMA} index")
    receipts = index.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        raise PlanError("no landed export receipt: the mirror has nothing to package")
    relative = receipts[-1]
    receipt = load_json(repo / relative)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise PlanError(f"{relative} is not a {RECEIPT_SCHEMA} receipt")
    for key in ("pulp_sha", "mirror_sha", "mirror_tree"):
        if not SHA40.match(str(receipt.get(key, ""))):
            raise PlanError(f"{relative}: {key} is not a full commit/tree SHA")
    if receipt.get("rename") != {"from_prefix": "", "to_prefix": f"{MIRROR_DIR}/"}:
        raise PlanError(f"{relative}: unsupported rename")
    return relative, receipt


def verify_mirror_identity(repo: Path, receipt: dict[str, Any]) -> int:
    """Prove the working tree's mirror/ is byte-identical to the receipt."""
    expected: dict[str, str] = {}
    for row in receipt.get("files", []):
        path, blob = row.get("mirror_path"), row.get("blob")
        if not isinstance(path, str) or not path.startswith(f"{MIRROR_DIR}/") or not SHA40.match(str(blob)):
            raise PlanError(f"receipt file row is malformed: {row!r}")
        expected[path] = blob
    mirror = repo / MIRROR_DIR
    actual = {
        path.relative_to(repo).as_posix()
        for path in mirror.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    extra = sorted(actual - set(expected))
    missing = sorted(set(expected) - actual)
    if extra:
        raise PlanError(f"mirror/ holds files the landed receipt does not list: {extra[:5]}")
    if missing:
        raise PlanError(f"mirror/ is missing files the landed receipt lists: {missing[:5]}")
    for path, blob in sorted(expected.items()):
        data = (repo / path).read_bytes()
        if git_blob_sha(data) != blob:
            raise PlanError(f"{path} differs from the landed mirror blob {blob}")
    return len(expected)


def load_closures(repo: Path, receipt: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    units = []
    for unit in receipt.get("units", []):
        relative = unit.get("closure_manifest")
        path = repo / str(relative)
        if not path.is_file():
            raise PlanError(f"closure manifest is missing: {relative}")
        if sha256_file(path) != unit.get("closure_sha256"):
            raise PlanError(f"closure manifest {relative} does not match the receipt digest")
        closure = load_json(path)
        if closure.get("schema") != CLOSURE_SCHEMA:
            raise PlanError(f"{relative} is not a {CLOSURE_SCHEMA} manifest")
        identity = (unit.get("configure") or {}).get("identity") or {}
        if identity.get("system") != "Darwin" or identity.get("processor") != "arm64" or not identity.get("skia"):
            raise PlanError(f"unit {unit.get('name')} was not exported for the Darwin arm64 Skia build Vellum packages")
        units.append((str(unit["name"]), closure))
    if not units:
        raise PlanError("the landed receipt names no units")
    return units


def mirror_cmake_files(repo: Path) -> dict[str, str]:
    """The landed mirror's CMake files, keyed by Pulp-relative path."""
    root = repo / MIRROR_DIR
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(root.rglob("*"))
        if path.is_file() and (path.name == "CMakeLists.txt" or path.suffix == ".cmake")
    }


def source_compile_options_for(repo: Path, receipt: dict[str, Any],
                               units: list[tuple[str, dict[str, Any]]]) -> dict[str, list[str]]:
    """Exported source -> the per-source COMPILE_OPTIONS Pulp's CMake applies to it
    under the closures' configure (tools/mirror/source_compile_options.py)."""
    configures = {json.dumps((closure.get("configure") or {}).get("options"), sort_keys=True)
                  for _, closure in units}
    if len(configures) != 1:
        raise PlanError("the landed closures were exported under different configures")
    options = json.loads(configures.pop())
    if not isinstance(options, dict):
        raise PlanError("the landed closures record no configure options")
    exported = {str(row.get("mirror_path", "")).removeprefix(f"{MIRROR_DIR}/")
                for row in receipt.get("files", [])}
    try:
        targets = {target["name"] for _, closure in units for target in closure.get("targets", [])}
        return source_compile_options.evaluate(mirror_cmake_files(repo), options, exported, targets)
    except source_compile_options.SourceOptionsError as error:
        raise PlanError(f"per-source compile options: {error}") from error


def external_dependencies(units: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Collect each pinned dependency and the exact files the closures read from it."""
    deps: dict[str, dict[str, Any]] = {}
    for unit, closure in units:
        for row in closure.get("external_inputs", []):
            path = row.get("path", "")
            match = re.match(r"@external/(?P<name>[A-Za-z0-9_.-]+)/(?P<root>[^/]+)/(?P<rel>.+)\Z", path)
            if not match:
                raise PlanError(f"{unit}: external input path is malformed: {path}")
            name = match.group("name")
            if name not in DEPENDENCIES:
                raise PlanError(f"{unit}: closure names dependency {name!r}, which has no Vellum build recipe")
            pin = row.get("pin")
            if not SHA40.match(str(pin)):
                raise PlanError(f"{unit}: {name} is not pinned to a full commit")
            entry = deps.setdefault(name, {
                "pin": pin, "source": row.get("source"), "asset_sha256": row.get("asset_sha256"),
                "root": match.group("root"), "files": {},
            })
            if (entry["pin"], entry["source"], entry["root"]) != (pin, row.get("source"), match.group("root")):
                raise PlanError(f"closures disagree on the pin of {name}")
            if not SHA256.match(str(row.get("sha256"))):
                raise PlanError(f"{unit}: {path} has no recorded digest")
            previous = entry["files"].get(match.group("rel"))
            if previous and previous != row["sha256"]:
                raise PlanError(f"closures disagree on the digest of {path}")
            entry["files"][match.group("rel")] = row["sha256"]
    return deps


def reusable_checkout(destination: Path, pin: str, patched: set[str]) -> bool:
    """A cached checkout is reused only at the pin with no edit but its patches.

    Untracked and ignored files count as edits: a source glob in a dependency's
    own build would pick them up. A checkout that is not reusable is fetched
    again from scratch.
    """
    if not (destination / ".git").exists():
        return False
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=destination, capture_output=True, text=True, check=False)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--ignored=matching",
         "--ignore-submodules=none"],
        cwd=destination, capture_output=True, text=True, check=False)
    if head.returncode or status.returncode or head.stdout.strip() != pin:
        return False
    changed = set()
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith(("??", "!!")):
            return False
        changed.add(line[3:])
    return changed <= patched


def fetch_git(name: str, source: str, pin: str, destination: Path, *, submodules: bool,
              patched: set[str] = frozenset()) -> None:
    def git(*args: str, cwd: Path) -> None:
        completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise PlanError(f"fetching {name} failed: git {' '.join(args)}\n{completed.stderr.strip()}")
    if reusable_checkout(destination, pin, patched):
        return
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    git("init", "-q", cwd=destination)
    git("remote", "add", "origin", source, cwd=destination)
    git("fetch", "-q", "--depth", "1", "origin", pin, cwd=destination)
    git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=destination)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=destination, capture_output=True, text=True, check=False)
    if head.stdout.strip() != pin:
        raise PlanError(f"{name} checked out {head.stdout.strip()!r}, not the pinned {pin}")
    if submodules:
        git("submodule", "update", "--init", "--recursive", "--depth", "1", cwd=destination)


CHOC_V8_ITERATE_BEFORE = (
    "                using IteratorType = decltype (iterator);\n"
    "\n"
    "                propNames.ToLocalChecked()->Iterate (c, +[] (uint32_t, v8::Local<v8::Value> key, void* i)\n"
    "                {\n"
    "                    (*static_cast<IteratorType*> (i)) (key);\n"
    "                    return v8::Array::CallbackResult::kContinue;\n"
    "                },\n"
    "                std::addressof (iterator));"
)
CHOC_V8_ITERATE_AFTER = (
    "                auto names = propNames.ToLocalChecked();\n"
    "\n"
    "                for (uint32_t i = 0; i < names->Length(); ++i)\n"
    "                {\n"
    "                    auto key = names->Get (c, i);\n"
    "\n"
    "                    if (! key.IsEmpty())\n"
    "                        iterator (key.ToLocalChecked());\n"
    "                }"
)


def apply_choc_v8_patch(root: Path) -> None:
    header = root / CHOC_V8_HEADER
    text = header.read_text(encoding="utf-8")
    if "Utf8LengthV2 (isolate)" in text:
        return
    original = text
    text = text.replace('#include "choc_javascript.h"', '#include "choc_javascript.h"\n#include <v8-version.h>', 1)
    text = text.replace('c->GetIsolate()->GetData (0)', 'v8::Isolate::GetCurrent()->GetData (0)')
    text = text.replace('v8::Local<v8::FixedArray> /*importAssertions*/)\n        {', 'v8::Local<v8::FixedArray> /*importAssertions*/)\n            -> v8::MaybeLocal<v8::Promise>\n        {', 1)
    text = text.replace('v8::Local<v8::FixedArray>, v8::Local<v8::Module>)\n        {', 'v8::Local<v8::FixedArray>, v8::Local<v8::Module>)\n            -> v8::MaybeLocal<v8::Module>\n        {', 1)
    text = text.replace('        auto len = s.Utf8Length (isolate);\n        result.resize (static_cast<std::string::size_type> (len));\n        s.WriteUtf8 (isolate, result.data(), len);', '       #if defined(V8_MAJOR_VERSION) && V8_MAJOR_VERSION >= 14\n        auto len = s.Utf8LengthV2 (isolate);\n        result.resize (static_cast<std::string::size_type> (len));\n        s.WriteUtf8V2 (isolate, result.data(), static_cast<size_t> (len), v8::String::WriteFlags::kNone);\n       #else\n        auto len = s.Utf8Length (isolate);\n        result.resize (static_cast<std::string::size_type> (len));\n        s.WriteUtf8 (isolate, result.data(), len);\n       #endif', 1)
    text = text.replace('#if V8_MAJOR_VERSION >= 14', '#if defined(V8_MAJOR_VERSION) && V8_MAJOR_VERSION >= 14')
    # Pulp's "V8 v8ToChoc property walk": walk property names by index rather
    # than through Array::Iterate, whose callback runs in its own HandleScope.
    text = text.replace(CHOC_V8_ITERATE_BEFORE, CHOC_V8_ITERATE_AFTER, 1)
    if text == original or "Utf8LengthV2 (isolate)" not in text or CHOC_V8_ITERATE_AFTER not in text:
        raise PlanError("choc V8 compatibility patch did not match reviewed source")
    header.write_text(text, encoding="utf-8")


def apply_patch(name: str, patch: str, root: Path) -> None:
    if name != "choc" or patch not in {"v8-compatibility", "json-positive-exponent"}:
        raise PlanError(f"unknown patch {patch!r} for {name}")
    if patch == "v8-compatibility":
        apply_choc_v8_patch(root)
        return
    header = root / CHOC_JSON_HEADER
    text = header.read_text(encoding="utf-8")
    if CHOC_JSON_AFTER in text:
        return
    if text.count(CHOC_JSON_BEFORE) != 1:
        raise PlanError("choc JSON exponent parser no longer matches the expected source")
    header.write_text(text.replace(CHOC_JSON_BEFORE, CHOC_JSON_AFTER), encoding="utf-8")


def dependency_source_dir(name: str, root: Path) -> Path:
    """The directory the mirror build adds for a fetched dependency.

    A recipe's `source_subdir` names the project to build inside the fetched
    tree, as FetchContent's SOURCE_SUBDIR does; Yoga's top level also adds its
    own tests, which the mirror never builds.
    """
    subdir = DEPENDENCIES[name].get("source_subdir")
    if not subdir:
        return root
    candidate = root / subdir
    if not (candidate / "CMakeLists.txt").is_file():
        raise PlanError(f"{name} has no {subdir}/CMakeLists.txt to build")
    return candidate


def prepare_dependencies(deps: dict[str, dict[str, Any]], deps_dir: Path, skia_dir: Path | None,
                         skia_asset_sha256: str | None) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for name, entry in sorted(deps.items()):
        recipe = DEPENDENCIES[name]
        if recipe["kind"] == "archive":
            if skia_dir is None or not skia_asset_sha256:
                raise PlanError(f"closure needs {name} but no extracted Skia/Dawn archive was given")
            if entry["asset_sha256"] != skia_asset_sha256:
                raise PlanError(
                    f"closure was exported against {name} archive {entry['asset_sha256']}, "
                    f"but Vellum locks {skia_asset_sha256}")
            root = skia_dir
        else:
            root = deps_dir / f"{name}-{entry['pin']}"
            fetch_git(name, entry["source"], entry["pin"], root, submodules=bool(recipe.get("submodules")),
                      patched={PATCHED_PATHS[p] for p in recipe.get("patches", [])})
            for patch in recipe.get("patches", []):
                apply_patch(name, patch, root)
        for relative, digest in sorted(entry["files"].items()):
            candidate = root / relative
            if not candidate.is_file():
                raise PlanError(f"{name}@{entry['pin'][:12]} has no {relative}")
            if sha256_file(candidate) != digest:
                raise PlanError(f"{name}@{entry['pin'][:12]}:{relative} does not match the closure digest")
        roots[name] = root
    return roots


def render_build_info(template: str, receipt: dict[str, Any], version: str, config: str, timestamp: str) -> str:
    stamp = f"{version} ({config}, {receipt['pulp_sha']}, {timestamp})"
    values = {
        "PULP_BUILD_TYPE": config,
        "PULP_BUILD_ISO8601": timestamp,
        "PULP_GIT_SHA": receipt["pulp_sha"],
        "PULP_GIT_DIRTY": "false",
        "PULP_VERSION": version,
        "PULP_BUILD_STAMP": stamp,
    }
    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PlanError(f"build_info template names an unknown value @{key}@")
        return values[key]
    return re.sub(r"@([A-Z0-9_]+)@", substitute, template)


def binary_data_targets(repo: Path) -> dict[str, dict[str, Any]]:
    """Read the mirrored canvas CMake for each binary-data target's inputs."""
    text = (repo / MIRROR_DIR / "core/canvas/CMakeLists.txt").read_text(encoding="utf-8")
    targets: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"pulp_add_binary_data\(\s*([A-Za-z0-9-]+)\s+SOURCES\s+(.*?)\s+NAMESPACE\s+([A-Za-z0-9_]+)\s*\)", text, re.S):
        sources = [
            re.sub(r"^\$\{[A-Z_]+\}/", "", item)
            for item in match.group(2).split()
        ]
        targets[match.group(1)] = {"namespace": match.group(3), "sources": sources}
    return targets


def c_identifier(name: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return f"_{ident}" if ident[:1].isdigit() else ident


def prepare_generated(repo: Path, receipt: dict[str, Any], units: list[tuple[str, dict[str, Any]]],
                      generated_dir: Path) -> dict[str, Any]:
    """Produce or prove every generated input the closures name."""
    build_info_dir = generated_dir / "build-info"
    prelude_dir = generated_dir / "preludes"
    binary_targets = binary_data_targets(repo)
    used_binary: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for unit, closure in units:
        for row in closure.get("generated_inputs", []):
            path = row.get("path", "")
            if path in seen:
                continue
            seen.add(path)
            digest = row.get("sha256")
            if not SHA256.match(str(digest)):
                raise PlanError(f"{unit}: generated input {path} has no recorded digest")
            info = BUILD_INFO_PATH.match(path)
            data = BINARY_DATA_PATH.match(path)
            prelude = PRELUDE_PATH.match(path)
            if info:
                config = info.group("config")
                inputs = BUILD_INFO_INPUTS.get(receipt["export_id"])
                if not inputs:
                    raise PlanError(f"no build-info inputs are recorded for export {receipt['export_id']}")
                template = (repo / MIRROR_DIR / "core/runtime/include/pulp/runtime/build_info.hpp.in").read_text(encoding="utf-8")
                normalized = render_build_info(template, receipt, inputs["version"], config, BUILD_TIMESTAMP_PLACEHOLDER)
                if (row.get("normalization") or {}).get("build_timestamp") != BUILD_TIMESTAMP_PLACEHOLDER:
                    raise PlanError(f"{path} is not timestamp-normalized")
                if sha256_bytes(normalized.encode("utf-8")) != digest:
                    raise PlanError(f"{path} rendered from the mirrored template does not match the closure digest")
                # The export time stands in for the configure time, so two
                # builds of one receipt carry the same bytes.
                rendered = render_build_info(template, receipt, inputs["version"], config, receipt["created_at"])
                target = build_info_dir / "pulp/runtime/build_info.hpp"
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
                    target.write_text(rendered, encoding="utf-8")
            elif prelude:
                cmake = (repo / MIRROR_DIR / "core/view/cmake/PulpViewJsPreludes.cmake").read_text(encoding="utf-8")
                js_paths = re.findall(r"\$\{CMAKE_CURRENT_SOURCE_DIR\}/(js/[^\s)]+)", cmake)
                js_paths = [js for js in js_paths if js != "js/embed_js.py"]
                prelude_dir.mkdir(parents=True, exist_ok=True)
                out = prelude_dir / f"web_compat_preludes_gen.{prelude.group('suffix')}"
                if prelude.group("suffix") == "hpp":
                    text = "#pragma once\n// Auto-generated from the JS prelude file list — do not edit\n\nnamespace pulp::view::preludes {\n\n" + "".join(f"extern const char* const {Path(js).stem.replace('-', '_')};\n" for js in js_paths) + "\n}  // namespace pulp::view::preludes\n"
                    out.write_text(text, encoding="utf-8")
                else:
                    completed = subprocess.run([sys.executable, str(repo / MIRROR_DIR / "core/view/js/embed_js.py"), str(out), *(str(repo / MIRROR_DIR / "core/view" / js) for js in js_paths)], capture_output=True, text=True, check=False)
                    if completed.returncode:
                        raise PlanError(f"{unit}: prelude generator failed: {completed.stderr.strip()}")
                if sha256_file(out) != digest:
                    raise PlanError(f"{path} generated by the mirrored producer does not match the closure digest")
            elif data:
                name = data.group("target")
                if name not in binary_targets:
                    raise PlanError(f"{path} names binary-data target {name}, which the mirrored canvas CMake does not declare")
                spec = used_binary.setdefault(name, {**binary_targets[name], "header_sha256": None, "sources_sha256": {}})
                if data.group("var") is None:
                    spec["header_sha256"] = digest
                else:
                    font = next((s for s in spec["sources"] if c_identifier(Path(s).name) == data.group("var")), None)
                    if font is None:
                        raise PlanError(f"{path} has no matching binary-data source")
                    encoder = repo / MIRROR_DIR / "tools/cmake/scripts/encode_binary_data.py"
                    with tempfile.TemporaryDirectory(prefix="vellum-binary-data-") as temporary:
                        out = Path(temporary) / "out.cpp"
                        completed = subprocess.run([
                            sys.executable, str(encoder), "--src", str(repo / MIRROR_DIR / font),
                            "--out", str(out), "--namespace", spec["namespace"], "--var", data.group("var"),
                        ], capture_output=True, text=True, check=False)
                        if completed.returncode:
                            raise PlanError(f"mirrored encoder failed for {font}: {completed.stderr.strip()}")
                        if sha256_file(out) != digest:
                            raise PlanError(f"{path} encoded by the mirrored producer does not match the closure digest")
                    spec["sources_sha256"][data.group("var")] = digest
            else:
                raise PlanError(f"{unit}: generated input {path} has no Vellum producer")
    for name, spec in used_binary.items():
        if spec["header_sha256"] is None:
            raise PlanError(f"binary-data target {name} has no recorded header digest")
        if set(spec["sources_sha256"]) != {c_identifier(Path(s).name) for s in spec["sources"]}:
            raise PlanError(f"binary-data target {name}'s closure rows do not cover its declared sources")
    return {"build_info_dir": build_info_dir, "prelude_dir": prelude_dir, "binary_data": used_binary}


def map_include(path: str, repo: Path, roots: dict[str, Path], generated: dict[str, Any],
                generated_binary_dir: str) -> str:
    if path in BUILD_INCLUDE_DIRS:
        kind = BUILD_INCLUDE_DIRS[path]
        if kind == "choc":
            return str(roots["choc"])
        if kind == "build-info":
            return str(generated["build_info_dir"])
        if kind == "preludes":
            return str(generated["prelude_dir"])
        return generated_binary_dir
    if path.startswith("@build/"):
        raise PlanError(f"closure names an unmapped build-tree include directory: {path}")
    match = re.match(r"@external/(?P<name>[A-Za-z0-9_.-]+)/(?P<root>[^/]+)(?:/(?P<rel>.*))?\Z", path)
    if match:
        root = roots.get(match.group("name"))
        if root is None:
            raise PlanError(f"include directory {path} names a dependency with no prepared source")
        return str(root / (match.group("rel") or ""))
    if path.startswith("@") or path.startswith("/") or ".." in Path(path).parts:
        raise PlanError(f"include directory is not a mirror-relative path: {path}")
    candidate = repo / MIRROR_DIR / path
    if not candidate.is_dir():
        raise PlanError(f"include directory {path} is not in the landed mirror")
    return str(candidate)


def plan(repo: Path, deps_dir: Path, generated_dir: Path, skia_dir: Path | None,
         skia_asset_sha256: str | None = None) -> dict[str, Any]:
    repo = repo.resolve()
    receipt_path, receipt = newest_receipt(repo)
    file_count = verify_mirror_identity(repo, receipt)
    units = load_closures(repo, receipt)
    source_options = source_compile_options_for(repo, receipt, units)
    attached: set[str] = set()
    deps = external_dependencies(units)
    roots = prepare_dependencies(deps, deps_dir, skia_dir, skia_asset_sha256)
    generated = prepare_generated(repo, receipt, units, generated_dir)
    binary_dir_token = "@VELLUM_MIRROR_BINARY_DATA_DIR@"

    provided = {target: name for name, recipe in DEPENDENCIES.items() for target in recipe.get("provides", [])}
    unit_rows = []
    all_targets: dict[str, str] = {}
    for unit, closure in units:
        for target in closure.get("targets", []):
            if target["name"] in all_targets:
                raise PlanError(f"target {target['name']} appears in two units")
            all_targets[target["name"]] = unit
    for unit, closure in units:
        targets = []
        for target in closure.get("targets", []):
            name = target["name"]
            if target.get("type") != "STATIC_LIBRARY":
                raise PlanError(f"{unit}/{name}: only static mirrored targets are packaged")
            sources = []
            for source in target.get("sources", []):
                candidate = repo / MIRROR_DIR / source
                generated_source = None
                if source == "@build/core/view/web_compat_preludes_gen.cpp":
                    generated_source = generated["prelude_dir"] / "web_compat_preludes_gen.cpp"
                if generated_source is not None:
                    if not generated_source.is_file():
                        raise PlanError(f"{unit}/{name}: generated source {source} was not produced")
                    sources.append(str(generated_source))
                    continue
                if not candidate.is_file():
                    raise PlanError(f"{unit}/{name}: source {source} is not in the landed mirror")
                if candidate.suffix in SOURCE_SUFFIXES:
                    sources.append(str(candidate))
            include_dirs = [map_include(p, repo, roots, generated, binary_dir_token) for p in target.get("include_dirs", [])]
            # Compilable inputs the closure attributes to a dependency whose
            # headers only this target reads are compiled into it.
            dep_names = {
                m.group(1) for p in target.get("include_dirs", [])
                if (m := re.match(r"@external/([A-Za-z0-9_.-]+)/", p))
            }
            for dep in sorted(dep_names):
                if DEPENDENCIES[dep]["kind"] != "sources":
                    continue
                for relative in sorted(deps[dep]["files"]):
                    if Path(relative).suffix in SOURCE_SUFFIXES:
                        sources.append(str(roots[dep] / relative))
            internal, external = [], []
            for link in target.get("link_targets", []):
                if link in all_targets:
                    internal.append(link)
                elif link in provided:
                    if provided[link] not in deps and provided[link] not in roots:
                        # A dependency built from source still has to be fetched.
                        raise PlanError(f"{unit}/{name} links {link}, but no closure pins {provided[link]}")
                    external.append(link)
                else:
                    raise PlanError(f"{unit}/{name} links {link}, which is neither mirrored nor a recipe target")
            skia = [row for row in target.get("external_link_inputs", []) if row.get("dependency") == "skia"]
            for row in skia:
                # The target links the archive Vellum locks, not merely one
                # whose headers happen to match.
                if row.get("asset_sha256") != skia_asset_sha256:
                    raise PlanError(
                        f"{unit}/{name} links skia archive {row.get('asset_sha256')}, "
                        f"but Vellum locks {skia_asset_sha256}")
            others = [row for row in target.get("external_link_inputs", []) if row.get("dependency") != "skia"]
            if others:
                raise PlanError(f"{unit}/{name} has external link inputs with no recipe: {others}")
            option_sources = []
            for source in target.get("sources", []):
                if source in source_options:
                    option_sources.append({"source": str(repo / MIRROR_DIR / source),
                                           "options": source_options[source]})
                    attached.add(source)
            targets.append({
                "name": name,
                "sources": sources,
                "source_compile_options": option_sources,
                "include_dirs": include_dirs,
                "compile_definitions": sorted(target.get("compile_definitions", [])),
                "internal_links": internal,
                "external_links": external,
                "skia": bool(skia),
                "skia_asset_sha256": skia[0].get("asset_sha256") if skia else None,
            })
        unit_rows.append({
            "name": unit,
            "targets": targets,
            "compile_definitions": sorted(closure.get("compile_definitions", [])),
        })
    unattached = sorted(set(source_options) - attached)
    if unattached:
        raise PlanError(f"per-source compile options name sources no mirrored target compiles: {unattached}")
    cmake_deps = {
        name: {"dir": str(dependency_source_dir(name, roots[name])),
               "options": DEPENDENCIES[name].get("options", {}),
               "provides": DEPENDENCIES[name].get("provides", [])}
        for name in sorted(roots) if DEPENDENCIES[name]["kind"] == "cmake"
    }
    choc_headers = sorted(rel for rel in deps.get("choc", {}).get("files", {}))
    # Every fetched dependency ships compiled into or alongside the package,
    # so its license travels with it.
    licenses = {}
    for name in sorted(roots):
        if DEPENDENCIES[name]["kind"] == "archive":
            continue
        found = sorted(
            str(path) for path in roots[name].iterdir()
            if path.is_file() and re.match(r"(?i)(license|copying|notice)", path.name)
        )
        if not found:
            raise PlanError(f"{name}@{deps[name]['pin'][:12]} carries no license file")
        licenses[name] = found
    return {
        "schema": PLAN_SCHEMA,
        "receipt": receipt_path,
        "export_id": receipt["export_id"],
        "pulp_sha": receipt["pulp_sha"],
        "mirror_sha": receipt["mirror_sha"],
        "mirror_tree": receipt["mirror_tree"],
        "mirror_files_verified": file_count,
        "closures": [unit["closure_manifest"] for unit in receipt["units"]],
        "dependencies": {name: {"pin": entry["pin"], "source": entry["source"],
                                 "files_verified": len(entry["files"])}
                          for name, entry in sorted(deps.items())},
        "cmake_dependencies": cmake_deps,
        "licenses": licenses,
        "choc_root": str(roots["choc"]) if "choc" in roots else None,
        "choc_headers": choc_headers,
        "build_info_dir": str(generated["build_info_dir"]),
        "binary_data": generated["binary_data"],
        "source_compile_options": source_options,
        "units": unit_rows,
    }


def cmake_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def cmake_list(name: str, values: list[str]) -> list[str]:
    return [f"set({name}"] + [f"    {cmake_quote(v)}" for v in values] + [")"]


def write_cmake(build_plan: dict[str, Any], output: Path) -> None:
    lines = ["# GENERATED by tools/mirror/mirror_package.py from the landed receipt; do not edit."]
    lines.append(f"set(VELLUM_MIRROR_EXPORT_ID {cmake_quote(build_plan['export_id'])})")
    lines.append(f"set(VELLUM_MIRROR_ORIGIN_SHA {cmake_quote(build_plan['pulp_sha'])})")
    lines.append(f"set(VELLUM_MIRROR_SHA {cmake_quote(build_plan['mirror_sha'])})")
    lines.append(f"set(VELLUM_MIRROR_RECEIPT {cmake_quote(build_plan['receipt'])})")
    lines += cmake_list("VELLUM_MIRROR_CLOSURES", build_plan["closures"])
    lines.append(f"set(VELLUM_MIRROR_BUILD_INFO_DIR {cmake_quote(build_plan['build_info_dir'])})")
    lines.append(f"set(VELLUM_MIRROR_CHOC_ROOT {cmake_quote(build_plan['choc_root'] or '')})")
    lines += cmake_list("VELLUM_MIRROR_CHOC_HEADERS", build_plan["choc_headers"])
    lines += cmake_list("VELLUM_MIRROR_LICENSED_DEPENDENCIES", list(build_plan["licenses"]))
    for name, files in build_plan["licenses"].items():
        key = re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        lines += cmake_list(f"VELLUM_MIRROR_LICENSE_{key}_FILES", files)
    lines += cmake_list("VELLUM_MIRROR_DEPENDENCIES", list(build_plan["cmake_dependencies"]))
    for name, dep in build_plan["cmake_dependencies"].items():
        key = re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        lines.append(f"set(VELLUM_MIRROR_DEP_{key}_DIR {cmake_quote(dep['dir'])})")
        lines += cmake_list(f"VELLUM_MIRROR_DEP_{key}_OPTIONS", [f"{k}={v}" for k, v in sorted(dep["options"].items())])
        lines += cmake_list(f"VELLUM_MIRROR_DEP_{key}_PROVIDES", dep["provides"])
    lines += cmake_list("VELLUM_MIRROR_BINARY_DATA_TARGETS", sorted(build_plan["binary_data"]))
    for name, spec in sorted(build_plan["binary_data"].items()):
        key = re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        lines.append(f"set(VELLUM_MIRROR_BINARY_{key}_NAMESPACE {cmake_quote(spec['namespace'])})")
        lines += cmake_list(f"VELLUM_MIRROR_BINARY_{key}_SOURCES", spec["sources"])
        lines.append(f"set(VELLUM_MIRROR_BINARY_{key}_HEADER_SHA256 {cmake_quote(spec['header_sha256'])})")
        for var, digest in sorted(spec["sources_sha256"].items()):
            lines.append(f"set(VELLUM_MIRROR_BINARY_{key}_{var}_SHA256 {cmake_quote(digest)})")
    lines += cmake_list("VELLUM_MIRROR_UNITS", [unit["name"] for unit in build_plan["units"]])
    for unit in build_plan["units"]:
        ukey = re.sub(r"[^A-Za-z0-9]", "_", unit["name"]).upper()
        lines += cmake_list(f"VELLUM_MIRROR_UNIT_{ukey}_TARGETS", [t["name"] for t in unit["targets"]])
        lines += cmake_list(f"VELLUM_MIRROR_UNIT_{ukey}_DEFINITIONS", unit["compile_definitions"])
        for target in unit["targets"]:
            tkey = re.sub(r"[^A-Za-z0-9]", "_", target["name"]).upper()
            lines += cmake_list(f"VELLUM_MIRROR_TARGET_{tkey}_SOURCES", target["sources"])
            includes = [
                i.replace("@VELLUM_MIRROR_BINARY_DATA_DIR@", "${VELLUM_MIRROR_BINARY_DATA_DIR}")
                for i in target["include_dirs"]
            ]
            lines.append(f"set(VELLUM_MIRROR_TARGET_{tkey}_INCLUDE_DIRS")
            for include in includes:
                if "${VELLUM_MIRROR_BINARY_DATA_DIR}" in include:
                    lines.append("    \"${VELLUM_MIRROR_BINARY_DATA_DIR}\"")
                else:
                    lines.append(f"    {cmake_quote(include)}")
            lines.append(")")
            lines += cmake_list(f"VELLUM_MIRROR_TARGET_{tkey}_DEFINITIONS", target["compile_definitions"])
            lines += cmake_list(f"VELLUM_MIRROR_TARGET_{tkey}_INTERNAL_LINKS", target["internal_links"])
            lines += cmake_list(f"VELLUM_MIRROR_TARGET_{tkey}_EXTERNAL_LINKS", target["external_links"])
            lines.append(f"set(VELLUM_MIRROR_TARGET_{tkey}_SKIA {'ON' if target['skia'] else 'OFF'})")
            option_sources = target.get("source_compile_options", [])
            lines.append(f"set(VELLUM_MIRROR_TARGET_{tkey}_OPTION_SOURCE_COUNT {len(option_sources)})")
            for index, entry in enumerate(option_sources):
                lines.append(f"set(VELLUM_MIRROR_TARGET_{tkey}_OPTION_SOURCE_{index} {cmake_quote(entry['source'])})")
                lines += cmake_list(f"VELLUM_MIRROR_TARGET_{tkey}_OPTION_SOURCE_{index}_OPTIONS", entry["options"])
            lines.append(f"set(VELLUM_MIRROR_TARGET_{tkey}_UNIT {cmake_quote(unit['name'])})")
    output.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    if not output.is_file() or output.read_text(encoding="utf-8") != text:
        output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--deps-dir", type=Path, required=True)
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument("--skia-dir", type=Path)
    parser.add_argument("--skia-asset-sha256", help="digest of the Skia/Dawn archive Vellum locks")
    parser.add_argument("--cmake-output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    try:
        build_plan = plan(args.repo, args.deps_dir.resolve(), args.generated_dir.resolve(),
                          args.skia_dir.resolve() if args.skia_dir else None,
                          args.skia_asset_sha256)
    except PlanError as error:
        print(f"vellum-mirror-package: {error}", file=sys.stderr)
        return 1
    write_cmake(build_plan, args.cmake_output)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(build_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"vellum-mirror-package: planned export {build_plan['export_id']} "
          f"({build_plan['mirror_files_verified']} mirrored files, "
          f"{sum(d['files_verified'] for d in build_plan['dependencies'].values())} pinned inputs verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
