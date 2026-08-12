#!/usr/bin/env python3

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_parity_proofs.py")
SPEC = importlib.util.spec_from_file_location("run_parity_proofs", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class Tests(unittest.TestCase):
    def test_executes_bound_python_proof_and_emits_run_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = root / "test_proof.py"
            proof.write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\nunittest.main()\n")
            digest = hashlib.sha256(proof.read_bytes()).hexdigest()
            completion = {"cells": [{"proof_executions": [{
                "path": "test_proof.py", "sha256": digest,
                "check": "gpu-macos-arm64", "test_id": "test_proof.py",
                "runner": "python-file",
            }]}]}
            receipt = runner.run(root, completion, run_id=42, head_sha="a" * 40)
            self.assertEqual(receipt["status"], "pass")
            self.assertEqual(receipt["proofs"][0]["sha256"], digest)

    def test_rejects_runner_that_does_not_match_proof_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = root / "proof.py"
            proof.write_text("pass\n")
            completion = {"cells": [{"proof_executions": [{
                "path": "proof.py",
                "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
                "check": "gpu-macos-arm64", "test_id": "proof.py",
                "runner": "node-test-file",
            }]}]}
            with self.assertRaisesRegex(ValueError, "unsupported proof runner"):
                runner.run(root, completion, run_id=42, head_sha="a" * 40)

    def test_argument_driven_proofs_use_closed_ctest_bindings(self) -> None:
        for path, (test_id, pattern) in runner.ARGUMENT_DRIVEN_PROOF_TESTS.items():
            with self.subTest(path=path):
                command = runner.command_for(Path("."), {
                    "path": path, "runner": "ctest-case", "test_id": test_id,
                })
                self.assertEqual(command[-2:], ["-R", pattern])
                with self.assertRaisesRegex(ValueError, "unsupported proof runner"):
                    runner.command_for(Path("."), {
                        "path": path, "runner": "ctest-case", "test_id": "other",
                    })


if __name__ == "__main__":
    unittest.main()
