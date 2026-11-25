#!/usr/bin/env python3
"""One CLI test per offline `vellum design` verb, each with a negative control.

The verbs run in the native vellum-design-tool. By default the suite drives the
checkout CLI with VELLUM_DESIGN_TOOL naming the built tool. With
VELLUM_TEST_PREFIX set it drives an installed `<prefix>/bin/vellum` instead,
which finds the tool in the installed SDK.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / "vellum_cli.py"
PREFIX = os.environ.get("VELLUM_TEST_PREFIX")
TOOL = os.environ.get("VELLUM_DESIGN_TOOL")

VALID_DESIGN_MD = """---
name: Sample
colors:
  primary: "#123456"
  surface: "#fafafa"
typography:
  body:
    fontFamily: Sample Sans
    fontSize: 14px
components:
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    typography: "{typography.body}"
---
# Sample
"""

# Two spellings of one token path are a parse error, which every DESIGN.md
# consumer must refuse with exit 3 before analysis.
COLLISION_DESIGN_MD = """---
name: Collision
colors:
  brand-primary: "#111111"
  brand:
    primary: "#222222"
---
"""


def command() -> list[str]:
    if PREFIX:
        return [str(Path(PREFIX) / "bin" / "vellum")]
    return [sys.executable, str(CLI)]


@unittest.skipUnless(PREFIX or TOOL, "set VELLUM_DESIGN_TOOL or VELLUM_TEST_PREFIX")
class DesignToolCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="vellum-design-tool-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def design(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        if PREFIX:
            env.pop("VELLUM_DESIGN_TOOL", None)
        return subprocess.run(
            [*command(), "design", *args], cwd=self.root, env=env,
            text=True, capture_output=True, check=False, encoding="utf-8",
        )

    def assert_parse_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 3, completed.stdout + completed.stderr)
        self.assertIn("[error] token-name-collision at ", completed.stderr)

    def test_md_lint(self) -> None:
        valid = self.write("DESIGN.md", VALID_DESIGN_MD)
        clean = self.design("md-lint", str(valid))
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn("Lint summary:", clean.stdout)
        invalid = self.write("bad.md", COLLISION_DESIGN_MD)
        rejected = self.design("md-lint", str(invalid))
        self.assert_parse_error(rejected)
        self.assertNotIn("Lint summary:", rejected.stdout)

    def test_md_diff(self) -> None:
        before = self.write("before.md", VALID_DESIGN_MD)
        same = self.design("md-diff", str(before), str(before))
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertIn("regression: false", same.stdout)
        after = self.write("after.md", VALID_DESIGN_MD.replace('  surface: "#fafafa"\n', ""))
        removed = self.design("md-diff", str(before), str(after))
        self.assertEqual(removed.returncode, 1, removed.stdout + removed.stderr)
        self.assertIn("  - surface", removed.stdout)
        self.assertIn("regression: true", removed.stdout)
        invalid = self.write("bad.md", COLLISION_DESIGN_MD)
        self.assert_parse_error(self.design("md-diff", str(before), str(invalid)))

    def test_compile(self) -> None:
        valid = self.write("DESIGN.md", VALID_DESIGN_MD)
        compiled = self.design("compile", "--design-md", str(valid), "--stdout", "--json")
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        manifest = json.loads(compiled.stdout)
        self.assertIn("manifest_version", manifest)
        self.assertTrue(manifest["tokens"])
        written = self.design("compile", "--out-dir", "out")
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertTrue((self.root / "out/design-manifest.json").is_file())
        self.assertTrue((self.root / "out/design-binding-prompt.md").is_file())
        invalid = self.write("bad.md", COLLISION_DESIGN_MD)
        rejected = self.design("compile", "--design-md", str(invalid), "--stdout", "--json")
        self.assert_parse_error(rejected)
        self.assertNotIn('"manifest_version"', rejected.stdout)

    def test_lint_adherence(self) -> None:
        clean = self.write("ui.js", "export const el = 1;\n")
        passed = self.design("lint-adherence", str(clean))
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        raw = self.write("raw.js", 'el.style.color = "#ff0000";\n')
        flagged = self.design("lint-adherence", str(raw))
        self.assertEqual(flagged.returncode, 1, flagged.stdout + flagged.stderr)
        self.assertIn("[error] raw-color at raw.js:1:", flagged.stdout)
        invalid = self.write("bad.md", COLLISION_DESIGN_MD)
        self.assert_parse_error(self.design("lint-adherence", str(clean), "--design-md", str(invalid)))

    def test_record(self) -> None:
        self.write("ui.js", "export const el = 1;\n")
        recorded = self.design("record", "--name", "panel", "--asset", "ui.js", "--source", "fig")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        listed = self.design("record", "--list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        assets = json.loads(listed.stdout)["assets"]
        self.assertEqual([(a["name"], a["version"], a["source"]) for a in assets], [("panel", "v1", "fig")])
        missing = self.design("record", "--name", "ghost")
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(json.loads(self.design("record", "--list", "--json").stdout)["assets"], assets)

    def test_gallery(self) -> None:
        self.write("cards/knob.js", "// @dsCard group=knobs viewport=120x140\nexport const k = 1;\n")
        self.write("cards/plain.js", "export const p = 1;\n")
        inventory = self.design("gallery", "--root", ".", "--json")
        self.assertEqual(inventory.returncode, 0, inventory.stderr)
        manifest = json.loads(inventory.stdout)
        self.assertEqual(manifest["total"], 1)
        self.assertEqual(manifest["groups"][0]["cards"][0]["file"], "cards/knob.js")
        written = self.design("gallery", "--root", ".", "--no-render")
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertTrue((self.root / ".pulp-gallery/gallery.html").is_file())
        # Rendering needs an offscreen capture tool the SDK does not ship, so it
        # is refused instead of producing a gallery without its PNGs.
        render = self.design("gallery", "--root", ".", "--out", "rendered")
        self.assertEqual(render.returncode, 2)
        self.assertIn("card rendering is not available", render.stderr)
        self.assertFalse((self.root / "rendered/gallery.html").exists())

    def test_handoff(self) -> None:
        self.write("proj/README.md", "# Handoff\n\nFidelity: hi-fi\n\n## Screens\n\n### Main\n"
                   "- Background: #101010\n")
        contract = self.design("handoff", "proj", "--json")
        self.assertEqual(contract.returncode, 0, contract.stderr)
        parsed = json.loads(contract.stdout)
        self.assertEqual(parsed["fidelity"], "hifi")
        self.assertEqual(parsed["screens"][0]["specs"], {"Background": "#101010"})
        missing = self.design("handoff", "absent", "--json")
        self.assertNotEqual(missing.returncode, 0)

    def test_variants(self) -> None:
        names = self.write("variants.txt", "# Button\nSize=Small, State=Default\n\nSize=Large, State=Hover\n")
        contract = self.design("variants", "--component", "Button", str(names), "--json")
        self.assertEqual(contract.returncode, 0, contract.stderr)
        parsed = json.loads(contract.stdout)
        self.assertEqual(parsed["variant_count"], 2)
        self.assertEqual({p["name"]: sorted(p["values"]) for p in parsed["props"]},
                         {"Size": ["Large", "Small"], "State": ["Default", "Hover"]})
        unnamed = self.design("variants", str(names), "--json")
        self.assertNotEqual(unnamed.returncode, 0)

    def test_tweak(self) -> None:
        artifact = self.write(
            "art.js", 'const cfg = /*EDITMODE-BEGIN*/{"accent":"#33aaff","radius":8}/*EDITMODE-END*/;\n')
        updated = self.design("tweak", str(artifact), "--set", "radius=12", "--set", "accent=#fff")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(
            artifact.read_text(encoding="utf-8"),
            'const cfg = /*EDITMODE-BEGIN*/{"accent":"#fff","radius":12}/*EDITMODE-END*/;\n')
        listed = self.design("tweak", str(artifact), "--json")
        self.assertEqual(json.loads(listed.stdout), {"accent": "#fff", "radius": 12})
        plain = self.write("plain.js", "const cfg = {};\n")
        refused = self.design("tweak", str(plain), "--set", "radius=1")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(plain.read_text(encoding="utf-8"), "const cfg = {};\n")

    def test_export_tokens(self) -> None:
        w3c = self.design("export-tokens", "--dry-run")
        self.assertEqual(w3c.returncode, 0, w3c.stderr)
        self.assertIn('"$type": "color"', w3c.stdout)
        css = self.design("export-tokens", "--format", "css-variables")
        self.assertEqual(css.returncode, 0, css.stderr)
        self.assertIn(":root {", (self.root / "theme.css").read_text(encoding="utf-8"))
        theme = self.write("theme.json", json.dumps({"colors": {"accent": "#123456"}}))
        exported = self.design("export-tokens", "--file", str(theme), "--tokens", "t.json")
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn("#123456", (self.root / "t.json").read_text(encoding="utf-8"))
        tailwind = self.design("export-tokens", "--format", "tailwind", "--dry-run")
        self.assertEqual(tailwind.returncode, 2)
        self.assertIn("requires DESIGN.md section context", tailwind.stderr)


class DesignToolDiscoveryTests(unittest.TestCase):
    def test_missing_tool_reports_capability_unavailable(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {"VELLUM_SDK_ROOT"}}
        env["VELLUM_DESIGN_TOOL"] = "/nonexistent/vellum-design-tool"
        completed = subprocess.run(
            [sys.executable, str(CLI), "design", "md-lint", "DESIGN.md", "--json"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(json.loads(completed.stdout)["status"], "capability_unavailable")

    def test_help_lists_every_design_verb(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI), "design", "--help"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for verb in ("check", "diff", "md-lint", "md-diff", "compile", "lint-adherence",
                     "record", "gallery", "handoff", "variants", "tweak", "export-tokens"):
            self.assertIn(verb, completed.stdout)


if __name__ == "__main__":
    unittest.main()
