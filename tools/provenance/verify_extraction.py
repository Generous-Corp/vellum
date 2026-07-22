#!/usr/bin/env python3
"""Verify Vellum's prepared extraction and active build quarantine.

This intentionally uses only the Python standard library so it can run before
the SDK/bootstrap exists. It proves provenance and containment; it does not
turn a prepared ownership record into an active one.
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
ACTIVE_ROOTS = (
    "CMakeLists.txt",
    "cmake",
    "foundation",
    "runtime",
    "modules",
    "authoring",
    "platforms",
    "testkit",
    "apps",
    "examples",
    "cli",
    "templates",
    "scripts",
    "packages/vellum-design-ir",
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
    "pulp-private-repository-reference": re.compile(
        r"(?:github\.com[:/]Generous-Corp/pulp(?:\.git)?|\.\./pulp(?:/|$))",
        re.IGNORECASE,
    ),
    "audio-plugin-include": re.compile(
        r"(?:#\s*include\s*[<\"]pulp/(?:audio|format|gpu_audio|graph|host|midi|playback|signal)/|"
        r"\bpulp::(?:audio|format|gpu_audio|graph|host|midi|playback|signal)::)"
    ),
    "audio-plugin-target": re.compile(
        r"\bpulp[-_:]{1,2}(?:audio|format|gpu_audio|graph|host|midi|playback|signal)\b",
        re.IGNORECASE,
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def debt_paths(path: Path) -> set[str]:
    in_paths = False
    paths: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "    paths:":
            in_paths = True
            continue
        if in_paths and line.startswith("      - "):
            paths.add(line.removeprefix("      - ").strip())
        elif in_paths and line and not line.startswith("      "):
            in_paths = False
    return paths


def active_text_files(root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for relative in ACTIVE_ROOTS:
        candidate = root / relative
        if candidate.is_file():
            files = [candidate]
        elif candidate.is_dir():
            files = [path for path in candidate.rglob("*") if path.is_file()]
        else:
            continue
        for path in files:
            if path in seen or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(part in {"node_modules", "build", ".git"} for part in path.parts):
                continue
            seen.add(path)
            yield path


def scan_active_surface(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in active_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in FORBIDDEN_ACTIVE_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    {
                        "rule": name,
                        "path": str(path.relative_to(root)),
                        "line": line,
                        "match": match.group(0)[:120],
                    }
                )
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
    if extraction["authority"]["state"] != "prepared":
        errors.append("authority changed from prepared without active verification support")

    manifest = json.loads((provenance / "cut-manifest.json").read_text())
    entries = manifest.get("entries", [])
    checks["manifest_entry_count"] = len(entries)
    if len(entries) != 235:
        errors.append(f"cut manifest has {len(entries)} entries, expected 235")
    if len({entry["source_path"] for entry in entries}) != len(entries):
        errors.append("cut manifest contains duplicate source paths")

    unresolved = {
        entry["source_path"]
        for entry in entries
        if entry["classification"] == "unresolved"
    }
    declared_debt = debt_paths(provenance / "extraction-debt/initial.yaml")
    checks["unresolved_entry_count"] = len(unresolved)
    checks["declared_debt_path_count"] = len(declared_debt)
    if unresolved != declared_debt:
        errors.append(
            "unresolved manifest paths and extraction debt differ: "
            f"missing={sorted(unresolved - declared_debt)}, "
            f"extra={sorted(declared_debt - unresolved)}"
        )

    blobs = seed_blobs(root)
    blob_mismatches = []
    for entry in entries:
        path = entry["source_path"]
        actual = blobs.get(path)
        if actual != entry["git_blob_sha"]:
            blob_mismatches.append(
                {"path": path, "expected": entry["git_blob_sha"], "actual": actual}
            )
    checks["seed_blob_mismatches"] = blob_mismatches
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
    expected_ref_row = f"{EXPECTED_SOURCE} {FILTERED_SEED} refs/heads/main"
    if expected_ref_row not in ref_map:
        errors.append("filtered main ref mapping is missing or changed")

    active_findings = scan_active_surface(root)
    checks["active_forbidden_findings"] = active_findings
    if active_findings:
        errors.append(f"active build/authoring surface has {len(active_findings)} forbidden references")

    root_cmake = root / "CMakeLists.txt"
    if root_cmake.exists():
        cmake_text = root_cmake.read_text(encoding="utf-8")
        if re.search(r"add_subdirectory\s*\(\s*core(?:/|\s*\))", cmake_text):
            errors.append("root CMake activates quarantined raw core/ sources")

    for dependency in json.loads((provenance / "third-party-lock.json").read_text())[
        "dependencies"
    ]:
        if "path" in dependency and "sha256" in dependency:
            actual = sha256(root / dependency["path"])
            if actual != dependency["sha256"]:
                errors.append(f"third-party checksum mismatch: {dependency['path']}")

    return {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "prepared_authority_only": True,
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
