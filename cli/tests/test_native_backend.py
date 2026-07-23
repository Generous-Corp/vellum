from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli/vellum_cli.py"
BACKEND = REPO / "cli/vellum_native_backend.py"
sys.path.insert(0, str(REPO / "cli"))
try:
    BACKEND_MODULE = runpy.run_path(str(BACKEND))
finally:
    sys.path.pop(0)
capture_matrix = BACKEND_MODULE["capture_matrix"]
scenario_arguments = BACKEND_MODULE["scenario_arguments"]
validate_component_source = BACKEND_MODULE["validate_component_source"]
build_component_modules = BACKEND_MODULE["build_component_modules"]
component_sdk_root = BACKEND_MODULE["component_sdk_root"]
build_app = BACKEND_MODULE["build_app"]
BackendFailure = BACKEND_MODULE["BackendFailure"]
command_result = BACKEND_MODULE["command_result"]


def run(arguments: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def fake_gpu_sdk(root: Path) -> Path:
    sdk = root / "sdk"
    sdk.mkdir()
    (sdk / "metadata.json").write_text(json.dumps({
        "schema": "vellum.sdk-artifact.v1",
        "target": "darwin-arm64",
        "capabilities": {"gpu_renderer": True, "custom_components": True},
    }), encoding="utf-8")
    return sdk


def fake_build_sdk(root: Path) -> Path:
    sdk = fake_gpu_sdk(root)
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("the native backend fixture requires Node")
    sdk_node = sdk / "node/bin/node"
    sdk_node.parent.mkdir(parents=True)
    sdk_node.write_text(
        f"#!/bin/sh\nexec {shlex.quote(node)} \"$@\"\n",
        encoding="utf-8",
    )
    sdk_node.chmod(0o755)
    host = sdk / "sdk/bin/vellum-app-host"
    host.parent.mkdir(parents=True)
    host.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    host.chmod(0o755)
    libraries = sdk / "sdk/lib"
    libraries.mkdir(parents=True)
    for name in ("libvellum-authoring.dylib", "libvellum-gpu.dylib"):
        (libraries / name).write_bytes(b"test")
    bundler = sdk / "ui/scripts/build-project.mjs"
    bundler.parent.mkdir(parents=True)
    bundler.write_text(
        "import { copyFileSync, writeFileSync } from 'node:fs';\n"
        "copyFileSync(process.argv[2], process.argv[3]);\n"
        "writeFileSync(process.argv[3] + '.map', "
        "\"{\\\"version\\\":3,\\\"sources\\\":[\\\"vellum://app/src/main.tsx\\\"],"
        "\\\"sourcesContent\\\":[\\\"\\\"],\\\"names\\\":[],\\\"mappings\\\":\\\"\\\"}\\n\");\n",
        encoding="utf-8",
    )
    return sdk


class NativeBackendTests(unittest.TestCase):
    def create_project(self, root: Path) -> Path:
        project = root / "application"
        completed = run([sys.executable, str(CLI), "create", "Native Test", "-d", str(project), "--json"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return project

    def test_unsupported_target_fails_with_stable_backend_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            completed = run([
                sys.executable, str(BACKEND), "build", "--project", str(project),
                "--json", "--target", "web",
            ], env={"VELLUM_SDK_ROOT": str(fake_gpu_sdk(root))})
            self.assertEqual(completed.returncode, 4)
            payload = json.loads(completed.stdout)
            self.assertEqual(set(payload), {
                "schema", "command", "ok", "status", "message", "data", "diagnostics"
            })
            self.assertEqual(payload["schema"], "vellum.backend.result.v1")
            self.assertEqual(payload["status"], "unsupported_target")
            self.assertFalse(payload["ok"])

    def test_dev_reload_gracefully_quits_then_relaunches_with_honest_continuity(self) -> None:
        app = {"app": Path("/tmp/reload-proof.app")}
        context = {
            "application_id": "dev.vellum.reload-proof",
            "capabilities": {"persistence": "state-v1"},
        }
        args = SimpleNamespace(
            target="macos", no_build=True, self_test=False,
            no_window=False, dev_reload=True,
        )
        completed = subprocess.CompletedProcess(["osascript"], 0, "", "")
        with (
            mock.patch.dict(
                command_result.__globals__,
                {"ensure_app": mock.Mock(return_value=app)},
            ),
            mock.patch.object(
                command_result.__globals__["subprocess"], "run",
                return_value=completed,
            ) as stop,
            mock.patch.dict(
                command_result.__globals__,
                {"run_checked": mock.Mock(return_value=completed)},
            ),
        ):
            payload = command_result("run", args, context, Path("/tmp/sdk"))
        self.assertEqual(payload["status"], "reloaded")
        self.assertEqual(payload["data"]["continuity"], "persisted-state-v1")
        self.assertIn('application id "dev.vellum.reload-proof"', stop.call_args.args[0][2])

    def test_entry_cannot_escape_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            manifest = (project / "app.toml").read_text(encoding="utf-8")
            manifest = manifest.replace('entry = "src/main.tsx"', 'entry = "../outside.tsx"')
            (project / "app.toml").write_text(manifest, encoding="utf-8")
            completed = run([
                sys.executable, str(BACKEND), "build", "--project", str(project), "--json",
            ], env={"VELLUM_SDK_ROOT": str(fake_gpu_sdk(root))})
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(json.loads(completed.stdout)["status"], "invalid_app_manifest")

    def test_argument_errors_remain_json(self) -> None:
        completed = run([sys.executable, str(BACKEND), "build", "--json"])
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "vellum.backend.result.v1")
        self.assertEqual(payload["status"], "invalid_arguments")

    def test_capture_matrix_is_versioned_bounded_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            parsed = capture_matrix({"root": project}, "tests/capture-matrix.json")
            self.assertEqual(parsed["captures"], [{"name": "home", "scenario": "smoke"}])
            self.assertEqual(parsed["columns"], 1)
            self.assertEqual(parsed["background"], (24, 26, 32, 255))

            matrix_path = project / "tests/capture-matrix.json"
            value = json.loads(matrix_path.read_text(encoding="utf-8"))
            value["captures"].append({"name": "home", "scenario": "smoke"})
            matrix_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(BackendFailure, "duplicated"):
                capture_matrix({"root": project}, "tests/capture-matrix.json")

    def test_semantic_input_and_key_steps_are_ordered_bounded_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            scenario_path = project / "tests/scenarios/editor.json"
            scenario_path.write_text(json.dumps({
                "schema": "vellum.scenario.v1",
                "name": "editor",
                "viewport": {"width": 640, "height": 400},
                "steps": [
                    {"action": "press", "target": "open-editor"},
                    {"action": "input", "target": "title-input", "text": "Roadmap 🧭"},
                    {"action": "key", "target": "title-input", "key": "Enter"},
                    {"action": "capture", "name": "saved"},
                ],
            }), encoding="utf-8")
            arguments, name = scenario_arguments({"root": project}, "editor")
            self.assertEqual(name, "editor")
            self.assertEqual(arguments, [
                "--expect-width", "640", "--expect-height", "400",
                "--press", "open-editor",
                "--input", "title-input", "Roadmap 🧭",
                "--key", "title-input", "Enter",
            ])

            invalid_steps = [
                {"action": "input", "target": "title-input", "text": "x", "mode": "append"},
                {"action": "input", "target": "title-input", "text": "x" * (64 * 1024 + 1)},
                {"action": "key", "target": "title-input", "key": "Meta+S"},
                {"action": "key", "target": "title-input", "key": ["Enter"]},
            ]
            for step in invalid_steps:
                value = json.loads(scenario_path.read_text(encoding="utf-8"))
                value["steps"] = [step]
                scenario_path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(step=step["action"]), self.assertRaises(BackendFailure) as caught:
                    scenario_arguments({"root": project}, "editor")
                self.assertEqual(caught.exception.status, "invalid_scenario")

    def test_state_v1_persistence_is_explicit_and_other_values_fail_closed(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            manifest_path = project / "app.toml"
            original = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                original.replace('persistence = "denied"', 'persistence = "state-v1"'),
                encoding="utf-8",
            )
            sdk = fake_build_sdk(root)
            accepted = run([
                sys.executable, str(BACKEND), "build", "--project", str(project), "--json",
            ], env={"VELLUM_SDK_ROOT": str(sdk)})
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            payload = json.loads(accepted.stdout)
            self.assertEqual(payload["status"], "built")
            plist_path = Path(payload["data"]["app"]) / "Contents/Info.plist"
            with plist_path.open("rb") as handle:
                self.assertEqual(plistlib.load(handle)["VellumPersistence"], "state-v1")

            manifest_path.write_text(
                original.replace('persistence = "denied"', 'persistence = "arbitrary"'),
                encoding="utf-8",
            )
            rejected = run([
                sys.executable, str(BACKEND), "build", "--project", str(project), "--json",
            ], env={"VELLUM_SDK_ROOT": str(sdk)})
            self.assertEqual(json.loads(rejected.stdout)["status"], "invalid_app_manifest")

    def test_component_manifest_is_declared_and_private_framework_headers_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            source = project / "native/level-meter.cpp"
            source.write_text('#include <vellum/components/abi.h>\n', encoding="utf-8")
            (project / "native/components.toml").write_text(
                '[manifest]\n'
                'schema = "vellum.components.v1"\n'
                'components = ["level-meter"]\n\n'
                '[component.level-meter]\n'
                'native_source = "native/level-meter.cpp"\n'
                'web = "fallback"\n',
                encoding="utf-8",
            )
            context = BACKEND_MODULE["project_context"](str(project))
            self.assertEqual(context["components"], [{
                "id": "level-meter", "native_source": "native/level-meter.cpp",
                "web": "fallback", "wasm_source": None,
            }])
            validate_component_source(source)
            doctor = run([
                sys.executable, str(CLI), "doctor", "--project", str(project), "--json",
            ], env={"VELLUM_SDK_ROOT": ""})
            doctor_payload = json.loads(doctor.stdout)
            component_check = next(
                item for item in doctor_payload["data"]["checks"]
                if item["name"] == "custom-components"
            )
            self.assertTrue(component_check["required"])
            self.assertFalse(component_check["available"])

            source.write_text('#include <vellum/graphics/scene.hpp>\n', encoding="utf-8")
            with self.assertRaisesRegex(BackendFailure, "public vellum/components/abi.h"):
                validate_component_source(source)

            (project / "native/components.toml").write_text(
                '[manifest]\nschema = "vellum.components.v1"\ncomponents = []\n\n'
                '[component.undeclared]\nnative_source = "native/level-meter.cpp"\nweb = "fallback"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BackendFailure, "differ.*from the declaration"):
                BACKEND_MODULE["project_context"](str(project))

    def test_component_build_selects_macos_sdk_and_uses_relocatable_install_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            source = project / "native/gamut-field.cpp"
            source.write_text('#include <vellum/components/abi.h>\n', encoding="utf-8")
            (project / "native/components.toml").write_text(
                '[manifest]\n'
                'schema = "vellum.components.v1"\n'
                'components = ["gamut-field"]\n\n'
                '[component.gamut-field]\n'
                'native_source = "native/gamut-field.cpp"\n'
                'web = "fallback"\n',
                encoding="utf-8",
            )
            sdk = fake_gpu_sdk(root)
            abi = sdk / "sdk/include/vellum/components/abi.h"
            abi.parent.mkdir(parents=True)
            abi.write_text("/* public ABI */\n", encoding="utf-8")
            context = BACKEND_MODULE["project_context"](str(project))
            selected_sdk = root / "MacOSX.sdk"
            selected_sdk.mkdir()
            commands: list[tuple[list[str], Path | None]] = []

            def record_command(
                arguments: list[str], *, cwd: Path | None = None,
            ) -> subprocess.CompletedProcess[str]:
                commands.append((arguments, cwd))
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with mock.patch.dict(build_component_modules.__globals__, {
                "component_compiler": lambda: "/toolchain/usr/bin/clang++",
                "component_sdk_root": lambda: selected_sdk,
                "run_checked": record_command,
            }):
                modules = build_component_modules(
                    context, sdk, root / "VellumComponents",
                )

            self.assertEqual(modules, [{
                "id": "gamut-field",
                "path": str(root / "VellumComponents/gamut-field.dylib"),
            }])
            self.assertEqual(len(commands), 1)
            arguments, cwd = commands[0]
            self.assertEqual(cwd, project.resolve())
            self.assertEqual(
                arguments[arguments.index("-isysroot"):arguments.index("-isysroot") + 2],
                ["-isysroot", str(selected_sdk)],
            )
            self.assertIn("-mmacosx-version-min=15.0", arguments)
            self.assertIn("-Wl,-install_name,@rpath/gamut-field.dylib", arguments)

    def test_native_build_bundles_the_declared_non_main_import_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            (project / "design").mkdir(exist_ok=True)
            (project / "design/import.lock.json").write_text(json.dumps({
                "graphVersion": 1,
                "schema": "vellum.design-import-lock.v1",
                "sources": {
                    "shell": {
                        "activeRevision": "revision-a",
                    },
                },
            }), encoding="utf-8")
            generated = project / "ui/generated"
            generated.mkdir(parents=True, exist_ok=True)
            materialized = generated / "shell.materialized.json"
            materialized.write_text('{"root":{}}\n', encoding="utf-8")
            (generated / "shell.bindings.json").write_text(
                '{"bindings":[]}\n', encoding="utf-8",
            )
            sdk = fake_build_sdk(root)
            context = BACKEND_MODULE["project_context"](str(project))
            commands: list[list[str]] = []

            def record_command(
                arguments: list[str], *, cwd: Path | None = None,
            ) -> subprocess.CompletedProcess[str]:
                commands.append(arguments)
                bundle = Path(arguments[3])
                bundle.write_text("void 0;\n", encoding="utf-8")
                bundle.with_suffix(f"{bundle.suffix}.map").write_text(
                    '{"version":3}\n', encoding="utf-8",
                )
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with mock.patch.dict(build_app.__globals__, {"run_checked": record_command}):
                build_app(context, sdk)

            self.assertEqual(len(commands), 1)
            self.assertEqual(Path(commands[0][-1]), materialized.resolve())

    def test_component_sdk_root_uses_selected_xcode_macos_sdk_not_sdkroot_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            selected_sdk = Path(temporary) / "MacOSX.sdk"
            selected_sdk.mkdir()
            commands: list[list[str]] = []

            def fake_run_checked(
                arguments: list[str], *, cwd: Path | None = None,
            ) -> subprocess.CompletedProcess[str]:
                commands.append(arguments)
                return subprocess.CompletedProcess(
                    arguments, 0, f"{selected_sdk}\n", "",
                )

            with (
                mock.patch.dict(component_sdk_root.__globals__, {
                    "run_checked": fake_run_checked,
                }),
                mock.patch("shutil.which", return_value="/usr/bin/xcrun"),
                mock.patch.dict(os.environ, {"SDKROOT": "/caller/must/not/set-this"}),
            ):
                self.assertEqual(component_sdk_root(), selected_sdk.resolve())

            self.assertEqual(commands, [[
                "/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path",
            ]])

    @unittest.skipUnless(sys.platform == "darwin", "requires the macOS toolchain")
    def test_component_builds_without_sdkroot_and_has_relocatable_macho_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create_project(root)
            source = project / "native/gamut-field.cpp"
            source.write_text('#include <vellum/components/abi.h>\n', encoding="utf-8")
            (project / "native/components.toml").write_text(
                '[manifest]\n'
                'schema = "vellum.components.v1"\n'
                'components = ["gamut-field"]\n\n'
                '[component.gamut-field]\n'
                'native_source = "native/gamut-field.cpp"\n'
                'web = "fallback"\n',
                encoding="utf-8",
            )
            sdk = fake_gpu_sdk(root)
            abi = sdk / "sdk/include/vellum/components/abi.h"
            abi.parent.mkdir(parents=True)
            shutil.copy2(
                REPO / "components/include/vellum/components/abi.h", abi,
            )
            context = BACKEND_MODULE["project_context"](str(project))
            environment = dict(os.environ)
            environment.pop("SDKROOT", None)

            with mock.patch.dict(os.environ, environment, clear=True):
                modules = build_component_modules(
                    context, sdk, root / "VellumComponents",
                )

            output = Path(modules[0]["path"])
            self.assertTrue(output.is_file())
            identity = subprocess.run(
                ["otool", "-D", str(output)],
                text=True, capture_output=True, check=True,
            ).stdout.splitlines()
            self.assertIn("@rpath/gamut-field.dylib", identity)
            load_commands = subprocess.run(
                ["otool", "-l", str(output)],
                text=True, capture_output=True, check=True,
            ).stdout
            self.assertRegex(load_commands, r"(?m)^\s+minos 15\.0$")


if __name__ == "__main__":
    unittest.main()
