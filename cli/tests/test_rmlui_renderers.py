from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "cli" / "vellum_cli.py"
CATALOG = ROOT / "rmlui" / "renderers.json"


class RmlUiRendererTests(unittest.TestCase):
    def test_catalog_schema_and_tested_claims_match_cmake_and_ci(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema"], "vellum.rmlui-renderers.v1")
        cmake = (ROOT / "rmlui" / "CMakeLists.txt").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "rmlui.yml").read_text(encoding="utf-8")
        readme = (ROOT / "rmlui" / "README.md").read_text(encoding="utf-8")
        jobs = set(re.findall(r"^  ([a-z0-9-]+):$", workflow, re.MULTILINE))
        self.assertEqual(len(catalog["renderers"]), 10)
        for renderer in catalog["renderers"]:
            self.assertIn(f"`{renderer['id']}`", readme)
            self.assertRegex(renderer["id"], r"^[a-z0-9_]+$")
            self.assertIn(renderer["kind"], {"vellum", "upstream-mit"})
            self.assertTrue(renderer["platforms"])
            for platform, status in renderer["status"].items():
                self.assertIn(platform, renderer["platforms"])
                self.assertIn(status, {"tested", "builds", "planned"})
                if status != "tested":
                    continue
                option = renderer["cmake"]["option"]
                self.assertIn(option.split("=")[0], cmake)
                if "target" in renderer["cmake"]:
                    self.assertIn(renderer["cmake"]["target"].split("::")[-1], cmake)
                if renderer["id"] == "metal":
                    self.assertIn("rmlui-metal", jobs)
                elif renderer["id"] == "cpu":
                    self.assertIn("rmlui-cpu", jobs)

    def test_cli_lists_table_and_json_catalog(self) -> None:
        table = subprocess.run(
            [sys.executable, str(CLI), "rmlui", "renderers"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(table.returncode, 0, table.stderr)
        self.assertIn("ID       KIND", table.stdout)
        self.assertIn("sdl_gpu", table.stdout)

        json_result = subprocess.run(
            [sys.executable, str(CLI), "--json", "rmlui", "renderers"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["command"], "rmlui.renderers")
        self.assertEqual(payload["data"], json.loads(CATALOG.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
