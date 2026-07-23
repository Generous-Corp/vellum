from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import struct
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest


REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "scripts/build_sdk_artifact.py"
VERIFIER = REPO / "scripts/verify_sdk_artifact.py"
VALIDATOR = REPO / "scripts/validate_installed_sdk.py"
INSTALLER = REPO / "scripts/install.sh"
CONSUMER = REPO / "apps/minimal-scene"


@unittest.skipUnless(shutil.which("cmake") and (shutil.which("shasum") or shutil.which("sha256sum")), "CMake/checksum tools unavailable")
class SdkArtifactTests(unittest.TestCase):
    def test_installed_browser_scenario_resolves_payload_from_prefix(self) -> None:
        module = runpy.run_path(str(VALIDATOR))
        validate_browser = module["validate_installed_phase3_browser"]
        validation_error = module["ValidationError"]
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run(arguments: list[str], *, cwd: Path | None = None,
                     env: dict[str, str] | None = None) -> SimpleNamespace:
            calls.append((arguments, cwd))
            return SimpleNamespace(
                stdout=(
                    '{"changed":true,"target":"mapped-error"}\n'
                    '{"target":"title-input"}\n'
                )
            )

        original_run = validate_browser.__globals__["run"]
        validate_browser.__globals__["run"] = fake_run
        try:
            prefix = Path("/installed/vellum")
            root = Path("/sterile/work")
            self.assertTrue(validate_browser(prefix, root, {"PATH": "/usr/bin"}))
            arguments, cwd = calls.pop()
            self.assertEqual(cwd, root)
            self.assertIn(
                "/installed/vellum/lib/vellum/web",
                arguments,
            )
            self.assertIn(
                "/installed/vellum/lib/vellum/node/bin/node",
                arguments,
            )
            self.assertIn(
                "/installed/vellum/lib/vellum/ui/scripts/build-project.mjs",
                arguments,
            )

            validate_browser.__globals__["run"] = lambda *args, **kwargs: SimpleNamespace(
                stdout='{"changed":true,"target":"title-input"}\n'
            )
            with self.assertRaisesRegex(validation_error, "evidence is incomplete"):
                validate_browser(prefix, root, {"PATH": "/usr/bin"})
        finally:
            validate_browser.__globals__["run"] = original_run

    def test_artifact_builder_rejects_cli_release_identity_drift(self) -> None:
        module = runpy.run_path(str(BUILDER))
        verify_cli_identity = module["verify_cli_identity"]
        artifact_error = module["ArtifactError"]
        verify_cli_identity(REPO)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(REPO / "cli", root / "cli")
            cli = root / "cli/vellum_cli.py"
            source = cli.read_text(encoding="utf-8")
            cli.write_text(
                source.replace(
                    "CLI_VERSION = FRAMEWORK_VERSION",
                    'CLI_VERSION = "9.9.9"',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                artifact_error, "authoring CLI identity must be exactly"
            ):
                verify_cli_identity(root)

    def test_web_payload_and_node_are_exact_fail_closed_inputs(self) -> None:
        module = runpy.run_path(str(BUILDER))
        copy_web_payload = module["copy_web_payload"]
        artifact_error = module["ArtifactError"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "web"
            source.mkdir()
            names = (
                "vellum_web_core.js", "vellum_web_core.wasm", "index.html",
                "style.css", "vellum_host.js", "browser_component_adapter.cpp",
                "text_semantics.js",
                "check_wasm_no_engine.py",
            )
            records = {}
            for name in names:
                content = f"fixture:{name}\n".encode()
                (source / name).write_bytes(content)
                records[name] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            commit = "a" * 40
            (source / "manifest.json").write_text(json.dumps({
                "schema": "vellum.web-payload.v1", "source_commit": commit,
                "compiler": "fixture", "backend": "fixture", "files": records,
            }), encoding="utf-8")
            destination = root / "copied"
            copy_web_payload(source, destination, commit)
            self.assertEqual((destination / "vellum_web_core.wasm").read_bytes(),
                             (source / "vellum_web_core.wasm").read_bytes())
            with self.assertRaisesRegex(artifact_error, "source commit"):
                copy_web_payload(source, root / "wrong-commit", "b" * 40)
            (source / "vellum_web_core.wasm").write_bytes(b"tampered")
            with self.assertRaisesRegex(artifact_error, "hash or size mismatch"):
                copy_web_payload(source, root / "tampered", commit)

            missing_node = subprocess.run([
                sys.executable, str(BUILDER), "--output-dir", str(root / "output"),
                "--source-commit", subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
                ).strip(),
                "--allow-dirty", "--graphics", "off", "--web-payload", str(source),
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(missing_node.returncode, 0)
            self.assertIn("require an exact --node-binary", missing_node.stderr)

    def test_macho_uuid_rewrite_is_deterministic_and_fail_closed(self) -> None:
        module = runpy.run_path(str(BUILDER))
        rewrite = module["rewrite_macho_uuid"]
        artifact_error = module["ArtifactError"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "host"
            command = struct.pack("<II", 0x1B, 24) + b"0123456789abcdef"
            header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0, 0, 0, 1, len(command), 0, 0)
            path.write_bytes(header + command + b"payload")
            identity = bytes(range(16))
            rewrite(path, identity)
            first = path.read_bytes()
            self.assertEqual(first[40:56], identity)
            rewrite(path, identity)
            self.assertEqual(path.read_bytes(), first)
            with self.assertRaises(artifact_error):
                rewrite(path, b"short")

    def test_node_license_and_provenance_match_exact_runtime(self) -> None:
        module = runpy.run_path(str(BUILDER))
        validate = module["validate_node_inputs"]
        artifact_error = module["ArtifactError"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "node"
            binary.write_bytes(b"exact node executable fixture\n")
            license_path = root / "LICENSE"
            license_path.write_text("Node.js license fixture\n" * 20, encoding="utf-8")
            provenance_path = root / "provenance.json"
            provenance = {
                "schema": "vellum.node-runtime-provenance.v1",
                "name": "Node.js",
                "version": "22.16.0",
                "target": "darwin-arm64",
                "source_url": "https://nodejs.org/dist/v22.16.0/node.tar.gz",
                "distribution_sha256": "a" * 64,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "license_file": "LICENSE",
                "license_sha256": hashlib.sha256(license_path.read_bytes()).hexdigest(),
            }
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
            self.assertEqual(
                validate(binary, license_path, provenance_path,
                         version="v22.16.0", target="darwin-arm64"),
                provenance,
            )
            binary.write_bytes(b"different node executable\n")
            with self.assertRaisesRegex(artifact_error, "binary_sha256"):
                validate(binary, license_path, provenance_path,
                         version="v22.16.0", target="darwin-arm64")

    def test_native_backend_resolves_only_sdk_local_node(self) -> None:
        sys.path.insert(0, str(REPO / "cli"))
        try:
            module = runpy.run_path(str(REPO / "cli/vellum_native_backend.py"))
        finally:
            sys.path.pop(0)
        sdk_node = module["sdk_node"]
        backend_failure = module["BackendFailure"]
        with tempfile.TemporaryDirectory() as temporary:
            sdk = Path(temporary)
            with self.assertRaisesRegex(backend_failure, "exact Node runtime"):
                sdk_node(sdk)
            local_node = sdk / "node/bin/node"
            local_node.parent.mkdir(parents=True)
            local_node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            local_node.chmod(0o755)
            self.assertEqual(sdk_node(sdk), local_node)

    def run_checked(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed

    def test_native_command_claims_require_every_composed_payload(self) -> None:
        derive_capabilities = runpy.run_path(str(BUILDER))["derive_capabilities"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            install_tree = root / "install"
            required_files = (
                "vellum_cli.py",
                "design-ir/bin/vellum-backend.js",
                "ui/package.json",
                "ui/package-lock.json",
                "ui/src/index.js",
                "ui/node_modules/esbuild/package.json",
                "ui/node_modules/@esbuild/darwin-arm64/package.json",
                "ui/node_modules/@esbuild/darwin-arm64/bin/esbuild",
                "ui/node_modules/typescript/package.json",
                "node/bin/node",
                "node/LICENSE",
                "node/provenance.json",
            )
            for relative in required_files:
                path = payload / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            package = install_tree / "lib/cmake/Vellum"
            package.mkdir(parents=True)
            (package / "VellumConfig.cmake").write_text(
                "# Vellum::Gpu Vellum::Authoring\n", encoding="utf-8"
            )
            for relative in (
                "bin/vellum-app-host",
                "lib/libvellum-authoring.dylib",
                "lib/libvellum-gpu.dylib",
            ):
                path = install_tree / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture\n")
            without_backend = derive_capabilities(payload, install_tree)
            self.assertTrue(without_backend["gpu_renderer"])
            self.assertFalse(any(
                without_backend["commands"][command]
                for command in ("build", "run", "test", "capture", "package")
            ))
            (payload / "vellum_native_backend.py").write_text("# fixture\n", encoding="utf-8")
            without_capture_support = derive_capabilities(payload, install_tree)
            self.assertFalse(any(
                without_capture_support["commands"][command]
                for command in ("build", "run", "test", "capture", "package")
            ))
            (payload / "vellum_png.py").write_text("# fixture\n", encoding="utf-8")
            (payload / "vellum_scenario.py").write_text("# fixture\n", encoding="utf-8")
            with_backend = derive_capabilities(payload, install_tree)
            self.assertTrue(all(
                with_backend["commands"][command]
                for command in ("build", "run", "test", "capture", "package")
            ))
            authoring = with_backend["authoring"]
            self.assertTrue(authoring["text_input_v1"]["native_direct_text"])
            self.assertTrue(authoring["scenario_v1"]["input"])
            self.assertTrue(authoring["persistence"]["state_v1"])
            self.assertTrue(authoring["text_input_v1"]["ime_composition"])
            self.assertTrue(authoring["text_input_v1"]["caret_and_selection"])
            self.assertTrue(authoring["text_input_v1"]["accessibility_text"])
            self.assertFalse(authoring["text_input_v1"]["clipboard_editing"])
            self.assertFalse(authoring["persistence"]["migration_api"])

    def test_reproducible_archive_installs_into_sterile_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = [root / "first", root / "second"]
            current_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip()
            wrong_commit = subprocess.run([
                sys.executable, str(BUILDER),
                "--output-dir", str(root / "wrong"),
                "--source-commit", "b" * 40,
                "--allow-dirty",
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(wrong_commit.returncode, 0)
            self.assertIn("must equal the checked-out Vellum HEAD", wrong_commit.stderr)
            evidence = []
            for output in outputs:
                completed = self.run_checked([
                    sys.executable, str(BUILDER),
                    "--output-dir", str(output),
                    "--source-commit", current_head,
                    "--allow-dirty",
                    "--target", "test-host",
                    "--graphics", "off",
                    "--json",
                ])
                evidence.append(json.loads(completed.stdout))
            self.assertEqual(evidence[0]["artifact_sha256"], evidence[1]["artifact_sha256"])
            first_archive = Path(evidence[0]["artifact_path"])
            first_sums = outputs[0] / "SHA256SUMS"

            verified = self.run_checked([
                sys.executable, str(VERIFIER),
                "--archive", str(first_archive),
                "--checksums", str(first_sums),
                "--json",
            ])
            verification = json.loads(verified.stdout)
            self.assertTrue(verification["ok"])
            self.assertEqual(verification["framework_version"], "0.1.4")
            self.assertEqual(verification["cli_version"], "0.1.4")
            self.assertTrue(verification["contamination_free"])
            self.assertEqual(verification["contamination_findings"], [])
            self.assertEqual(verification["claims"]["gpu_renderer"], False)
            self.assertEqual(verification["claims"]["commands"]["import"], True)
            self.assertEqual(verification["claims"]["commands"]["reimport"], True)
            for command in ("build", "run", "test", "capture", "package"):
                self.assertEqual(verification["claims"]["commands"][command], False)

            drifted_archive = root / "vellum-sdk-drifted-cli.tar.gz"
            with tarfile.open(first_archive, "r:gz") as source, tarfile.open(
                drifted_archive, "w:gz"
            ) as output:
                for member in source.getmembers():
                    file_object = source.extractfile(member) if member.isfile() else None
                    if member.name == "metadata.json":
                        drifted_metadata = json.load(file_object)
                        drifted_metadata["cli_version"] = "9.9.9"
                        content = (
                            json.dumps(drifted_metadata, indent=2, sort_keys=True) + "\n"
                        ).encode()
                        replacement = copy.copy(member)
                        replacement.size = len(content)
                        output.addfile(replacement, io.BytesIO(content))
                    else:
                        output.addfile(member, file_object)
            drifted_sums = root / "DRIFTED-SHA256SUMS"
            drifted_sums.write_text(
                f"{hashlib.sha256(drifted_archive.read_bytes()).hexdigest()}  "
                f"{drifted_archive.name}\n",
                encoding="utf-8",
            )
            drifted = subprocess.run([
                sys.executable, str(VERIFIER),
                "--archive", str(drifted_archive),
                "--checksums", str(drifted_sums), "--json",
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(drifted.returncode, 0)
            self.assertIn("CLI version does not match", drifted.stderr)

            forged_archive = root / "vellum-sdk-forged-claims.tar.gz"
            with tarfile.open(first_archive, "r:gz") as source, tarfile.open(forged_archive, "w:gz") as output:
                for member in source.getmembers():
                    file_object = source.extractfile(member) if member.isfile() else None
                    if member.name == "metadata.json":
                        forged_metadata = json.load(file_object)
                        forged_metadata["capabilities"]["commands"]["build"] = True
                        content = (json.dumps(forged_metadata, indent=2, sort_keys=True) + "\n").encode()
                        replacement = copy.copy(member)
                        replacement.size = len(content)
                        output.addfile(replacement, io.BytesIO(content))
                    else:
                        output.addfile(member, file_object)
            forged_sums = root / "FORGED-SHA256SUMS"
            forged_sums.write_text(
                f"{hashlib.sha256(forged_archive.read_bytes()).hexdigest()}  {forged_archive.name}\n",
                encoding="utf-8",
            )
            forged = subprocess.run([
                sys.executable, str(VERIFIER),
                "--archive", str(forged_archive),
                "--checksums", str(forged_sums), "--json",
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(forged.returncode, 0)
            self.assertIn("capability claims do not match", forged.stderr)

            validated = self.run_checked([
                sys.executable, str(VALIDATOR),
                "--archive", str(first_archive),
                "--checksums", str(first_sums),
                "--json",
            ])
            validation = json.loads(validated.stdout)
            self.assertTrue(validation["ok"])
            self.assertEqual(validation["cli_version"], "0.1.4")
            self.assertTrue(all(validation["checks"].values()))

            prefix = root / "prefix"
            installed = self.run_checked([
                "sh", str(INSTALLER), "--archive", str(first_archive),
                "--checksums", str(first_sums), "--install-dir", str(prefix),
            ])
            self.assertIn("Verified SHA-256", installed.stdout)
            install_manifest = json.loads(
                (prefix / "lib/vellum/install-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(install_manifest["artifact_sha256"], verification["sha256"])
            self.assertEqual(install_manifest["source_commit"], current_head)
            self.assertTrue(install_manifest["verified"])
            if shutil.which("curl"):
                release_dir = root / "release/v0.1.4"
                release_dir.mkdir(parents=True)
                shutil.copy2(first_archive, release_dir / first_archive.name)
                shutil.copy2(INSTALLER, release_dir / "install.sh")
                shutil.copy2(
                    REPO / "scripts/install_core.py",
                    release_dir / "install_core.py",
                )
                release_assets = (
                    release_dir / first_archive.name,
                    release_dir / "install.sh",
                    release_dir / "install_core.py",
                )
                (release_dir / "SHA256SUMS").write_text(
                    "".join(
                        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                        for path in sorted(release_assets, key=lambda item: item.name)
                    ),
                    encoding="utf-8",
                )
                release_prefix = root / "release-prefix"
                release_temporary = root / "release-temporary"
                release_temporary.mkdir()
                release_install = self.run_checked([
                    "sh", str(INSTALLER), "--version", "0.1.4",
                    "--target", "test-host",
                    "--release-base-url", (root / "release").as_uri(),
                    "--install-dir", str(release_prefix),
                ], env={**os.environ, "TMPDIR": str(release_temporary)})
                self.assertIn("Verified SHA-256", release_install.stdout)
                self.assertTrue((release_prefix / "lib/vellum/sdk/lib/cmake/Vellum/VellumConfig.cmake").is_file())
                self.assertEqual(list(release_temporary.iterdir()), [])
            shutil.copytree(CONSUMER, root / "consumer-source")
            consumer_build = root / "consumer-build"
            self.run_checked([
                "cmake", "-S", str(root / "consumer-source"), "-B", str(consumer_build),
                f"-DCMAKE_PREFIX_PATH={prefix / 'lib/vellum/sdk'}",
                "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
                "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
                "-DCMAKE_BUILD_TYPE=Release",
            ], cwd=root)
            self.run_checked(["cmake", "--build", str(consumer_build), "--parallel"], cwd=root)
            self.run_checked(["ctest", "--test-dir", str(consumer_build), "--output-on-failure"], cwd=root)

            package_dir = prefix / "lib/vellum/sdk/lib/cmake/Vellum"
            package_text = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.cmake"))
            self.assertNotIn(str(REPO), package_text)

            project = root / "project"
            created = self.run_checked([
                str(prefix / "bin/vellum"), "create", "Artifact Consumer",
                "--directory", str(project), "--json",
            ], cwd=root)
            self.assertEqual(json.loads(created.stdout)["status"], "created")
            lock = json.loads((project / "framework.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["version"], "0.1.4")
            self.assertEqual(lock["framework"]["artifact"], {
                "verified": True,
                "sha256": verification["sha256"],
                "target": "test-host",
                "sourceCommit": current_head,
            })
            doctor = self.run_checked([str(prefix / "bin/vellum"), "doctor", "--json"], cwd=project)
            doctor_payload = json.loads(doctor.stdout)
            sdk_check = next(item for item in doctor_payload["data"]["checks"] if item["name"] == "sdk-artifact")
            compatibility = next(item for item in doctor_payload["data"]["checks"] if item["name"] == "project-sdk-compatibility")
            self.assertTrue(sdk_check["available"])
            self.assertTrue(compatibility["available"])


if __name__ == "__main__":
    unittest.main()
