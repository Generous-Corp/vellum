"""Strict browser provenance never falls back to an installed Chrome.

With VELLUM_REQUIRE_CHROME_PROVENANCE=1 and no VELLUM_CHROME_PATH, the web
backend's chrome_path() and the run_browser_smoke.py and
run_text_semantics_browser.py proofs must refuse both installed candidates,
`google-chrome` on PATH and the /Applications bundle, and must launch nothing.
A pinned executable with a matching receipt is accepted. The executables here
are shell scripts that only print a version; no browser runs.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
VERSION = "158.0.8127.88"
ACTION = "browser-actions/setup-chrome@2e1d749697dd1612b833dba4a722266286fbefcd"
INSTALLED = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

sys.path.insert(0, str(REPO / "cli"))
sys.path.insert(0, str(REPO / "web/tests"))
try:
    BACKEND = runpy.run_path(str(REPO / "cli/vellum_web_backend.py"))
    import run_browser_smoke
    import run_text_semantics_browser
finally:
    sys.path.pop(0)
    sys.path.pop(0)


def backend_select() -> str:
    try:
        return BACKEND["chrome_path"]()
    except BACKEND["BackendFailure"] as failure:
        raise SystemExit(str(failure)) from None


SELECTORS = {
    "chrome_path": backend_select,
    "run_browser_smoke": run_browser_smoke.select_chrome,
    "run_text_semantics_browser": run_text_semantics_browser.select_chrome,
}


def installed_candidates(which: str | None):
    """Make the installed fallbacks resolve, as on a developer Mac."""
    real_is_file = Path.is_file

    def is_file(path: Path) -> bool:
        return str(path) == INSTALLED or str(path) == which or real_is_file(path)

    return (mock.patch.object(shutil, "which", return_value=which),
            mock.patch.object(Path, "is_file", is_file))


class StrictProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("VELLUM_CHROME") and key != "CODEX_SANDBOX"}

    def test_strict_mode_refuses_each_installed_fallback_without_launching(self) -> None:
        # A receipt is configured too: strict mode must refuse discovery, not
        # merely fail to verify whatever it discovered.
        strict = {**self.environment, "VELLUM_REQUIRE_CHROME_PROVENANCE": "1",
                  "VELLUM_CHROME_PROVENANCE": str(REPO / "browser-provenance.json")}
        for which in ("/usr/bin/google-chrome", None):
            for name, select in SELECTORS.items():
                with self.subTest(selector=name, which=which):
                    which_patch, file_patch = installed_candidates(which)
                    with mock.patch.dict(os.environ, strict, clear=True), which_patch, \
                            file_patch, mock.patch.object(subprocess, "Popen") as popen, \
                            mock.patch.object(subprocess, "run") as run:
                        with self.assertRaises(SystemExit) as refused:
                            select()
                    self.assertIn("VELLUM_CHROME_PATH", str(refused.exception))
                    popen.assert_not_called()
                    run.assert_not_called()

    def test_control_without_strict_mode_reaches_the_installed_fallbacks(self) -> None:
        # The same stubs, so a refusal above comes from strict mode alone.
        for which, expected in (("/usr/bin/google-chrome", "/usr/bin/google-chrome"),
                                (None, INSTALLED)):
            for name, select in SELECTORS.items():
                with self.subTest(selector=name, which=which):
                    which_patch, file_patch = installed_candidates(which)
                    with mock.patch.dict(os.environ, self.environment, clear=True), \
                            which_patch, file_patch:
                        self.assertEqual(select(), expected)

    def test_pinned_browser_with_matching_receipt_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / "chrome-headless-shell"
            browser.write_text(
                f"#!/bin/sh\nprintf 'Google Chrome for Testing {VERSION}\\n'\n", encoding="utf-8")
            browser.chmod(0o755)
            receipt = root / "browser-provenance.json"
            created = subprocess.run([
                sys.executable, str(REPO / "scripts/create_browser_provenance.py"),
                "--browser", str(browser), "--requested-version", VERSION,
                "--source-action", ACTION, "--output", str(receipt),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)
            pinned = {**self.environment, "VELLUM_REQUIRE_CHROME_PROVENANCE": "1",
                      "VELLUM_CHROME_PATH": str(browser), "VELLUM_CHROME_PROVENANCE": str(receipt)}
            for name, select in SELECTORS.items():
                with self.subTest(selector=name):
                    with mock.patch.dict(os.environ, pinned, clear=True):
                        self.assertEqual(select(), str(browser))
            # A browser that no longer matches its receipt is refused.
            browser.write_text("#!/bin/sh\nprintf 'Google Chrome for Testing 158.0.8127.89\\n'\n",
                               encoding="utf-8")
            for name, select in SELECTORS.items():
                with self.subTest(selector=name, tampered=True):
                    with mock.patch.dict(os.environ, pinned, clear=True):
                        with self.assertRaisesRegex(SystemExit, "provenance"):
                            select()


if __name__ == "__main__":
    unittest.main()
