#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("verify_current_state_docs.py")
SPEC = importlib.util.spec_from_file_location("verify_current_state_docs", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
ROOT = SCRIPT.parents[2]


class Tests(unittest.TestCase):
    def test_workflow_checks_documents_only_after_active_phase_resolution(self) -> None:
        workflow = (ROOT / ".github/workflows/provenance.yml").read_text(
            encoding="utf-8"
        )
        phase = workflow.index("- name: Resolve the exact authority lifecycle phase")
        documents = workflow.index("- name: Verify active repository documentation")
        self.assertGreater(documents, phase)
        self.assertIn(
            "if: steps.authority-phase.outputs.phase == 'active'",
            workflow[documents : documents + 240],
        )

    def test_repository_documents_match_active_state(self) -> None:
        verifier.verify_documents(ROOT)

    def test_stale_active_claim_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            required = {
                *verifier.ACTIVE_REQUIRED,
                "provenance/ownership-map.yaml",
                "provenance/pulp-observatory/provenance.lock",
                "provenance/pulp-extraction.json",
                "docs/architecture/gpu-boundary.md",
                "docs/cli/contract.md",
            }
            for relative in required:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            readme = root / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8")
                + "\nAuthority is not active.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verifier.VerificationError, "stale claim"
            ):
                verifier.verify_documents(root)

    def test_coordinate_disagreement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "provenance/ownership-map.yaml",
                "provenance/pulp-observatory/provenance.lock",
                "provenance/pulp-extraction.json",
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            ownership = root / "provenance/ownership-map.yaml"
            ownership.write_text(
                ownership.read_text(encoding="utf-8").replace(
                    "a106a02816a0cde53daac83f36a6630d664f6637",
                    "f" * 40,
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verifier.VerificationError, "ownership map disagrees"
            ):
                verifier.active_coordinates(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
