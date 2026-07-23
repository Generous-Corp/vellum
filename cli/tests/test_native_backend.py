from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
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


if __name__ == "__main__":
    unittest.main()
