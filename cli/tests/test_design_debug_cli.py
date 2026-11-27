"""`vellum design-debug` through the built runner, with recorded responses only.

Runs the checkout CLI against the native vellum-design-debug named by
$VELLUM_DESIGN_DEBUG. The script is synthetic and every run passes
--response-file with a failing --ai-cli, so no run can call a model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
TOOL = os.environ.get("VELLUM_DESIGN_DEBUG", "")
FAILING_AI_CLI = "sh -c 'exit 97'"

SCRIPT = """
createLabel('target1', 'Before', 10, 10, 120, 24);
function setDesignDebugTarget(id) { globalThis.__target = id; }
function clearInspectedComponent() { globalThis.__target = 'all'; }
function setDesignDebugAIConfig(provider, model, effort) { globalThis.__model = model; }
function buildDesignChatPrompt(prompt) { return 'prompt:' + prompt + ':' + globalThis.__target; }
function applyDesignChatResponse(response) { setText('target1', response); return 'applied:' + response; }
function getDesignDebugStateJson() {
  return '{"targetBounds":{"x":10,"y":10,"width":120,"height":24}}';
}
"""


def vellum(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments], text=True, capture_output=True, check=False,
        env={**os.environ, "VELLUM_AI_CLI": FAILING_AI_CLI, **(env or {})},
    )


@unittest.skipIf(os.name == "nt", "executable script fixtures require POSIX")
class DesignDebugDispatchTests(unittest.TestCase):
    """Exercise CLI dispatch with a local runner fixture, without a native SDK."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = self.root / "bin" / "vellum-design-debug"
        self.tool.parent.mkdir()
        self.tool.write_text(
            f"#!{sys.executable}\nimport json, sys\n"
            "print(json.dumps(sys.argv[1:]))\n"
            "print('runner diagnostic', file=sys.stderr)\n"
            "sys.exit(7 if '--fail' in sys.argv else 0)\n", encoding="utf-8",
        )
        self.tool.chmod(0o755)
        self.env = {"VELLUM_DESIGN_DEBUG": str(self.tool), "VELLUM_SDK_ROOT": ""}

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return vellum(*arguments, env=self.env)

    def payload(self, completed: subprocess.CompletedProcess[str]) -> dict:
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.splitlines()), 1, completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "vellum.cli.result.v1")
        self.assertEqual(payload["command"], "design-debug")
        return payload

    def test_sdk_bin_fallback(self) -> None:
        self.env.update(VELLUM_DESIGN_DEBUG="", VELLUM_SDK_ROOT=str(self.root))
        completed = self.run_cli("design-debug", "--prompt", "two words")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), ["--prompt", "two words"])

    def test_human_output_and_arguments_are_forwarded(self) -> None:
        completed = self.run_cli("design-debug", "--prompt", "two words")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout), ["--prompt", "two words"])
        self.assertEqual(completed.stderr, "runner diagnostic\n")

    def test_available_runner_json_success(self) -> None:
        completed = self.run_cli("--json", "design-debug", "--prompt", "two words")
        self.assertEqual(completed.returncode, 0)
        payload = self.payload(completed)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(json.loads(payload["data"]["stdout"]), ["--prompt", "two words"])
        self.assertEqual(payload["data"]["stderr"], "runner diagnostic\n")
        self.assertEqual(payload["data"]["exit_code"], 0)

    def test_available_runner_json_failure(self) -> None:
        completed = self.run_cli("--json", "design-debug", "--fail")
        self.assertEqual(completed.returncode, 5)
        payload = self.payload(completed)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "backend_failed")
        self.assertEqual(payload["data"]["exit_code"], 7)
        self.assertEqual(payload["data"]["stderr"], "runner diagnostic\n")

    def test_missing_explicit_runner_does_not_fall_back(self) -> None:
        self.env.update(VELLUM_DESIGN_DEBUG=str(self.root / "absent"),
                        VELLUM_SDK_ROOT=str(self.root))
        completed = self.run_cli("--json", "design-debug")
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(self.payload(completed)["status"], "capability_unavailable")

    def test_nonexecutable_runner_is_unavailable(self) -> None:
        self.tool.chmod(0o644)
        for explicit in (str(self.tool), ""):
            with self.subTest(explicit=bool(explicit)):
                self.env.update(VELLUM_DESIGN_DEBUG=explicit, VELLUM_SDK_ROOT=str(self.root))
                completed = self.run_cli("--json", "design-debug")
                self.assertEqual(completed.returncode, 4, completed.stderr)
                self.assertEqual(self.payload(completed)["status"], "capability_unavailable")

    def test_launch_error_is_structured(self) -> None:
        self.tool.write_text("#!/nonexistent/vellum-test-interpreter\n", encoding="utf-8")
        completed = self.run_cli("--json", "design-debug")
        self.assertEqual(completed.returncode, 5, completed.stderr)
        payload = self.payload(completed)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "backend_execution_error")
        human = self.run_cli("design-debug")
        self.assertEqual(human.returncode, 5)
        self.assertNotIn("Traceback", human.stderr)


@unittest.skipUnless(TOOL, "VELLUM_DESIGN_DEBUG is unset; the vellum.cli-design-debug ctest sets it")
class DesignDebugCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not Path(TOOL).is_file():
            raise AssertionError(f"VELLUM_DESIGN_DEBUG names no file: {TOOL}")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "debug-tool.js"
        self.script.write_text(SCRIPT, encoding="utf-8")
        self.response = self.root / "response.txt"
        self.response.write_text("After", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_recorded_response_run_writes_report(self) -> None:
        out = self.root / "artifacts"
        completed = vellum("design-debug", "--prompt", "make the label explicit",
                           "--target", "target1", "--script", str(self.script),
                           "--response-file", str(self.response), "--ai-cli", FAILING_AI_CLI,
                           "--output-dir", str(out), "--width", "180", "--height", "80",
                           "--scale", "1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((out / "latest-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["tool"], "vellum-design-debug")
        self.assertEqual(report["apply_summary"], "applied:After")
        self.assertTrue(report["target_found"])
        self.assertTrue(report["target_region_changed"])
        self.assertNotIn("ai_command", report)
        for key in ("before_image", "after_image", "target_before_image", "target_after_image"):
            self.assertTrue(Path(report[key]).is_file(), key)

    def test_missing_runner_is_unavailable(self) -> None:
        completed = vellum("--json", "design-debug", "--prompt", "x",
                           env={"VELLUM_DESIGN_DEBUG": str(self.root / "absent"),
                                "VELLUM_SDK_ROOT": ""})
        self.assertEqual(completed.returncode, 4, completed.stdout)
        self.assertEqual(json.loads(completed.stdout)["status"], "capability_unavailable")


if __name__ == "__main__":
    unittest.main()
