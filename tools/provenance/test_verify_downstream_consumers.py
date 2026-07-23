#!/usr/bin/env python3
"""Tests for the offline downstream-consumer registry verifier."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import verify_downstream_consumers as verifier


class DownstreamConsumerRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = verifier.load(verifier.DEFAULT_REGISTRY)

    def assert_invalid(self, mutation, message: str) -> None:
        value = copy.deepcopy(self.registry)
        mutation(value)
        with self.assertRaisesRegex(verifier.RegistryError, message):
            verifier.validate_registry(value)

    def test_checked_in_registry_is_valid(self) -> None:
        verifier.validate_registry(self.registry)

    def test_palette_board_proof_is_pinned_to_reviewed_tuple(self) -> None:
        framework = self.registry["framework"]
        consumer = self.registry["consumers"][0]
        self.assertEqual(framework["version"], "v0.1.1")
        self.assertEqual(
            framework["sourceCommit"],
            "e282eb6b133c1275eda4c7338acf817e18af599c",
        )
        self.assertEqual(
            framework["artifact"]["sha256"],
            "1866345a14d74d19da053f08bb4b61ecae98ef98dfd53b0cd38b174281f2ccd5",
        )
        self.assertEqual(consumer["id"], "vellum-palette-board")
        self.assertEqual(
            consumer["commit"],
            "c8f23db2af615b0c1d480d058eef8a03867f738a",
        )
        self.assertEqual(
            consumer["evidenceDigest"]["sha256"],
            "24c89d7509335661ccfb5e812fcb9da3af5ab6d0398a2695bdbc200f7c1d70f8",
        )

    def test_cli_is_offline_and_accepts_explicit_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(self.registry), encoding="utf-8")
            self.assertEqual(verifier.main(["--registry", str(path)]), 0)

    def test_rejects_abbreviated_consumer_commit(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0].__setitem__("commit", "c8f23db"),
            "immutable full value",
        )

    def test_rejects_unpinned_framework_version(self) -> None:
        self.assert_invalid(
            lambda value: value["framework"].__setitem__("version", "latest"),
            "immutable full value",
        )

    def test_rejects_malformed_artifact_digest(self) -> None:
        self.assert_invalid(
            lambda value: value["framework"]["artifact"].__setitem__(
                "sha256", "sha256:mutable"
            ),
            "immutable full value",
        )

    def test_rejects_framework_as_consumer_repository(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0].__setitem__(
                "repository", value["framework"]["repository"]
            ),
            "separate repository",
        )

    def test_rejects_missing_evidence_rung(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0]["evidenceLadder"].pop(),
            "evidenceLadder ids differ",
        )

    def test_rejects_non_string_evidence_id_as_schema_error(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0]["evidenceLadder"][0].__setitem__(
                "id", []
            ),
            "id must be non-empty",
        )

    def test_rejects_unpassed_evidence(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0]["evidenceLadder"][0].__setitem__(
                "status", "planned"
            ),
            "status must be passed",
        )

    def test_rejects_consumer_first_fix_order(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0][
                "frameworkFirstFixProtocol"
            ].__setitem__(
                "requiredSequence",
                list(reversed(verifier.FIX_SEQUENCE)),
            ),
            "framework-first sequence differs",
        )

    def test_rejects_incomplete_workaround_exception(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0][
                "frameworkFirstFixProtocol"
            ]["exceptions"].append({"owner": "someone"}),
            "fields differ",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
