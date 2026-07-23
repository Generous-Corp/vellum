from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts/install.ps1"
UNSUPPORTED = (
    "Verified archive and release installation are unavailable in PowerShell"
)


class PowerShellInstallerContractTests(unittest.TestCase):
    def test_verified_modes_fail_closed_without_an_independent_verifier(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(UNSUPPORTED, source)
        self.assertIn("PowerShell currently supports only -LocalRoot", source)

        # Verification, extraction, immutable storage, and activation belong
        # exclusively to install_core.py. PowerShell must not grow a second
        # parser while its verified modes are unsupported.
        for forbidden in (
            "Get-FileHash",
            "Invoke-WebRequest",
            "tarfile",
            "extractall",
            "ConvertFrom-Json",
            "SHA256SUMS",
        ):
            self.assertNotIn(forbidden, source)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is unavailable")
    def test_archive_arguments_fail_before_reading_or_mutating_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_archive = root / "must-not-be-read.tar.gz"
            missing_sums = root / "must-not-be-read-SHA256SUMS"
            prefix = root / "must-not-be-created"
            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INSTALLER),
                    "-Archive",
                    str(missing_archive),
                    "-Checksums",
                    str(missing_sums),
                    "-InstallDir",
                    str(prefix),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(UNSUPPORTED, completed.stderr + completed.stdout)
            self.assertFalse(prefix.exists())


if __name__ == "__main__":
    unittest.main()
