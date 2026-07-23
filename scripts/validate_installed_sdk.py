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

from verify_sdk_artifact import payload_contamination_findings


REPO = Path(__file__).resolve().parents[1]


class ValidationError(RuntimeError):
    pass


def installed_contamination_findings(prefix: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in sorted(item for item in prefix.rglob("*") if item.is_file()):
        findings.extend(
            payload_contamination_findings(path.relative_to(prefix).as_posix(), path.read_bytes())
        )
    return findings


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

        install_manifest_path = prefix / "lib/vellum/install-manifest.json"
        try:
            install_manifest = json.loads(install_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(f"installed SDK has no valid install manifest: {error}") from error
        expected_manifest = {
            "schema": "vellum.sdk-install.v1",
            "verified": True,
            "artifact": verification["artifact"],
            "artifact_sha256": verification["sha256"],
            "framework_version": verification["framework_version"],
            "target": verification["target"],
            "source_commit": verification["source_commit"],
        }
        if install_manifest != expected_manifest:
            raise ValidationError("installed SDK identity does not match the verified archive")

        contamination = installed_contamination_findings(prefix)
        if contamination:
            first = contamination[0]
            raise ValidationError(
                f"installed SDK contamination: {first['rule']} in {first['path']}"
            )

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
        gpu_claimed = verification["claims"]["gpu_renderer"] is True
        native_claimed = any(
            verification["claims"]["commands"][command]
            for command in ("build", "run", "test", "capture", "package")
        )
        ui_present = (prefix / "lib/vellum/ui/package.json").is_file()
        native_present = (
            (prefix / "lib/vellum/vellum_native_backend.py").is_file() and
            (prefix / "lib/vellum/bin/vellum-native-backend").is_file()
        )
        if gpu_claimed and (
            "Vellum::Gpu" not in package_text or
            "Vellum::Authoring" not in package_text or
            not ui_present
        ):
            raise ValidationError("GPU artifact is missing its installed GPU/authoring/UI payload")
        if native_claimed and not native_present:
            raise ValidationError("native command claims have no installed native backend")

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
        expected_lock_identity = {
            "verified": True,
            "sha256": verification["sha256"],
            "target": verification["target"],
            "sourceCommit": verification["source_commit"],
        }
        if lock["framework"].get("artifact") != expected_lock_identity:
            raise ValidationError("created project lock does not pin the installed artifact SHA")
        if created.get("status") != "created" or doctor.get("status") != "ready":
            raise ValidationError("installed CLI create/doctor journey did not become ready")
        if imported.get("status") != "imported" or reimported.get("status") != "reimported":
            raise ValidationError("installed CLI import/reimport journey did not complete")
        if active_revision != "palette-board-b":
            raise ValidationError("installed CLI reimport did not advance the active revision")

        native_enabled = verification["claims"]["gpu_renderer"] is True
        native_results: dict[str, dict[str, object]] = {}
        native_capture = project / "artifacts/installed-proof.png"
        native_package = project / "dist/sterile-artifact-app.app"
        imported_bundle_contains_design = False
        native_capture_produced = False
        native_package_produced = False
        if native_enabled:
            for name, arguments in {
                "build": ["build"],
                "run": ["run", "--self-test", "--no-build"],
                "test": ["test", "--scenario", "smoke"],
                "capture": [
                    "capture", "--scenario", "smoke", "--output",
                    "artifacts/installed-proof.png",
                ],
                "package": ["package", "--output", "dist"],
            }.items():
                native_results[name] = json.loads(run([
                    str(prefix / "bin/vellum"), *arguments, "--json",
                ], cwd=project).stdout)
            if any(not value.get("ok") for value in native_results.values()):
                raise ValidationError("installed native CLI journey did not complete")
            native_bundle = project / ".vellum/build/macos/sterile-artifact-app.app/Contents/Resources/app.js"
            imported_bundle_contains_design = (
                native_bundle.is_file()
                and "Palette Board" in native_bundle.read_text(encoding="utf-8")
                and "main/app-root" in native_bundle.read_text(encoding="utf-8")
            )
            if not imported_bundle_contains_design:
                raise ValidationError("installed native app did not embed the imported DesignIR")
            if (not native_capture.is_file() or native_capture.read_bytes()[:4] != b"\x89PNG"):
                raise ValidationError("installed native capture did not produce a PNG")
            if not (native_package / "Contents/MacOS/sterile-artifact-app").is_file():
                raise ValidationError("installed native package did not produce a runnable .app")
            native_capture_produced = True
            native_package_produced = True

    checks = {
        "checksum_and_payload_manifest": True,
        "artifact_contamination_scan": verification["contamination_free"],
        "clean_prefix_install": True,
        "installed_artifact_identity": install_manifest == expected_manifest,
        "installed_tree_contamination_scan": not contamination,
        "relocatable_cmake_package": True,
        "gpu_authoring_ui_payload": not gpu_claimed or ui_present,
        "native_backend_payload": not native_claimed or native_present,
        "sterile_consumer_configure": True,
        "sterile_consumer_build": True,
        "sterile_consumer_test": True,
        "project_created_by_installed_cli": created.get("status") == "created",
        "project_lock_matches_sdk": True,
        "project_lock_pins_artifact_sha": lock["framework"].get("artifact") == expected_lock_identity,
        "installed_cli_doctor": doctor.get("status") == "ready",
        "installed_cli_import": imported.get("status") == "imported",
        "installed_cli_reimport": reimported.get("status") == "reimported",
        "active_reimport_revision": active_revision == "palette-board-b",
        "native_capability_claim_consistent": all(
            verification["claims"]["commands"][name] is native_enabled
            for name in ("build", "run", "test", "capture", "package")
        ),
        "installed_native_build": not native_enabled or native_results["build"]["status"] == "built",
        "installed_native_finite_run": not native_enabled or native_results["run"]["status"] == "self_test_passed",
        "installed_native_scenario": not native_enabled or native_results["test"]["status"] == "tests_passed",
        "installed_imported_design_bundle": not native_enabled or imported_bundle_contains_design,
        "installed_native_capture": not native_enabled or native_capture_produced,
        "installed_native_package": not native_enabled or native_package_produced,
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise ValidationError(f"installed SDK validation checks failed: {failed}")

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
        "checks": checks,
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
