from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


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
            install_manifest = json.loads(
                (prefix / "lib/vellum/install-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(install_manifest, {
                "schema": "vellum.sdk-install.v1",
                "verified": False,
                "artifact": None,
                "artifact_sha256": None,
                "framework_version": "0.1.0",
                "target": "local-development",
                "source_commit": None,
            })
            lock = json.loads((project / "vellum.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["artifact"], {
                "verified": False,
                "sha256": None,
                "target": "local-development",
                "sourceCommit": None,
            })
            source = root / "revision-a.source.json"
            updated_source = root / "revision-b.source.json"
            shutil.copy2(REPO / "fixtures/design-ir/revision-a.source.json", source)
            shutil.copy2(REPO / "fixtures/design-ir/revision-b.source.json", updated_source)
            imported = subprocess.run(
                [str(prefix / "bin/vellum"), "import", str(source), "--json"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)
            self.assertTrue((project / "design/ir/sources/main.designir.json").is_file())
            self.assertTrue((prefix / "lib/vellum/bin/vellum-backend").is_file())
            self.assertTrue((prefix / "lib/vellum/bin/vellum-import-backend").is_file())
            self.assertFalse((prefix / "lib/vellum/bin/vellum-native-backend").exists())
            handshake = subprocess.run(
                [
                    str(prefix / "lib/vellum/bin/vellum-backend"),
                    "import", "--project", str(project), "--json",
                    "--framework-version", "9.9.9", "--cli-api", "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(handshake.returncode, 3, handshake.stdout)
            self.assertEqual(json.loads(handshake.stdout)["status"], "backend_handshake_mismatch")
            reimported = subprocess.run(
                [str(prefix / "bin/vellum"), "reimport", "--source", str(updated_source), "--json"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            active = json.loads((project / "design/import.lock.json").read_text())
            self.assertEqual(active["sources"]["main"]["activeRevision"], "palette-board-b")
            unavailable = subprocess.run(
                [str(prefix / "bin/vellum"), "build", "--json"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(unavailable.returncode, 4, unavailable.stdout)
            self.assertEqual(json.loads(unavailable.stdout)["status"], "capability_unavailable")

    def test_local_install_requires_node_20_or_newer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_node = fake_bin / "node"
            fake_node.write_text("#!/bin/sh\nprintf '%s\\n' 'v18.20.0'\n", encoding="utf-8")
            fake_node.chmod(0o755)
            completed = subprocess.run(
                ["sh", str(INSTALLER), "--local", str(REPO), "--install-dir", str(root / "prefix")],
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin"},
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Node.js 20+", completed.stderr)
            self.assertIn("v18.20.0", completed.stderr)

    def test_archive_hash_is_verified_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            shutil.copy2(REPO / "cli/vellum_cli.py", payload / "vellum_cli.py")
            shutil.copy2(REPO / "cli/vellum_backend.py", payload / "vellum_backend.py")
            shutil.copytree(REPO / "templates", payload / "templates")
            shutil.copytree(REPO / "packages/vellum-design-ir", payload / "design-ir")
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
                    "capabilities": {
                        "cmake_sdk": True,
                        "authoring_cli": True,
                        "gpu_renderer": False,
                        "commands": {
                            "import": True,
                            "reimport": True,
                            "build": False,
                            "run": False,
                            "test": False,
                            "capture": False,
                            "package": False,
                        },
                    },
                    "files": [],
                }),
                encoding="utf-8",
            )
            archive = root / "vellum-sdk-test.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(payload / "vellum_cli.py", arcname="vellum_cli.py")
                handle.add(payload / "vellum_backend.py", arcname="vellum_backend.py")
                handle.add(payload / "templates", arcname="templates")
                handle.add(payload / "design-ir", arcname="design-ir")
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
            manifest = json.loads(
                (prefix / "lib/vellum/install-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["artifact_sha256"], digest)
            self.assertEqual(manifest["artifact"], archive.name)
            self.assertEqual(manifest["target"], "test")
            self.assertEqual(manifest["source_commit"], "a" * 40)
            project = root / "verified-project"
            created = subprocess.run(
                [str(prefix / "bin/vellum"), "create", "Verified", "-d", str(project), "--json"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            lock = json.loads((project / "vellum.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["artifact"]["sha256"], digest)
            self.assertTrue(lock["framework"]["artifact"]["verified"])

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
