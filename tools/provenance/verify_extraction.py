#!/usr/bin/env python3
"""Verify immutable extraction history and the retired active-source boundary.

The historical cut manifest and filter maps describe the original projection.
They are verified against the preserved seed commit, not against HEAD. HEAD is
checked independently to prove that the projection is no longer an editable
source copy and cannot leak into Vellum's build, SDK, or authoring surface.
"""

from __future__ import annotations

import argparse
import hashlib
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
POLICY_IMPLEMENTATION_PATHS = {
    "scripts/verify_sdk_artifact.py",
}


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


def verify(root: Path) -> dict[str, object]:
    provenance = root / "provenance"
    errors: list[str] = []
    checks: dict[str, object] = {}

    for name, expected in EXPECTED_HASHES.items():
        actual = sha256(provenance / name)
        checks[f"sha256:{name}"] = actual
        if actual != expected:
            errors.append(f"{name}: expected sha256 {expected}, got {actual}")

    extraction = json.loads((provenance / "pulp-extraction.json").read_text())
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

    inventory = sorted(path.relative_to(root).as_posix() for path in active_files(root, active_roots))
    checks["active_source_file_count"] = len(inventory)
    checks["active_source_files"] = inventory

    retired_findings = retired_path_findings(root, retired_prefixes)
    checks["retired_path_findings"] = retired_findings
    if retired_findings:
        errors.append(f"active tip still contains {len(retired_findings)} retired projection paths")

    historical_source_blobs = {
        entry["git_blob_sha"]
        for entry in entries
        if entry["source_path"] not in {"DEPENDENCIES.md", "LICENSE.md", "NOTICE.md"}
    }
    copied_findings = copied_seed_findings(root, active_roots, historical_source_blobs)
    checks["active_historical_blob_matches"] = copied_findings
    if copied_findings:
        errors.append(f"active source contains {len(copied_findings)} exact historical seed copies")

    active_findings = scan_active_surface(root, active_roots)
    checks["active_forbidden_findings"] = active_findings
    if active_findings:
        errors.append(f"active build/authoring surface has {len(active_findings)} forbidden references")

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
        "schema_version": 2,
        "status": "pass" if not errors else "fail",
        "authority_transferred": False,
        "raw_seed_present_at_active_tip": bool(retired_findings or copied_findings),
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.root.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
