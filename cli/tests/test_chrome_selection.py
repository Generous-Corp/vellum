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
    import run_editor_session_browser
    import run_text_semantics_browser
    import vellum_browser
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


def fake_browser(path: Path, version: str = VERSION) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf 'Google Chrome for Testing {version}\\n'\n",
                    encoding="utf-8")
    path.chmod(0o755)
    return path


def receipt(browser: Path, output: Path, product: str) -> Path:
    created = subprocess.run([
        sys.executable, str(REPO / "scripts/create_browser_provenance.py"),
        "--browser", str(browser), "--requested-version", VERSION,
        "--source-action", ACTION, "--output", str(output), "--product", product,
    ], text=True, capture_output=True, check=False)
    if created.returncode:
        raise AssertionError(created.stderr)
    return output


HEADLESS_SELECTORS = {
    **SELECTORS,
    "run_editor_session_browser": run_editor_session_browser.browser,
}


class HeadlessShellPreferenceTests(unittest.TestCase):
    """A pinned Chrome for Testing release runs headless work in its shell."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        cache = Path(self.directory.name) / "chrome-for-testing"
        self.chrome = fake_browser(
            cache / "chrome" / f"mac_arm-{VERSION}" / "chrome-mac-arm64"
            / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        self.shell = cache / "chrome-headless-shell" / f"mac_arm-{VERSION}" \
            / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("VELLUM_CHROME") and key != "CODEX_SANDBOX"
                            and key != "VELLUM_REQUIRE_CHROME_PROVENANCE"}
        self.environment["VELLUM_CHROME_PATH"] = str(self.chrome)

    def selected(self, environment: dict[str, str]) -> dict[str, str]:
        chosen = {}
        for name, select in HEADLESS_SELECTORS.items():
            with mock.patch.dict(os.environ, environment, clear=True):
                chosen[name] = select()
        return chosen

    def assert_all(self, environment: dict[str, str], expected: Path) -> None:
        for name, chosen in self.selected(environment).items():
            with self.subTest(selector=name):
                self.assertEqual(chosen, str(expected))

    def assert_all_refuse(self, environment: dict[str, str], pattern: str,
                          selectors: dict = HEADLESS_SELECTORS) -> None:
        for name, select in selectors.items():
            with self.subTest(selector=name):
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(SystemExit, pattern):
                        select()

    def test_same_release_shell_is_preferred(self) -> None:
        fake_browser(self.shell)
        self.assert_all(self.environment, self.shell)
        with mock.patch.dict(os.environ, self.environment, clear=True):
            self.assertEqual(run_text_semantics_browser.select_browser(),
                             ("headless-shell", str(self.shell)))

    def test_without_a_shell_the_pinned_chrome_is_used(self) -> None:
        self.assert_all(self.environment, self.chrome)
        with mock.patch.dict(os.environ, self.environment, clear=True):
            self.assertEqual(run_text_semantics_browser.select_browser()[0], "chrome")

    def test_shell_from_another_release_is_not_used(self) -> None:
        fake_browser(self.shell, "158.0.8127.89")
        self.assert_all(self.environment, self.chrome)

    def test_named_shell_from_another_release_is_refused(self) -> None:
        other = fake_browser(Path(self.directory.name) / "other" / "chrome-headless-shell",
                             "157.0.1.2")
        self.assert_all_refuse(
            {**self.environment, "VELLUM_CHROME_HEADLESS_SHELL_PATH": str(other)},
            "not the release of the pinned Chrome")

    def test_strict_mode_needs_the_shells_own_receipt(self) -> None:
        fake_browser(self.shell)
        root = Path(self.directory.name)
        strict = {**self.environment, "VELLUM_REQUIRE_CHROME_PROVENANCE": "1",
                  "VELLUM_CHROME_PROVENANCE": str(receipt(
                      self.chrome, root / "chrome.json", "Google Chrome"))}
        # A discovered shell without a receipt leaves the verified Chrome.
        self.assert_all(strict, self.chrome)
        shell_receipt = receipt(self.shell, root / "shell.json", "chrome-headless-shell")
        accepted = {**strict, "VELLUM_CHROME_HEADLESS_SHELL_PROVENANCE": str(shell_receipt)}
        self.assert_all(accepted, self.shell)
        # The Chrome receipt does not vouch for the shell.
        self.assert_all_refuse(
            {**strict, "VELLUM_CHROME_HEADLESS_SHELL_PROVENANCE": strict["VELLUM_CHROME_PROVENANCE"]},
            "refused")
        # A shell that no longer matches its receipt is refused.
        self.shell.write_text(self.shell.read_text(encoding="utf-8") + "# changed\n",
                              encoding="utf-8")
        self.assert_all_refuse(accepted, "differs from its provenance")

    def test_strict_mode_still_refuses_installed_browsers_with_a_shell_present(self) -> None:
        fake_browser(self.shell)
        strict = {key: value for key, value in self.environment.items()
                  if key != "VELLUM_CHROME_PATH"}
        strict["VELLUM_REQUIRE_CHROME_PROVENANCE"] = "1"
        which_patch, file_patch = installed_candidates("/usr/bin/google-chrome")
        # The editor session proof skips rather than discovers, so only the
        # discovering selectors are asked.
        with which_patch, file_patch:
            self.assert_all_refuse(strict, "VELLUM_CHROME_PATH", SELECTORS)

    def test_headless_flag_matches_the_browser_kind(self) -> None:
        self.assertEqual(vellum_browser.headless_mode_arguments(self.shell), [])
        self.assertEqual(vellum_browser.headless_mode_arguments(self.chrome), ["--headless=new"])


if __name__ == "__main__":
    unittest.main()
