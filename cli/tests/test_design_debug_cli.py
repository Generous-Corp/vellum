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
