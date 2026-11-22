#!/usr/bin/env python3
"""Tests for the installed Vellum notices manifest."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_notices_manifest as generator  # noqa: E402


class NoticesManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "share/vellum/notices"
        generator.generate(ROOT, self.output)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manifest_has_one_entry_and_text_for_each_bundled_component(self) -> None:
        manifest = json.loads((self.output / "notices.json").read_text())
        bundled = json.loads((ROOT / "provenance/third-party-lock.json").read_text())[
            "runtime_toolchain_dependencies"
        ]["renderer"]["bundled_components"]
        self.assertEqual(len(manifest["entries"]), 27)
        self.assertEqual(
            sorted(row["name"] for row in bundled),
            sorted(entry["name"] for entry in manifest["entries"] if entry["name"] in {row["name"] for row in bundled}),
        )
        generator.validate_installed_package(self.output / "notices.json", ROOT / "provenance/third-party-lock.json")

    def test_installed_package_check_rejects_planted_license_gap(self) -> None:
        manifest = json.loads((self.output / "notices.json").read_text())
        planted = self.output / manifest["entries"][0]["license_text"]
        planted.unlink()
        with self.assertRaisesRegex(ValueError, "missing license text"):
            generator.validate_installed_package(self.output / "notices.json", ROOT / "provenance/third-party-lock.json")

    def test_installed_package_check_rejects_planted_bundled_row_gap(self) -> None:
        path = self.output / "notices.json"
        manifest = json.loads(path.read_text())
        manifest["entries"] = [entry for entry in manifest["entries"] if entry["name"] != "HarfBuzz"]
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "bundled_components must appear exactly once"):
            generator.validate_installed_package(path, ROOT / "provenance/third-party-lock.json")


if __name__ == "__main__":
    unittest.main()
