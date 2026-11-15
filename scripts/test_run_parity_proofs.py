#!/usr/bin/env python3

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_parity_proofs.py")
SPEC = importlib.util.spec_from_file_location("run_parity_proofs", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
ROOT = SCRIPT.resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/provenance"))
try:
    import verify_authority_expansion as verifier
finally:
    sys.path.pop(0)

WEB_PROOF = "web/tests/run_text_semantics_browser.py"


def frozen_vellum_proofs() -> list[str]:
    matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text(encoding="utf-8"))
    amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text(encoding="utf-8"))
    return sorted(
        row["path"] for row in verifier.exact_route_rows(matrix, amendment)
        if row["repository"] == "Generous-Corp/vellum"
        and any(role["role"] == "vellum_future_proof" for role in row["cell_roles"])
    )


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
                commands = runner.commands_for(Path("."), {
                    "path": path, "runner": "ctest-case", "test_id": test_id,
                })
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0][-2:], ["-R", pattern])
                with self.assertRaisesRegex(ValueError, "unsupported proof runner"):
                    runner.commands_for(Path("."), {
                        "path": path, "runner": "ctest-case", "test_id": "other",
                    })

    def test_bindings_cover_exactly_the_verifiers_argument_driven_ids(self) -> None:
        bound = {path: binding[0] for path, binding in {
            **runner.ARGUMENT_DRIVEN_PROOF_TESTS, **runner.WEB_PAYLOAD_PROOF_TESTS}.items()}
        self.assertEqual(bound, verifier.ARGUMENT_DRIVEN_PROOF_TEST_IDS)

    def web_payload(self, root: Path, head_sha: str) -> Path:
        payload = root / "web-payload"
        payload.mkdir()
        for name in ("vellum_web_core.js", "vellum_web_core.wasm"):
            (payload / name).write_bytes(b"core")
        (payload / "manifest.json").write_text(json.dumps({"source_commit": head_sha}))
        return payload

    def web_proof(self) -> dict[str, str]:
        return {"path": WEB_PROOF, "runner": "ctest-case",
                "test_id": runner.WEB_PAYLOAD_PROOF_TESTS[WEB_PROOF][0]}

    def test_web_proof_runs_against_the_browser_payload_not_build_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.web_payload(root, "a" * 40)
            commands = runner.commands_for(root, self.web_proof(), web_payload=payload, head_sha="a" * 40)
        common = [sys.executable, WEB_PROOF, "--core-root", str(payload), "--source-root", str(root)]
        self.assertEqual(commands, [common, [*common, "--fixture", "phase3"]])
        for command in commands:
            self.assertNotIn("ctest", command)
            self.assertNotIn("build-gpu", command)

    def test_web_proof_fails_without_a_matching_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = self.web_proof()
            with self.assertRaisesRegex(ValueError, "need the Emscripten browser payload"):
                runner.commands_for(root, proof, head_sha="a" * 40)
            payload = self.web_payload(root, "a" * 40)
            with self.assertRaisesRegex(ValueError, "was not built from " + "b" * 40):
                runner.commands_for(root, proof, web_payload=payload, head_sha="b" * 40)
            (payload / "vellum_web_core.wasm").unlink()
            with self.assertRaisesRegex(ValueError, "lacks vellum_web_core.wasm"):
                runner.commands_for(root, proof, web_payload=payload, head_sha="a" * 40)

    def test_web_commands_match_the_registered_web_ctest_cases(self) -> None:
        # The runner stands in for the two registered web CTest cases that run
        # this script, so it must pass the arguments they pass.
        text = (ROOT / "web/CMakeLists.txt").read_text(encoding="utf-8")
        registered = []
        for name in ("text-ime-accessibility", "phase3-exact-browser"):
            block = re.search(r"add_test\(NAME vellum\.web\." + re.escape(name) + r"\s(.*?)\)", text, re.DOTALL)
            self.assertIsNotNone(block, name)
            arguments = block.group(1).split()
            self.assertEqual(arguments[:3], ["COMMAND", '"${Python3_EXECUTABLE}"',
                                             '"${CMAKE_CURRENT_SOURCE_DIR}/tests/run_text_semantics_browser.py"'])
            self.assertEqual(arguments[3:7], ["--core-root", '"$<TARGET_FILE_DIR:vellum-web-core>"',
                                              "--source-root", '"${PROJECT_SOURCE_DIR}"'])
            registered.append(tuple(arguments[7:]))
        self.assertEqual(tuple(registered), runner.WEB_PAYLOAD_PROOF_TESTS[WEB_PROOF][1])

    def test_frozen_python_proofs_load_when_run_by_path(self) -> None:
        # The python-file runner executes `python3 <path>` from the root, which
        # puts the proof's own directory on sys.path, not the root. Load each
        # frozen proof the same way (without running its tests) so an import
        # that needs the root on sys.path fails here, not in the release run.
        loaded = 0
        for path in frozen_vellum_proofs():
            if not path.endswith(".py") or not (ROOT / path).is_file():
                continue
            with self.subTest(path=path):
                completed = subprocess.run([
                    sys.executable, "-I", "-c",
                    "import runpy, sys; sys.path.insert(0, sys.argv[2]); "
                    "runpy.run_path(sys.argv[1], run_name='parity_proof_load')",
                    path, str((ROOT / path).parent),
                ], cwd=ROOT, capture_output=True, text=True, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                loaded += 1
        self.assertGreater(loaded, 0)


if __name__ == "__main__":
    unittest.main()
