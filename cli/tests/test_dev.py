from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli/vellum_cli.py"
sys.path.insert(0, str(CLI.parent))
try:
    import vellum_dev
finally:
    sys.path.pop(0)


def invoke(*arguments: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=cwd, text=True, capture_output=True, check=False,
        env={**os.environ, **(env or {})},
    )


class DevLoopTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, dict[str, str], Path]:
        project = root / "app"
        created = invoke("create", "Reload Proof", "-d", str(project), "--json", cwd=REPO)
        self.assertEqual(created.returncode, 0, created.stderr)

        sdk = root / "sdk"
        sdk.mkdir()
        commands = {
            "import": False, "reimport": False, "build": True, "run": True,
            "test": True, "capture": False, "package": True,
        }
        (sdk / "metadata.json").write_text(json.dumps({
            "schema": "vellum.sdk-artifact.v1",
            "framework_version": "0.1.5",
            "cli_version": "0.1.5",
            "cli_api": 1,
            "source_commit": None,
            "target": "local-development",
            "capabilities": {
                "authoring_cli": True,
                "cmake_sdk": False,
                "gpu_renderer": False,
                "custom_components": False,
                "commands": commands,
                "targets": {
                    "macos": {"commands": commands},
                    "web": {"commands": commands},
                },
            },
        }), encoding="utf-8")
        (sdk / "install-manifest.json").write_text(json.dumps({
            "schema": "vellum.sdk-install.v1",
            "verified": False,
            "artifact": None,
            "artifact_sha256": None,
            "framework_version": "0.1.5",
            "target": "local-development",
            "source_commit": None,
        }), encoding="utf-8")

        calls = root / "backend-calls.jsonl"
        backend = root / "vellum-backend"
        backend.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "record = {'command': sys.argv[1], 'argv': sys.argv[2:]}\n"
            "with pathlib.Path(os.environ['VELLUM_DEV_TEST_CALLS']).open('a') as stream:\n"
            "    stream.write(json.dumps(record, sort_keys=True) + '\\n')\n"
            "status = 'built' if sys.argv[1] == 'build' else 'tests_passed'\n"
            "print(json.dumps({'schema':'vellum.backend.result.v1','ok':True,"
            "'status':status,'message':'fixture ok','data':{},'diagnostics':[]}))\n",
            encoding="utf-8",
        )
        backend.chmod(0o755)
        environment = {
            "VELLUM_SDK_ROOT": str(sdk),
            "VELLUM_BACKEND": str(backend),
            "VELLUM_DEV_TEST_CALLS": str(calls),
        }
        return project, environment, calls

    def test_source_edit_triggers_one_bounded_verified_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, environment, calls = self._fixture(root)
            transcript = root / "dev.jsonl"
            process = subprocess.Popen(
                [
                    sys.executable, str(CLI), "dev", "--target", "macos",
                    "--test-mode", "--max-reloads", "1", "--timeout", "10",
                    "--poll-interval", "0.02", "--debounce", "0.02",
                    "--transcript", str(transcript), "--json",
                ],
                cwd=project, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, **environment},
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if transcript.is_file() and '"event":"build-finished"' in transcript.read_text():
                    break
                time.sleep(0.02)
            else:
                process.kill()
                self.fail("dev loop did not finish its initial build")

            source = project / "src/App.tsx"
            source.write_text(source.read_text(encoding="utf-8") + "\n// reload proof\n", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "dev_completed")
            self.assertEqual(payload["data"]["reloads"], 1)
            self.assertEqual(payload["data"]["continuity"], "none")

            events = [json.loads(line) for line in transcript.read_text().splitlines()]
            self.assertTrue(all(event["schema"] == "vellum.dev.transcript.v1" for event in events))
            self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
            change = next(event for event in events if event["event"] == "change-detected")
            self.assertEqual(change["data"]["paths"], ["src/App.tsx"])
            reload_event = next(event for event in events if event["event"] == "reload-completed")
            self.assertEqual(reload_event["data"]["adapter"], "verification")
            self.assertTrue(reload_event["data"]["ok"])
            self.assertEqual(events[-1]["data"]["reason"], "reload-limit")

            backend_calls = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual([call["command"] for call in backend_calls], ["build", "build", "run"])
            self.assertEqual(backend_calls[-1]["argv"][-3:], ["--target", "macos", "--no-build", "--no-window"][-3:])

    def test_interactive_native_adapter_launches_then_requests_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, environment, calls = self._fixture(root)
            transcript = root / "native-dev.jsonl"
            process = subprocess.Popen(
                [
                    sys.executable, str(CLI), "dev", "--target", "macos",
                    "--max-reloads", "1", "--timeout", "10",
                    "--poll-interval", "0.02", "--debounce", "0.02",
                    "--transcript", str(transcript), "--json",
                ],
                cwd=project, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, **environment},
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if calls.is_file():
                    records = [json.loads(line) for line in calls.read_text().splitlines()]
                    if [record["command"] for record in records] == ["build", "run"]:
                        break
                time.sleep(0.02)
            else:
                process.kill()
                self.fail("native dev adapter did not launch after its initial build")
            source = project / "src/App.tsx"
            source.write_text(source.read_text() + "\n// restart proof\n", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            records = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual(
                [record["command"] for record in records],
                ["build", "run", "build", "run"],
            )
            self.assertEqual(records[-1]["argv"][-2:], ["--no-build", "--dev-reload"])

    def test_test_mode_is_required_to_be_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, environment, _ = self._fixture(root)
            completed = invoke("dev", "--test-mode", "--json", cwd=project, env=environment)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)["status"], "invalid_arguments")

    def test_snapshot_is_content_based_and_excludes_build_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            source = root / "src/main.tsx"
            source.write_text("export default 1;\n", encoding="utf-8")
            first_digest, first_files = vellum_dev.snapshot(root)
            (root / ".vellum/build").mkdir(parents=True)
            (root / ".vellum/build/app.js").write_text("generated", encoding="utf-8")
            second_digest, second_files = vellum_dev.snapshot(root)
            self.assertEqual((first_digest, first_files), (second_digest, second_files))
            source.write_text("export default 2;\n", encoding="utf-8")
            third_digest, third_files = vellum_dev.snapshot(root)
            self.assertNotEqual(first_digest, third_digest)
            self.assertEqual(vellum_dev.changed(first_files, third_files), ["src/main.tsx"])

    def test_web_adapter_injects_reload_client_without_editing_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            index = build / "index.html"
            original = b"<!doctype html><body>app</body>"
            index.write_bytes(original)
            server = vellum_dev.WebReloadServer(build, 0)
            server.start(open_browser=False)
            try:
                with urlopen(server.url, timeout=2) as response:
                    served = response.read()
                self.assertIn(b"data-vellum-dev", served)
                self.assertIn(b"EventSource('/__vellum_dev_events')", served)
                self.assertIn(b"snapshotStateJSON", served)
                self.assertIn(b"restoreStateJSON", served)
                self.assertIn(b"sessionStorage", served)
                self.assertIn(b"finally{location.reload();}", served)
                self.assertLess(
                    served.index(b"sessionStorage.removeItem(k)"),
                    served.index(b"restoreStateJSON(s)"),
                )
                self.assertEqual(index.read_bytes(), original)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
