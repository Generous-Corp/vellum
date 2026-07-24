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
        self.assertEqual(framework["version"], "v0.1.6")
        self.assertEqual(
            framework["sourceCommit"],
            "9595737b1903819bc497ee41f870b0f36e667c42",
        )
        self.assertEqual(
            framework["artifact"]["sha256"],
            "cb2a6372f266a8baeb6f7b52c273cb453daa798846b6abf7c59e45dab38e652b",
        )
        self.assertEqual(consumer["id"], "vellum-palette-board")
        self.assertEqual(
            consumer["commit"],
            "7137e045b6a135595704b06b009fdc3c19691410",
        )
        self.assertEqual(
            consumer["evidenceDigest"]["sha256"],
            "b2072e7c1e05e1015091bb4a2cdb4c2f7fca6eebf0b49b3334dc3d3c15780423",
        )

    def test_cli_is_offline_and_accepts_explicit_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(self.registry), encoding="utf-8")
            self.assertEqual(verifier.main(["--registry", str(path)]), 0)

    def test_rejects_abbreviated_consumer_commit(self) -> None:
        self.assert_invalid(
            lambda value: value["consumers"][0].__setitem__("commit", "7137e04"),
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
