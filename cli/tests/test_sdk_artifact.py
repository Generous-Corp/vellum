from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "scripts/build_sdk_artifact.py"
VERIFIER = REPO / "scripts/verify_sdk_artifact.py"
VALIDATOR = REPO / "scripts/validate_installed_sdk.py"
INSTALLER = REPO / "scripts/install.sh"
CONSUMER = REPO / "apps/smoke-native/install-consumer"


@unittest.skipUnless(shutil.which("cmake") and (shutil.which("shasum") or shutil.which("sha256sum")), "CMake/checksum tools unavailable")
class SdkArtifactTests(unittest.TestCase):
    def run_checked(self, arguments: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed

    def test_reproducible_archive_installs_into_sterile_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs = [root / "first", root / "second"]
            current_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip()
            wrong_commit = subprocess.run([
                sys.executable, str(BUILDER),
                "--output-dir", str(root / "wrong"),
                "--source-commit", "b" * 40,
                "--allow-dirty",
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(wrong_commit.returncode, 0)
            self.assertIn("must equal the checked-out Vellum HEAD", wrong_commit.stderr)
            evidence = []
            for output in outputs:
                completed = self.run_checked([
                    sys.executable, str(BUILDER),
                    "--output-dir", str(output),
                    "--source-commit", current_head,
                    "--allow-dirty",
                    "--target", "test-host",
                    "--graphics", "off",
                    "--json",
                ])
                evidence.append(json.loads(completed.stdout))
            self.assertEqual(evidence[0]["artifact_sha256"], evidence[1]["artifact_sha256"])
            first_archive = Path(evidence[0]["artifact_path"])
            first_sums = outputs[0] / "SHA256SUMS"

            verified = self.run_checked([
                sys.executable, str(VERIFIER),
                "--archive", str(first_archive),
                "--checksums", str(first_sums),
                "--json",
            ])
            verification = json.loads(verified.stdout)
            self.assertTrue(verification["ok"])
            self.assertTrue(verification["contamination_free"])
            self.assertEqual(verification["contamination_findings"], [])
            self.assertEqual(verification["claims"]["gpu_renderer"], False)
            self.assertEqual(verification["claims"]["commands"]["import"], True)
            self.assertEqual(verification["claims"]["commands"]["reimport"], True)
            for command in ("build", "run", "test", "capture", "package"):
                self.assertEqual(verification["claims"]["commands"][command], False)

            validated = self.run_checked([
                sys.executable, str(VALIDATOR),
                "--archive", str(first_archive),
                "--checksums", str(first_sums),
                "--json",
            ])
            validation = json.loads(validated.stdout)
            self.assertTrue(validation["ok"])
            self.assertTrue(all(validation["checks"].values()))

            prefix = root / "prefix"
            installed = self.run_checked([
                "sh", str(INSTALLER), "--archive", str(first_archive),
                "--checksums", str(first_sums), "--install-dir", str(prefix),
            ])
            self.assertIn("Verified SHA-256", installed.stdout)
            if shutil.which("curl"):
                release_dir = root / "release/v0.1.0"
                release_dir.mkdir(parents=True)
                shutil.copy2(first_archive, release_dir / first_archive.name)
                shutil.copy2(first_sums, release_dir / "SHA256SUMS")
                release_prefix = root / "release-prefix"
                release_install = self.run_checked([
                    "sh", str(INSTALLER), "--version", "0.1.0",
                    "--target", "test-host",
                    "--release-base-url", (root / "release").as_uri(),
                    "--install-dir", str(release_prefix),
                ])
                self.assertIn("Verified SHA-256", release_install.stdout)
                self.assertTrue((release_prefix / "lib/vellum/sdk/lib/cmake/Vellum/VellumConfig.cmake").is_file())
            shutil.copytree(CONSUMER, root / "consumer-source")
            consumer_build = root / "consumer-build"
            self.run_checked([
                "cmake", "-S", str(root / "consumer-source"), "-B", str(consumer_build),
                f"-DCMAKE_PREFIX_PATH={prefix / 'lib/vellum/sdk'}",
                "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
                "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
                "-DCMAKE_BUILD_TYPE=Release",
            ], cwd=root)
            self.run_checked(["cmake", "--build", str(consumer_build), "--parallel"], cwd=root)
            self.run_checked(["ctest", "--test-dir", str(consumer_build), "--output-on-failure"], cwd=root)

            package_dir = prefix / "lib/vellum/sdk/lib/cmake/Vellum"
            package_text = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.cmake"))
            self.assertNotIn(str(REPO), package_text)

            project = root / "project"
            created = self.run_checked([
                str(prefix / "bin/vellum"), "create", "Artifact Consumer",
                "--directory", str(project), "--json",
            ], cwd=root)
            self.assertEqual(json.loads(created.stdout)["status"], "created")
            lock = json.loads((project / "vellum.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["version"], "0.1.0")
            doctor = self.run_checked([str(prefix / "bin/vellum"), "doctor", "--json"], cwd=project)
            doctor_payload = json.loads(doctor.stdout)
            sdk_check = next(item for item in doctor_payload["data"]["checks"] if item["name"] == "sdk-artifact")
            compatibility = next(item for item in doctor_payload["data"]["checks"] if item["name"] == "project-sdk-compatibility")
            self.assertTrue(sdk_check["available"])
            self.assertTrue(compatibility["available"])


if __name__ == "__main__":
    unittest.main()
