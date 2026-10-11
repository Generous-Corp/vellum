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
    "cli",
    "cmake",
    "foundation",
    "graphics",
    "packages/vellum-design-ir",
    "packages/vellum-ui",
    "runtime",
    "scripts",
    "templates",
)
MIRROR_ROOT = "mirror/"
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
TEXT_SUFFIXES = {
    "",
    ".c",
    ".cc",
    ".cmake",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".js",
    ".json",
    ".m",
    ".md",
    ".mm",
    ".ps1",
    ".sh",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
FORBIDDEN_ACTIVE_PATTERNS = {
    "pulp-public-namespace": re.compile(
        r"(?:\bnamespace\s+pulp\b|\bpulp::|#\s*include\s*[<\"]pulp/)",
        re.IGNORECASE,
    ),
    "pulp-package-or-target": re.compile(
        r"(?:@pulp/|\bPULP_[A-Z0-9_]+|\bpulp[-_]"
        r"(?:audio|canvas|format|gpu|graph|host|midi|plugin|render|runtime|signal|view)\b)",
        re.IGNORECASE,
    ),
    "audio-plugin-sdk": re.compile(
        r"\b(?:AudioUnit|VST3|CLAP|LV2|Oboe)\b",
        re.IGNORECASE,
    ),
}
# Pulp subsystems that stay Pulp-owned under Decision 0010: audio, DSP, MIDI,
# music theory, plug-in formats, plug-in hosting, GPU audio, and product
# content. A mirrored framework file may not reach any of them.
_PULP_PRODUCT_SUBSYSTEMS = r"(?:audio|signal|midi|music|format|host|gpu_audio|content)"
FORBIDDEN_MIRROR_EDGE_PATTERNS = {
    "mirror-product-include": re.compile(
        r"#\s*(?:include|import)\s*[<\"]"
        r"(?:pulp/" + _PULP_PRODUCT_SUBSYSTEMS + r"/"
        r"|pluginterfaces/|public\.sdk/|clap/|lv2/|AudioUnitSDK/|oboe/"
        r"|AudioToolbox/|AudioUnit/|CoreAudio/|CoreAudioKit/|CoreMIDI/|AVFAudio/)",
    ),
    "mirror-product-symbol": re.compile(
        r"\bpulp::" + _PULP_PRODUCT_SUBSYSTEMS + r"\b"
        r"|\bpulp[-_](?:audio|signal|midi|music|format|host|gpu[-_]audio|content)\b",
    ),
    "mirror-audio-framework-link": re.compile(
        r"\b(?:AudioToolbox|AudioUnit|CoreAudio|CoreAudioKit|CoreMIDI|AVFAudio)"
        r"\.framework\b|-framework\s+"
        r"(?:AudioToolbox|AudioUnit|CoreAudio|CoreAudioKit|CoreMIDI|AVFAudio)\b",
    ),
}
# Pulp-kept files inside otherwise generic Pulp roots (Decision 0010 / D3):
# the view-audio widgets, the graph editor view, and the product content
# registry. Mirrored code may neither include them nor be one of them.
_PULP_KEPT_VIEW_FILES = (
    r"(?:audio_bridge|audio_inspector_\w+|eq_curve_view|midi_binding|midi_keyboard"
    r"|musical_typing_keyboard\w*|plugin_manager_panel|sample_asset_drop_target"
    r"|step_grid_view|waveform_\w+|graph_editor_view)"
)
FORBIDDEN_MIRROR_EDGE_PATTERNS["mirror-pulp-kept-include"] = re.compile(
    r"#\s*(?:include|import)\s*[<\"]"
    r"(?:pulp/view/(?:audio/|(?:widgets/)?" + _PULP_KEPT_VIEW_FILES + r"\.h)"
    r"|pulp/state/content_registry\.h)",
)
PULP_KEPT_MIRROR_PATH = re.compile(
    r"\Amirror/core/(?:view/(?:audio/|(?:.*/)?" + _PULP_KEPT_VIEW_FILES + r"\.)"
    r"|host/|state/(?:.*/)?content_registry\.)"
)
MIRROR_EDGE_SUFFIXES = TEXT_SUFFIXES | {".inl", ".ipp", ".txt"}
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
        if path.relative_to(root).as_posix() in POLICY_IMPLEMENTATION_PATHS:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
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


def mirror_edge_findings(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in mirror_files(root):
        relative = path.relative_to(root).as_posix()
        if PULP_KEPT_MIRROR_PATH.match(relative):
            findings.append(
                {"rule": "mirror-pulp-kept-file", "path": relative, "line": 0, "match": relative}
            )
        if path.suffix.lower() not in MIRROR_EDGE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in FORBIDDEN_MIRROR_EDGE_PATTERNS.items():
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


def verify_origins_against_pulp(
    pulp_root: Path, records: Iterable[dict[str, str]]
) -> list[str]:
    """Prove each record against the Pulp history it cites (identity oracle)."""
    errors: list[str] = []
    try:
        main = git(pulp_root, "rev-parse", "--verify", "--quiet", "HEAD")
    except subprocess.CalledProcessError:
        return [f"Pulp checkout {pulp_root} has no HEAD"]
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


def verify(
    root: Path, pulp_root: Path | None = None, defer_mirror_identity: bool = False
) -> dict[str, object]:
    provenance = root / "provenance"
    errors: list[str] = []
    checks: dict[str, object] = {}

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
    checks["mirror_export_pulp_identity_checked"] = export_report["pulp_identity_checked"]
    errors.extend(f"mirror export: {error}" for error in export_report["errors"])
    edge_findings = mirror_edge_findings(root)
    checks["mirror_edge_findings"] = edge_findings
    if edge_findings:
        errors.append(f"mirror root has {len(edge_findings)} audio, plug-in, host, or product edges")

    active_findings = scan_active_surface(root, active_roots)
    checks["active_forbidden_findings"] = active_findings
    if active_findings:
        errors.append(f"native build/authoring surface has {len(active_findings)} forbidden references")

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
        "--defer-mirror-identity",
        action="store_true",
        help="leave the Pulp identity proof of mirror/ to the required provenance lane",
    )
    args = parser.parse_args()
    report = verify(
        args.root.resolve(),
        args.pulp_root.resolve() if args.pulp_root else None,
        defer_mirror_identity=args.defer_mirror_identity,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
