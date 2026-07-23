#!/usr/bin/env python3
"""Negative controls for the Pulp tooling-disposition observation gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify_pulp_tooling_disposition.py")
SPEC = importlib.util.spec_from_file_location("verify_pulp_tooling_disposition", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

ROOT = Path(__file__).resolve().parents[2]


class PulpToolingDispositionVerificationTests(unittest.TestCase):
    def temporary_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = ROOT / "provenance/pulp-tooling-disposition"
        target = root / "provenance/pulp-tooling-disposition"
        target.parent.mkdir(parents=True)
        shutil.copytree(source, target)
        return temporary, root

    def test_committed_observation_passes(self) -> None:
        report = verifier.verify(ROOT)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["checks"]["inventory_counts"], verifier.EXPECTED_COUNTS)
        self.assertFalse(report["checks"]["authority_transfer"])
        self.assertFalse(report["checks"]["pulp_adoption"])

    def test_malformed_json_is_rejected(self) -> None:
        temporary, root = self.temporary_root()
        self.addCleanup(temporary.cleanup)
        snapshot = root / verifier.EXPECTED_SNAPSHOT_PATH
        snapshot.write_text(
            '{"schema_version": 1, "schema_version": 1}\n',
            encoding="utf-8",
        )

        report = verifier.verify(root)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("duplicate JSON key" in error for error in report["errors"]),
            report["errors"],
        )

    def test_incomplete_inventory_fails_even_with_forged_data_digests(self) -> None:
        temporary, root = self.temporary_root()
        self.addCleanup(temporary.cleanup)
        snapshot = root / verifier.EXPECTED_SNAPSHOT_PATH
        lock_path = root / verifier.EXPECTED_LOCK_PATH
        inventory = json.loads(snapshot.read_text(encoding="utf-8"))
        inventory["entries"]["cli"].pop()
        content = (json.dumps(inventory, indent=2) + "\n").encode("utf-8")
        snapshot.write_bytes(content)

        # Simulate an accidental or dishonest edit that also refreshes the
        # data-file digests. The verifier's independently pinned constants and
        # structural counts must still reject it.
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["pulp_source"]["authoritative_map"]["sha256"] = (
            verifier.sha256_bytes(content)
        )
        lock["pulp_source"]["authoritative_map"]["git_blob_sha1"] = (
            verifier.git_blob_sha1(content)
        )
        lock["inventory_counts"]["cli"] -= 1
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

        report = verifier.verify(root)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("expected 54 entries, got 53" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertTrue(
            any("differs from pinned baseline" in error for error in report["errors"]),
            report["errors"],
        )

    def test_source_blob_tamper_fails_with_refreshed_declared_digest(self) -> None:
        temporary, root = self.temporary_root()
        self.addCleanup(temporary.cleanup)
        snapshot = root / verifier.EXPECTED_SNAPSHOT_PATH
        lock_path = root / verifier.EXPECTED_LOCK_PATH
        inventory = json.loads(snapshot.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["pulp_source"]["concrete_source_blobs"]["cli"][0][
            "git_blob_sha1"
        ] = "0" * 40
        canonical = verifier._canonical_source_rows(
            lock["pulp_source"]["concrete_source_blobs"],
            list(inventory["source_files"]),
        )
        lock["pulp_source"]["concrete_source_blobs_sha256"] = (
            verifier.sha256_bytes(canonical)
        )
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

        report = verifier.verify(root)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any(
                "concrete_source_blobs: differs from pinned baseline" in error
                for error in report["errors"]
            ),
            report["errors"],
        )

    def test_malformed_source_blob_record_is_rejected_without_crashing(self) -> None:
        temporary, root = self.temporary_root()
        self.addCleanup(temporary.cleanup)
        lock_path = root / verifier.EXPECTED_LOCK_PATH
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["pulp_source"]["concrete_source_blobs"]["cli"][0] = "not-an-object"
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

        report = verifier.verify(root)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("expected object" in error for error in report["errors"]),
            report["errors"],
        )

    def test_authority_or_adoption_claim_is_rejected(self) -> None:
        temporary, root = self.temporary_root()
        self.addCleanup(temporary.cleanup)
        lock_path = root / verifier.EXPECTED_LOCK_PATH
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["status"]["authority_transfer"] = True
        lock["status"]["pulp_adoption"] = True
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

        report = verifier.verify(root)

        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "lock.status.authority_transfer: expected False",
            report["errors"],
        )
        self.assertIn(
            "lock.status.pulp_adoption: expected False",
            report["errors"],
        )


if __name__ == "__main__":
    unittest.main()
