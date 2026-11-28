#!/usr/bin/env python3
"""Positive and planted-negative contract tests for ``vellum ui build/check/lint``."""
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The suite runs the public `vellum ui` verb in a subprocess; the verb loads
# cli/vellum_ui_build.py, which loads cli/vellum_ui_lint.py by path.
CLI = Path(__file__).resolve().parents[1] / "vellum_cli.py"


class UiBuildContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="vellum-ui-build-")
        self.root = Path(self.tmp.name)
        self.source = self.root / "native-ui" / "src"
        self.out = self.root / "build" / "native-ui"
        self.source.mkdir(parents=True)
        (self.source / "Editor.tsx").write_text(
            "export function Editor({value}: {value: number}) {\n"
            "  return <button data-pulp-action=\"value\">{value}</button>;\n"
            "}\n", encoding="utf-8")
        (self.source / "tokens.css").write_text(":root { --accent: #fff; }\n", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli_in(self.root, *args)

    def run_cli_in(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run `vellum ui ARGS`; with --json, stdout carries the result's data."""
        completed = subprocess.run(
            [sys.executable, str(CLI), "ui", *args], cwd=root,
            text=True, capture_output=True, check=False, encoding="utf-8")
        if "--json" in args and completed.stdout:
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["schema"], "vellum.cli.result.v1")
            self.assertEqual(payload["command"], "ui")
            self.assertEqual(payload["ok"], completed.returncode == 0)
            completed.stdout = json.dumps(payload["data"])
        return completed

    def test_build_then_check_is_deterministic(self):
        built = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui", "--json")
        self.assertEqual(built.returncode, 0, built.stderr)
        first = json.loads(built.stdout)
        checked = self.run_cli("check", "--source", "native-ui/src", "--out", "build/native-ui", "--json")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        checked_receipt = json.loads(checked.stdout)
        self.assertEqual(first["source_digest"], checked_receipt["source_digest"])
        self.assertEqual(first["files"], checked_receipt["files"])
        manifest = (self.out / "ui-build-manifest.json").read_bytes()
        rebuilt = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui", "--json")
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(first, json.loads(rebuilt.stdout))
        self.assertEqual(manifest, (self.out / "ui-build-manifest.json").read_bytes())
        self.assertEqual((self.out / "Editor.tsx").read_bytes(), (self.source / "Editor.tsx").read_bytes())

    def test_manifest_is_independent_of_checkout_root(self):
        first = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_manifest = (self.out / "ui-build-manifest.json").read_bytes()

        other_root = self.root / "other-checkout"
        other_source = other_root / "native-ui" / "src"
        other_source.mkdir(parents=True)
        for source_file in self.source.iterdir():
            (other_source / source_file.name).write_bytes(source_file.read_bytes())
        second = self.run_cli_in(
            other_root, "build", "--source", "native-ui/src", "--out", "build/native-ui", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        second_manifest = (other_root / "build" / "native-ui" / "ui-build-manifest.json").read_bytes()

        self.assertEqual(first_manifest, second_manifest)
        self.assertNotIn(str(self.root).encode(), first_manifest)
        manifest = json.loads(first_manifest)
        self.assertEqual(manifest["source_root"], ".")

    def test_check_accepts_a_manifest_written_by_the_legacy_producer(self):
        built = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertEqual(built.returncode, 0, built.stderr)
        manifest_path = self.out / "ui-build-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["producer"], "vellum ui build")
        self.assertEqual(manifest["schema"], "pulp.ui.build-manifest.v1")
        manifest["producer"] = "pulp ui build"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        checked = self.run_cli("check", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        manifest["producer"] = "someone else"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rejected = self.run_cli("check", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("manifest producer does not match `vellum ui build`", rejected.stderr)

    def test_failed_check_reports_one_json_result(self):
        built = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertEqual(built.returncode, 0, built.stderr)
        (self.out / "Editor.tsx").write_text("tampered\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(CLI), "--json", "ui", "check", "--source", "native-ui/src",
             "--out", "build/native-ui"], cwd=self.root,
            text=True, capture_output=True, check=False, encoding="utf-8")
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ui_check_failed")
        self.assertFalse(payload["ok"])
        self.assertIn("built output bytes differ for Editor.tsx", payload["message"])

    def test_ui_requires_a_known_action(self):
        for args in ((), ("emit",)):
            with self.subTest(args=args):
                completed = subprocess.run(
                    [sys.executable, str(CLI), "--json", "ui", *args], cwd=self.root,
                    text=True, capture_output=True, check=False, encoding="utf-8")
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(json.loads(completed.stdout)["status"], "invalid_arguments")

    def test_check_rejects_source_drift(self):
        built = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertEqual(built.returncode, 0, built.stderr)
        (self.source / "Editor.tsx").write_text(
            (self.source / "Editor.tsx").read_text(encoding="utf-8") + "// changed\n",
            encoding="utf-8")
        checked = self.run_cli("check", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("source tree digest differs", checked.stderr)

    def test_check_rejects_tampered_output_and_extra_file(self):
        built = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertEqual(built.returncode, 0, built.stderr)
        (self.out / "Editor.tsx").write_text("tampered\n", encoding="utf-8")
        (self.out / "stale.js").write_text("stale\n", encoding="utf-8")
        checked = self.run_cli("check", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("built output bytes differ for Editor.tsx", checked.stderr)
        self.assertIn("untracked file stale.js", checked.stderr)

    def test_missing_or_empty_source_fails_closed(self):
        missing = self.run_cli("build", "--source", "missing", "--out", "build/native-ui")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("source root does not exist", missing.stderr)
        empty = self.root / "empty"
        empty.mkdir()
        result = self.run_cli("build", "--source", "empty", "--out", "build/empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains no supported source files", result.stderr)

    def test_symlink_source_is_rejected(self):
        (self.source / "link.tsx").symlink_to(self.source / "Editor.tsx")
        result = self.run_cli("build", "--source", "native-ui/src", "--out", "build/native-ui")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains a symlink", result.stderr)

    def test_symlinked_output_tree_is_rejected_by_build_and_check(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "Editor.tsx").write_text("sentinel\n", encoding="utf-8")
        self.assertEqual(self.run_cli("build").returncode, 0)
        built = self.out / "Editor.tsx"
        built.unlink()
        built.symlink_to(outside / "Editor.tsx")
        for command in ("build", "check"):
            completed = self.run_cli(command)
            self.assertNotEqual(completed.returncode, 0, command)
            self.assertIn("symlink", completed.stdout + completed.stderr)
        self.assertEqual((outside / "Editor.tsx").read_text(encoding="utf-8"), "sentinel\n")
        built.unlink()
        # A symlinked output subdirectory must not redirect writes either.
        (self.source / "sub").mkdir()
        (self.source / "sub" / "a.css").write_text("a{}\n", encoding="utf-8")
        (self.out / "sub").symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_cli("build").returncode, 0)
        self.assertFalse((outside / "a.css").exists())

    def test_built_files_are_world_readable(self):
        self.assertEqual(self.run_cli("build").returncode, 0)
        for path in (self.out / "Editor.tsx", self.out / "ui-build-manifest.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o644, path.name)

    def test_reserved_manifest_name_source_is_rejected(self):
        (self.source / "ui-build-manifest.json").write_text("{}\n", encoding="utf-8")
        completed = self.run_cli("build")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("reserved", completed.stdout + completed.stderr)

    def test_nested_manifest_named_output_is_untracked(self):
        self.assertEqual(self.run_cli("build").returncode, 0)
        (self.out / "sub").mkdir()
        (self.out / "sub" / "ui-build-manifest.json").write_text("{}\n", encoding="utf-8")
        completed = self.run_cli("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("untracked", completed.stdout + completed.stderr)

    def test_rebuild_removes_outputs_the_source_no_longer_has(self):
        extra = self.source / "Extra.tsx"
        extra.write_text((self.source / "Editor.tsx").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(self.run_cli("build").returncode, 0)
        self.assertTrue((self.out / "Extra.tsx").is_file())
        extra.unlink()
        self.assertEqual(self.run_cli("build").returncode, 0)
        self.assertFalse((self.out / "Extra.tsx").exists())
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_rebuild_refuses_files_the_build_never_wrote(self):
        self.assertEqual(self.run_cli("build").returncode, 0)
        stray = self.out / "notes.txt"
        stray.write_text("keep\n", encoding="utf-8")
        completed = self.run_cli("build")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("untracked", completed.stdout + completed.stderr)
        self.assertTrue(stray.is_file())

    def test_lint_command_filters_explicit_generated_vendor_roles(self):
        vendor = self.source / "vendor" / "runtime.js"
        vendor.parent.mkdir()
        vendor.write_text("export const stamp = Math.random();\n", encoding="utf-8")
        manifest = self.root / "corpus-manifest.json"
        files = []
        for path, role in ((self.source / "Editor.tsx", "owned-source"),
                           (vendor, "generated-vendor")):
            files.append({
                "path": path.relative_to(self.source).as_posix(),
                "role": role,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        manifest.write_text(json.dumps({
            "schema": "pulp.clean-output-corpus.v1",
            "producer": "pulp import-design --emit source",
            "files": files,
        }), encoding="utf-8")

        result = self.run_cli("lint", "--source", "native-ui/src",
                              "--manifest", "corpus-manifest.json", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["files"], 1)
        self.assertTrue(report["ok"], report)


if __name__ == "__main__":
    unittest.main()
