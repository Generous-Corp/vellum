from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import json


REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts" / "install.sh"


@unittest.skipUnless(shutil.which("tar") and (shutil.which("shasum") or shutil.which("sha256sum")), "archive tools unavailable")
class InstallerTests(unittest.TestCase):
    def test_local_install_runs_and_creates_project_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "prefix"
            completed = subprocess.run(
                ["sh", str(INSTALLER), "--local", str(REPO), "--install-dir", str(prefix)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("LOCAL DEVELOPMENT INSTALL", completed.stdout)
            project = root / "sterile-consumer"
            create = subprocess.run(
                [str(prefix / "bin/vellum"), "create", "Sterile Consumer", "-d", str(project), "--json"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            self.assertTrue((project / "vellum.lock.json").is_file())

    def test_archive_hash_is_verified_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            shutil.copy2(REPO / "cli/vellum_cli.py", payload / "vellum_cli.py")
            shutil.copytree(REPO / "templates", payload / "templates")
            (payload / "sdk/include").mkdir(parents=True)
            (payload / "sdk/include/placeholder.hpp").write_text("// fixture\n", encoding="utf-8")
            (payload / "metadata.json").write_text(
                json.dumps({
                    "schema": "vellum.sdk-artifact.v1",
                    "framework_version": "0.1.0",
                    "cli_version": "0.1.0-dev",
                    "cli_api": 1,
                    "source_commit": "a" * 40,
                    "target": "test",
                    "capabilities": {"cmake_sdk": True, "authoring_cli": True, "native_backend": False, "gpu_renderer": False},
                    "files": [],
                }),
                encoding="utf-8",
            )
            archive = root / "vellum-sdk-test.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(payload / "vellum_cli.py", arcname="vellum_cli.py")
                handle.add(payload / "templates", arcname="templates")
                handle.add(payload / "sdk", arcname="sdk")
                handle.add(payload / "metadata.json", arcname="metadata.json")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            sums = root / "SHA256SUMS"
            sums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

            prefix = root / "verified-prefix"
            valid = subprocess.run(
                ["sh", str(INSTALLER), "--archive", str(archive), "--checksums", str(sums), "--install-dir", str(prefix)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("Verified SHA-256", valid.stdout)

            tampered = root / "tampered-prefix"
            archive.write_bytes(archive.read_bytes() + b"tampered")
            invalid = subprocess.run(
                ["sh", str(INSTALLER), "--archive", str(archive), "--checksums", str(sums), "--install-dir", str(tampered)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("Refusing to extract", invalid.stderr)
            self.assertFalse(tampered.exists())

            missing_sums = root / "MISSING-SHA256SUMS"
            missing_sums.write_text(f"{'0' * 64}  another-asset.tar.gz\n", encoding="utf-8")
            missing_prefix = root / "missing-prefix"
            missing = subprocess.run(
                ["sh", str(INSTALLER), "--archive", str(archive), "--checksums", str(missing_sums), "--install-dir", str(missing_prefix)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("exactly one checksum", missing.stderr)
            self.assertFalse(missing_prefix.exists())

    def test_verified_archive_rejects_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            (payload / "vellum_cli.py").write_text("print('unsafe')\n", encoding="utf-8")
            link = payload / "templates"
            link.symlink_to("/tmp")
            archive = root / "vellum-sdk-link.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(payload / "vellum_cli.py", arcname="vellum_cli.py")
                handle.add(link, arcname="templates", recursive=False)
            sums = root / "SHA256SUMS"
            sums.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
            prefix = root / "prefix"
            completed = subprocess.run(
                ["sh", str(INSTALLER), "--archive", str(archive), "--checksums", str(sums), "--install-dir", str(prefix)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unsafe archive member", completed.stderr)
            self.assertFalse(prefix.exists())


if __name__ == "__main__":
    unittest.main()
