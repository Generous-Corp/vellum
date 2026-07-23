#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_extraction.py")
SPEC = importlib.util.spec_from_file_location("verify_extraction", MODULE_PATH)
assert SPEC and SPEC.loader
verify_extraction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_extraction)
ARTIFACT_MODULE_PATH = MODULE_PATH.parents[2] / "scripts/verify_sdk_artifact.py"
ARTIFACT_SPEC = importlib.util.spec_from_file_location(
    "verify_sdk_artifact", ARTIFACT_MODULE_PATH
)
assert ARTIFACT_SPEC and ARTIFACT_SPEC.loader
verify_sdk_artifact = importlib.util.module_from_spec(ARTIFACT_SPEC)
ARTIFACT_SPEC.loader.exec_module(verify_sdk_artifact)


class VerifyExtractionTests(unittest.TestCase):
    def test_git_blob_identity_matches_git_format(self) -> None:
        self.assertEqual(
            verify_extraction.git_blob_sha(b"test content\n"),
            "d670460b4b4aece5915caf5c68d12f560a9fe3e4",
        )

    def test_active_scan_finds_pulp_namespace_and_plugin_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "foundation").mkdir()
            (root / "foundation/bad.cpp").write_text(
                "auto* x = pulp::audio::engine();\nAudioUnit thing;\n", encoding="utf-8"
            )
            findings = verify_extraction.scan_active_surface(root, ("foundation",))
            self.assertEqual({finding["rule"] for finding in findings}, {
                "pulp-public-namespace", "audio-plugin-sdk"
            })

    def test_active_scan_ignores_historical_evidence_outside_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "provenance").mkdir()
            (root / "provenance/history.txt").write_text(
                "pulp::audio::engine();\n", encoding="utf-8"
            )
            self.assertEqual(
                verify_extraction.scan_active_surface(root, ("foundation",)), []
            )

    def test_artifact_scan_finds_retired_path_namespace_and_plugin_sdk(self) -> None:
        findings = verify_sdk_artifact.payload_contamination_findings(
            "sdk/include/pulp/audio.hpp", b"namespace pulp { AudioUnit x; }\n"
        )
        self.assertEqual(
            {finding["rule"] for finding in findings},
            {"pulp-named-payload", "pulp-public-namespace", "audio-plugin-sdk"},
        )

    def test_repository_passes_full_retirement_verification(self) -> None:
        root = MODULE_PATH.parents[2]
        report = verify_extraction.verify(root)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertFalse(report["raw_seed_present_at_active_tip"])
        self.assertEqual(report["checks"]["retired_path_findings"], [])
        self.assertEqual(report["checks"]["active_historical_blob_matches"], [])


if __name__ == "__main__":
    unittest.main()
