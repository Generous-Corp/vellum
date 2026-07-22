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
                Path("native/README.md"),
                Path("tests/scenarios/smoke.json"),
                Path("packaging/vellum.package.json"),
            }
            self.assertTrue(required.issubset(set(first_files)))
            lock = json.loads((first / "vellum.lock.json").read_text())
            self.assertEqual(lock["project"]["id"], hashlib.sha256(b"vellum-project-v1:example-app").hexdigest()[:24])

    def test_create_refuses_nonempty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / "owned.txt").write_text("keep", encoding="utf-8")
            completed = invoke("--json", "create", "Nope", "--directory", str(destination))
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "destination_not_empty")
            self.assertEqual((destination / "owned.txt").read_text(), "keep")

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
                ["build", "--project", str(project.resolve()), "--json", "--target", "web"],
            )

    def test_invalid_lock_fails_before_backend_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "vellum.lock.json").write_text('{"schema":"wrong"}\n', encoding="utf-8")
            completed = invoke("--json", "test", cwd=project)
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(json.loads(completed.stdout)["status"], "invalid_project_lock")

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
