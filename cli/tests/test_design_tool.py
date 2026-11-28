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

    @unittest.skipIf(os.name == "nt", "symlink pre-creation is POSIX-only")
    def test_write_does_not_follow_predictable_temp_symlink(self) -> None:
        # A pre-created symlink at the old predictable sibling temp path must
        # not redirect compile output to an arbitrary target.
        victim = self.write("victim.txt", "KEEP")
        out = self.root / "out"
        out.mkdir()
        for name in ("design-manifest.json", "design-binding-prompt.md"):
            (out / (name + ".vellum-tmp")).symlink_to(victim)
        written = self.design("compile", "--out-dir", "out")
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP")
        manifest = out / "design-manifest.json"
        self.assertTrue(manifest.is_file() and not manifest.is_symlink())
        json.loads(manifest.read_text(encoding="utf-8"))

    def test_record_refuses_malformed_ledger_assets(self) -> None:
        self.write("ui.js", "export const el = 1;\n")
        ledger = self.write(".pulp-design-meta.json", '{"assets":"corrupt"}')
        refused = self.design("record", "--name", "new", "--asset", "ui.js")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(ledger.read_text(encoding="utf-8"), '{"assets":"corrupt"}')

    def test_export_tokens_rejects_malformed_theme_json(self) -> None:
        bad = self.write("bad.json", "{not json")
        completed = self.design("export-tokens", "--file", str(bad), "--dry-run")
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("not valid theme JSON", completed.stderr)

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

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_non_executable_tool_reports_capability_unavailable(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="vellum-not-exec-")
        self.addCleanup(tmp.cleanup)
        tool = Path(tmp.name) / "not-exec"
        tool.write_text("x", encoding="utf-8")
        tool.chmod(0o644)
        env = {k: v for k, v in os.environ.items() if k not in {"VELLUM_SDK_ROOT"}}
        env["VELLUM_DESIGN_TOOL"] = str(tool)
        completed = subprocess.run(
            [sys.executable, str(CLI), "design", "md-lint", "DESIGN.md", "--json"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 4, completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "capability_unavailable")

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_global_json_forwards_only_supported_flags(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="vellum-fake-tool-")
        self.addCleanup(tmp.cleanup)
        tool = Path(tmp.name) / "fake-design-tool"
        tool.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "verb, rest = sys.argv[1], sys.argv[2:]\n"
            "native = verb in ('compile', 'record')\n"
            "if '--json' in rest and not native:\n"
            "    sys.stderr.write('unknown option --json\\n'); sys.exit(2)\n"
            "print('{\"native\": true}' if '--json' in rest else 'plain output')\n",
            encoding="utf-8")
        tool.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in {"VELLUM_SDK_ROOT"}}
        env["VELLUM_DESIGN_TOOL"] = str(tool)

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(CLI), *args], env=env,
                                  text=True, capture_output=True, check=False)

        for verb in ("export-tokens", "lint-adherence", "md-lint", "md-diff"):
            completed = run("--json", "design", verb, "--dry-run")
            self.assertEqual(completed.returncode, 0, (verb, completed.stderr))
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"], verb)
            self.assertEqual(payload["data"]["stdout"].strip(), "plain output")
        for verb in ("compile", "record"):
            completed = run("--json", "design", verb)
            self.assertEqual(completed.returncode, 0, (verb, completed.stderr))
            self.assertEqual(json.loads(completed.stdout), {"native": True})

    def test_help_lists_every_design_verb(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI), "design", "--help"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for verb in ("check", "diff", "md-lint", "md-diff", "compile", "lint-adherence",
                     "record", "export-tokens"):
            self.assertIn(verb, completed.stdout)


if __name__ == "__main__":
    unittest.main()
