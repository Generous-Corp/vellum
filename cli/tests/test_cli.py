from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
sys.path.insert(0, str(CLI.parent))
try:
    import vellum_cli as cli_module
finally:
    sys.path.pop(0)


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
    def test_version_matches_the_immutable_framework_release(self) -> None:
        completed = invoke("--version")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "vellum 0.1.7")

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
                Path("app.toml"),
                Path("framework.lock"),
                Path("package-lock.json"),
                Path("AGENTS.md"),
                Path(".vellum/agent-instructions.md"),
                Path("sources/imported/README.md"),
                Path("design/ir/app.designir.json"),
                Path("ui/generated/Home.generated.tsx"),
                Path("src/App.tsx"),
                Path("src/main.tsx"),
                Path("native/README.md"),
                Path("tests/scenarios/smoke.json"),
                Path("tests/capture-matrix.json"),
                Path("native/components.toml"),
            }
            self.assertTrue(required.issubset(set(first_files)))
            self.assertNotIn(
                "npm ci",
                (first / "README.md").read_text(encoding="utf-8"),
            )
            lock = json.loads((first / "framework.lock").read_text())
            self.assertEqual(lock["project"]["id"], hashlib.sha256(b"vellum-project-v1:example-app").hexdigest()[:24])
            self.assertEqual(lock["framework"]["version"], "0.1.7")
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

    def test_create_run_launches_after_build_and_test_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            sdk.mkdir()
            commands = {
                "import": False, "reimport": False, "build": True, "run": True,
                "test": True, "capture": False, "package": False,
            }
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.1.7",
                "cli_version": "0.1.7",
                "cli_api": 1,
                "source_commit": None,
                "target": "test-host",
                "capabilities": {
                    "authoring_cli": True,
                    "cmake_sdk": False,
                    "gpu_renderer": False,
                    "custom_components": False,
                    "commands": commands,
                    "targets": {
                        "macos": {
                            "commands": {
                                "build": True,
                                "run": True,
                                "test": True,
                            },
                        },
                    },
                },
                "files": [],
            }), encoding="utf-8")
            (sdk / "install-manifest.json").write_text(json.dumps({
                "schema": "vellum.sdk-install.v1",
                "verified": False,
                "artifact": None,
                "artifact_sha256": None,
                "framework_version": "0.1.7",
                "target": "test-host",
                "source_commit": None,
            }), encoding="utf-8")
            invocations = root / "invocations.jsonl"
            backend = root / "backend"
            backend.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['VELLUM_TEST_INVOCATIONS'], 'a', encoding='utf-8') as output:\n"
                "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "command = sys.argv[1]\n"
                "status = {'build':'built','test':'tests_passed','run':'launched'}[command]\n"
                "print(json.dumps({'schema':'vellum.backend.result.v1','ok':True,'status':status,'message':'ok','data':{},'diagnostics':[]}))\n",
                encoding="utf-8",
            )
            backend.chmod(0o755)
            destination = root / "app"
            completed = invoke(
                "create", "Launchable", "-d", str(destination), "--run", "--json",
                env={
                    "VELLUM_SDK_ROOT": str(sdk),
                    "VELLUM_BACKEND": str(backend),
                    "VELLUM_TEST_INVOCATIONS": str(invocations),
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = [
                json.loads(line)
                for line in invocations.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([call[0] for call in calls], ["build", "test", "run"])
            self.assertIn("--no-build", calls[-1])
            self.assertNotIn("--no-window", calls[-1])
            validation = json.loads(completed.stdout)["data"]["validation"]
            self.assertEqual(validation["commands"][-1], {
                "command": "run",
                "status": "launched",
            })

    def test_create_from_requires_import_capability_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "frame.json"
            source.write_text("{}\n", encoding="utf-8")
            destination = root / "app"
            completed = invoke(
                "create", "Imported", "-d", str(destination),
                "--from", "figma", str(source), "--json",
            )
            self.assertEqual(completed.returncode, 4)
            self.assertEqual(json.loads(completed.stdout)["status"], "capability_unavailable")
            self.assertFalse(destination.exists())

    def test_create_from_composes_initial_import_with_permanent_source_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            sdk.mkdir()
            commands = {
                "import": True, "reimport": True, "build": False, "run": False,
                "test": False, "capture": False, "package": False,
            }
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1", "framework_version": "0.1.7",
                "cli_version": "0.1.7", "cli_api": 1, "source_commit": None,
                "target": "local-development",
                "capabilities": {"authoring_cli": True, "cmake_sdk": False, "gpu_renderer": False, "custom_components": False, "commands": commands},
                "files": [],
            }), encoding="utf-8")
            (sdk / "install-manifest.json").write_text(json.dumps({
                "schema": "vellum.sdk-install.v1", "verified": False,
                "artifact": None, "artifact_sha256": None, "framework_version": "0.1.7",
                "target": "local-development", "source_commit": None,
            }), encoding="utf-8")
            backend = root / "backend"
            backend.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "project = Path(args[args.index('--project') + 1])\n"
                "design = json.loads((project / 'design/ir/app.designir.json').read_text())\n"
                "design['root'] = {'properties': {'layout': {'width': 420, 'height': 240}}}\n"
                "(project / 'design/ir/app.designir.json').write_text(json.dumps(design))\n"
                "print(json.dumps({'schema':'vellum.backend.result.v1','ok':True,'status':'imported','message':'ok','data':{'argv':sys.argv[1:]},'diagnostics':[]}))\n",
                encoding="utf-8",
            )
            backend.chmod(0o755)
            source = root / "frame.json"
            source.write_text("{}\n", encoding="utf-8")
            destination = root / "app"
            completed = invoke(
                "create", "Imported", "-d", str(destination), "--no-verify",
                "--from", "figma", str(source), "--as", "shell", "--json",
                env={"VELLUM_SDK_ROOT": str(sdk), "VELLUM_BACKEND": str(backend)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["data"]["template"], "imported-app")
            self.assertIsNone(payload["data"]["template_requested"])
            self.assertEqual(payload["data"]["validation"]["commands"], [
                {"command": "import", "status": "imported"}
            ])
            smoke = json.loads(
                (destination / "tests/scenarios/smoke.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(smoke["steps"], [
                {"action": "wait-for-idle"},
                {"action": "capture", "name": "imported-design"},
            ])
            self.assertEqual(smoke["viewport"], {"width": 420, "height": 240})

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
            self.assertEqual(json.loads((destination / "framework.lock").read_text())["project"]["name"], 'A "Quoted" App')
            self.assertIn('name = "A \\"Quoted\\" App"', (destination / "app.toml").read_text())
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

    def test_doctor_requires_a_compiler_for_declared_custom_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            self.assertEqual(
                invoke("create", "Component Doctor", "-d", str(project)).returncode,
                0,
            )
            (project / "native/components.toml").write_text(
                '[manifest]\n'
                'schema = "vellum.components.v1"\n'
                'components = ["gamut-field"]\n\n'
                '[component.gamut-field]\n'
                'native_source = "native/gamut-field.cpp"\n'
                'web = "fallback"\n',
                encoding="utf-8",
            )
            (project / "native/gamut-field.cpp").write_text(
                '#include <vellum/components/abi.h>\n',
                encoding="utf-8",
            )
            completed = invoke(
                "doctor", "--project", str(project), "--json",
                env={"PATH": "/path-that-does-not-exist"},
            )
            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            compiler = next(
                item
                for item in payload["data"]["checks"]
                if item["name"] == "custom-component-compiler"
            )
            self.assertTrue(compiler["required"])
            self.assertFalse(compiler["available"])
            self.assertIn("xcode-select --install", compiler["fix"])

    def test_doctor_required_target_fails_instead_of_reporting_cli_only_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            self.assertEqual(
                invoke("create", "Target Doctor", "-d", str(project)).returncode,
                0,
            )
            completed = invoke(
                "doctor", "--project", str(project),
                "--require-target", "macos", "--json",
                env={"VELLUM_SDK_ROOT": ""},
            )
            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "needs_attention")
            target = next(
                item
                for item in payload["data"]["checks"]
                if item["name"] == "target-macos"
            )
            self.assertTrue(target["required"])
            self.assertFalse(target["available"])

    def test_doctor_requires_chrome_only_for_requested_web_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            sdk.mkdir()
            native_commands = {
                name: True
                for name in ("build", "run", "test", "capture", "package")
            }
            web_commands = {
                **native_commands,
                "capture": False,
            }
            all_commands = {
                "import": True,
                "reimport": True,
                **native_commands,
            }
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.1.7",
                "cli_version": "0.1.7",
                "cli_api": 1,
                "source_commit": None,
                "target": "dual-target-test",
                "capabilities": {
                    "authoring_cli": True,
                    "cmake_sdk": True,
                    "gpu_renderer": True,
                    "custom_components": False,
                    "commands": all_commands,
                    "targets": {
                        "macos": {"commands": native_commands},
                        "web": {"commands": web_commands},
                    },
                },
                "files": [],
            }), encoding="utf-8")
            (sdk / "install-manifest.json").write_text(json.dumps({
                "schema": "vellum.sdk-install.v1",
                "verified": False,
                "artifact": None,
                "artifact_sha256": None,
                "framework_version": "0.1.7",
                "target": "dual-target-test",
                "source_commit": None,
            }), encoding="utf-8")
            node = sdk / "node/bin/node"
            node.parent.mkdir(parents=True)
            node.write_text("#!/bin/sh\nprintf 'v20.0.0\\n'\n", encoding="utf-8")
            node.chmod(0o755)
            backend = sdk / "bin/vellum-backend"
            backend.parent.mkdir()
            backend.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            backend.chmod(0o755)

            project = root / "app"
            installed_environment = {"VELLUM_SDK_ROOT": str(sdk)}
            created = invoke(
                "create", "Dual Target", "--directory", str(project),
                "--no-verify", "--json", env=installed_environment,
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            def run_doctor(target: str) -> dict[str, object]:
                arguments = cli_module.parser().parse_args([
                    "doctor", "--project", str(project),
                    "--require-target", target,
                ])
                original_is_file = Path.is_file
                original_which = shutil.which
                with (
                    mock.patch.dict(
                        os.environ,
                        {"VELLUM_SDK_ROOT": str(sdk)},
                        clear=False,
                    ),
                    mock.patch.object(
                        cli_module,
                        "component_compiler_status",
                        return_value=(False, "not installed"),
                    ),
                    mock.patch.object(
                        cli_module.Path,
                        "is_file",
                        autospec=True,
                        side_effect=lambda path: (
                            False
                            if str(path)
                            == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                            else original_is_file(path)
                        ),
                    ),
                    mock.patch.object(
                        cli_module.shutil,
                        "which",
                        side_effect=lambda name: (
                            None if name == "google-chrome" else original_which(name)
                        ),
                    ),
                ):
                    return cli_module.doctor(arguments)

            native = run_doctor("macos")
            native_chrome = next(
                item for item in native["data"]["checks"]
                if item["name"] == "chrome"
            )
            self.assertTrue(native["ok"])
            self.assertFalse(native_chrome["required"])
            self.assertFalse(native_chrome["available"])

            web = run_doctor("web")
            web_chrome = next(
                item for item in web["data"]["checks"]
                if item["name"] == "chrome"
            )
            self.assertFalse(web["ok"])
            self.assertTrue(web_chrome["required"])
            self.assertFalse(web_chrome["available"])

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
                    "--framework-version", "0.1.7", "--cli-api", "1",
                    "--target", "web",
                ],
            )

    def test_design_check_and_diff_use_stable_nested_cli_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "app"
            self.assertEqual(invoke("create", "Design", "-d", str(project)).returncode, 0)
            backend = root / "vellum-backend"
            backend.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "operation = sys.argv[1]\n"
                "status = 'design_clean' if operation == 'design-check' else 'design_diff'\n"
                "print(json.dumps({'schema':'vellum.backend.result.v1','ok':True,'status':status,'message':'ok','data':{'argv':sys.argv[1:]},'diagnostics':[]}))\n",
                encoding="utf-8",
            )
            backend.chmod(0o755)
            for operation, expected_backend, expected_status in (
                ("check", "design-check", "design_clean"),
                ("diff", "design-diff", "design_diff"),
            ):
                completed = invoke(
                    "design", operation, "--as", "shell", "--json",
                    cwd=project / "src",
                    env={"VELLUM_BACKEND": str(backend)},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["schema"], "vellum.cli.result.v1")
                self.assertEqual(payload["command"], "design")
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(payload["data"]["operation"], operation)
                self.assertEqual(
                    payload["data"]["backend"]["data"]["argv"],
                    [
                        expected_backend, "--project", str(project.resolve()), "--json",
                        "--framework-version", "0.1.7", "--cli-api", "1",
                        "--as", "shell",
                    ],
                )

    def test_design_check_validates_project_lock_before_backend_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "framework.lock").write_text(
                '{"schema":"wrong"}\n',
                encoding="utf-8",
            )
            completed = invoke("design", "check", "--json", cwd=project)
            self.assertEqual(completed.returncode, 3)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "design")
            self.assertEqual(payload["status"], "invalid_project_lock")

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
            (project / "framework.lock").write_text('{"schema":"wrong"}\n', encoding="utf-8")
            completed = invoke("--json", "test", cwd=project)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "invalid_project_lock")

    def test_app_manifest_and_npm_lock_are_fail_closed_authorities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            self.assertEqual(invoke("create", "Authority", "-d", str(project)).returncode, 0)

            manifest_path = project / "app.toml"
            original_manifest = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                original_manifest.replace('entry = "src/main.tsx"', 'entry = "../escape.tsx"'),
                encoding="utf-8",
            )
            invalid_manifest = invoke("build", "--json", cwd=project)
            self.assertEqual(invalid_manifest.returncode, 3)
            self.assertEqual(json.loads(invalid_manifest.stdout)["status"], "invalid_app_manifest")

            manifest_path.write_text(original_manifest, encoding="utf-8")
            package_lock_path = project / "package-lock.json"
            package_lock = json.loads(package_lock_path.read_text(encoding="utf-8"))
            package_lock["packages"][".vellum/packages/vellum-ui"]["version"] = "9.9.9"
            package_lock_path.write_text(json.dumps(package_lock), encoding="utf-8")
            invalid_package_lock = invoke("build", "--json", cwd=project)
            self.assertEqual(invalid_package_lock.returncode, 3)
            self.assertEqual(json.loads(invalid_package_lock.stdout)["status"], "invalid_package_lock")

            # Application dependencies remain developer-owned. The framework
            # validates agreement with the npm lock without claiming exclusive
            # ownership of the dependency graph.
            package = json.loads((project / "package.json").read_text(encoding="utf-8"))
            package["dependencies"]["example-app-library"] = "1.2.3"
            (project / "package.json").write_text(json.dumps(package), encoding="utf-8")
            package_lock["packages"][".vellum/packages/vellum-ui"]["version"] = "0.1.0-experimental.0"
            package_lock["packages"][""]["dependencies"]["example-app-library"] = "1.2.3"
            package_lock["packages"]["node_modules/example-app-library"] = {
                "version": "1.2.3", "resolved": "https://example.invalid/example-app-library.tgz"
            }
            package_lock_path.write_text(json.dumps(package_lock), encoding="utf-8")
            developer_dependency = invoke("build", "--json", cwd=project)
            self.assertEqual(developer_dependency.returncode, 4)
            self.assertEqual(json.loads(developer_dependency.stdout)["status"], "capability_unavailable")

    def test_installed_ui_projection_satisfies_exact_npm_lock(self) -> None:
        npm = shutil.which("npm")
        if not npm:
            self.skipTest("npm is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            sdk.mkdir()
            shutil.copytree(REPO / "packages/vellum-ui", sdk / "ui")
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.1.7",
                "cli_version": "0.1.7",
                "cli_api": 1,
                "source_commit": None,
                "target": "local-development",
                "capabilities": {
                    "authoring_cli": True, "cmake_sdk": False, "gpu_renderer": False,
                    "custom_components": False,
                    "commands": {name: False for name in (
                        "import", "reimport", "build", "run", "test", "capture", "package"
                    )},
                },
                "files": [],
            }), encoding="utf-8")
            (sdk / "install-manifest.json").write_text(json.dumps({
                "schema": "vellum.sdk-install.v1", "verified": False,
                "artifact": None, "artifact_sha256": None,
                "framework_version": "0.1.7", "target": "local-development",
                "source_commit": None,
            }), encoding="utf-8")
            project = root / "app"
            created = invoke(
                "create", "Npm Consumer", "-d", str(project), "--json",
                env={"VELLUM_SDK_ROOT": str(sdk)},
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            installed = subprocess.run(
                [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=project, text=True, capture_output=True, check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            package = json.loads((project / "node_modules/@vellum/ui/package.json").read_text())
            self.assertEqual(package["version"], "0.1.0-experimental.0")
            self.assertNotIn("dependencies", package)
            self.assertFalse((project / ".vellum/packages/vellum-ui/node_modules").exists())

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
                    "gpu_renderer": False, "custom_components": False,
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
                "gpu_renderer": False, "custom_components": False,
                "commands": {
                    "import": True, "reimport": True, "build": False,
                    "run": False, "test": False, "capture": False, "package": False,
                },
            }
            metadata = {
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.1.7",
                "cli_version": "0.1.7",
                "cli_api": 1,
                "source_commit": "a" * 40,
                "target": "test-host",
                "capabilities": capabilities,
            }
            manifest = {
                "schema": "vellum.sdk-install.v1",
                "verified": True,
                "artifact": "vellum-sdk-0.1.7-test-host.tar.gz",
                "artifact_sha256": "b" * 64,
                "framework_version": "0.1.7",
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
            lock = json.loads((project / "framework.lock").read_text(encoding="utf-8"))
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
            lock_path = project / "framework.lock"
            lock = json.loads(lock_path.read_text())
            lock["framework"]["version"] = "0.1.7"
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
