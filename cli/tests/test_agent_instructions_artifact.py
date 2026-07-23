from __future__ import annotations

import hashlib
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "scripts/build_sdk_artifact.py"
VERIFIER = REPO / "scripts/verify_sdk_artifact.py"
INSTALLER = REPO / "scripts/install.sh"
SKILL_RELATIVE = Path(".agents/skills/vellum-app-authoring/SKILL.md")
MANIFEST_RELATIVE = Path(".agents/skills/vellum-app-authoring/manifest.v1.json")


@unittest.skipUnless(
    shutil.which("node") and (shutil.which("shasum") or shutil.which("sha256sum")),
    "Node/checksum tools unavailable",
)
class AgentInstructionArtifactTests(unittest.TestCase):
    def test_verified_artifact_and_installer_preserve_agent_contract_bytes(self) -> None:
        builder = runpy.run_path(str(BUILDER))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_tree = root / "cmake-install"
            cmake_package = install_tree / "lib/cmake/Vellum"
            cmake_package.mkdir(parents=True)
            (cmake_package / "VellumConfig.cmake").write_text(
                "# minimal packaging fixture\n", encoding="utf-8"
            )
            payload = root / "payload"
            payload.mkdir()
            builder["copy_payload"](
                REPO,
                install_tree,
                payload,
                "a" * 40,
                True,
                "test-host",
                include_ui=False,
            )
            for relative in (SKILL_RELATIVE, MANIFEST_RELATIVE):
                self.assertEqual((payload / relative).read_bytes(), (REPO / relative).read_bytes())

            archive = root / "vellum-sdk-0.1.4-test-host.tar.gz"
            builder["write_archive"](payload, archive)
            checksums = root / "SHA256SUMS"
            checksums.write_text(
                f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
                encoding="utf-8",
            )
            verified = subprocess.run(
                [
                    sys.executable,
                    str(VERIFIER),
                    "--archive", str(archive),
                    "--checksums", str(checksums),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

            prefix = root / "prefix"
            installed = subprocess.run(
                [
                    "sh", str(INSTALLER),
                    "--archive", str(archive),
                    "--checksums", str(checksums),
                    "--install-dir", str(prefix),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            installed_root = prefix / "lib/vellum"
            for relative in (SKILL_RELATIVE, MANIFEST_RELATIVE):
                self.assertEqual(
                    (installed_root / relative).read_bytes(),
                    (REPO / relative).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
