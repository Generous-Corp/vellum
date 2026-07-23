from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest


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
BackendFailure = BACKEND_MODULE["BackendFailure"]


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
        "capabilities": {"gpu_renderer": True},
    }), encoding="utf-8")
    return sdk


def fake_build_sdk(root: Path) -> Path:
    sdk = fake_gpu_sdk(root)
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
        "import { copyFileSync } from 'node:fs';\n"
        "copyFileSync(process.argv[2], process.argv[3]);\n",
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
                original.replace('persistence = "none"', 'persistence = "state-v1"'),
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
                original.replace('persistence = "none"', 'persistence = "arbitrary"'),
                encoding="utf-8",
            )
            rejected = run([
                sys.executable, str(BACKEND), "build", "--project", str(project), "--json",
            ], env={"VELLUM_SDK_ROOT": str(sdk)})
            self.assertEqual(json.loads(rejected.stdout)["status"], "invalid_app_manifest")


if __name__ == "__main__":
    unittest.main()
