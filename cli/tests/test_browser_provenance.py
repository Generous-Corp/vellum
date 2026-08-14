from __future__ import annotations

import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
from vellum_browser import BrowserProvenanceError, configured_provenance, validate_record  # noqa: E402
sys.path.pop(0)


class BrowserProvenanceTests(unittest.TestCase):
    def make_browser(self, root: Path, version: str = "151.0.7922.47") -> Path:
        browser = root / "chrome"
        browser.write_text(f"#!/bin/sh\nprintf 'Google Chrome {version}\\n'\n", encoding="utf-8")
        browser.chmod(0o755)
        return browser

    def make_record(self, root: Path, browser: Path, version: str = "151.0.7922.47") -> Path:
        script = REPO / "scripts/create_browser_provenance.py"
        record = root / "browser-provenance.json"
        completed = subprocess.run([
            sys.executable, str(script), "--browser", str(browser),
            "--requested-version", version,
            "--source-action", "browser-actions/setup-chrome@2e1d749697dd1612b833dba4a722266286fbefcd",
            "--output", str(record),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return record

    def test_create_and_validate_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browser = self.make_browser(root)
            record = self.make_record(root, browser)
            self.assertEqual(validate_record(record, browser)["version"], "151.0.7922.47")

    def test_digest_or_version_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browser = self.make_browser(root)
            record = self.make_record(root, browser)
            browser.write_text("#!/bin/sh\nprintf 'Google Chrome 151.0.7922.48\\n'\n", encoding="utf-8")
            browser.chmod(0o755)
            with self.assertRaises(BrowserProvenanceError):
                validate_record(record, browser)

    def test_unrecorded_browser_is_reported_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            browser = self.make_browser(Path(temporary))
            self.assertEqual(configured_provenance(browser), (False, "no VELLUM_CHROME_PROVENANCE record (browser is unverified)"))

