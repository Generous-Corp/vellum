#!/usr/bin/env python3
"""Contract tests for the renderer digest-to-provenance binding."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from verify_renderer_dependency import VerificationError, validate_renderer_attestation


ROOT = Path(__file__).resolve().parents[2]


class RendererAttestationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        lock = json.loads((ROOT / "provenance/third-party-lock.json").read_text())
        cls.renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        cls.attestation = json.loads(
            (ROOT / "provenance/renderer-attestation.json").read_text()
        )

    def test_pending_record_binds_the_locked_digest(self) -> None:
        record = validate_renderer_attestation(self.renderer, self.attestation)
        self.assertEqual(record["artifact"]["sha256"], self.renderer["artifact"]["sha256"])

    def test_artifact_digest_without_provenance_record_fails(self) -> None:
        missing = copy.deepcopy(self.attestation)
        missing["artifact"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(VerificationError, "no matching provenance record"):
            validate_renderer_attestation(self.renderer, missing)

        with self.assertRaisesRegex(VerificationError, "no provenance record"):
            validate_renderer_attestation(self.renderer, None)


if __name__ == "__main__":
    unittest.main()
