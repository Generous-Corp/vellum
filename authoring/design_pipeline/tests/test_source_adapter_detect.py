#!/usr/bin/env python3
"""Exercise `vellum-source-adapter detect` on the shipped detection fixtures."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "product/source-detection.json"
FIXTURES = REPO / "fixtures/source-detection"
ADAPTER: Path | None = None


def detect(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(ADAPTER), "detect", *arguments], text=True,
                          capture_output=True, check=False, cwd=cwd)


class SourceAdapterDetectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_detects_a_stitch_directory_export(self) -> None:
        completed = detect("--compat", str(MANIFEST), str(FIXTURES / "stitch/2025.04"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), [
            "detected source: stitch",
            "  format-version: 2025.04",
            "  parser-version: 1.0",
            "  fingerprint match: 3/3 (directory-files, html-script-src, tailwind-config-token)",
            "  confidence: 100%",
        ])

    def test_json_reports_every_field(self) -> None:
        completed = detect("--compat", str(MANIFEST), "--json",
                           str(FIXTURES / "claude/2024.10/example.html"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual((report["source"], report["format_version"], report["confidence_pct"]),
                         ("claude", "2024.10", 100))
        self.assertEqual(report["matched_kinds"], ["html-script-type"])
        self.assertEqual(report["compat"], str(MANIFEST))
        self.assertEqual(report["compat_schema_version"], "0.3")

    def test_an_unknown_page_is_not_detected(self) -> None:
        page = FIXTURES / "_unknown/random.html"
        completed = detect("--compat", str(MANIFEST), str(page))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout.splitlines()[0], f"no detected source for {page}")
        json_run = detect("--compat", str(MANIFEST), "--json", str(page))
        self.assertEqual(json_run.returncode, 2)
        self.assertEqual(json.loads(json_run.stdout)["source"], "")

    def test_report_new_format_names_unknown_tokens(self) -> None:
        export = self.root / "export"
        shutil.copytree(FIXTURES / "stitch/2025.04", export)
        code = export / "code.html"
        code.write_text(code.read_text(encoding="utf-8").replace(
            '"on-primary": "#ffffff"', '"on-primary": "#ffffff", "sample-glow": "#000"'),
            encoding="utf-8")
        completed = detect("--compat", str(MANIFEST), "--report-new-format", str(export))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["candidate-source"], "stitch")
        self.assertIn("sample-glow", report["fingerprint-additions"][0]["any-of"])

    def test_manifest_resolution_order(self) -> None:
        # A compat.json above the input wins over --default-compat.
        tree = self.root / "tree"
        export = tree / "nested/export"
        shutil.copytree(FIXTURES / "stitch/2025.04", export)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        del manifest["imports"]["stitch"]
        (tree / "compat.json").write_text(json.dumps(manifest), encoding="utf-8")
        walked = detect("--default-compat", str(MANIFEST), "--json", str(export), cwd=self.root)
        self.assertEqual(walked.returncode, 2, walked.stdout)
        self.assertEqual(json.loads(walked.stdout)["compat"], str(tree / "compat.json"))
        # With no compat.json anywhere above, the default manifest is used.
        bare = self.root / "bare/export"
        shutil.copytree(FIXTURES / "stitch/2025.04", bare)
        fallback = detect("--default-compat", str(MANIFEST), "--json", str(bare), cwd=self.root)
        self.assertEqual(fallback.returncode, 0, fallback.stderr)
        self.assertEqual(json.loads(fallback.stdout)["compat"], str(MANIFEST))
        # An explicit --compat wins over both.
        explicit = detect("--compat", str(MANIFEST), "--default-compat", "/missing.json",
                          "--json", str(export), cwd=self.root)
        self.assertEqual(json.loads(explicit.stdout)["source"], "stitch")

    def test_errors_exit_one(self) -> None:
        missing = detect("--compat", str(MANIFEST), str(self.root / "missing"))
        self.assertEqual(missing.returncode, 1)
        self.assertIn("path does not exist", missing.stderr)
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        malformed = detect("--compat", str(broken), str(FIXTURES / "_unknown"))
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("malformed compat.json", malformed.stderr)
        no_manifest = detect(str(self.root), cwd=self.root)
        self.assertEqual(no_manifest.returncode, 1)
        self.assertIn("compat.json not found", no_manifest.stderr)
        usage = detect("--bogus")
        self.assertEqual(usage.returncode, 1)
        self.assertIn("unexpected detect argument", usage.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: test_source_adapter_detect.py <vellum-source-adapter>")
    ADAPTER = Path(sys.argv.pop(1)).resolve()
    unittest.main()
