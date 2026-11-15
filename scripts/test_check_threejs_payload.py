import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_threejs_payload as checker  # noqa: E402


class ThreeJsPayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "assets/threejs", self.root / "assets/threejs")
        (self.root / "provenance").mkdir()
        shutil.copy(ROOT / "provenance/third-party-lock.json", self.root / "provenance")
        self.addCleanup(self.tmp.cleanup)

    def test_committed_payload_matches_its_pin(self):
        self.assertEqual(checker.check(ROOT), [])

    def test_manifest_pins_the_demo_payload(self):
        manifest = json.loads((ROOT / "assets/threejs/payload-manifest.json").read_text())
        self.assertEqual(manifest["package_version"], "0.183.1")
        self.assertEqual(manifest["revision"], "077dd13c0e869d9f3dbe55875686f920367de457")
        self.assertIn("build/three.webgpu.js", manifest["files"])

    def test_tampered_byte_is_refused(self):
        self.assertEqual(checker.check(self.root), [])
        target = self.root / "assets/threejs/build/three.webgpu.js"
        data = bytearray(target.read_bytes())
        data[100] ^= 1
        target.write_bytes(bytes(data))
        self.assertEqual(checker.check(self.root), ["digest mismatch for build/three.webgpu.js"])

    def test_missing_and_extra_files_are_refused(self):
        (self.root / "assets/threejs/LICENSE").unlink()
        (self.root / "assets/threejs/extra.js").write_text("x")
        errors = checker.check(self.root)
        self.assertIn("missing LICENSE", errors)
        self.assertIn("unlisted file extra.js", errors)

    def test_manifest_edit_without_lock_is_refused(self):
        path = self.root / "assets/threejs/payload-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"]["LICENSE"] = "0" * 64
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        errors = checker.check(self.root)
        self.assertIn("digest mismatch for LICENSE", errors)
        self.assertIn("lock payload_manifest_sha256 differs from payload-manifest.json", errors)

    def test_cli_exit_status(self):
        script = str(ROOT / "scripts/check_threejs_payload.py")
        ok = subprocess.run([sys.executable, "-I", script, "--root", str(ROOT)], capture_output=True)
        self.assertEqual(ok.returncode, 0)
        (self.root / "assets/threejs/build/three.core.js").write_bytes(b"")
        bad = subprocess.run([sys.executable, "-I", script, "--root", str(self.root)], capture_output=True)
        self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
