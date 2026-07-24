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
    def test_authority_phase_is_cross_bound_to_active_lock(self) -> None:
        start = "a" * 40
        record = "b" * 40
        activation = "c" * 40
        extraction = {
            "status": "active",
            "authority": {
                "state": "active",
                "ownership_start_commit": start,
                "authority_record_commit": record,
                "authority_record_path": "provenance/authority/records/native.json",
                "authority_ref": "refs/tags/authority/native",
                "pulp_activation_commit": activation,
                "pulp_authority_event_path": ".github/vellum-change-events/active.json",
                "pulp_authority_event_id": "active",
                "accepted_by": "@owner",
                "accepted_at": "2026-07-24T02:00:00Z",
            },
        }
        lock = {
            "state": "active",
            "vellum_authority_start_commit": start,
            "vellum_authority_record_commit": record,
            "pulp_activation_commit": activation,
        }
        self.assertEqual(
            verify_extraction.verify_authority_phase(extraction, lock), (True, [])
        )
        extraction["authority"]["pulp_activation_commit"] = "d" * 40
        transferred, errors = verify_extraction.verify_authority_phase(
            extraction, lock
        )
        self.assertFalse(transferred)
        self.assertIn(
            "active extraction authority has invalid pulp_activation_commit", errors
        )

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

    def test_default_inventory_scans_authoring_and_ui_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "authoring").mkdir()
            (root / "authoring/host.mm").write_text(
                "auto legacy = pulp::view::host();\n", encoding="utf-8"
            )
            (root / "packages/vellum-ui/src").mkdir(parents=True)
            (root / "packages/vellum-ui/src/runtime.js").write_text(
                "const forbidden = '@pulp/audio';\n", encoding="utf-8"
            )

            findings = verify_extraction.scan_active_surface(root)

            self.assertEqual(
                {finding["path"] for finding in findings},
                {
                    "authoring/host.mm",
                    "packages/vellum-ui/src/runtime.js",
                },
            )

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
