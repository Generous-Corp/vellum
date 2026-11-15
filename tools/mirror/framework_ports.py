#!/usr/bin/env python3
"""The framework/ ports ledger: which files joined or left framework/ after the transfer.

The transfer record lists the files Pulp's export carried into framework/.
`provenance/framework/ports.json` is the append-only list of every later
change to that file set. Each row names one framework/ path, whether it was
added or removed, where it came from (the Pulp path and commits, or `native`
for a file Vellum wrote), and the mirrored target that compiles it when it is
a compilable source. A row that adds a file of Pulp origin also records the
blob the file has when it joins: Pulp's file at the last named commit, put
through the transfer's rename. A port that differs from that is a native row. The file set framework/ must hold is the transfer
record's paths, minus the removed rows, plus the added rows.

This module reads the ledger and replays it; it runs no git. Two tools share
it so they hold the same set: tools/provenance/verify_extraction.py, which
also proves the rows append-only and the Pulp commits real, and
tools/mirror/mirror_package.py, which compiles the added sources.

A missing ledger is an empty one.

A row's target is usually a mirrored target a closure describes. A port may
also need a target no closure builds (the closures describe only what Pulp
exported). Such a target is declared in `provenance/framework/units.json`:
its name, whether it is a static library (in a new unit, which gets its own
package library) or an executable, its sources, include directories, compile
definitions and link targets. Each declared source must also have the ledger
row that adds it to that target, so nothing is built that a row does not
account for. A missing manifest declares nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LEDGER = "provenance/framework/ports.json"
SCHEMA = "vellum.framework-ports.v1"
FRAMEWORK_PREFIX = "framework/"
# Third-party source is pinned by blob and changes only through
# provenance/framework/external-updates.json, never through this ledger.
EXTERNAL_PREFIX = "framework/external/"
ROW_KEYS = {"framework_path", "change", "target", "origin", "pulp_path", "pulp_commits", "framework_blob",
            "reason"}
CHANGES = ("added", "removed")
ORIGINS = ("pulp", "native")
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".m", ".mm"}
SAFE_PATH = re.compile(r"(?:[A-Za-z0-9_.+@-]+/)*[A-Za-z0-9_.+@-]+\Z")
TARGET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def safe_relative(path: Any) -> bool:
    return (isinstance(path, str) and bool(SAFE_PATH.match(path))
            and not any(part in ("", ".", "..") for part in path.split("/")))


def compiled(path: str) -> bool:
    """Whether the mirror build compiles a file at this path."""
    return Path(path).suffix in SOURCE_SUFFIXES


def row_errors(row: Any, where: str) -> list[str]:
    """What is wrong with one row's shape; empty when it is well formed."""
    if not isinstance(row, dict) or set(row) != ROW_KEYS:
        return [f"{where}: must have exactly {sorted(ROW_KEYS)}"]
    errors: list[str] = []
    path, change, origin = row["framework_path"], row["change"], row["origin"]
    if not safe_relative(path) or not path.startswith(FRAMEWORK_PREFIX):
        return [f"{where}: framework_path {path!r} is not a safe path inside {FRAMEWORK_PREFIX}"]
    if path.startswith(EXTERNAL_PREFIX):
        errors.append(f"{where}: {path} is third-party source; {EXTERNAL_PREFIX} changes only through "
                      "provenance/framework/external-updates.json")
    if change not in CHANGES:
        errors.append(f"{where}: change must be one of {list(CHANGES)}")
    if not isinstance(row["reason"], str) or not row["reason"].strip():
        errors.append(f"{where}: reason must be a non-empty string")
    commits, pulp_path = row["pulp_commits"], row["pulp_path"]
    if origin == "pulp":
        if not safe_relative(pulp_path):
            errors.append(f"{where}: pulp_path {pulp_path!r} is not a safe relative path")
        if (not isinstance(commits, list) or not commits
                or not all(isinstance(c, str) and COMMIT.match(c) for c in commits)
                or len(set(commits)) != len(commits)):
            errors.append(f"{where}: a pulp row names one or more distinct full Pulp commit ids in pulp_commits")
    elif origin == "native":
        if pulp_path is not None or commits != []:
            errors.append(f"{where}: a native row has pulp_path null and no pulp_commits")
    else:
        errors.append(f"{where}: origin must be one of {list(ORIGINS)}")
    blob = row["framework_blob"]
    if change == "added" and origin == "pulp":
        if not isinstance(blob, str) or not COMMIT.match(blob):
            errors.append(f"{where}: a row adding a Pulp file records the file's git blob id in framework_blob")
    elif blob is not None:
        errors.append(f"{where}: framework_blob is null unless the row adds a file of Pulp origin")
    target = row["target"]
    if change == "added" and compiled(path):
        if not isinstance(target, str) or not TARGET_NAME.match(target):
            errors.append(f"{where}: {path} is a compilable source, so target must name the mirrored "
                          "target that compiles it")
    elif target is not None:
        errors.append(f"{where}: target is null unless the row adds a compilable source")
    return errors


def parse(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """(rows, errors) for the ledger's text. Rows are returned only when every one is well formed."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        return [], [f"{LEDGER}: unreadable: {exc}"]
    if not isinstance(document, dict) or set(document) != {"schema", "rows"} \
            or document["schema"] != SCHEMA or not isinstance(document["rows"], list):
        return [], [f"{LEDGER}: must be an object with schema {SCHEMA} and a rows list"]
    errors = [error for index, row in enumerate(document["rows"])
              for error in row_errors(row, f"{LEDGER} rows[{index}]")]
    return ([] if errors else document["rows"]), errors


def load(repo: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """(rows, errors) for the checkout's ledger; a missing ledger has no rows."""
    path = repo / LEDGER
    if path.is_symlink():
        return [], [f"{LEDGER}: is a symlink"]
    if not path.exists():
        return [], []
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"{LEDGER}: unreadable: {exc}"]


def replay(rows: list[dict[str, Any]], transferred: set[str]) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    """Apply well-formed rows in order to the transferred set: (added, removed, errors).

    `added` maps each path the ledger currently adds to its row; `removed` is
    the transferred and once-added paths the ledger currently removes. A path
    the transfer record lists may only be removed, once: its content is the
    transfer's, so it cannot come back as a port. A path may be added only
    while it is absent, and removed only while it is present.
    """
    added: dict[str, dict[str, Any]] = {}
    removed: set[str] = set()
    errors: list[str] = []
    for index, row in enumerate(rows):
        where, path = f"{LEDGER} rows[{index}]", row["framework_path"]
        if row["change"] == "added":
            if path in transferred:
                errors.append(f"{where}: {path} is in the transfer record; a ledger row may only remove it")
            elif path in added:
                errors.append(f"{where}: {path} is already added")
            else:
                added[path] = row
                removed.discard(path)
        elif path in added:
            del added[path]
            removed.add(path)
        elif path in transferred and path not in removed:
            removed.add(path)
        else:
            errors.append(f"{where}: {path} is not a file framework/ holds, so it cannot be removed")
    return added, removed, errors


UNITS = "provenance/framework/units.json"
UNITS_SCHEMA = "vellum.framework-units.v1"
UNIT_TARGET_KEYS = {"name", "type", "unit", "sources", "include_dirs", "compile_definitions", "link_targets",
                    "reason"}
TARGET_TYPES = ("STATIC_LIBRARY", "EXECUTABLE")
DEFINITION = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z0-9_.+-]*)?\Z")
# Owned configuration: VELLUM_MIRROR_BUILD turns off the GPU-surface guard and
# GPU_RUNTIME_* definitions are the in-tree ABI. Unowned manifest data sets neither.
RESERVED_DEFINITION = re.compile(r"(?:VELLUM_MIRROR_BUILD|GPU_RUNTIME_[A-Za-z0-9_]*)(?:=|\Z)")


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value) and len(set(value)) == len(value)


def unit_target_errors(target: Any, where: str) -> list[str]:
    """What is wrong with one declared target's shape; empty when it is well formed.

    A declared target is a mirrored target no closure describes: a static
    library in a new unit, or an executable. Everything the build does with it
    is written here; nothing is inferred from the sources.
    """
    if not isinstance(target, dict) or set(target) != UNIT_TARGET_KEYS:
        return [f"{where}: must have exactly {sorted(UNIT_TARGET_KEYS)}"]
    errors: list[str] = []
    name, kind, unit = target["name"], target["type"], target["unit"]
    if not isinstance(name, str) or not TARGET_NAME.match(name):
        return [f"{where}: name {name!r} is not a target name"]
    # New targets carry Vellum's spelling: the name debt may only fall.
    if "pulp" in name.lower():
        errors.append(f"{where}: {name} is a new target, so it may not carry a Pulp name")
    if kind not in TARGET_TYPES:
        errors.append(f"{where}: type must be one of {list(TARGET_TYPES)}")
    if kind == "STATIC_LIBRARY":
        if not isinstance(unit, str) or not TARGET_NAME.match(unit):
            errors.append(f"{where}: a static library names the new unit whose library carries it")
        elif "pulp" in unit.lower():
            errors.append(f"{where}: unit {unit} is new, so it may not carry a Pulp name")
    elif kind == "EXECUTABLE" and unit is not None:
        errors.append(f"{where}: an executable belongs to no unit; unit is null")
    sources = target["sources"]
    if not string_list(sources) or not sources:
        errors.append(f"{where}: sources must list one or more distinct framework/ paths")
    else:
        for source in sources:
            if not safe_relative(source) or not source.startswith(FRAMEWORK_PREFIX) or not compiled(source):
                errors.append(f"{where}: source {source!r} is not a compilable path inside {FRAMEWORK_PREFIX}")
    includes = target["include_dirs"]
    if not string_list(includes):
        errors.append(f"{where}: include_dirs must be a list of distinct paths relative to {FRAMEWORK_PREFIX}")
    else:
        for include in includes:
            if not safe_relative(include):
                errors.append(f"{where}: include directory {include!r} is not a safe path relative to "
                              f"{FRAMEWORK_PREFIX}")
    definitions = target["compile_definitions"]
    if not string_list(definitions) or not all(DEFINITION.match(item) for item in definitions):
        errors.append(f"{where}: compile_definitions must be distinct NAME or NAME=value definitions")
    else:
        for item in definitions:
            if RESERVED_DEFINITION.match(item):
                errors.append(f"{where}: compile definition {item} is reserved for the mirror's own configuration")
    links = target["link_targets"]
    if not string_list(links) or not all(TARGET_NAME.match(link) for link in links):
        errors.append(f"{where}: link_targets must be a list of distinct target names")
    elif name in links:
        errors.append(f"{where}: {name} links itself")
    if not isinstance(target["reason"], str) or not target["reason"].strip():
        errors.append(f"{where}: reason must be a non-empty string")
    return errors


def parse_units(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """(targets, errors) for the unit manifest's text. Targets are returned only when every one is well formed."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        return [], [f"{UNITS}: unreadable: {exc}"]
    if not isinstance(document, dict) or set(document) != {"schema", "targets"} \
            or document["schema"] != UNITS_SCHEMA or not isinstance(document["targets"], list):
        return [], [f"{UNITS}: must be an object with schema {UNITS_SCHEMA} and a targets list"]
    errors = [error for index, target in enumerate(document["targets"])
              for error in unit_target_errors(target, f"{UNITS} targets[{index}]")]
    return ([] if errors else document["targets"]), errors


def load_units(repo: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """(targets, errors) for the checkout's unit manifest; a missing manifest declares nothing."""
    path = repo / UNITS
    if path.is_symlink():
        return [], [f"{UNITS}: is a symlink"]
    if not path.exists():
        return [], []
    try:
        return parse_units(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"{UNITS}: unreadable: {exc}"]


def units_errors(repo: Path, targets: list[dict[str, Any]], added: dict[str, dict[str, Any]]) -> list[str]:
    """What is wrong with well-formed declared targets against the ledger and the checkout.

    Each source is declared twice and the two must agree: the target lists it,
    and its ledger row adds it with that target. A row naming a declared
    target whose list leaves it out, and a listed source no row adds to the
    target, are both refused. A declared link to another declared target must
    name a static library declared earlier, so the units build in order and
    form no cycle. Links to closure and recipe targets are judged by the build
    plan, which reads the closures (tools/mirror/mirror_package.py).
    """
    errors: list[str] = []
    seen: dict[str, str] = {}
    for index, target in enumerate(targets):
        where, name = f"{UNITS} targets[{index}]", target["name"]
        if name in seen:
            errors.append(f"{where}: {name} is declared twice")
        for link in target["link_targets"]:
            if seen.get(link) == "EXECUTABLE":
                errors.append(f"{where}: {name} links the executable {link}")
        later = {other["name"] for other in targets[index + 1:]}
        for link in sorted(set(target["link_targets"]) & later - set(seen)):
            errors.append(f"{where}: {name} links {link}, which is declared after it")
        seen.setdefault(name, target["type"])
        for source in target["sources"]:
            row = added.get(source)
            if row is None:
                errors.append(f"{where}: source {source} has no ports ledger row adding it")
            elif row["target"] != name:
                errors.append(f"{where}: source {source} is added by its ledger row to {row['target']}, not {name}")
        for include in target["include_dirs"]:
            directory = repo / FRAMEWORK_PREFIX / include
            if directory.is_symlink() or not directory.is_dir():
                errors.append(f"{where}: include directory {FRAMEWORK_PREFIX}{include} is not a directory "
                              "framework/ holds")
    declared = {target["name"]: set(target["sources"]) for target in targets}
    for path, row in sorted(added.items()):
        if row["target"] in declared and path not in declared[row["target"]]:
            errors.append(f"{LEDGER}: {path} is added to the declared target {row['target']}, "
                          f"which does not list it in {UNITS}")
    return errors


def declared_units(repo: Path, added: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """(targets, errors) for the unit manifest, judged against the ledger's added rows.

    With errors no target is returned, so a broken manifest never widens what is built.
    """
    targets, errors = load_units(repo)
    if not errors:
        errors = units_errors(repo, targets, added)
    return ([] if errors else targets), errors


def file_set(repo: Path, transferred: set[str]) -> tuple[set[str], dict[str, dict[str, Any]], set[str], list[str]]:
    """The set framework/ must hold: (expected, added, removed, errors).

    With errors the expected set is the transfer record's alone, so a broken
    ledger never widens what is accepted.
    """
    rows, errors = load(repo)
    added, removed, replay_errors = replay(rows, transferred)
    errors = errors + replay_errors
    if errors:
        return set(transferred), {}, set(), errors
    return (set(transferred) - removed) | set(added), added, removed, errors
