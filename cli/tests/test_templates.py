from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "cli/vellum_cli.py"
sys.path.insert(0, str(ROOT / "cli"))
import vellum_cli  # noqa: E402


def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )


class TemplateTests(unittest.TestCase):
    def test_public_variants_scaffold_and_report_stable_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("blank", "cpp-component"):
                destination = root / name
                completed = invoke(
                    "--json", "create", name, "--directory", str(destination),
                    "--template", name, "--no-verify",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["schema"], "vellum.cli.result.v1")
                self.assertEqual(payload["data"]["template"], name)
                self.assertEqual(payload["data"]["template_requested"], name)
                lock = json.loads(
                    (destination / "framework.lock").read_text(encoding="utf-8")
                )
                self.assertEqual(lock["template"], {"name": name, "version": 3})
            self.assertTrue(
                (root / "cpp-component/native/level-meter.cpp").is_file()
            )

    def test_default_is_blank_and_basic_remains_a_compatibility_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            default = invoke(
                "--json", "create", "Default", "-d", str(root / "default"),
                "--no-verify",
            )
            legacy = invoke(
                "--json", "create", "Legacy", "-d", str(root / "legacy"),
                "--template", "basic", "--no-verify",
            )
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(json.loads(default.stdout)["data"]["template"], "blank")
            self.assertEqual(json.loads(legacy.stdout)["data"]["template"], "basic")

    def test_imported_app_requires_a_source_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "imported"
            completed = invoke(
                "--json", "create", "Imported", "-d", str(destination),
                "--template", "imported-app", "--no-verify",
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                json.loads(completed.stdout)["status"], "template_requires_source"
            )
            self.assertFalse(destination.exists())

    def test_missing_required_file_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "templates"
            shutil.copytree(ROOT / "templates", root)
            (root / "basic/app.toml.template").unlink()
            with self.assertRaisesRegex(vellum_cli.CliFailure, "missing required"):
                vellum_cli.template_files(root, "blank")

    def test_unknown_placeholder_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "templates"
            shutil.copytree(ROOT / "templates", root)
            with (root / "blank/README.md.template").open("a", encoding="utf-8") as out:
                out.write("\n{{UNDECLARED_TEMPLATE_VALUE}}\n")
            with self.assertRaisesRegex(vellum_cli.CliFailure, "unknown placeholders"):
                vellum_cli.template_files(root, "blank")

    @unittest.skipIf(os.name == "nt", "symlink creation is not generally available")
    def test_symbolic_link_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "templates"
            shutil.copytree(ROOT / "templates", root)
            (root / "blank/link.template").symlink_to(
                root / "blank/README.md.template"
            )
            with self.assertRaisesRegex(vellum_cli.CliFailure, "symbolic link"):
                vellum_cli.template_files(root, "blank")


if __name__ == "__main__":
    unittest.main(verbosity=2)
