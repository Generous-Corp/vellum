#!/usr/bin/env python3
"""Checks for Vellum's design-import source contracts.

product/source-contracts.json records, for each design-import source, what
Vellum promises: its parser, dispatch label, detection-manifest entry, tests,
fixtures and installed journey. A promise whose Vellum equivalent is still in
an open pull request is listed under `pending`; one with no Vellum
equivalent is listed under `deferred` with its reason. The report always
prints how many of each there are, and `--list-open` prints every one, so
nothing is dropped silently.

Structural drift fails under ``--strict``. Scheduled-recheck cadence stays
advisory: a recheck due within ``--warn-days`` (default 7) is reported ahead
of time, and under GitHub Actions each cadence finding is also printed as a
workflow warning annotation, without ever failing a check on the date alone.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "product/source-contracts.json"
DETECTION_MANIFEST = "product/source-detection.json"
SCHEMA_VERSION = "vellum.source-contracts.v1"
DEFAULT_WARN_DAYS = 7
CADENCE_CODES = {"source-contract-recheck-due", "stale-source-contract"}

KINDS = {"screen-export", "system-tokens", "static-only"}
TRUST_LEVELS = {"official", "observed", "absent"}
DISPATCH_KINDS = {"design-source-label", "explicit-runtime-parser", "static-only"}
COMPAT_MODES = {"foreign-key", "not-applicable"}
REFERENCE_STATUSES = {"present", "pending", "not-applicable"}
OPEN_CHECKS = {"tests", "fixtures", "journey", "render_reference", "upstream", "parser"}
SURFACE_OWNERS = ("framework/", "packages/", "product/")
VERSION = re.compile(r"\d+\.\d+\.\d+")
PULL_REQUEST = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
DESIGN_IMPORT = "framework/core/view/src/design_import.cpp"
SOURCE_SUPPORT = "product/source-support.yaml"
CI_LANES = "scripts/ci_lanes.json"
JOURNEY = re.compile(r"(?P<path>[^:\s]+)::(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\[(?P<lane>[a-z0-9-]+)\])?")
TEST_KINDS = ("ctest:", "lane:", "package:")
REQUIRED_FIELDS = (
    "source", "display_name", "kind", "trust", "compat", "dispatch", "upstream",
    "detected_artifact_shape", "runtime_export_shape", "runtime_requirements", "parser",
    "format_versions", "validation", "pending", "deferred",
)


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    source: str
    message: str
    path: str | None = None


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _source(entry: dict[str, Any]) -> str:
    value = entry.get("source")
    return value if isinstance(value, str) else "<unknown>"


def _add(findings: list[Finding], code: str, source: str, message: str, *,
         path: str | None = None, severity: str = "warning") -> None:
    findings.append(Finding(severity, code, source, message, path))


def _check_schema(findings: list[Finding], registry: dict[str, Any]) -> None:
    if registry.get("registry-schema-version") != SCHEMA_VERSION:
        _add(findings, "missing-schema-version", "<registry>",
             f"registry-schema-version must be present and equal to {SCHEMA_VERSION}")
    if not isinstance(registry.get("contracts"), list):
        _add(findings, "missing-contracts", "<registry>", "contracts must be a list")


def _check_entry_shape(findings: list[Finding], entry: dict[str, Any]) -> None:
    source = _source(entry)
    for field in REQUIRED_FIELDS:
        if field not in entry:
            _add(findings, "missing-required-field", source, f"missing required field: {field}")
    if entry.get("kind") not in KINDS:
        _add(findings, "invalid-kind", source, f"invalid kind: {entry.get('kind')!r}")
    if entry.get("trust") not in TRUST_LEVELS:
        _add(findings, "invalid-trust", source, f"invalid trust: {entry.get('trust')!r}")
    compat = entry.get("compat") or {}
    if compat.get("mode") not in COMPAT_MODES:
        _add(findings, "invalid-compat-mode", source, f"invalid compat.mode: {compat.get('mode')!r}")
    dispatch = entry.get("dispatch") or {}
    if dispatch.get("kind") not in DISPATCH_KINDS:
        _add(findings, "invalid-dispatch-kind", source,
             f"invalid dispatch.kind: {dispatch.get('kind')!r}")
    ref = (entry.get("validation") or {}).get("render_reference") or {}
    if ref.get("status") not in REFERENCE_STATUSES:
        _add(findings, "invalid-reference-status", source,
             f"invalid render_reference.status: {ref.get('status')!r}")


# The nested shape every contract must have: field -> expected type, where a
# dict describes an object's own fields and a one-element list describes a
# list of that item type. `str?` allows null.
STR, OPT_STR, BOOL = "str", "str?", "bool"
NESTED_SHAPE: dict[str, Any] = {
    "source": STR, "display_name": STR, "kind": STR, "trust": STR,
    "compat": {"mode": STR, "source": OPT_STR, "parser_version_owner": STR},
    "dispatch": {"kind": STR},
    "upstream": [{"url": STR, "kind": STR, "proves": STR}],
    "detected_artifact_shape": STR, "runtime_export_shape": STR,
    "runtime_requirements": {"sdk_min": STR, "surface_refs": [STR]},
    "parser": {"file": STR},
    "format_versions": [{"version": STR, "fixture_paths": [STR], "expected_paths": [STR]}],
    "validation": {"tests": [STR], "fixtures": [STR], "journey": OPT_STR,
                   "render_reference": {"status": STR, "path": OPT_STR}},
}
NON_EMPTY = {"upstream", "runtime_requirements.surface_refs"}


def _shape_problems(value: Any, shape: Any, where: str) -> list[str]:
    if shape == STR:
        return [] if isinstance(value, str) and value.strip() else [f"{where} must be a non-empty string"]
    if shape == OPT_STR:
        return [] if value is None or (isinstance(value, str) and value.strip()) else \
            [f"{where} must be a non-empty string or null"]
    if shape == BOOL:
        return [] if isinstance(value, bool) else [f"{where} must be true or false"]
    if isinstance(shape, list):
        if not isinstance(value, list):
            return [f"{where} must be a list"]
        if not value and where.removeprefix("contract.") in NON_EMPTY:
            return [f"{where} must not be empty"]
        problems = []
        for index, item in enumerate(value):
            problems += _shape_problems(item, shape[0], f"{where}[{index}]")
        return problems
    if not isinstance(value, dict):
        return [f"{where} must be an object"]
    problems = []
    for key, inner in shape.items():
        if key not in value:
            problems.append(f"{where}.{key} is missing")
        else:
            problems += _shape_problems(value[key], inner, f"{where}.{key}")
    return problems


def _check_nested_shape(findings: list[Finding], entry: dict[str, Any]) -> None:
    source = _source(entry)
    for key, shape in NESTED_SHAPE.items():
        if key in entry:
            for problem in _shape_problems(entry[key], shape, f"contract.{key}"):
                _add(findings, "invalid-field", source, problem)
    dispatch = entry.get("dispatch")
    if isinstance(dispatch, dict):
        needed = {"design-source-label": "label", "explicit-runtime-parser": "parser"}.get(
            dispatch.get("kind"))
        if needed and not (isinstance(dispatch.get(needed), str) and dispatch[needed].strip()):
            _add(findings, "invalid-field", source,
                 f"contract.dispatch.{needed} must be a non-empty string for {dispatch.get('kind')}")
    parser = entry.get("parser")
    if isinstance(parser, dict):
        for key in ("runtime_file", "runtime", "static"):
            if key in parser and parser[key] is not None and not (
                    isinstance(parser[key], str) and parser[key].strip()):
                _add(findings, "invalid-field", source,
                     f"contract.parser.{key} must be a non-empty string or null")


def contained_path(root: Path, rel_path: Any) -> Path | None:
    """`rel_path` under `root`, or None if it is absolute, empty, or escapes root."""
    if not isinstance(rel_path, str) or not rel_path or Path(rel_path).is_absolute():
        return None
    if ".." in PurePosixPath(rel_path).parts:
        return None
    resolved = (root / rel_path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _check_open_items(findings: list[Finding], entry: dict[str, Any]) -> None:
    """Pending items name what is open and where; deferred items say why."""
    source = _source(entry)
    for item in _as_list(entry.get("pending")):
        if (not isinstance(item, dict) or item.get("check") not in OPEN_CHECKS
                or not isinstance(item.get("item"), str) or not item["item"]
                or not isinstance(item.get("reason"), str) or not item["reason"].strip()
                or not (item.get("pr") is None
                        or (isinstance(item.get("pr"), str) and PULL_REQUEST.fullmatch(item["pr"])))):
            _add(findings, "invalid-pending-item", source,
                 f"pending items need check, item, reason and a pull request URL or null: {item!r}")
    if not isinstance(entry.get("pending"), list) and "pending" in entry:
        _add(findings, "invalid-pending-item", source, "pending must be a list")
    for item in _as_list(entry.get("deferred")):
        if (not isinstance(item, dict) or item.get("check") not in OPEN_CHECKS
                or not isinstance(item.get("reason"), str) or len(item["reason"].strip()) < 20):
            _add(findings, "invalid-deferred-item", source,
                 f"deferred items need a known check and a reason of at least 20 characters: {item!r}")
    if not isinstance(entry.get("deferred"), list) and "deferred" in entry:
        _add(findings, "invalid-deferred-item", source, "deferred must be a list")
    ref = (entry.get("validation") or {}).get("render_reference") or {}
    deferred_checks = {item.get("check") for item in _as_list(entry.get("deferred"))
                       if isinstance(item, dict)}
    if ref.get("status") == "pending" and "render_reference" not in deferred_checks and not any(
            isinstance(item, dict) and item.get("check") == "render_reference"
            for item in _as_list(entry.get("pending"))):
        _add(findings, "unexplained-pending-reference", source,
             "a pending render_reference must be listed under pending or deferred")


def _detection_imports(root: Path) -> dict[str, dict[str, Any]]:
    path = root / DETECTION_MANIFEST
    try:
        imports = json.loads(path.read_text(encoding="utf-8")).get("imports") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in imports.items() if isinstance(value, dict)}


def _check_compat(findings: list[Finding], imports: dict[str, dict[str, Any]],
                  entry: dict[str, Any]) -> None:
    source = _source(entry)
    compat = entry.get("compat") or {}
    if compat.get("mode") != "foreign-key":
        return
    compat_source = compat.get("source")
    if not isinstance(compat_source, str) or compat_source not in imports:
        _add(findings, "missing-compat-source", source,
             f"compat source does not exist in {DETECTION_MANIFEST} imports: {compat_source!r}")
        return
    detected = {item.get("format-version")
                for item in _as_list(imports[compat_source].get("detected-formats"))
                if isinstance(item, dict)}
    for version in _as_list(entry.get("format_versions")):
        name = version.get("version") if isinstance(version, dict) else None
        if isinstance(name, str) and name not in detected:
            _add(findings, "missing-compat-format", source,
                 f"format version {name!r} is not in {DETECTION_MANIFEST} "
                 f"imports.{compat_source}.detected-formats")


def _check_surface_refs(findings: list[Finding], root: Path, entry: dict[str, Any]) -> None:
    source = _source(entry)
    reqs = entry.get("runtime_requirements") or {}
    if not isinstance(reqs.get("sdk_min"), str) or not VERSION.fullmatch(reqs["sdk_min"]):
        _add(findings, "missing-sdk-min", source,
             "runtime_requirements.sdk_min must be a Vellum release version such as 0.2.3")
    for ref in _as_list(reqs.get("surface_refs")):
        if not isinstance(ref, str) or not ref.startswith(SURFACE_OWNERS) or \
                contained_path(root, ref) is None:
            _add(findings, "invalid-surface-ref", source, f"unknown surface ref owner: {ref!r}")
        elif not contained_path(root, ref).exists():
            _add(findings, "missing-surface-ref", source, f"surface ref does not exist: {ref}",
                 path=ref)


# Rechecks from this date on must record their evidence in verification_note.
EVIDENCE_REQUIRED_FROM = _dt.date(2026, 11, 10)


def _note_evidences(entry: dict[str, Any], last_verified: str) -> bool:
    note = entry.get("verification_note")
    return isinstance(note, str) and last_verified in note and len(note.strip()) > len(last_verified) + 10


def _check_cadence(findings: list[Finding], entry: dict[str, Any], today: _dt.date,
                   warn_days: int = DEFAULT_WARN_DAYS) -> None:
    source = _source(entry)
    last_verified = entry.get("last_verified")
    if last_verified == "unverified":
        _add(findings, "unverified-source", source, "source contract has not been fully verified")
        return
    if entry.get("recheck_interval") == "manual":
        return
    days = entry.get("recheck_interval_days")
    if not isinstance(days, int):
        return
    if not isinstance(last_verified, str):
        _add(findings, "invalid-last-verified", source, "last_verified must be ISO date or unverified")
        return
    try:
        verified_date = _dt.date.fromisoformat(last_verified)
    except ValueError:
        _add(findings, "invalid-last-verified", source, f"invalid ISO date: {last_verified!r}")
        return
    if verified_date >= EVIDENCE_REQUIRED_FROM and not _note_evidences(entry, last_verified):
        # A recheck moves last_verified only together with its evidence.
        _add(findings, "unevidenced-verification", source,
             f"last_verified {last_verified} has no verification_note naming that date")
        return
    due = verified_date + _dt.timedelta(days=days)
    if due < today:
        _add(findings, "stale-source-contract", source,
             f"source contract recheck is overdue: last verified {verified_date}, due {due}",
             severity="info")
    elif (due - today).days <= warn_days:
        left = (due - today).days
        when = "today" if left == 0 else f"in {left} day{'s' if left != 1 else ''}"
        _add(findings, "source-contract-recheck-due", source,
             f"source contract recheck is due {when}: last verified {verified_date}, due {due}",
             severity="info")


def _read(root: Path, rel_path: str) -> str:
    """A file's text, or empty when the path escapes the checkout or is unreadable."""
    path = contained_path(root, rel_path)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _design_source_labels(root: Path) -> set[str]:
    match = re.search(r"parse_design_source\s*\([^)]*\)\s*\{(?P<body>.*?)\n\}",
                      _read(root, DESIGN_IMPORT), flags=re.S)
    return set(re.findall(r'name\s*==\s*"([^"]+)"', match.group("body"))) if match else set()


def _symbol_exists(root: Path, rel_file: str, symbol: str) -> bool:
    return re.search(rf"\b{re.escape(symbol)}\b", _read(root, rel_file)) is not None


def cmake_graph(root: Path) -> list[Path]:
    """The CMakeLists.txt files the root build reaches through add_subdirectory().

    Only literal relative subdirectories are followed, so a CMakeLists.txt that
    no add_subdirectory() names is not part of the graph.
    """
    seen: list[Path] = []
    pending = [root.resolve()]
    while pending:
        directory = pending.pop()
        cmake = directory / "CMakeLists.txt"
        if not cmake.is_file() or cmake in seen:
            continue
        seen.append(cmake)
        text = cmake.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"(?m)^\s*add_subdirectory\(\s*\"?([A-Za-z0-9_./-]+)\"?", text):
            child = (directory / name).resolve()
            try:
                child.relative_to(root.resolve())
            except ValueError:
                continue
            pending.append(child)
    return seen


def ctest_names(root: Path) -> set[str]:
    """Every test name the configured CMake graph registers."""
    names: set[str] = set()
    for path in cmake_graph(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        names |= set(re.findall(r"add_test\(\s*NAME\s+\"?([A-Za-z0-9_.-]+)", text))
        names |= {f"vellum.framework.{name}"
                  for name in re.findall(r"vellum_add_framework_test\(\s*([A-Za-z0-9_-]+)", text)}
    return names


def _lane_covered(root: Path, rel_path: str) -> bool:
    try:
        manifest = json.loads(_read(root, CI_LANES))
    except json.JSONDecodeError:
        return False
    if rel_path in (manifest.get("elsewhere") or {}):
        return True
    for lane in (manifest.get("lanes") or {}).values():
        for entry in lane:
            if entry.get("script") == rel_path:
                return True
            if entry.get("module") == rel_path.removesuffix(".py").replace("/", "."):
                return True
            discover = entry.get("discover")
            if discover and str(PurePosixPath(rel_path).parent) == discover.rstrip("/") and \
                    fnmatch.fnmatch(PurePosixPath(rel_path).name, entry.get("pattern", "")):
                return True
    return False


def _package_covered(root: Path, rel_path: str) -> bool:
    parts = PurePosixPath(rel_path).parts
    if len(parts) < 4 or parts[0] != "packages":
        return False
    try:
        script = json.loads(_read(root, f"packages/{parts[1]}/package.json"))["scripts"]["test"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return False
    local = str(PurePosixPath(*parts[2:]))
    return any(fnmatch.fnmatch(local, pattern) for pattern in script.split() if "*" in pattern)


def test_resolves(root: Path, reference: str, ctests: set[str]) -> bool:
    kind, _, name = reference.partition(":")
    if kind == "ctest":
        return name in ctests
    path = contained_path(root, name)
    if kind == "lane":
        return path is not None and path.is_file() and _lane_covered(root, name)
    if kind == "package":
        return path is not None and path.is_file() and _package_covered(root, name)
    return False


def journey_resolves(root: Path, reference: str) -> bool:
    match = JOURNEY.fullmatch(reference)
    if not match or contained_path(root, match["path"]) is None:
        return False
    text = _read(root, match["path"])
    function = match["name"]
    if not re.search(rf"(?m)^\s*def {function}\(", text):
        return False
    if function != "validate" and len(re.findall(rf"\b{function}\(", text)) < 2:
        return False
    return not match["lane"] or f'"{match["lane"]}"' in text


def _manifest_journeys(root: Path) -> dict[str, str]:
    journeys: dict[str, str] = {}
    current = None
    for line in _read(root, SOURCE_SUPPORT).splitlines():
        entry = re.fullmatch(r"  ([a-z0-9-]+):", line)
        if entry:
            current = entry.group(1)
            continue
        field = re.fullmatch(r"    journey: (.+)", line)
        if field and current:
            journeys[current] = field.group(1).strip().strip('"')
    return journeys


def _check_references(findings: list[Finding], root: Path, entry: dict[str, Any],
                      labels: set[str], ctests: set[str], journeys: dict[str, str]) -> None:
    source = _source(entry)
    parser = entry.get("parser") or {}
    parser_file = parser.get("file")
    runtime_file = parser.get("runtime_file", parser_file)
    for label, rel in (("parser.file", parser_file), ("parser.runtime_file", runtime_file)):
        path = contained_path(root, rel)
        if path is None or not path.is_file():
            _add(findings, "missing-parser-file", source, f"{label} does not exist: {rel!r}", path=rel)
    dispatch = entry.get("dispatch") or {}
    if dispatch.get("kind") == "design-source-label" and dispatch.get("label") not in labels:
        _add(findings, "missing-design-source-label", source,
             f"dispatch label is not present in parse_design_source(): {dispatch.get('label')!r}")
    if dispatch.get("kind") == "explicit-runtime-parser" and (
            not isinstance(runtime_file, str) or not isinstance(dispatch.get("parser"), str)
            or not _symbol_exists(root, runtime_file, dispatch["parser"])):
        _add(findings, "missing-explicit-runtime-parser", source,
             f"explicit runtime parser symbol is missing: {dispatch.get('parser')!r}")
    for role, role_file in (("runtime", runtime_file), ("static", parser_file)):
        symbol = parser.get(role)
        if symbol is None:
            continue
        if not isinstance(symbol, str) or not isinstance(role_file, str) or \
                not _symbol_exists(root, role_file, symbol):
            _add(findings, "missing-parser-symbol", source,
                 f"parser.{role} symbol is missing from {role_file}: {symbol!r}", path=role_file)

    validation = entry.get("validation") or {}
    paths = [("fixture", p) for p in _as_list(validation.get("fixtures"))]
    for version in _as_list(entry.get("format_versions")):
        if isinstance(version, dict):
            paths += [("fixture", p) for p in _as_list(version.get("fixture_paths"))]
            paths += [("expected file", p) for p in _as_list(version.get("expected_paths"))]
    paths += [("evidence", item.get("url")) for item in _as_list(entry.get("upstream"))
              if isinstance(item, dict) and isinstance(item.get("url"), str)
              and not item["url"].startswith("https://")]
    for label, rel in paths:
        path = contained_path(root, rel)
        if path is None or not path.exists():
            _add(findings, f"missing-{label.replace(' ', '-')}", source,
                 f"{label} does not exist: {rel!r}", path=rel if isinstance(rel, str) else None)
    ref = validation.get("render_reference") or {}
    ref_path = contained_path(root, ref.get("path"))
    if ref.get("status") == "present" and not (ref_path is not None and ref_path.is_file()):
        _add(findings, "missing-reference-screenshot", source,
             f"render reference does not exist: {ref.get('path')!r}")

    open_checks = {item.get("check") for key in ("pending", "deferred")
                   for item in _as_list(entry.get(key)) if isinstance(item, dict)}
    tests = [t for t in _as_list(validation.get("tests")) if isinstance(t, str)]
    if not tests and "tests" not in open_checks:
        _add(findings, "missing-tests", source,
             "validation.tests is empty and no tests item is pending or deferred")
    for reference in tests:
        if not reference.startswith(TEST_KINDS) or not test_resolves(root, reference, ctests):
            _add(findings, "missing-test", source, f"test reference does not resolve: {reference}")
    if "journey_required" in validation and not isinstance(validation["journey_required"], bool):
        _add(findings, "invalid-field", source, "contract.validation.journey_required must be true or false")
    journey = validation.get("journey")
    if journey is None:
        required = validation.get("journey_required") is True or source in journeys
        if required and "journey" not in open_checks:
            _add(findings, "missing-journey", source,
                 "a journey is required but validation.journey is null and no journey item "
                 "is pending or deferred")
    elif not isinstance(journey, str) or not journey_resolves(root, journey):
        _add(findings, "missing-journey", source, f"journey does not resolve: {journey!r}")
    elif source in journeys and journeys[source] != journey:
        _add(findings, "journey-drift", source,
             f"journey {journey} differs from {SOURCE_SUPPORT}: {journeys[source]}")

    for item in _as_list(entry.get("pending")):
        if not isinstance(item, dict) or not isinstance(item.get("item"), str):
            continue
        target = item["item"]
        landed = (item.get("check") == "tests" and target.startswith(TEST_KINDS)
                  and test_resolves(root, target, ctests)) or \
                 (item.get("check") == "journey" and journey_resolves(root, target)) or \
                 (item.get("check") == "fixtures" and contained_path(root, target) is not None
                  and contained_path(root, target).exists())
        if landed:
            _add(findings, "pending-item-landed", source,
                 f"pending {item.get('check')} item now exists; move it into the contract: {target}",
                 severity="info")


def _check_coverage(findings: list[Finding], entries: list[dict[str, Any]], labels: set[str]) -> None:
    sources = {_source(entry) for entry in entries}
    for label in sorted(labels - sources):
        _add(findings, "missing-source-row", label,
             f"parse_design_source() label has no source-contract row: {label}")


def open_items(registry: dict[str, Any]) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    """Every (source, item) under pending and under deferred."""
    pending: list[tuple[str, dict]] = []
    deferred: list[tuple[str, dict]] = []
    for entry in _as_list(registry.get("contracts")):
        if not isinstance(entry, dict):
            continue
        for item in _as_list(entry.get("pending")):
            if isinstance(item, dict):
                pending.append((_source(entry), item))
        for item in _as_list(entry.get("deferred")):
            if isinstance(item, dict):
                deferred.append((_source(entry), item))
    return pending, deferred


def compute_findings(root: Path = REPO_ROOT, registry_path: Path = DEFAULT_REGISTRY, *,
                     today: _dt.date | None = None,
                     warn_days: int = DEFAULT_WARN_DAYS) -> list[Finding]:
    root = root.resolve()
    registry = load_registry(registry_path)
    findings: list[Finding] = []
    today = today or _dt.date.today()
    _check_schema(findings, registry)
    contracts = _as_list(registry.get("contracts"))
    entries = [entry for entry in contracts if isinstance(entry, dict)]
    if len(entries) != len(contracts):
        _add(findings, "invalid-contract-entry", "<registry>", "all contracts entries must be objects")
    imports = _detection_imports(root)
    labels = _design_source_labels(root)
    ctests = ctest_names(root)
    journeys = _manifest_journeys(root)
    for entry in entries:
        _check_entry_shape(findings, entry)
        _check_nested_shape(findings, entry)
        _check_open_items(findings, entry)
        _check_compat(findings, imports, entry)
        _check_surface_refs(findings, root, entry)
        _check_cadence(findings, entry, today, warn_days)
        _check_references(findings, root, entry, labels, ctests, journeys)
    _check_coverage(findings, entries, labels)
    return findings


def render_report(findings: list[Finding], *, fmt: str = "text") -> str:
    if fmt == "markdown":
        if not findings:
            return "Source-contract check: no findings.\n"
        lines = ["| Severity | Code | Source | Path | Message |", "|---|---|---|---|---|"]
        for finding in findings:
            message = finding.message.replace("|", "\\|")
            lines.append(f"| {finding.severity} | `{finding.code}` | `{finding.source}` | "
                         f"`{finding.path or ''}` | {message} |")
        return "\n".join(lines) + "\n"
    if not findings:
        return "source-contracts: no findings\n"
    lines = []
    for finding in findings:
        path = f" ({finding.path})" if finding.path else ""
        lines.append(f"[{finding.severity}] {finding.code} {finding.source}: {finding.message}{path}")
    return "\n".join(lines) + "\n"


def render_open_items(registry: dict[str, Any], *, detail: bool) -> str:
    pending, deferred = open_items(registry)
    lines = [f"source-contracts: {len(pending)} pending, {len(deferred)} deferred"]
    if detail:
        for source, item in pending:
            where = f" ({item['pr']})" if item.get("pr") else ""
            lines.append(f"  pending  {source} {item.get('check')}: {item.get('item')}; "
                         f"{item.get('reason')}{where}")
        for source, item in deferred:
            lines.append(f"  deferred {source} {item.get('check')}: {item.get('reason')}")
    return "\n".join(lines) + "\n"


def github_annotations(findings: list[Finding]) -> str:
    """Workflow commands that surface every cadence finding as a warning."""

    def escape(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    lines = []
    for finding in findings:
        if finding.code not in CADENCE_CODES:
            continue
        title = escape(f"{finding.code} {finding.source}").replace(",", "%2C").replace(":", "%3A")
        lines.append(f"::warning title={title}::{escape(finding.message)}")
    return "".join(f"{line}\n" for line in lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on structural warning/error findings; cadence remains advisory")
    parser.add_argument("--warn-days", type=int, default=DEFAULT_WARN_DAYS,
                        help="report a recheck this many days before it falls due (default: %(default)s)")
    parser.add_argument("--format", choices=("text", "markdown"), default="text")
    parser.add_argument("--list-open", action="store_true",
                        help="print every pending and deferred item, not only their counts")
    parser.add_argument("--today", type=_dt.date.fromisoformat, default=None,
                        help="evaluate recheck cadence as of this ISO date (default: today)")
    args = parser.parse_args(argv)
    findings = compute_findings(root=args.root, registry_path=args.registry,
                                warn_days=max(args.warn_days, 0), today=args.today)
    sys.stdout.write(render_report(findings, fmt=args.format))
    sys.stdout.write(render_open_items(load_registry(args.registry), detail=args.list_open))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        sys.stdout.write(github_annotations(findings))
    if args.strict and any(f.severity in {"warning", "error"} for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
