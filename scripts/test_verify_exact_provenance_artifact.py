#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify_exact_provenance_artifact.py")
SPEC = importlib.util.spec_from_file_location("verify_exact_provenance_artifact", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

COMMIT = "a" * 40
TAG_OBJECT = "b" * 40


def trust() -> dict:
    return {
        "repository": "Generous-Corp/vellum",
        "tag": "v1.2.3",
        "source_commit": COMMIT,
        "tag_object_sha": TAG_OBJECT,
        "tag_object_type": "tag",
        "peeled_commit": COMMIT,
    }


class Tests(unittest.TestCase):
    def test_accepts_exact_release_scoped_sibling_evidence(self) -> None:
        verifier.verify(
            trust(), {"status": "pass", "release_readiness_requested": True},
            repository="Generous-Corp/vellum", tag="v1.2.3",
            source_commit=COMMIT, tag_object_sha=TAG_OBJECT,
        )

    def test_rejects_recreated_tag_object_and_coordinate_drift(self) -> None:
        for key, value in (
            ("tag_object_sha", "c" * 40),
            ("source_commit", "c" * 40),
            ("tag", "v9.9.9"),
            ("repository", "danielraffel/vellum"),
        ):
            evidence = trust()
            evidence[key] = value
            with self.subTest(key=key), self.assertRaises(verifier.VerificationError):
                verifier.verify(
                    evidence, {"status": "pass", "release_readiness_requested": True},
                    repository="Generous-Corp/vellum", tag="v1.2.3",
                    source_commit=COMMIT, tag_object_sha=TAG_OBJECT,
                )

    def test_rejects_non_release_or_failed_readiness(self) -> None:
        for readiness in (
            {"status": "fail", "release_readiness_requested": True},
            {"status": "pass", "release_readiness_requested": False},
        ):
            with self.assertRaises(verifier.VerificationError):
                verifier.verify(
                    trust(), readiness, repository="Generous-Corp/vellum",
                    tag="v1.2.3", source_commit=COMMIT,
                    tag_object_sha=TAG_OBJECT,
                )

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "duplicate JSON key"):
            verifier.strict_object([("tag", "v1"), ("tag", "v2")])


if __name__ == "__main__":
    unittest.main()
