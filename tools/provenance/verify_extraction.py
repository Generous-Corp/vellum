#!/usr/bin/env python3
"""Verify immutable extraction history and the active-source boundary.

The historical cut manifest and filter maps describe the original projection.
They are verified against the preserved seed commit, not against HEAD. HEAD is
checked independently in two scopes (Decision 0010):

- Vellum-native roots must contain no Pulp namespace, Pulp package or target
  name, audio/plug-in SDK reference, or byte copy of any Pulp-origin blob.
- The mirror root holds Pulp framework source exported by automation. Every
  mirrored file must match an append-only blob-origin record, and no mirrored
  file may carry an audio, plug-in, host, or product edge. The Pulp namespace
  is permitted there because mirrored bytes stay identical to their origin.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


FILTERED_SEED = "e4f8c96fcfd19bac433252c36fdf5bfa681e6d25"
EXPECTED_SOURCE = "2ccff748f0d59da34b01ce1fbceabcf19f452731"
EXPECTED_HASHES = {
    "cut-manifest.json": "c2b392afa4c6a05d3079e31e24e21275b9a72e058adb4036539c7f5634e2d78d",
    "cut-paths.txt": "efe6dbb8d517bdee801dcc37ab4dd4a9a08868618e870acd9101a195d6caa753",
    "filter-repo-commit-map.txt": "c2bfb2665fbecfdab2407c02143bea0f8bf9d18cc779abb12293ff29b7f909c7",
    "filter-repo-ref-map.txt": "ef7546204c7bcbdffbd7bee4d234a2956a201bf51a287de1385afe9a15b01582",
}
EXPECTED_ACTIVE_ROOTS = (
    "CMakeLists.txt",
    "apps",
    "authoring",
    "bindings",
    "cli",
    "cmake",
    "foundation",
    "gpu-runtime",
    "graphics",
    "packages/vellum-design-ir",
    "packages/vellum-swift",
    "packages/vellum-ui",
    "rmlui",
    "runtime",
    "scripts",
    "templates",
)
MIRROR_ROOT = "mirror/"
# Decision 0010 option (a), M2 8b: the renamed, formerly mirrored units live in
# this native root. Its content is proved once, at the transfer commit, by the
# transfer record; from then on it is ordinary editable Vellum source under its
# own content regime (verify_framework below).
FRAMEWORK_ROOT = "framework/"
FRAMEWORK_TRANSFER_RECORD = "provenance/framework/transfer.json"
# The transfer record is immutable once it lands: its bytes are pinned here,
# so repointing it (at another receipt, records or tree) is an edit to this
# verifier, not to data, and it must also equal its copy at the merge base.
FRAMEWORK_TRANSFER_SHA256 = "81e8916ef73d0bf9b4d3a31409208524897ba2795e17fd749fea69756547e65c"
FRAMEWORK_TRANSFER_SCHEMA = "vellum.framework-transfer.v1"
FRAMEWORK_NAME_DEBT = "provenance/framework-name-debt.json"
# The native name rules framework/ carries as a counted debt until 8c respells
# its kept PULP_* macros, pulp-* targets and plug-in-host prose.
FRAMEWORK_RATCHETED_RULES = ("pulp-package-or-target", "audio-plugin-sdk", "pulp-public-namespace")
MIRROR_ORIGIN_RECORDS = "provenance/mirror/blob-origins.json"
MIRROR_ORIGIN_SCHEMA = "vellum.mirror-blob-origins.v1"
MIRROR_DECISION = "decisions/0010-mirror-extracted-pulp-framework-source.md"
MIRROR_RECORD_KEYS = {
    "mirror_path",
    "git_blob_sha",
    "pulp_commit",
    "pulp_path",
    "export_receipt",
}
EXPECTED_RULES = {
    "retired_paths_at_active_tip": 0,
    "native_files_matching_pulp_origin_blobs": 0,
    "mirror_files_without_matching_origin_record": 0,
    "mirror_files_without_pulp_identity_proof": 0,
    "mirror_origin_records_naming_unlanded_receipts": 0,
    "native_forbidden_namespace_or_product_references": 0,
    "mirror_audio_plugin_host_or_product_edges": 0,
    "open_extraction_debts": 0,
}
EXPECTED_RETIRED_PREFIXES = (
    "core/",
    "external/fonts/",
    "external/nanosvg/",
    "packages/pulp-import-ir/",
    "tools/figma-plugin/",
)
# Which files are text, and the Pulp names native source refuses, are shared
# with the SDK installer's payload scan (content_scan_policy.py).
CONTENT_SCAN_POLICY_PATH = Path(__file__).with_name("content_scan_policy.py")
_POLICY_SPEC = importlib.util.spec_from_file_location("content_scan_policy", CONTENT_SCAN_POLICY_PATH)
assert _POLICY_SPEC and _POLICY_SPEC.loader
content_scan_policy = importlib.util.module_from_spec(_POLICY_SPEC)
_POLICY_SPEC.loader.exec_module(content_scan_policy)
RENAMED_MIRROR_RULE = "renamed-mirror-namespace"
_ALL_NATIVE_PATTERNS = content_scan_policy.compiled_text_patterns()
# The renamed-mirror rule has its own, wider scope (scan_renamed_mirror below).
FORBIDDEN_ACTIVE_PATTERNS = {n: p for n, p in _ALL_NATIVE_PATTERNS.items() if n != RENAMED_MIRROR_RULE}
RENAMED_MIRROR_PATTERN = _ALL_NATIVE_PATTERNS[RENAMED_MIRROR_RULE]
# Where a copied, renamed mirror file must not land: every native root, plus
# the trees outside the active inventory that still hold Vellum source.
RENAMED_MIRROR_SCAN_ROOTS = EXPECTED_ACTIVE_ROOTS + ("components", "product", "tools", "web")
# Paths that must spell the forbidden names: the consumer built against the
# installed mirror, and the policy, its installer copy and their tests.
RENAMED_MIRROR_EXEMPT_PREFIXES: tuple[str, ...] = ()
RENAMED_MIRROR_EXEMPT_PATHS = {
    # The consumer built against the installed mirror, file by file: a new
    # file under tools/mirror/consumer/ is scanned like any other.
    "tools/mirror/consumer/cross_image_main.cpp",
    "tools/mirror/consumer/direct_gpu_main.cpp",
    "tools/mirror/consumer/direct_main.cpp",
    "tools/mirror/consumer/main.cpp",
    "tools/mirror/consumer/runtime_image_census_main.cpp",
    # The mirror package's own build glue: the provider anchor compiled into
    # the canvas unit, and the GPU guard whose #error names the guarded header.
    "tools/mirror/canvas_provider_anchor.cpp",
    "tools/mirror/gpu_surface_guard.py",
    # The rule itself, its installer copy and its tests.
    "scripts/install_core.py",
    "tools/provenance/content_scan_policy.py",
    "tools/provenance/test_verify_extraction.py",
    "tools/provenance/verify_extraction.py",
    # The vendored mirror-edge table and the inertness checker spell the
    # renamed namespaces to refuse them, with planted controls in its tests.
    "tools/provenance/mirror_edge_patterns.json",
    "tools/mirror/verify_mirror_inertness.py",
    "tools/mirror/test_verify_mirror_inertness.py",
    # The 8b rename tool's tests plant the renamed spellings they check for.
    "tools/mirror/test_rename_mirror_namespace.py",
}


def scans_active_content(relative: str) -> bool:
    """Whether the native scan reads this checkout path as text."""
    suffix = Path(relative).suffix.lower()
    if suffix in content_scan_policy.NATIVE_SOURCE_EXCLUDED_SUFFIXES:
        return False
    return content_scan_policy.should_scan_content(relative, extensionless=True)


# Forbidden mirror edges (Decision 0010 / D3): audio, DSP, MIDI, music theory,
# plug-in formats, plug-in hosting, GPU audio and product content stay
# Pulp-owned, and so do the Pulp-kept view and state files. The table is
# vendored byte-for-byte from Pulp's tools/scripts/vellum_mirror_edge_patterns.json,
# which Pulp's closure gate applies before export. mirror_edge_patterns.pin.json
# records the Pulp commit and git blob it was vendored from: every run checks
# the vendored bytes against that blob, --mirror-edge-pulp-root proves the blob
# against a Pulp checkout of the pinned commit, and --pulp-root additionally
# compares it with the Pulp commit of the last export.
MIRROR_EDGE_TABLE = Path(__file__).with_name("mirror_edge_patterns.json")
MIRROR_EDGE_TABLE_PIN = Path(__file__).with_name("mirror_edge_patterns.pin.json")
PULP_MIRROR_EDGE_TABLE = "tools/scripts/vellum_mirror_edge_patterns.json"
_MIRROR_EDGE_TABLE = json.loads(MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
FORBIDDEN_MIRROR_EDGE_PATTERNS = {
    name: re.compile(pattern) for name, pattern in _MIRROR_EDGE_TABLE["content_patterns"].items()
}
# Path rules are relative to the mirror root.
MIRROR_EDGE_PATH_PATTERNS = {
    name: re.compile(pattern) for name, pattern in _MIRROR_EDGE_TABLE["path_patterns"].items()
}
PULP_KEPT_MIRROR_PATH = MIRROR_EDGE_PATH_PATTERNS["mirror-pulp-kept-file"]
MIRROR_EDGE_SUFFIXES = set(_MIRROR_EDGE_TABLE["suffixes"])
MIRROR_EDGE_BINARY_SUFFIXES = set(_MIRROR_EDGE_TABLE["binary_suffixes"])
# One exact line of one Pulp path may be exempt from one content rule, each
# tied to a recorded Pulp view-audio seam debt (Pulp generates these rows).
MIRROR_EDGE_EXCEPTIONS = {
    (row["rule"], row["path"], row["line"]) for row in _MIRROR_EDGE_TABLE.get("approved_exceptions", [])
}


def mirror_edge_exempt(
    rule: str, path_in_mirror: str, text: str, start: int, exceptions: set | None = None
) -> bool:
    """Whether the match at `start` sits on an approved exception line."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    line = text[line_start:] if line_end < 0 else text[line_start:line_end]
    return (rule, path_in_mirror, line) in (MIRROR_EDGE_EXCEPTIONS if exceptions is None else exceptions)
POLICY_IMPLEMENTATION_PATHS = {
    "scripts/verify_sdk_artifact.py",
}
SHA_RE = re.compile(r"[0-9a-f]{40}")
# An empty file is identical everywhere and proves no copying.
EMPTY_BLOB_SHA = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def load_mirror_export_verifier():
    path = Path(__file__).with_name("verify_mirror_export.py")
    spec = importlib.util.spec_from_file_location("verify_mirror_export", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git object identity


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT
    ).strip()


def seed_blobs(root: Path) -> dict[str, str]:
    output = git(root, "ls-tree", "-r", FILTERED_SEED)
    blobs: dict[str, str] = {}
    for line in output.splitlines():
        metadata, path = line.split("\t", 1)
        _mode, object_type, object_id = metadata.split(" ")
        if object_type == "blob":
            blobs[path] = object_id
    return blobs


def top_level_scalar(path: Path, key: str) -> str | None:
    prefix = f"{key}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def configured_boundary(root: Path) -> dict[str, object]:
    return json.loads((root / "provenance/active-source-boundary.json").read_text())


def active_files(root: Path, roots: Iterable[str]) -> Iterable[Path]:
    seen: set[Path] = set()
    for relative in roots:
        candidate = root / relative
        if candidate.is_file():
            files = [candidate]
        elif candidate.is_dir():
            files = [path for path in candidate.rglob("*") if path.is_file()]
        else:
            continue
        for path in files:
            if path in seen:
                continue
            if any(part in {"node_modules", "__pycache__", ".git"} for part in path.parts):
                continue
            seen.add(path)
            yield path


def scan_active_surface(root: Path, roots: Iterable[str] = EXPECTED_ACTIVE_ROOTS) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in active_files(root, roots):
        relative = path.relative_to(root).as_posix()
        if relative in POLICY_IMPLEMENTATION_PATHS or not scans_active_content(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Fail closed: a text-class file the scan cannot read is not proof
            # that it holds nothing forbidden.
            findings.append({"rule": "undecodable-native-text", "path": relative, "line": 0, "match": ""})
            continue
        for name, pattern in FORBIDDEN_ACTIVE_PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "rule": name,
                        "path": path.relative_to(root).as_posix(),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "match": match.group(0)[:120],
                    }
                )
    return findings


def scan_renamed_mirror(root: Path, roots: Iterable[str] = RENAMED_MIRROR_SCAN_ROOTS) -> list[dict[str, object]]:
    """The renamed-mirror rule over every native tree, Python included.

    Python is read here, unlike the main native scan: a respelled copy of a
    mirrored generator (core/view/js/embed_js.py) emits `namespace
    vellum::view::...` from Python.
    """
    findings: list[dict[str, object]] = []
    for path in active_files(root, roots):
        relative = path.relative_to(root).as_posix()
        if (relative.startswith(MIRROR_ROOT) or relative in RENAMED_MIRROR_EXEMPT_PATHS
                or relative.startswith(RENAMED_MIRROR_EXEMPT_PREFIXES)
                or not content_scan_policy.should_scan_content(relative, extensionless=True)):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append({"rule": "undecodable-native-text", "path": relative, "line": 0, "match": ""})
            continue
        for match in RENAMED_MIRROR_PATTERN.finditer(text):
            findings.append({"rule": RENAMED_MIRROR_RULE, "path": relative,
                             "line": text.count("\n", 0, match.start()) + 1, "match": match.group(0)[:120]})
    return findings


# A mirror file copied into a native root and edited only in comments,
# whitespace or line endings keeps the same normalized content. Normalizing
# also respells pulp -> vellum, so the raw and the renamed spelling of a mirror
# file are one fingerprint. Files whose normalized form is shorter than this
# carry too little to fingerprint (`#pragma once`, empty initializers).
NORMALIZED_MIN_BYTES = 64
NORMALIZED_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inl", ".inc", ".ipp", ".in", ".c", ".cc", ".cpp", ".cxx",
                       ".m", ".mm", ".cmake", ".py", ".js", ".mjs", ".ts"}
# String and character literals are matched first and kept, so a `/*` or
# `//` inside one ("include/*.hpp") cannot swallow the code after it.
_COMMENTS = re.compile(
    rb'("(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
    rb"|//[^\n]*|/\*.*?\*/"
    rb"|(?m:^\s*#(?!\s*(?:include|import|define|if|ifdef|ifndef|else|elif|endif|pragma|undef))[^\n]*)", re.S)


def _strip_comments(data: bytes) -> bytes:
    return _COMMENTS.sub(lambda m: m.group(1) or b"", data)
_SPACE = re.compile(rb"\s+")


def normalized_fingerprint(data: bytes) -> str | None:
    text = _SPACE.sub(b"", _strip_comments(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")))
    text = text.replace(b"namespacepulp", b"namespacevellum").replace(b"pulp::", b"vellum::")
    text = text.replace(b"pulp/", b"vellum/")
    if len(text) < NORMALIZED_MIN_BYTES:
        return None
    return hashlib.sha256(text).hexdigest()


def framework_or_mirror_files(root: Path) -> tuple[str, list[Path]]:
    """The renamed units' files: framework/ once transferred, mirror/ before."""
    if (root / FRAMEWORK_ROOT.rstrip("/")).is_dir():
        return FRAMEWORK_ROOT, list(active_files(root, (FRAMEWORK_ROOT.rstrip("/"),)))
    return MIRROR_ROOT, list(mirror_files(root))


def normalized_copy_findings(root: Path, roots: Iterable[str] = RENAMED_MIRROR_SCAN_ROOTS) -> list[dict[str, str]]:
    """Native files whose normalized content equals a renamed unit's (a second copy).

    The units are framework/ after the 8b transfer and mirror/ before it.
    Normalizing respells pulp -> vellum, so a copy of Pulp's frozen source is
    the same fingerprint as a copy of the renamed unit.
    """
    mirror: dict[str, str] = {}
    prefix, units = framework_or_mirror_files(root)
    for path in units:
        relative = path.relative_to(root).as_posix()
        if relative.startswith(prefix + "external/") or path.suffix.lower() not in NORMALIZED_SUFFIXES:
            continue
        fingerprint = normalized_fingerprint(path.read_bytes())
        if fingerprint:
            mirror.setdefault(fingerprint, relative)
    findings: list[dict[str, str]] = []
    for path in active_files(root, roots):
        relative = path.relative_to(root).as_posix()
        if relative.startswith((MIRROR_ROOT, FRAMEWORK_ROOT)) or path.suffix.lower() not in NORMALIZED_SUFFIXES:
            continue
        fingerprint = normalized_fingerprint(path.read_bytes())
        if fingerprint and fingerprint in mirror:
            findings.append({"path": relative, "mirror": mirror[fingerprint]})
    return findings


def symlink_findings(root: Path, roots: Iterable[str] = RENAMED_MIRROR_SCAN_ROOTS) -> list[str]:
    """Symlinks in the scanned native trees, tracked or on disk.

    The scans walk with rglob, which does not descend into a symlinked
    directory, so `graphics/linkdir -> ../mirror/core/view/src` would hide its
    files from every native check. None is allowed.
    """
    found: set[str] = set()
    for relative in roots:
        top = root / relative
        if top.is_symlink():
            found.add(relative)
            continue
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            # Installed dependencies (`npm ci` leaves node_modules/.bin links)
            # are gitignored, so never part of the verified tree. Tracked
            # symlinks anywhere are still caught by the ls-files pass below.
            dirnames[:] = [name for name in dirnames if name != "node_modules" or (Path(dirpath) / name).is_symlink()]
            for name in dirnames + filenames:
                path = Path(dirpath) / name
                if path.is_symlink():
                    found.add(path.relative_to(root).as_posix())
    try:
        staged = git(root, "ls-files", "-s", "--", *roots)
    except (subprocess.CalledProcessError, FileNotFoundError):
        staged = ""
    for line in staged.splitlines():
        if line.startswith("120000 "):
            found.add(line.split("\t", 1)[1])
    return sorted(found)


def retired_path_findings(root: Path, prefixes: Iterable[str]) -> list[str]:
    tracked = set(git(root, "ls-files").splitlines())
    findings: set[str] = set()
    for prefix in prefixes:
        findings.update(path for path in tracked if path.startswith(prefix))
        candidate = root / prefix.rstrip("/")
        if candidate.is_file():
            findings.add(candidate.relative_to(root).as_posix())
        elif candidate.is_dir():
            findings.update(
                path.relative_to(root).as_posix()
                for path in candidate.rglob("*")
                if path.is_file()
            )
    return sorted(findings)


def copied_seed_findings(
    root: Path, roots: Iterable[str], historical_blob_ids: set[str]
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in active_files(root, roots):
        blob = git_blob_sha(path.read_bytes())
        if blob in historical_blob_ids:
            findings.append({"path": path.relative_to(root).as_posix(), "git_blob_sha": blob})
    return findings


def load_mirror_origin_records(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Load the append-only blob-origin records for the mirror root."""
    if not path.is_file():
        return [], [f"{MIRROR_ORIGIN_RECORDS} is missing"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [f"{MIRROR_ORIGIN_RECORDS} is unreadable: {exc}"]
    if not isinstance(data, dict) or set(data) != {"schema", "pulp_repository", "records"}:
        return [], [f"{MIRROR_ORIGIN_RECORDS} has unexpected top-level keys"]
    errors: list[str] = []
    if data["schema"] != MIRROR_ORIGIN_SCHEMA:
        errors.append(f"{MIRROR_ORIGIN_RECORDS} schema must be {MIRROR_ORIGIN_SCHEMA}")
    if data["pulp_repository"] != "Generous-Corp/pulp":
        errors.append(f"{MIRROR_ORIGIN_RECORDS} must name Generous-Corp/pulp")
    records = data["records"]
    if not isinstance(records, list):
        return [], errors + [f"{MIRROR_ORIGIN_RECORDS} records must be a list"]
    valid: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, record in enumerate(records):
        label = f"{MIRROR_ORIGIN_RECORDS} record {index}"
        if not isinstance(record, dict) or set(record) != MIRROR_RECORD_KEYS:
            errors.append(f"{label} has unexpected keys")
            continue
        if not all(isinstance(record[key], str) for key in MIRROR_RECORD_KEYS):
            errors.append(f"{label} has a non-string field")
            continue
        if not SHA_RE.fullmatch(record["git_blob_sha"]):
            errors.append(f"{label} git_blob_sha is not a full object ID")
        if not SHA_RE.fullmatch(record["pulp_commit"]):
            errors.append(f"{label} pulp_commit is not a full commit SHA")
        mirror_path = record["mirror_path"]
        if not mirror_path.startswith(MIRROR_ROOT) or ".." in mirror_path.split("/"):
            errors.append(f"{label} mirror_path is outside {MIRROR_ROOT}")
        pulp_path = record["pulp_path"]
        if not pulp_path or pulp_path.startswith("/") or ".." in pulp_path.split("/"):
            errors.append(f"{label} pulp_path is not a repository-relative path")
        if not record["export_receipt"]:
            errors.append(f"{label} names no export receipt")
        identity = tuple(record[key] for key in sorted(MIRROR_RECORD_KEYS))
        if identity in seen:
            errors.append(f"{label} duplicates an earlier record")
        seen.add(identity)
        valid.append(record)
    return valid, errors


def mirror_files(root: Path) -> Iterable[Path]:
    return active_files(root, (MIRROR_ROOT.rstrip("/"),))


def mirror_origin_findings(
    root: Path, records: Iterable[dict[str, str]]
) -> list[dict[str, str]]:
    """Every mirrored file must equal a recorded Pulp-origin blob at its path.

    Records are append-only, so an earlier export's record may remain after a
    later export replaced or removed the file; only the reverse is a failure.
    """
    recorded: dict[str, set[str]] = {}
    for record in records:
        recorded.setdefault(record["mirror_path"], set()).add(record["git_blob_sha"])
    findings: list[dict[str, str]] = []
    for path in mirror_files(root):
        relative = path.relative_to(root).as_posix()
        blob = git_blob_sha(path.read_bytes())
        if relative not in recorded:
            rule = "mirror-file-without-origin-record"
        elif blob not in recorded[relative]:
            rule = "mirror-blob-differs-from-origin-record"
        else:
            continue
        findings.append({"rule": rule, "path": relative, "git_blob_sha": blob})
    return findings


def mirror_edge_table_drift(
    pulp_root: Path, table: Path = MIRROR_EDGE_TABLE, pin_path: Path = MIRROR_EDGE_TABLE_PIN
) -> list[str]:
    """The vendored edge table is Pulp's copy at this checkout, or a later one.

    The checkout is the Pulp commit of the last export. The table may advance
    on Pulp main between exports (a stricter rule lands before the next
    export), so a different copy passes only when the pinned commit is on
    Pulp main (refs/remotes/origin/main), descends from this checkout and
    holds the vendored blob. verify() then scans the mirror under both the
    vendored table and this checkout's table.
    """
    pulp_table = pulp_root / PULP_MIRROR_EDGE_TABLE
    if not pulp_table.is_file():
        return [f"Pulp checkout {pulp_root} has no {PULP_MIRROR_EDGE_TABLE}"]
    vendored = table.read_bytes()
    if pulp_table.read_bytes() == vendored:
        return []
    differs = f"{table.name} differs from Pulp's {PULP_MIRROR_EDGE_TABLE}"
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        pinned = str(pin["pulp_commit"])
        head = git(pulp_root, "rev-parse", "HEAD")
        descends = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head, pinned],
            cwd=pulp_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode == 0
        blob = git(pulp_root, "rev-parse", f"{pinned}:{PULP_MIRROR_EDGE_TABLE}")
    except (OSError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError):
        return [f"{differs}, and the pinned commit cannot be resolved in {pulp_root}"]
    # A descendant on a side branch is unreviewed: only Pulp main may advance the table.
    main = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"],
        cwd=pulp_root, capture_output=True, text=True, check=False,
    )
    if main.returncode != 0 or not main.stdout.strip():
        return [f"{differs}, and {pulp_root} has no refs/remotes/origin/main to prove the pin is on Pulp main"]
    on_main = subprocess.run(
        ["git", "merge-base", "--is-ancestor", pinned, main.stdout.strip()],
        cwd=pulp_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0
    if not on_main:
        return [f"{differs}, and the pinned commit {pinned} is not on Pulp main"]
    if not descends:
        return [f"{differs}, and the pinned commit {pinned} does not descend from {head}"]
    if blob != git_blob_sha(vendored) or blob != pin.get("git_blob"):
        return [f"{differs}, and the pinned commit {pinned} does not hold the vendored table"]
    return []


def mirror_edge_table_pin_errors(
    pulp_root: Path | None = None, table: Path = MIRROR_EDGE_TABLE, pin_path: Path = MIRROR_EDGE_TABLE_PIN
) -> list[str]:
    """The vendored table is the exact blob its pin names, and, given a Pulp
    checkout of the pinned commit, that blob is Pulp's table there."""
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{pin_path.name} is unreadable: {exc}"]
    if pin.get("schema") != "vellum.mirror-edge-table-pin.v1" \
            or pin.get("pulp_repository") != "Generous-Corp/pulp" \
            or pin.get("pulp_path") != PULP_MIRROR_EDGE_TABLE \
            or not re.fullmatch(r"[0-9a-f]{40}", str(pin.get("pulp_commit", ""))) \
            or not re.fullmatch(r"[0-9a-f]{40}", str(pin.get("git_blob", ""))):
        return [f"{pin_path.name} must name Generous-Corp/pulp, {PULP_MIRROR_EDGE_TABLE}, a commit and a git blob"]
    errors: list[str] = []
    if git_blob_sha(table.read_bytes()) != pin["git_blob"]:
        errors.append(f"{table.name} is not the git blob {pin['git_blob']} its pin names")
    if pulp_root is not None:
        try:
            head = git(pulp_root, "rev-parse", "HEAD")
            blob = git(pulp_root, "rev-parse", f"HEAD:{PULP_MIRROR_EDGE_TABLE}")
        except (OSError, subprocess.CalledProcessError) as exc:
            return errors + [f"Pulp checkout {pulp_root} cannot resolve the pinned table: {exc}"]
        if head != pin["pulp_commit"]:
            errors.append(f"Pulp checkout {pulp_root} is at {head}, not the pinned {pin['pulp_commit']}")
        if blob != pin["git_blob"]:
            errors.append(f"Pulp's {PULP_MIRROR_EDGE_TABLE} at {head} is blob {blob}, not the pinned {pin['git_blob']}")
    return errors


def compile_mirror_edge_table(table: dict) -> dict[str, object]:
    return {
        "content": {name: re.compile(pattern) for name, pattern in table["content_patterns"].items()},
        "path": {name: re.compile(pattern) for name, pattern in table["path_patterns"].items()},
        "suffixes": set(table["suffixes"]),
        "binary_suffixes": set(table.get("binary_suffixes", [])),
        "exceptions": {(row["rule"], row["path"], row["line"]) for row in table.get("approved_exceptions", [])},
    }


def mirror_edge_findings(root: Path, table: dict | None = None, prefix: str = MIRROR_ROOT) -> list[dict[str, object]]:
    """Mirror edges under the vendored table, or under another table's rules.

    `prefix` is the root scanned: mirror/, or framework/ after the transfer,
    whose paths keep the export's layout below it.
    """
    if table is not None:
        rules = compile_mirror_edge_table(table)
        content_rules, path_rules = rules["content"], rules["path"]
        suffixes, binary, exceptions = rules["suffixes"], rules["binary_suffixes"], rules["exceptions"]
    else:
        content_rules, path_rules = FORBIDDEN_MIRROR_EDGE_PATTERNS, MIRROR_EDGE_PATH_PATTERNS
        suffixes, binary, exceptions = MIRROR_EDGE_SUFFIXES, MIRROR_EDGE_BINARY_SUFFIXES, MIRROR_EDGE_EXCEPTIONS
    findings: list[dict[str, object]] = []
    for path in active_files(root, (prefix.rstrip("/"),)):
        relative = path.relative_to(root).as_posix()
        in_mirror = relative.removeprefix(prefix)
        for name, pattern in path_rules.items():
            if pattern.search(in_mirror):
                findings.append({"rule": name, "path": relative, "line": 0, "match": relative})
        suffix = path.suffix.lower()
        if suffix in binary:
            continue
        if suffix not in suffixes:
            findings.append({"rule": "mirror-unclassified-suffix", "path": relative, "line": 0, "match": suffix})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in content_rules.items():
            for match in pattern.finditer(text):
                if mirror_edge_exempt(name, in_mirror, text, match.start(), exceptions):
                    continue
                findings.append(
                    {
                        "rule": name,
                        "path": path.relative_to(root).as_posix(),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "match": match.group(0)[:120],
                    }
                )
    return findings


PULP_DEPENDENCY_MANIFEST = "tools/deps/manifest.json"
RENDERER_ASSET_KEY = "mac-universal"


def current_mirror_receipts(
    root: Path, receipts: Iterable[str]
) -> tuple[list[str], list[str], list[str]]:
    """Split landed receipts into the current one and the older ones.

    The current receipt is the one whose mirror_tree is HEAD:mirror: the
    mirror Vellum builds today. Returns (current, older, errors); exactly one
    receipt may be current.
    """
    receipts = list(receipts)
    if not receipts:
        return [], [], []
    try:
        head_tree = git(root, "rev-parse", "HEAD:mirror")
    except subprocess.CalledProcessError:
        head_tree = None
    if head_tree is None:
        # After the 8b transfer the receipt Vellum builds from is the one the
        # transfer record names, and its mirror_tree is the record's.
        try:
            transfer = json.loads((root / FRAMEWORK_TRANSFER_RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], [], ["HEAD has no mirror/ tree to name the current export receipt"]
        named = transfer.get("export_receipt") if isinstance(transfer, dict) else None
        if named not in receipts:
            return [], receipts, [f"{FRAMEWORK_TRANSFER_RECORD} names {named!r}, which is not a landed receipt"]
        head_tree = transfer.get("mirror_tree")
    current: list[str] = []
    older: list[str] = []
    for receipt_path in receipts:
        try:
            receipt = json.loads((root / receipt_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            receipt = {}
        tree = receipt.get("mirror_tree") if isinstance(receipt, dict) else None
        (current if tree == head_tree else older).append(receipt_path)
    if len(current) != 1:
        return current, older, [
            f"{len(current)} landed receipts name HEAD:mirror ({head_tree}); exactly one must"]
    return current, older, []


def renderer_pin_errors(
    root: Path, pulp_root: Path, receipts: Iterable[str]
) -> tuple[list[str], list[dict[str, object]]]:
    """The current mirror export must come from a Pulp commit pinning the locked renderer.

    A mirror export copies Pulp sources that compile against Pulp's Skia/Dawn
    pin. If that pin is not the archive and source tuple Vellum's lock names,
    the export and the renderer it links disagree, and the dependency checksums
    alone would not notice. Callers pass the current receipt only (see
    current_mirror_receipts): the pin is a property of the mirror Vellum builds
    today, so an older receipt is never judged against a lock it was not
    exported under; it keeps its identity, origin and blob checks.
    """
    lock = json.loads((root / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
    renderer = lock["runtime_toolchain_dependencies"]["renderer"]
    artifact = renderer["artifact"]
    identities = renderer["source_identities"]
    expected = {
        "skia_branch": artifact["release_tag"],
        "skia_commit": identities["skia"]["commit"],
        "built_dawn": identities["dawn"]["commit"],
        "skia_builder_ref": identities["builder"]["commit"],
        "archive_url": artifact["url"],
        "archive_sha256": artifact["sha256"],
    }
    errors: list[str] = []
    rows: list[dict[str, object]] = []
    for receipt_path in receipts:
        receipt = json.loads((root / receipt_path).read_text(encoding="utf-8"))
        pulp_sha = receipt.get("pulp_sha")
        label = f"{receipt_path} (Pulp {pulp_sha})"
        try:
            manifest = json.loads(git(pulp_root, "show", f"{pulp_sha}:{PULP_DEPENDENCY_MANIFEST}"))
        except (subprocess.CalledProcessError, ValueError) as exc:
            errors.append(f"{label}: cannot read Pulp's {PULP_DEPENDENCY_MANIFEST}: {exc}")
            continue
        pins = [d.get("determinism") for d in manifest.get("dependencies", [])
                if isinstance(d, dict) and isinstance(d.get("determinism"), dict)
                and d["determinism"].get("skia_branch")]
        if len(pins) != 1:
            errors.append(f"{label}: expected one Skia determinism pin, found {len(pins)}")
            continue
        pin = pins[0]
        asset = (pin.get("release_assets") or {}).get(RENDERER_ASSET_KEY) or {}
        actual = {key: pin.get(key) for key in ("skia_branch", "skia_commit", "built_dawn", "skia_builder_ref")}
        actual.update(archive_url=asset.get("url"), archive_sha256=asset.get("sha256"))
        mismatched = sorted(key for key in expected if actual[key] != expected[key])
        rows.append({"receipt": receipt_path, "pulp_sha": pulp_sha, "matches_lock": not mismatched})
        if mismatched:
            errors.append(
                f"{label}: Pulp's renderer pin differs from third-party-lock.json in "
                + ", ".join(f"{key} ({actual[key]} != {expected[key]})" for key in mismatched))
    return errors, rows


def mirror_renderer_pin_check(
    root: Path, pulp_root: Path, receipts: Iterable[str]
) -> tuple[list[str], list[dict[str, object]]]:
    """Check the current receipt's renderer pin; report older receipts as unchecked."""
    current, older, errors = current_mirror_receipts(root, receipts)
    pin_errors, rows = renderer_pin_errors(root, pulp_root, current) if not errors else ([], [])
    rows += [{"receipt": path, "matches_lock": None, "checked": "older export, not HEAD:mirror"}
             for path in older]
    return errors + pin_errors, rows


def verify_origins_against_pulp(
    pulp_root: Path, records: Iterable[dict[str, str]]
) -> list[str]:
    """Prove each record against the Pulp history it cites (identity oracle)."""
    errors: list[str] = []
    try:
        main = git(pulp_root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    except subprocess.CalledProcessError:
        return [f"Pulp checkout {pulp_root} has no refs/remotes/origin/main"]
    for record in records:
        on_main = subprocess.run(
            ["git", "merge-base", "--is-ancestor", record["pulp_commit"], main],
            cwd=pulp_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not on_main:
            errors.append(
                f"mirror origin {record['mirror_path']} cites Pulp commit "
                f"{record['pulp_commit']}, which is not on the checked-out Pulp main"
            )
            continue
        spec = f"{record['pulp_commit']}:{record['pulp_path']}"
        try:
            actual = git(pulp_root, "rev-parse", "--verify", "--quiet", spec)
        except subprocess.CalledProcessError:
            actual = ""
        if actual != record["git_blob_sha"]:
            errors.append(
                f"mirror origin {record['mirror_path']} is not blob "
                f"{record['git_blob_sha']} at Pulp {spec}"
            )
    return errors


def verify_authority_phase(
    extraction: dict[str, object], lock: dict[str, object]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    authority = extraction.get("authority")
    if not isinstance(authority, dict):
        return False, ["pulp-extraction.json authority record is malformed"]
    state = authority.get("state")
    if extraction.get("status") == "prepared" and state == "prepared":
        for field in (
            "ownership_start_commit",
            "authority_record_commit",
            "pulp_activation_commit",
            "accepted_by",
            "accepted_at",
        ):
            if authority.get(field) is not None:
                errors.append(f"prepared extraction authority carries {field}")
        if lock.get("state") != "prepared":
            errors.append("prepared extraction authority differs from observatory lock")
        return False, errors
    if extraction.get("status") != "active" or state != "active":
        return False, ["extraction status and authority state must be prepared or active"]
    coordinates = {
        "ownership_start_commit": lock.get("vellum_authority_start_commit"),
        "authority_record_commit": lock.get("vellum_authority_record_commit"),
        "pulp_activation_commit": lock.get("pulp_activation_commit"),
    }
    if lock.get("state") != "active":
        errors.append("active extraction authority differs from observatory lock")
    for field, expected in coordinates.items():
        actual = authority.get(field)
        if (
            not isinstance(actual, str)
            or not SHA_RE.fullmatch(actual)
            or actual != expected
        ):
            errors.append(f"active extraction authority has invalid {field}")
    for field in (
        "authority_record_path",
        "authority_ref",
        "pulp_authority_event_path",
        "pulp_authority_event_id",
        "accepted_by",
    ):
        if not isinstance(authority.get(field), str) or not authority[field]:
            errors.append(f"active extraction authority lacks {field}")
    accepted_at = authority.get("accepted_at")
    if not isinstance(accepted_at, str) or not UTC_RE.fullmatch(accepted_at):
        errors.append("active extraction authority has invalid accepted_at")
    return not errors, errors


def framework_counts_content(relative: str) -> bool:
    """Whether the ratchet reads a framework/ path: the native scan's text, plus the units' Python generators."""
    return scans_active_content(relative) or Path(relative).suffix.lower() == ".py"


def framework_name_debt(root: Path) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Hits of the ratcheted native name rules in framework/, per rule and file (external/ excluded).

    Returns (counts, undecodable): a file the ratchet should read but cannot
    decode is reported rather than skipped, so it cannot leave the count.
    """
    counts: dict[str, dict[str, int]] = {name: {} for name in FRAMEWORK_RATCHETED_RULES}
    undecodable: list[str] = []
    for path in active_files(root, (FRAMEWORK_ROOT.rstrip("/"),)):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(FRAMEWORK_ROOT + "external/") or not framework_counts_content(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            undecodable.append(relative)
            continue
        for name in FRAMEWORK_RATCHETED_RULES:
            hits = len(_ALL_NATIVE_PATTERNS[name].findall(text))
            if hits:
                counts[name][relative] = hits
    return counts, undecodable


def framework_debt_baseline_at_base(root: Path) -> tuple[dict | None, list[str]]:
    """The committed name-debt baseline at the history base, or None where there is none to compare."""
    base, error = load_mirror_export_verifier().history_base(root)
    if error:
        return None, [f"{FRAMEWORK_NAME_DEBT}: {error}"]
    if base is None:
        return None, []
    shown = subprocess.run(["git", "-C", str(root), "show", f"{base}:{FRAMEWORK_NAME_DEBT}"],
                           capture_output=True, check=False)
    if shown.returncode != 0:
        return None, []  # the change that introduces the baseline
    try:
        return json.loads(shown.stdout)["rules"], []
    except (ValueError, KeyError) as exc:
        return None, [f"{FRAMEWORK_NAME_DEBT}: unreadable at {base}: {exc}"]


def framework_debt_errors(root: Path) -> tuple[list[str], dict[str, int]]:
    """The ratchet: no (rule, file) count may rise above the committed baseline,
    and the committed baseline may not rise above its value at the history base."""
    current, undecodable = framework_name_debt(root)
    totals = {rule: sum(files.values()) for rule, files in current.items()}
    errors = [f"framework name debt: {path} is not UTF-8, so the ratchet cannot count it" for path in undecodable]
    try:
        baseline = json.loads((root / FRAMEWORK_NAME_DEBT).read_text(encoding="utf-8"))["rules"]
    except (OSError, ValueError, KeyError) as exc:
        return errors + [f"{FRAMEWORK_NAME_DEBT}: unreadable: {exc}"], totals
    for rule, files in current.items():
        known = baseline.get(rule, {})
        for path, n in sorted(files.items()):
            if n > known.get(path, 0):
                errors.append(f"framework name debt rose: {rule} in {path} is {n}, baseline {known.get(path, 0)}")
    base_rules, base_errors = framework_debt_baseline_at_base(root)
    errors.extend(base_errors)
    if base_rules is not None:
        for rule, files in sorted(baseline.items()):
            before = base_rules.get(rule, {})
            for path, n in sorted(files.items()):
                if not isinstance(n, int) or n > before.get(path, 0):
                    errors.append(f"{FRAMEWORK_NAME_DEBT} raised {rule} in {path} to {n}, "
                                  f"above {before.get(path, 0)} at the merge base; the baseline may only fall")
    return errors, totals


# Spellings that must be gone from framework/ entirely: the rename made them
# vellum. Case-sensitive and boundary-free over all bytes.
FRAMEWORK_RESIDUAL_PULP = re.compile(rb"pulp::|namespace\s+pulp(?![A-Za-z0-9_])|[<\"]pulp/|include/pulp/")


def verify_framework(root: Path, pulp_root: Path | None, pulp_main_ref: str,
                     defer_identity: bool = False) -> tuple[list[str], dict[str, object]]:
    """framework/'s content regime and its transfer proof (Decision 0010 option a)."""
    errors: list[str] = []
    checks: dict[str, object] = {}
    framework = root / FRAMEWORK_ROOT.rstrip("/")
    if not framework.is_dir():
        checks["framework"] = "absent"
        return errors, checks
    if (root / MIRROR_ROOT.rstrip("/")).exists():
        errors.append("mirror/ and framework/ both exist: the transfer moves the renamed units out of mirror/")
    # 1. Edges: the mirror-edge table's rules, exactly as mirror/ had them.
    edges = mirror_edge_findings(root, prefix=FRAMEWORK_ROOT)
    checks["framework_edge_findings"] = edges
    if edges:
        errors.append(f"framework/ has {len(edges)} audio, plug-in, host, or product edges")
    # 2. No old spelling left anywhere in it.
    residual = []
    for path in active_files(root, (FRAMEWORK_ROOT.rstrip("/"),)):
        if FRAMEWORK_RESIDUAL_PULP.search(path.read_bytes()):
            residual.append(path.relative_to(root).as_posix())
    checks["framework_residual_pulp_spellings"] = residual
    if residual:
        errors.append(f"framework/ still spells pulp::, namespace pulp or <pulp/ in {len(residual)} files")
    # 3. The ratchet on the native name rules.
    debt_errors, totals = framework_debt_errors(root)
    checks["framework_name_debt"] = totals
    errors.extend(debt_errors)
    # 4. The transfer proof: the recorded framework tree is a pure rename of
    # authoritative Pulp at the export receipt's commit.
    try:
        transfer = json.loads((root / FRAMEWORK_TRANSFER_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: unreadable: {exc}")
        return errors, checks
    if transfer.get("schema") != FRAMEWORK_TRANSFER_SCHEMA:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: schema must be {FRAMEWORK_TRANSFER_SCHEMA}")
        return errors, checks
    transfer_bytes = (root / FRAMEWORK_TRANSFER_RECORD).read_bytes()
    if hashlib.sha256(transfer_bytes).hexdigest() != FRAMEWORK_TRANSFER_SHA256:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: bytes differ from the pinned transfer record; it is immutable")
    base, base_error = load_mirror_export_verifier().history_base(root)
    if base_error:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: {base_error}")
    elif base is not None:
        before = subprocess.run(["git", "-C", str(root), "show", f"{base}:{FRAMEWORK_TRANSFER_RECORD}"],
                                capture_output=True, check=False)
        if before.returncode == 0 and before.stdout != transfer_bytes:
            errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: changed from the merge base; it is immutable")
    # The file set is the transfer record's: a native edit may change a file's
    # bytes but not add or remove one, because the build compiles the export's
    # closures (mirror_package.verify_framework_identity holds the same set).
    # framework/external/, which the ratchet skips, is reported on its own.
    recorded = {r.get("framework_path") for r in transfer.get("records", [])}
    present = {p.relative_to(root).as_posix() for p in framework.rglob("*") if p.is_file() or p.is_symlink()}
    unrecorded, missing = sorted(present - recorded), sorted(recorded - present)
    checks["framework_external_unrecorded"] = [p for p in unrecorded if p.startswith(FRAMEWORK_ROOT + "external/")]
    checks["framework_unrecorded_files"] = unrecorded
    checks["framework_missing_files"] = missing
    if checks["framework_external_unrecorded"]:
        errors.append(f"framework/external/ holds {len(checks['framework_external_unrecorded'])} files "
                      "the transfer record does not list")
    if unrecorded:
        errors.append(f"framework/ holds {len(unrecorded)} files the transfer record does not list: {unrecorded[:5]}")
    if missing:
        errors.append(f"framework/ is missing {len(missing)} files the transfer record lists: {missing[:5]}")
    # The runtime collision census, permanent: framework/ now shares the
    # vellum namespace with native code, so a native declaration that collides
    # with a framework/ name fails here. A census that sees no declarations on
    # either side is itself an error (collision_errors), its built-in control.
    rename_tool = load_rename_tool()
    census = tracked_collision_census(rename_tool, root, framework)
    checks["framework_collision_census"] = {
        key: (len(value) if isinstance(value, (list, dict)) and key != "declarations" else value)
        for key, value in census.items() if key != "native_root"}
    if census.get("status") == "error":
        errors.append(f"framework collision census: {census['reason']}")
    errors.extend(f"framework collision census: {e}" for e in rename_tool.collision_errors(census))
    identity = load_rename_identity_verifier()
    tree = transfer.get("framework_tree", "")
    if not SHA_RE.fullmatch(str(tree)) or subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{tree}^{{tree}}"], capture_output=True).returncode:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: framework_tree {tree!r} is not a tree in this repository")
        return errors, checks
    try:
        receipt = json.loads((root / transfer["export_receipt"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as exc:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: export receipt unreadable: {exc}")
        return errors, checks
    if receipt.get("pulp_sha") != transfer.get("pulp_sha") or receipt.get("mirror_tree") != transfer.get("mirror_tree"):
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: pulp_sha or mirror_tree differs from {transfer['export_receipt']}")
    receipt_files = {row["pulp_path"]: row["blob"] for row in receipt.get("files", [])}
    records = transfer.get("records", [])
    if {r.get("pulp_path"): r.get("pulp_git_blob_sha") for r in records} != receipt_files:
        errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: records differ from the export receipt's file list")
    checks["framework_transfer_records"] = len(records)
    if pulp_root is None:
        # Fail closed, as the mirror's origin proof does: only a lane that is
        # not the required provenance lane may defer the proof to it.
        checks["framework_transfer_proved"] = "deferred: no Pulp checkout"
        if not defer_identity:
            errors.append(f"{FRAMEWORK_TRANSFER_RECORD}: no Pulp checkout proved the transfer; pass --pulp-root")
        return errors, checks
    raw, source_errors = identity.raw_from_records(pulp_root, records, transfer.get("pulp_sha", ""), pulp_main_ref)
    proof = source_errors + identity.verify(raw, identity.git_tree(root, tree), transfer.get("rename", {}))
    checks["framework_transfer_proved"] = not proof
    errors.extend(f"framework transfer: {e}" for e in proof)
    return errors, checks


def tracked_collision_census(rename_tool, root: Path, framework: Path) -> dict:
    """The rename tool's collision census, over the files this repository tracks.

    The census walks its trees from the filesystem. A checkout can hold
    untracked trees the commit does not: the required provenance lane checks
    Pulp out at .mirror-pulp inside the Vellum checkout, and every Pulp file
    there would read as a native second copy. Only tracked files are Vellum
    source, so both walks are confined to them. The tool itself is pinned by
    the transfer record, so it is wrapped here rather than changed.
    """
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    if listed.returncode != 0:
        return {"status": "error", "reason": "cannot list tracked files"}
    top = root.resolve()
    tracked = {name for name in listed.stdout.decode("utf-8", errors="surrogateescape").split("\0") if name}

    def keep(paths):
        kept = []
        for path in paths:
            try:
                relative = path.resolve().relative_to(top).as_posix()
            except ValueError:
                continue
            if relative in tracked:
                kept.append(path)
        return kept

    collisions = rename_tool.collisions
    walk, iter_sources = rename_tool.walk, collisions.iter_sources
    rename_tool.walk = lambda tree: keep(walk(tree))
    collisions.iter_sources = lambda tree, skip_top=(): keep(iter_sources(tree, skip_top))
    try:
        return rename_tool.collision_census(rename_tool.load_spelling(MIRROR_EDGE_TABLE, "vellum"), framework, root, {})
    finally:
        rename_tool.walk, collisions.iter_sources = walk, iter_sources


def load_rename_tool():
    path = Path(__file__).resolve().parents[1] / "mirror/rename_mirror_namespace.py"
    if "rename_mirror_namespace" in sys.modules:
        return sys.modules["rename_mirror_namespace"]
    spec = importlib.util.spec_from_file_location("rename_mirror_namespace", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules.
    sys.modules["rename_mirror_namespace"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules["rename_mirror_namespace"]
        raise
    return module


def load_rename_identity_verifier():
    path = Path(__file__).resolve().parents[1] / "mirror/verify_mirror_rename_identity.py"
    spec = importlib.util.spec_from_file_location("verify_mirror_rename_identity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(
    root: Path,
    pulp_root: Path | None = None,
    defer_mirror_identity: bool = False,
    mirror_edge_pulp_root: Path | None = None,
    pulp_main_ref: str = "origin/main",
) -> dict[str, object]:
    provenance = root / "provenance"
    errors: list[str] = []
    checks: dict[str, object] = {}

    pin_errors = mirror_edge_table_pin_errors(mirror_edge_pulp_root)
    checks["mirror_edge_table_matches_pin"] = not pin_errors
    checks["mirror_edge_table_proved_against_pulp"] = mirror_edge_pulp_root is not None and not pin_errors
    errors.extend(pin_errors)

    for name, expected in EXPECTED_HASHES.items():
        actual = sha256(provenance / name)
        checks[f"sha256:{name}"] = actual
        if actual != expected:
            errors.append(f"{name}: expected sha256 {expected}, got {actual}")

    extraction = json.loads((provenance / "pulp-extraction.json").read_text())
    lock = json.loads(
        (provenance / "pulp-observatory/provenance.lock").read_text()
    )
    authority_transferred, authority_errors = verify_authority_phase(
        extraction, lock
    )
    errors.extend(authority_errors)
    if extraction["source"]["commit"] != EXPECTED_SOURCE:
        errors.append("pulp-extraction.json source commit drifted")
    declared_hashes = {
        "cut-manifest.json": extraction["cut"]["manifest_sha256"],
        "cut-paths.txt": extraction["cut"]["path_specification_sha256"],
        "filter-repo-commit-map.txt": extraction["history_extraction"]["commit_map_sha256"],
        "filter-repo-ref-map.txt": extraction["history_extraction"]["ref_map_sha256"],
    }
    if declared_hashes != EXPECTED_HASHES:
        errors.append("pulp-extraction.json immutable digest declarations drifted")

    manifest = json.loads((provenance / "cut-manifest.json").read_text())
    entries = manifest.get("entries", [])
    unresolved = [entry for entry in entries if entry["classification"] == "unresolved"]
    checks["historical_manifest_entry_count"] = len(entries)
    checks["historical_unresolved_entry_count"] = len(unresolved)
    if len(entries) != extraction["cut"]["entry_count"] or len(entries) != 235:
        errors.append(f"cut manifest has {len(entries)} entries, expected 235")
    if len(unresolved) != extraction["cut"]["unresolved_entry_count"] or len(unresolved) != 39:
        errors.append(f"cut manifest has {len(unresolved)} unresolved entries, expected 39")
    if len({entry["source_path"] for entry in entries}) != len(entries):
        errors.append("cut manifest contains duplicate source paths")

    blobs = seed_blobs(root)
    blob_mismatches = []
    for entry in entries:
        historical_path = entry["source_path"]
        actual = blobs.get(historical_path)
        if actual != entry["git_blob_sha"]:
            blob_mismatches.append(
                {"path": historical_path, "expected": entry["git_blob_sha"], "actual": actual}
            )
    checks["historical_seed_blob_mismatches"] = blob_mismatches
    if blob_mismatches:
        errors.append(f"{len(blob_mismatches)} filtered-seed blobs differ from the cut manifest")

    source_row = None
    for line in (provenance / "filter-repo-commit-map.txt").read_text().splitlines():
        if line.startswith(EXPECTED_SOURCE + " "):
            source_row = line.split()[1]
            break
    checks["source_commit_map_row"] = source_row
    if source_row != "0" * 40:
        errors.append("expected source tip to map to zero because it touched no selected path")
    ref_map = (provenance / "filter-repo-ref-map.txt").read_text()
    if f"{EXPECTED_SOURCE} {FILTERED_SEED} refs/heads/main" not in ref_map:
        errors.append("filtered main ref mapping is missing or changed")

    boundary = configured_boundary(root)
    active_roots = tuple(boundary.get("active_source_roots", []))
    retired_prefixes = tuple(boundary.get("retired_prefixes", []))
    if active_roots != EXPECTED_ACTIVE_ROOTS:
        errors.append("active source roots differ from the required complete inventory")
    if retired_prefixes != EXPECTED_RETIRED_PREFIXES:
        errors.append("retired prefixes differ from the required extraction boundary")
    expected_mirror = {
        "root": MIRROR_ROOT,
        "origin_records": MIRROR_ORIGIN_RECORDS,
        "decision": MIRROR_DECISION,
    }
    if boundary.get("mirror") != expected_mirror:
        errors.append("mirror scope differs from the Decision 0010 boundary")
    expected_framework = {
        "root": FRAMEWORK_ROOT,
        "transfer_record": FRAMEWORK_TRANSFER_RECORD,
        "name_debt": FRAMEWORK_NAME_DEBT,
        "decision": MIRROR_DECISION,
    }
    if boundary.get("framework") != expected_framework:
        errors.append("framework scope differs from the Decision 0010 option (a) boundary")
    if boundary.get("rules") != EXPECTED_RULES:
        errors.append("declared boundary rules differ from the enforced rules")
    if not (root / MIRROR_DECISION).is_file():
        errors.append(f"{MIRROR_DECISION} is missing")

    inventory = sorted(path.relative_to(root).as_posix() for path in active_files(root, active_roots))
    checks["active_source_file_count"] = len(inventory)
    checks["active_source_files"] = inventory

    retired_findings = retired_path_findings(root, retired_prefixes)
    checks["retired_path_findings"] = retired_findings
    if retired_findings:
        errors.append(f"active tip still contains {len(retired_findings)} retired projection paths")

    records, record_errors = load_mirror_origin_records(root / MIRROR_ORIGIN_RECORDS)
    errors.extend(record_errors)
    checks["mirror_origin_record_count"] = len(records)

    # A Pulp-origin blob is legal only under the mirror root with a matching
    # record. In a native root it would be a second editable copy.
    pulp_origin_blobs = {
        entry["git_blob_sha"]
        for entry in entries
        if entry["source_path"] not in {"DEPENDENCIES.md", "LICENSE.md", "NOTICE.md"}
    } | {record["git_blob_sha"] for record in records}
    pulp_origin_blobs.discard(EMPTY_BLOB_SHA)
    copied_findings = copied_seed_findings(root, active_roots, pulp_origin_blobs)
    checks["active_historical_blob_matches"] = copied_findings
    if copied_findings:
        errors.append(
            f"native source contains {len(copied_findings)} unrecorded Pulp-origin blob copies"
        )

    mirror_inventory = sorted(path.relative_to(root).as_posix() for path in mirror_files(root))
    checks["mirror_file_count"] = len(mirror_inventory)
    origin_findings = mirror_origin_findings(root, records)
    checks["mirror_origin_findings"] = origin_findings
    if origin_findings:
        errors.append(f"mirror root has {len(origin_findings)} files without a matching origin record")
    # Fail closed: a self-consistent record proves nothing about origin, so a
    # mirrored file passes only where a Pulp checkout proved it. Lanes that are
    # not the required provenance lane may defer that proof to it.
    if pulp_root is not None:
        drift = mirror_edge_table_drift(pulp_root)
        checks["mirror_edge_table_matches_pulp"] = not drift
        errors.extend(drift)
        pulp_errors = verify_origins_against_pulp(pulp_root, records)
        checks["mirror_origins_verified_against_pulp"] = not pulp_errors
        errors.extend(pulp_errors)
    elif mirror_inventory and defer_mirror_identity:
        checks["mirror_identity_deferred"] = True
    elif mirror_inventory:
        errors.append(
            f"mirror root has {len(mirror_inventory)} files but no Pulp checkout proved "
            "them; pass --pulp-root"
        )
    export_verifier = load_mirror_export_verifier()
    landed_receipts = set(export_verifier.landed_receipts(root))
    unlanded = sorted(
        {record["export_receipt"] for record in records} - landed_receipts
    )
    checks["mirror_origin_records_naming_unlanded_receipts"] = unlanded
    if unlanded:
        errors.append(
            f"blob-origin records name {len(unlanded)} receipts absent from the receipt index"
        )
    export_report = export_verifier.verify(root, pulp_root)
    checks["mirror_export_receipts"] = export_report["receipts"]
    if pulp_root is not None:
        pin_errors, pin_rows = mirror_renderer_pin_check(
            root, pulp_root, export_verifier.landed_receipts(root))
        checks["mirror_renderer_pin_matches_lock"] = pin_rows
        errors.extend(pin_errors)
    else:
        checks["mirror_renderer_pin_matches_lock"] = "deferred: no Pulp checkout"
    checks["mirror_export_pulp_identity_checked"] = export_report["pulp_identity_checked"]
    errors.extend(f"mirror export: {error}" for error in export_report["errors"])
    edge_findings = mirror_edge_findings(root)
    checks["mirror_edge_findings"] = edge_findings
    if edge_findings:
        errors.append(f"mirror root has {len(edge_findings)} audio, plug-in, host, or product edges")
    # The export commit's own table must pass too, so an advanced table
    # cannot admit an edge the exporting gate refused.
    export_table_path = pulp_root / PULP_MIRROR_EDGE_TABLE if pulp_root is not None else None
    if export_table_path is not None and export_table_path.is_file() \
            and export_table_path.read_bytes() != MIRROR_EDGE_TABLE.read_bytes():
        # After the transfer the export's units are framework/; the export
        # commit's table still applies to them.
        units_prefix = FRAMEWORK_ROOT if (root / FRAMEWORK_ROOT).is_dir() else MIRROR_ROOT
        export_findings = mirror_edge_findings(root, json.loads(export_table_path.read_text(encoding="utf-8")),
                                               prefix=units_prefix)
        checks["mirror_edge_findings_under_export_table"] = export_findings
        if export_findings:
            errors.append(
                f"mirror root has {len(export_findings)} edges under the export commit's mirror-edge table")

    active_findings = scan_active_surface(root, active_roots)
    checks["active_forbidden_findings"] = active_findings
    if active_findings:
        errors.append(f"native build/authoring surface has {len(active_findings)} forbidden references")

    symlinks = symlink_findings(root, RENAMED_MIRROR_SCAN_ROOTS + (FRAMEWORK_ROOT.rstrip("/"),))
    checks["native_tree_symlinks"] = symlinks
    if symlinks:
        errors.append(f"native trees hold {len(symlinks)} symlinks, which the native scans cannot see through")
    renamed_findings = scan_renamed_mirror(root)
    checks["renamed_mirror_findings"] = renamed_findings
    if renamed_findings:
        errors.append(f"native trees use the renamed mirror's namespaces or headers {len(renamed_findings)} times")
    normalized_copies = normalized_copy_findings(root)
    checks["normalized_mirror_copies"] = normalized_copies
    if normalized_copies:
        errors.append(f"native trees hold {len(normalized_copies)} copies of mirror files (normalized content)")
    framework_errors, framework_checks = verify_framework(root, pulp_root, pulp_main_ref, defer_mirror_identity)
    checks.update(framework_checks)
    errors.extend(framework_errors)

    debt = provenance / "extraction-debt/initial.yaml"
    debt_status = top_level_scalar(debt, "status")
    open_debt_count = top_level_scalar(debt, "open_debt_count")
    debt_text = debt.read_text(encoding="utf-8")
    checks["extraction_debt"] = {"status": debt_status, "open_debt_count": open_debt_count}
    if debt_status != "closed" or open_debt_count != "0" or "\ndebts: []\n" not in debt_text:
        errors.append("extraction debt must be closed with zero open rows after seed retirement")

    dependency_lock = json.loads((provenance / "third-party-lock.json").read_text())
    dependencies = dependency_lock.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("third-party dependency inventory is malformed")
        dependencies = []
    for dependency in dependencies:
        if "path" in dependency and "sha256" in dependency:
            dependency_path = root / dependency["path"]
            if not dependency_path.is_file() or sha256(dependency_path) != dependency["sha256"]:
                errors.append(f"third-party checksum mismatch: {dependency['path']}")
    checks["active_third_party_dependency_count"] = len(dependencies)

    return {
        "schema_version": 3,
        "status": "pass" if not errors else "fail",
        "authority_transferred": authority_transferred,
        "raw_seed_present_at_active_tip": bool(retired_findings or copied_findings),
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--pulp-root",
        type=Path,
        help="Pulp checkout used to prove every mirror origin record",
    )
    parser.add_argument(
        "--mirror-edge-pulp-root",
        type=Path,
        help="Pulp checkout of the commit mirror_edge_patterns.pin.json names",
    )
    parser.add_argument(
        "--defer-mirror-identity",
        action="store_true",
        help="leave the Pulp identity proof of mirror/ to the required provenance lane",
    )
    parser.add_argument(
        "--pulp-main-ref",
        default="origin/main",
        help="the ref in --pulp-root that the framework transfer's Pulp commit must descend into",
    )
    args = parser.parse_args()
    report = verify(
        args.root.resolve(),
        args.pulp_root.resolve() if args.pulp_root else None,
        defer_mirror_identity=args.defer_mirror_identity,
        mirror_edge_pulp_root=args.mirror_edge_pulp_root.resolve() if args.mirror_edge_pulp_root else None,
        pulp_main_ref=args.pulp_main_ref,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
