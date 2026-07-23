#!/usr/bin/env python3
"""Validate a checksummed SDK through an installed, sterile CMake consumer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verify_sdk_artifact import (
    payload_contamination_findings,
    should_scan_payload_content,
)


REPO = SCRIPT_DIR.parents[0]
SUPPORT_ROOT = (
    SCRIPT_DIR / "sterile-support"
    if (SCRIPT_DIR / "sterile-support").is_dir()
    else REPO
)


class ValidationError(RuntimeError):
    pass


def installed_contamination_findings(prefix: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in sorted(item for item in prefix.rglob("*") if item.is_file()):
        relative = path.relative_to(prefix).as_posix()
        content = path.read_bytes() if should_scan_payload_content(relative) else b""
        findings.extend(
            payload_contamination_findings(relative, content)
        )
    return findings


def checkout_contamination_findings(search_roots: Iterable[Path]) -> list[str]:
    """Find source-checkout markers in the bounded roots supplied by CI.

    A sterile runner can still have an empty GitHub workspace directory. What
    it may not have is a Vellum checkout or cache that could satisfy an
    accidental relative/source dependency.
    """
    findings: list[str] = []
    markers = (
        Path(".git"),
        Path("provenance/pulp-extraction.json"),
        Path("scripts/build_sdk_artifact.py"),
    )
    for raw_root in search_roots:
        root = raw_root.expanduser().resolve()
        if not root.exists():
            continue
        candidates = [root]
        if root.is_dir():
            candidates.extend(
                path for path in root.iterdir()
                if path.is_dir() and not path.is_symlink()
            )
        for candidate in candidates:
            present = [
                marker.as_posix()
                for marker in markers
                if (candidate / marker).exists()
            ]
            if (
                ".git" in present
                and (
                    "provenance/pulp-extraction.json" in present
                    or "scripts/build_sdk_artifact.py" in present
                )
            ):
                findings.append(str(candidate))
    return sorted(set(findings))


def run(arguments: list[str], *, cwd: Path | None = None,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False,
                               env=env)
    if completed.returncode:
        raise ValidationError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed


def validate_installed_phase3(prefix: Path, root: Path,
                              env: dict[str, str]) -> bool:
    """Run the unchanged scenario using only installed SDK/runtime bytes."""
    fixture = root / "phase3-installed"
    shutil.copytree(
        SUPPORT_ROOT / "fixtures/authoring-phase3",
        fixture,
    )
    modules = fixture / "node_modules/@vellum"
    modules.mkdir(parents=True)
    for name in ("pure-esm-root", "pure-esm-leaf"):
        shutil.copytree(
            fixture / f"vendor/{name}",
            modules / f"fixture-{name}",
        )

    library = prefix / "lib/vellum"
    node = library / "node/bin/node"
    build_script = library / "ui/scripts/build-project.mjs"
    bundle = fixture / "build/app.js"
    run(
        [str(node), str(build_script), str(fixture / "src/App.tsx"), str(bundle)],
        cwd=fixture,
        env={
            **env,
            "VELLUM_BUILD_FORMAT": "iife",
            "VELLUM_PROJECT_ROOT": str(fixture),
        },
    )

    backend_path = library / "vellum_native_backend.py"
    sys.path.insert(0, str(library))
    try:
        spec = importlib.util.spec_from_file_location(
            "vellum_installed_native_backend", backend_path
        )
        if spec is None or spec.loader is None:
            raise ValidationError("cannot load installed native scenario adapter")
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        capabilities = {
            "commands": "v1",
            "files": "denied",
            "clipboard": "text-v1",
            "open_url": "external-v1",
            "network": False,
            "persistence": "state-v1",
        }
        arguments, scenario_name = backend.scenario_arguments(
            {"root": fixture, "capabilities": capabilities},
            "scenarios/phase3.json",
        )
    finally:
        sys.path.remove(str(library))

    if scenario_name != "unchanged authoring fixture on native and browser":
        raise ValidationError("installed Phase 3 scenario identity drifted")
    host = library / "sdk/bin/vellum-app-host"
    completed = run(
        [str(host), "--bundle", str(bundle), "--self-test", *arguments],
        cwd=fixture,
        env=env,
    )
    if (
        "renderer=Skia Graphite backend=Metal fallback=false"
        not in completed.stdout
        or "text_inputs=1" not in completed.stdout
    ):
        raise ValidationError(
            "installed Phase 3 scenario did not use the native GPU/text host"
        )

    capability_json = json.dumps(
        capabilities, sort_keys=True, separators=(",", ":")
    )
    for label, negative in {
        "unknown-command": ["--command", "missing.command"],
        "wrong-text": ["--assert-text", "item-list", "not present"],
        "unchanged-touch": [
            "--touch", "open", '{"pointerType":"touch"}',
        ],
        "wrong-throw": [
            "--expected-throw", "mapped-error", "vellum://wrong.tsx",
        ],
    }.items():
        rejected = subprocess.run(
            [
                str(host), "--bundle", str(bundle), "--self-test",
                "--service-capabilities", capability_json, *negative,
            ],
            cwd=fixture,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if rejected.returncode == 0:
            raise ValidationError(
                f"installed Phase 3 negative control passed: {label}"
            )
    return True


def validate(
    archive: Path,
    checksums: Path,
    forbid_path: Path | None,
    runner_search_roots: Iterable[Path] = (),
) -> dict[str, object]:
    runner_roots = [path.resolve() for path in runner_search_roots]
    checkout_findings = checkout_contamination_findings(runner_roots)
    if checkout_findings:
        raise ValidationError(
            "sterile runner contains a Vellum source checkout: "
            + ", ".join(checkout_findings)
        )
    verification = json.loads(
        run([
            sys.executable, str(SCRIPT_DIR / "verify_sdk_artifact.py"),
            "--archive", str(archive), "--checksums", str(checksums), "--json",
        ]).stdout
    )
    with tempfile.TemporaryDirectory(prefix="vellum-sterile-consumer-") as temporary_text:
        root = Path(temporary_text)
        prefix = root / "prefix"
        no_external_node = {**os.environ, "PATH": "/usr/bin:/bin"}
        journey_env = (
            no_external_node if verification["claims"].get("node_runtime") else os.environ
        )
        installed = run([
            "sh", str(SCRIPT_DIR / "install.sh"),
            "--archive", str(archive), "--checksums", str(checksums),
            "--install-dir", str(prefix),
        ], cwd=root)
        reinstalled = run([
            "sh", str(SCRIPT_DIR / "install.sh"),
            "--archive", str(archive), "--checksums", str(checksums),
            "--install-dir", str(prefix),
        ], cwd=root)
        verified_install = run([
            "sh", str(SCRIPT_DIR / "install.sh"),
            "--verify-installed", "--install-dir", str(prefix),
        ], cwd=root)
        if (
            "Vellum installer: installed" not in installed.stdout
            or "Vellum installer: already_installed" not in reinstalled.stdout
            or "Vellum installer: verified" not in verified_install.stdout
        ):
            raise ValidationError(
                "transactional install, exact reinstall, or verification did not complete"
            )

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

        installed_cli_version = run(
            [str(prefix / "bin/vellum"), "--version"], cwd=root
        ).stdout.strip()
        expected_cli_version = f"vellum {verification['cli_version']}"
        if installed_cli_version != expected_cli_version:
            raise ValidationError(
                "installed CLI identity does not match the verified archive"
            )

        contamination = installed_contamination_findings(prefix)
        if contamination:
            first = contamination[0]
            raise ValidationError(
                f"installed SDK contamination: {first['rule']} in {first['path']}"
            )

        consumer_source = root / "consumer-source"
        shutil.copytree(
            SUPPORT_ROOT / "apps/minimal-scene",
            consumer_source,
        )
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
            verification["claims"]["targets"]["macos"]["commands"][command]
            for command in ("build", "run", "test", "capture", "package")
        )
        ui_present = (prefix / "lib/vellum/ui/package.json").is_file()
        native_present = (
            (prefix / "lib/vellum/vellum_native_backend.py").is_file() and
            (prefix / "lib/vellum/vellum_scenario.py").is_file() and
            (prefix / "lib/vellum/bin/vellum-native-backend").is_file()
        )
        component_abi_present = (
            (sdk_prefix / "include/vellum/components/abi.h").is_file() and
            "Vellum::ComponentAbi" in package_text
        )
        if gpu_claimed and (
            "Vellum::Gpu" not in package_text or
            "Vellum::Authoring" not in package_text or
            not ui_present
        ):
            raise ValidationError("GPU artifact is missing its installed GPU/authoring/UI payload")
        if native_claimed and not native_present:
            raise ValidationError("native command claims have no installed native backend")
        installed_phase3_scenario = (
            validate_installed_phase3(prefix, root, journey_env)
            if native_claimed else True
        )
        web_claimed = all(
            verification["claims"]["targets"]["web"]["commands"][command]
            for command in ("build", "run", "test", "package")
        )
        web_present = all((prefix / "lib/vellum" / path).is_file() for path in (
            "vellum_web_backend.py", "bin/vellum-web-backend",
            "vellum_scenario.py",
            "web/manifest.json", "web/vellum_web_core.js", "web/vellum_web_core.wasm",
            "web/vellum_host.js",
            "web/browser_component_adapter.cpp",
            "web/text_semantics.js",
        )) and any((prefix / "lib/vellum" / path).is_file() for path in (
            "node/bin/node", "node/bin/node.exe",
        ))
        if web_claimed and not web_present:
            raise ValidationError("web command claims have no complete installed runtime/backend")
        installed_phase3_browser_scenario = True
        if web_claimed:
            phase3_browser = run([
                sys.executable,
                str(
                    SUPPORT_ROOT
                    / "web/tests/run_text_semantics_browser.py"
                ),
                "--core-root", str(library / "web"),
                "--source-root", str(SUPPORT_ROOT),
                "--fixture", "phase3",
                "--node", str(library / "node/bin/node"),
                "--build-script",
                str(library / "ui/scripts/build-project.mjs"),
            ], cwd=root, env=journey_env)
            installed_phase3_browser_scenario = (
                '"changed":true' in phase3_browser.stdout
                and '"target":"mapped-error"' in phase3_browser.stdout
                and '"target":"title-input"' in phase3_browser.stdout
            )
            if not installed_phase3_browser_scenario:
                raise ValidationError(
                    "installed exact Phase 3 browser scenario evidence is incomplete"
                )
        custom_claimed = verification["claims"].get("custom_components") is True
        if custom_claimed and not component_abi_present:
            raise ValidationError("custom component claim has no installed ABI target/header")

        project = root / "application"
        created = json.loads(run([
            str(prefix / "bin/vellum"), "create", "Sterile Artifact App",
            "--directory", str(project), "--json",
        ], cwd=root, env=journey_env).stdout)
        doctor = json.loads(run([
            str(prefix / "bin/vellum"), "doctor", "--json",
        ], cwd=project, env=journey_env).stdout)
        imported = json.loads(run([
            str(prefix / "bin/vellum"), "import",
            str(SUPPORT_ROOT / "fixtures/design-ir/revision-a.source.json"),
            "--source-type", "figma", "--as", "main", "--json",
        ], cwd=project, env=journey_env).stdout)
        authored_source = project / "src/App.tsx"
        authored_source.write_text(
            '''import { importedBindings, importedDesign } from "@vellum/imported";\n'''
            '''import { Design, Stack, Text, useState } from "@vellum/ui";\n\n'''
            '''export function App() {\n'''
            '''  const [boards, setBoards] = useState(0);\n'''
            '''  if (!importedDesign) throw new Error("imported design is required");\n'''
            '''  return (\n'''
            '''    <Stack id="app-shell" style={{ width: 640, height: 400, gap: 8, backgroundColor: "#111827" }}>\n'''
            '''      <Text id="board-count" style={{ height: 28, fontSize: 18, color: "#ffffff" }}>Boards created: {boards}</Text>\n'''
            '''      <Design document={importedDesign} bindings={importedBindings}\n'''
            '''        actions={{ "boards.create": () => setBoards((count) => count + 1) }} />\n'''
            '''    </Stack>\n'''
            '''  );\n'''
            '''}\n''',
            encoding="utf-8",
        )
        authored_source_before = authored_source.read_bytes()
        authored_overlay = project / "design/overlays/main.authored.json"
        overlay = json.loads(authored_overlay.read_text(encoding="utf-8"))
        overlay["aliases"] = {
            "main/create-button-v1": "main/create-button-v2",
        }
        overlay["bindings"] = [{
            "action": "boards.create",
            "event": "press",
            "nodeId": "main/create-button-v1",
        }]
        authored_overlay.write_text(
            json.dumps(overlay, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reimported = json.loads(run([
            str(prefix / "bin/vellum"), "reimport",
            "--source", str(SUPPORT_ROOT / "fixtures/design-ir/revision-b.source.json"),
            "--as", "main", "--json",
        ], cwd=project, env=journey_env).stdout)
        active_revision = json.loads(
            (project / "design/import.lock.json").read_text(encoding="utf-8")
        )["sources"]["main"]["activeRevision"]
        lock = json.loads((project / "framework.lock").read_text(encoding="utf-8"))
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
        if authored_source.read_bytes() != authored_source_before:
            raise ValidationError("installed CLI reimport overwrote developer-owned application code")
        resolved_bindings = json.loads(
            (project / "ui/generated/main.bindings.json").read_text(encoding="utf-8")
        )["bindings"]
        if (
            len(resolved_bindings) != 1
            or resolved_bindings[0].get("action") != "boards.create"
            or resolved_bindings[0].get("resolvedNodeId") != "main/create-button-v2"
        ):
            raise ValidationError("installed CLI reimport did not preserve authored behavior")
        imported_scenario = project / "tests/scenarios/smoke.json"
        imported_scenario.write_text(json.dumps({
            "schema": "vellum.scenario.v1",
            "name": "imported-smoke",
            "viewport": {"width": 640, "height": 400},
            "steps": [
                {"action": "wait-for-idle"},
                {"action": "capture", "name": "before"},
                {"action": "press", "target": "main/create-button-v2"},
                {"action": "wait-for-idle"},
                {"action": "capture", "name": "after"},
            ],
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        zip_project = root / "zip-application"
        zip_created = json.loads(run([
            str(prefix / "bin/vellum"), "create", "Sterile ZIP App",
            "--directory", str(zip_project),
            "--from", "figma",
            str(SUPPORT_ROOT / "fixtures/design-ir/pulp-emitter-generic.pulp.zip"),
            "--json",
        ], cwd=root, env=journey_env).stdout)
        zip_lock = json.loads(
            (zip_project / "design/import.lock.json").read_text(encoding="utf-8")
        )["sources"]["main"]
        zip_snapshot = (
            zip_project / "sources/imported/main" /
            zip_lock["activeRevision"] / "source.pulp.zip"
        )
        zip_snapshot_verified = (
            zip_created.get("status") == "created"
            and zip_lock.get("sourceArtifactKind") == "pulp-zip"
            and zip_snapshot.read_bytes()
            == (
                SUPPORT_ROOT
                / "fixtures/design-ir/pulp-emitter-generic.pulp.zip"
            ).read_bytes()
        )
        if not zip_snapshot_verified:
            raise ValidationError("installed CLI create --from figma ZIP journey did not complete")

        native_enabled = all(
            verification["claims"]["targets"]["macos"]["commands"][name]
            for name in ("build", "run", "test", "capture", "package")
        )
        native_results: dict[str, dict[str, object]] = {}
        native_capture = project / "artifacts/installed-proof.png"
        native_montage = project / "artifacts/installed-montage.png"
        native_matrix_capture = project / "artifacts/installed-montage-captures/home.png"
        native_package = project / "dist/sterile-artifact-app.app"
        imported_bundle_contains_design = False
        native_capture_produced = False
        native_montage_produced = False
        native_package_produced = False
        custom_component_produced = False
        if native_enabled:
            for name, arguments in {
                "build": ["build"],
                "run": ["run", "--self-test", "--no-build"],
                "test": ["test", "--scenario", "smoke"],
                "capture": [
                    "capture", "--scenario", "smoke", "--output",
                    "artifacts/installed-proof.png",
                ],
                "montage": [
                    "capture", "--matrix", "tests/capture-matrix.json", "--montage",
                    "--output", "artifacts/installed-montage.png",
                ],
                "package": ["package", "--output", "dist"],
            }.items():
                native_results[name] = json.loads(run([
                    str(prefix / "bin/vellum"), *arguments, "--json",
                ], cwd=project, env=journey_env).stdout)
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
            if (
                not native_montage.is_file() or native_montage.read_bytes()[:4] != b"\x89PNG"
                or not native_matrix_capture.is_file()
                or native_results["montage"].get("data", {}).get("backend", {}).get("data", {}).get("montage") is None
            ):
                raise ValidationError("installed capture matrix did not produce a PNG montage and source capture")
            if not (native_package / "Contents/MacOS/sterile-artifact-app").is_file():
                raise ValidationError("installed native package did not produce a runnable .app")
            native_capture_produced = True
            native_montage_produced = True
            native_package_produced = True

        web_results: dict[str, dict[str, object]] = {}
        web_reproducible = False
        web_runtime_exact = False
        web_node_self_contained = False
        web_same_source_imported_behavior = False
        if web_claimed:
            web_project = project
            for name, arguments in {
                "build": ["build", "--target", "web"],
                "test": ["test", "--target", "web", "--scenario", "smoke"],
                "run": ["run", "--target", "web", "--no-build"],
                "package_a": ["package", "--target", "web", "--output", "dist-a"],
                "package_b": ["package", "--target", "web", "--output", "dist-b"],
            }.items():
                web_results[name] = json.loads(run([
                    str(prefix / "bin/vellum"), *arguments, "--json",
                ], cwd=web_project, env=no_external_node).stdout)
            if any(not value.get("ok") for value in web_results.values()):
                raise ValidationError("installed web CLI journey did not complete")
            archives = [
                web_project / "dist-a/sterile-artifact-app-web.tar.gz",
                web_project / "dist-b/sterile-artifact-app-web.tar.gz",
            ]
            web_reproducible = (
                all(path.is_file() for path in archives) and
                hashlib.sha256(archives[0].read_bytes()).digest()
                == hashlib.sha256(archives[1].read_bytes()).digest()
            )
            built_wasm = web_project / ".vellum/build/web/vellum_web_core.wasm"
            installed_wasm = prefix / "lib/vellum/web/vellum_web_core.wasm"
            web_runtime_exact = built_wasm.read_bytes() == installed_wasm.read_bytes()
            web_doctor = json.loads(run([
                str(prefix / "bin/vellum"), "doctor", "--json",
            ], cwd=web_project, env=no_external_node).stdout)
            node_check = next(item for item in web_doctor["data"]["checks"] if item["name"] == "node")
            web_node_self_contained = "SDK-local" in node_check["detail"]
            web_bundle = web_project / ".vellum/build/web/app.js"
            web_same_source_imported_behavior = (
                web_bundle.is_file()
                and "main/create-button-v2" in web_bundle.read_text(encoding="utf-8")
                and "boards.create" in web_bundle.read_text(encoding="utf-8")
            )
            if (
                not web_reproducible or not web_runtime_exact
                or not web_node_self_contained or not web_same_source_imported_behavior
            ):
                raise ValidationError("installed web reproducibility/runtime/Node proof failed")
        if custom_claimed:
            custom_project = root / "custom-component-application"
            custom_created = json.loads(run([
                str(prefix / "bin/vellum"), "create", "Custom Component App",
                "--directory", str(custom_project), "--template", "cpp-component",
                "--no-verify", "--json",
            ], cwd=root, env=journey_env).stdout)
            if (
                custom_created.get("status") != "created"
                or custom_created.get("data", {}).get("template") != "cpp-component"
            ):
                raise ValidationError("installed CLI did not create the custom component app")
            custom_capture = custom_project / "artifacts/custom-component.png"
            custom_results = {
                "build": json.loads(run([
                    str(prefix / "bin/vellum"), "build", "--json",
                ], cwd=custom_project, env=journey_env).stdout),
                "test": json.loads(run([
                    str(prefix / "bin/vellum"), "test", "--scenario", "smoke",
                    "--json",
                ], cwd=custom_project, env=journey_env).stdout),
                "capture": json.loads(run([
                    str(prefix / "bin/vellum"), "capture", "--scenario", "smoke",
                    "--output", "artifacts/custom-component.png", "--json",
                ], cwd=custom_project, env=journey_env).stdout),
                "package": json.loads(run([
                    str(prefix / "bin/vellum"), "package", "--output", "dist", "--json",
                ], cwd=custom_project, env=journey_env).stdout),
            }
            custom_app = custom_project / ".vellum/build/macos/custom-component-app.app"
            custom_module = custom_app / "Contents/PlugIns/VellumComponents/level-meter.dylib"
            custom_package_module = (
                custom_project / "dist/custom-component-app.app/Contents/PlugIns/"
                "VellumComponents/level-meter.dylib"
            )
            custom_component_produced = (
                all(value.get("ok") for value in custom_results.values())
                and custom_results["build"].get("data", {}).get("backend", {}).get("data", {}).get("components") == ["level-meter"]
                and "components=1" in custom_results["capture"].get("data", {}).get("backend", {}).get("data", {}).get("host_output", "")
                and custom_module.is_file() and custom_package_module.is_file()
                and custom_capture.is_file() and custom_capture.read_bytes()[:4] == b"\x89PNG"
            )
            if not custom_component_produced:
                raise ValidationError("installed app-owned custom C++ component journey did not complete")

        unrelated = prefix / "share/unrelated/keep.txt"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("not owned by Vellum\n", encoding="utf-8")
        uninstalled = run([
            "sh", str(SCRIPT_DIR / "install.sh"),
            "--uninstall", "--install-dir", str(prefix),
        ], cwd=root)
        uninstalled_again = run([
            "sh", str(SCRIPT_DIR / "install.sh"),
            "--uninstall", "--install-dir", str(prefix),
        ], cwd=root)
        uninstall_preserved_unrelated = (
            "Vellum installer: uninstalled" in uninstalled.stdout
            and "Vellum installer: already_absent" in uninstalled_again.stdout
            and unrelated.read_text(encoding="utf-8") == "not owned by Vellum\n"
            and not (prefix / "lib/vellum").exists()
            and not (prefix / "bin/vellum").exists()
        )
        if not uninstall_preserved_unrelated:
            raise ValidationError(
                "transactional uninstall was not idempotent or removed unrelated prefix data"
            )

    checks = {
        "checksum_and_payload_manifest": True,
        "runner_has_no_framework_checkout": not checkout_findings,
        "artifact_contamination_scan": verification["contamination_free"],
        "clean_prefix_install": True,
        "transactional_exact_reinstall": True,
        "transactional_install_verification": True,
        "transactional_uninstall_preserves_unrelated": uninstall_preserved_unrelated,
        "installed_artifact_identity": install_manifest == expected_manifest,
        "installed_cli_identity": installed_cli_version == expected_cli_version,
        "installed_tree_contamination_scan": not contamination,
        "relocatable_cmake_package": True,
        "gpu_authoring_ui_payload": not gpu_claimed or ui_present,
        "native_backend_payload": not native_claimed or native_present,
        "custom_component_abi": not verification["claims"].get("custom_components") or component_abi_present,
        "sterile_consumer_configure": True,
        "sterile_consumer_build": True,
        "sterile_consumer_test": True,
        "project_created_by_installed_cli": created.get("status") == "created",
        "installed_blank_template_selected": (
            created.get("data", {}).get("template") == "blank"
        ),
        "project_lock_matches_sdk": True,
        "project_lock_pins_artifact_sha": lock["framework"].get("artifact") == expected_lock_identity,
        "installed_cli_doctor": doctor.get("status") == "ready",
        "installed_cli_import": imported.get("status") == "imported",
        "installed_cli_reimport": reimported.get("status") == "reimported",
        "active_reimport_revision": active_revision == "palette-board-b",
        "authored_behavior_survives_reimport": (
            len(resolved_bindings) == 1
            and resolved_bindings[0].get("resolvedNodeId") == "main/create-button-v2"
        ),
        "installed_cli_pulp_zip_create_from": zip_created.get("status") == "created",
        "installed_imported_template_selected": (
            zip_created.get("data", {}).get("template") == "imported-app"
        ),
        "installed_pulp_zip_snapshot": zip_snapshot_verified,
        "native_capability_claim_consistent": all(
            verification["claims"]["targets"]["macos"]["commands"][name] is native_enabled
            for name in ("build", "run", "test", "capture", "package")
        ),
        "installed_native_build": not native_enabled or native_results["build"]["status"] == "built",
        "installed_native_finite_run": not native_enabled or native_results["run"]["status"] == "self_test_passed",
        "installed_native_scenario": not native_enabled or native_results["test"]["status"] == "tests_passed",
        "installed_phase3_unchanged_scenario": installed_phase3_scenario,
        "installed_imported_design_bundle": not native_enabled or imported_bundle_contains_design,
        "installed_native_capture": not native_enabled or native_capture_produced,
        "installed_native_montage": not native_enabled or native_montage_produced,
        "installed_native_package": not native_enabled or native_package_produced,
        "web_backend_payload": not web_claimed or web_present,
        "installed_web_build": not web_claimed or web_results["build"]["status"] == "built",
        "installed_web_real_chrome_scenario": not web_claimed or web_results["test"]["status"] == "tests_passed",
        "installed_web_run_instructions": not web_claimed or web_results["run"]["status"] == "ready_to_serve",
        "installed_web_reproducible_package": not web_claimed or web_reproducible,
        "installed_web_exact_wasm": not web_claimed or web_runtime_exact,
        "installed_sdk_local_node": not web_claimed or web_node_self_contained,
        "installed_web_same_source_imported_behavior": (
            not web_claimed or web_same_source_imported_behavior
        ),
        "installed_phase3_browser_scenario": installed_phase3_browser_scenario,
        "installed_custom_cpp_component": not custom_claimed or custom_component_produced,
        "installed_cpp_component_template_selected": (
            not custom_claimed
            or custom_created.get("data", {}).get("template") == "cpp-component"
        ),
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
        "cli_version": verification["cli_version"],
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
    parser.add_argument(
        "--runner-search-root",
        action="append",
        default=[],
        type=Path,
        help="bounded root that must not contain a Vellum source checkout",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        evidence = validate(
            args.archive.resolve(),
            args.checksums.resolve(),
            args.forbid_path,
            args.runner_search_root,
        )
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
