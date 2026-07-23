#!/usr/bin/env python3
"""Validate a checksummed SDK through an installed, sterile CMake consumer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


REPO = Path(__file__).resolve().parents[1]


class ValidationError(RuntimeError):
    pass


def run(arguments: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise ValidationError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed


def validate(archive: Path, checksums: Path, forbid_path: Path | None) -> dict[str, object]:
    verification = json.loads(
        run([
            sys.executable, str(REPO / "scripts/verify_sdk_artifact.py"),
            "--archive", str(archive), "--checksums", str(checksums), "--json",
        ]).stdout
    )
    with tempfile.TemporaryDirectory(prefix="vellum-sterile-consumer-") as temporary_text:
        root = Path(temporary_text)
        prefix = root / "prefix"
        run([
            "sh", str(REPO / "scripts/install.sh"),
            "--archive", str(archive), "--checksums", str(checksums),
            "--install-dir", str(prefix),
        ], cwd=root)

        consumer_source = root / "consumer-source"
        shutil.copytree(REPO / "apps/smoke-native/install-consumer", consumer_source)
        consumer_build = root / "consumer-build"
        sdk_prefix = prefix / "lib/vellum/sdk"
        run([
            "cmake", "-S", str(consumer_source), "-B", str(consumer_build),
            f"-DCMAKE_PREFIX_PATH={sdk_prefix}",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
            "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
            "-DCMAKE_BUILD_TYPE=Release",
        ], cwd=root)
        run(["cmake", "--build", str(consumer_build), "--parallel"], cwd=root)
        run(["ctest", "--test-dir", str(consumer_build), "--output-on-failure"], cwd=root)

        package_dir = sdk_prefix / "lib/cmake/Vellum"
        package_files = sorted(package_dir.glob("*.cmake"))
        if not package_files:
            raise ValidationError("installed SDK has no CMake package files")
        package_text = "\n".join(path.read_text(encoding="utf-8") for path in package_files)
        if forbid_path and str(forbid_path.resolve()) in package_text:
            raise ValidationError("installed CMake package refers to the forbidden source checkout")

        project = root / "application"
        created = json.loads(run([
            str(prefix / "bin/vellum"), "create", "Sterile Artifact App",
            "--directory", str(project), "--json",
        ], cwd=root).stdout)
        doctor = json.loads(run([
            str(prefix / "bin/vellum"), "doctor", "--json",
        ], cwd=project).stdout)
        imported = json.loads(run([
            str(prefix / "bin/vellum"), "import",
            str(REPO / "fixtures/design-ir/revision-a.source.json"),
            "--source-type", "figma", "--as", "main", "--json",
        ], cwd=project).stdout)
        reimported = json.loads(run([
            str(prefix / "bin/vellum"), "reimport",
            "--source", str(REPO / "fixtures/design-ir/revision-b.source.json"),
            "--as", "main", "--json",
        ], cwd=project).stdout)
        active_revision = json.loads(
            (project / "design/import.lock.json").read_text(encoding="utf-8")
        )["sources"]["main"]["activeRevision"]
        lock = json.loads((project / "vellum.lock.json").read_text(encoding="utf-8"))
        if lock["framework"]["version"] != verification["framework_version"]:
            raise ValidationError("created project lock does not match the installed SDK artifact")
        if created.get("status") != "created" or doctor.get("status") != "ready":
            raise ValidationError("installed CLI create/doctor journey did not become ready")
        if imported.get("status") != "imported" or reimported.get("status") != "reimported":
            raise ValidationError("installed CLI import/reimport journey did not complete")
        if active_revision != "palette-board-b":
            raise ValidationError("installed CLI reimport did not advance the active revision")

    return {
        "schema": "vellum.installed-sdk-validation.v1",
        "ok": True,
        "artifact": verification["artifact"],
        "artifact_sha256": verification["sha256"],
        "framework_version": verification["framework_version"],
        "source_commit": verification["source_commit"],
        "source_tree_clean": verification["source_tree_clean"],
        "target": verification["target"],
        "claims": verification["claims"],
        "checks": {
            "checksum_and_payload_manifest": True,
            "clean_prefix_install": True,
            "relocatable_cmake_package": True,
            "sterile_consumer_configure": True,
            "sterile_consumer_build": True,
            "sterile_consumer_test": True,
            "project_created_by_installed_cli": created.get("status") == "created",
            "project_lock_matches_sdk": True,
            "installed_cli_doctor": doctor.get("status") == "ready",
            "installed_cli_import": imported.get("status") == "imported",
            "installed_cli_reimport": reimported.get("status") == "reimported",
            "active_reimport_revision": active_revision == "palette-board-b",
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--forbid-path", type=Path, default=REPO)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        evidence = validate(args.archive.resolve(), args.checksums.resolve(), args.forbid_path)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"vellum-installed-sdk-validation: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.json:
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Validated installed SDK artifact: {evidence['artifact']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
