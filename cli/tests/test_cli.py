from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"


def invoke(*arguments: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=cwd or REPO,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


class CliTests(unittest.TestCase):
    def test_create_is_deterministic_and_has_maintainable_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            for destination in (first, second):
                completed = invoke("--json", "create", "Example App", "--directory", str(destination))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["schema"], "vellum.cli.result.v1")
                self.assertEqual(payload["status"], "created")

            first_files = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
            second_files = sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
            self.assertEqual(first_files, second_files)
            for relative in first_files:
                self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())

            required = {
                Path("vellum.lock.json"),
                Path("AGENTS.md"),
                Path(".vellum/agent-instructions.md"),
                Path("sources/imported/README.md"),
                Path("design/ir/design-ir.json"),
                Path("ui/generated/Home.generated.tsx"),
                Path("src/App.tsx"),
                Path("src/main.tsx"),
                Path("native/README.md"),
                Path("tests/scenarios/smoke.json"),
                Path("packaging/vellum.package.json"),
            }
            self.assertTrue(required.issubset(set(first_files)))
            lock = json.loads((first / "vellum.lock.json").read_text())
            self.assertEqual(lock["project"]["id"], hashlib.sha256(b"vellum-project-v1:example-app").hexdigest()[:24])
            self.assertEqual(lock["framework"]["version"], "0.1.0")
            self.assertEqual(lock["framework"]["artifact"], {
                "verified": False,
                "sha256": None,
                "target": "local-development",
                "sourceCommit": None,
            })

    def test_create_refuses_nonempty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / "owned.txt").write_text("keep", encoding="utf-8")
            completed = invoke("--json", "create", "Nope", "--directory", str(destination))
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "destination_not_empty")
            self.assertEqual((destination / "owned.txt").read_text(), "keep")

    def test_create_run_fails_honestly_without_native_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "app"
            completed = invoke("create", "Cannot Run", "-d", str(destination), "--run", "--json")
            self.assertEqual(completed.returncode, 4)
            self.assertEqual(json.loads(completed.stdout)["status"], "capability_unavailable")
            self.assertFalse(destination.exists())

    def test_create_refuses_file_destination_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "a-file"
            destination.write_text("keep", encoding="utf-8")
            completed = invoke("create", "Nope", "-d", str(destination), "--json")
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "destination_not_empty")
            self.assertEqual(destination.read_text(), "keep")

    def test_project_name_is_escaped_for_code_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "quoted"
            completed = invoke("create", 'A "Quoted" App', "-d", str(destination), "--json")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads((destination / "vellum.lock.json").read_text())["project"]["name"], 'A "Quoted" App')
            self.assertIn('title={"A \\"Quoted\\" App"}', (destination / "src/App.tsx").read_text())

    def test_json_mode_reports_argument_errors_as_json(self) -> None:
        completed = invoke("build", "--not-a-real-option", "--json")
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "invalid_arguments")

    def test_doctor_finds_lock_from_nested_directory_and_fix_is_project_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            self.assertEqual(invoke("create", "Nested", "-d", str(project)).returncode, 0)
            nested = project / "src" / "deep"
            nested.mkdir(parents=True)
            completed = invoke("doctor", "--fix", "--json", cwd=nested)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["data"]["project_root"], str(project.resolve()))
            self.assertTrue((project / ".vellum/cache").is_dir())
            self.assertTrue((project / ".vellum/state").is_dir())

    def test_backend_command_fails_honestly_with_stable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            self.assertEqual(invoke("create", "No Backend", "-d", str(project)).returncode, 0)
            completed = invoke("build", "--json", cwd=project)
            self.assertEqual(completed.returncode, 4)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                set(payload),
                {"schema", "cli_version", "command", "ok", "status", "message", "data", "diagnostics"},
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "capability_unavailable")

    def test_backend_protocol_receives_locked_project_and_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "app"
            self.assertEqual(invoke("create", "Backend", "-d", str(project)).returncode, 0)
            backend = root / "vellum-backend"
            backend.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "print(json.dumps({'ok': True, 'status': 'built', 'message': 'built by fixture', 'data': {'argv': sys.argv[1:]}, 'diagnostics': []}))\n",
                encoding="utf-8",
            )
            backend.chmod(0o755)
            completed = invoke(
                "build",
                "--target",
                "web",
                "--json",
                cwd=project / "src",
                env={"VELLUM_BACKEND": str(backend)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "built")
            self.assertEqual(
                payload["data"]["backend"]["data"]["argv"],
                [
                    "build", "--project", str(project.resolve()), "--json",
                    "--framework-version", "0.1.0", "--cli-api", "1",
                    "--target", "web",
                ],
            )

    def test_run_forwards_finite_no_window_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "app"
            self.assertEqual(invoke("create", "Backend", "-d", str(project)).returncode, 0)
            backend = root / "vellum-backend"
            backend.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "print(json.dumps({'schema':'vellum.backend.result.v1','ok':True,'status':'self_test_passed','message':'ok','data':{'argv':sys.argv[1:]},'diagnostics':[]}))\n",
                encoding="utf-8",
            )
            backend.chmod(0o755)
            completed = invoke(
                "run", "--self-test", "--no-window", "--no-build", "--json",
                cwd=project,
                env={"VELLUM_BACKEND": str(backend)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            forwarded = json.loads(completed.stdout)["data"]["backend"]["data"]["argv"]
            self.assertEqual(forwarded[-3:], ["--no-build", "--self-test", "--no-window"])

    def test_invalid_lock_fails_before_backend_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "vellum.lock.json").write_text('{"schema":"wrong"}\n', encoding="utf-8")
            completed = invoke("--json", "test", cwd=project)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "invalid_project_lock")

    def test_installed_sdk_metadata_enforces_exact_framework_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "app"
            self.assertEqual(invoke("create", "Pinned", "-d", str(project)).returncode, 0)
            sdk = root / "sdk"
            sdk.mkdir()
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.2.0",
                "cli_version": "0.2.0",
                "cli_api": 1,
                "source_commit": "a" * 40,
                "target": "test-host",
                "capabilities": {
                    "authoring_cli": True,
                    "cmake_sdk": True,
                    "gpu_renderer": False,
                    "commands": {
                        "import": True,
                        "reimport": True,
                        "build": False,
                        "run": False,
                        "test": False,
                        "capture": False,
                        "package": False,
                    },
                },
            }), encoding="utf-8")
            (sdk / "install-manifest.json").write_text(json.dumps({
                "schema": "vellum.sdk-install.v1",
                "verified": True,
                "artifact": "vellum-sdk-0.2.0-test-host.tar.gz",
                "artifact_sha256": "b" * 64,
                "framework_version": "0.2.0",
                "target": "test-host",
                "source_commit": "a" * 40,
            }), encoding="utf-8")
            completed = invoke("build", "--json", cwd=project, env={"VELLUM_SDK_ROOT": str(sdk)})
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "sdk_version_mismatch")

    def test_project_pins_and_enforces_exact_installed_artifact_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            sdk.mkdir()
            capabilities = {
                "authoring_cli": True,
                "cmake_sdk": True,
                "gpu_renderer": False,
                "commands": {
                    "import": True, "reimport": True, "build": False,
                    "run": False, "test": False, "capture": False, "package": False,
                },
            }
            metadata = {
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.1.0",
                "cli_version": "0.1.0-dev",
                "cli_api": 1,
                "source_commit": "a" * 40,
                "target": "test-host",
                "capabilities": capabilities,
            }
            manifest = {
                "schema": "vellum.sdk-install.v1",
                "verified": True,
                "artifact": "vellum-sdk-0.1.0-test-host.tar.gz",
                "artifact_sha256": "b" * 64,
                "framework_version": "0.1.0",
                "target": "test-host",
                "source_commit": "a" * 40,
            }
            (sdk / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            manifest_path = sdk / "install-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            project = root / "app"
            installed_env = {"VELLUM_SDK_ROOT": str(sdk)}
            created = invoke("create", "Pinned artifact", "-d", str(project), "--json", env=installed_env)
            self.assertEqual(created.returncode, 0, created.stderr)
            lock = json.loads((project / "vellum.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["artifact"]["sha256"], "b" * 64)
            self.assertTrue(lock["framework"]["artifact"]["verified"])

            manifest["artifact_sha256"] = "c" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            mismatch = invoke("build", "--json", cwd=project, env=installed_env)
            self.assertEqual(mismatch.returncode, 3)
            self.assertEqual(json.loads(mismatch.stdout)["status"], "sdk_artifact_mismatch")

    def test_cli_api_mismatch_requires_migration_but_framework_patch_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            self.assertEqual(invoke("create", "Compatibility", "-d", str(project)).returncode, 0)
            lock_path = project / "vellum.lock.json"
            lock = json.loads(lock_path.read_text())
            lock["framework"]["version"] = "0.1.1"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            compatible = invoke("build", "--json", cwd=project)
            self.assertEqual(compatible.returncode, 4)
            self.assertEqual(json.loads(compatible.stdout)["status"], "capability_unavailable")

            lock["cli"]["api"] = 2
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            incompatible = invoke("build", "--json", cwd=project)
            self.assertEqual(incompatible.returncode, 3)
            self.assertEqual(json.loads(incompatible.stdout)["status"], "cli_api_mismatch")


if __name__ == "__main__":
    unittest.main()
