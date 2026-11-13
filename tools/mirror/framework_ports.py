#!/usr/bin/env python3
"""The framework/ ports ledger: which files joined or left framework/ after the transfer.

The transfer record lists the files Pulp's export carried into framework/.
`provenance/framework/ports.json` is the append-only list of every later
change to that file set. Each row names one framework/ path, whether it was
added or removed, where it came from (the Pulp path and commits, or `native`
for a file Vellum wrote), and the mirrored target that compiles it when it is
a compilable source. The file set framework/ must hold is the transfer
record's paths, minus the removed rows, plus the added rows.

This module reads the ledger and replays it; it runs no git. Two tools share
it so they hold the same set: tools/provenance/verify_extraction.py, which
also proves the rows append-only and the Pulp commits real, and
tools/mirror/mirror_package.py, which compiles the added sources.

A missing ledger is an empty one.
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
ROW_KEYS = {"framework_path", "change", "target", "origin", "pulp_path", "pulp_commits", "reason"}
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
