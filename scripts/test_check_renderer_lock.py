#!/usr/bin/env python3
"""Unit tests for check_renderer_lock.py: each kind of drift fails on its own."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_renderer_lock as drift  # noqa: E402

COPIED = (drift.LOCK, *drift.READERS, *drift.RECORDS, *drift.DOCUMENTS)


class RendererLockDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in COPIED:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.lock = json.loads((self.root / drift.LOCK).read_text(encoding="utf-8"))
        self.platform = self.lock["platforms"][drift.PLATFORM]

    def edit_json(self, relative: str, change) -> None:
        path = self.root / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def edit_text(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"{old!r} is not in {relative}, so the case tests nothing")
        path.write_text(text.replace(old, new), encoding="utf-8")

    def assert_drift(self, fragment: str) -> None:
        problems = drift.check(self.root)
        self.assertTrue(any(fragment in problem for problem in problems),
                        f"expected {fragment!r} in {problems}")

    def test_the_checkout_agrees(self) -> None:
        self.assertEqual(drift.check(ROOT), [])

    def test_an_unchanged_copy_agrees(self) -> None:
        self.assertEqual(drift.check(self.root), [])

    def test_bumping_the_lock_alone_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock.update(milestone="chrome/m159"))
        self.assert_drift("third-party-lock.json: renderer artifact release_tag")
        self.assert_drift("renderer-attestation.json: artifact release_tag")
        self.assert_drift("README.md: names chrome/m158")

    def test_one_changed_archive_digest_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock["platforms"][drift.PLATFORM]["archives"]
                       .update({"libskia.a": "0" * 64}))
        self.assert_drift("renderer archives differ from the lock: libskia.a")

    def test_a_changed_source_commit_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock["sources"].update(dawn="1" * 40))
        self.assert_drift("third-party-lock.json: dawn source commit")
        self.assert_drift("renderer-attestation.json: dawn source commit")

    def test_a_digest_restated_in_cmake_is_drift(self) -> None:
        self.edit_text("cmake/VellumSkiaDawn.cmake",
                       'set(_vellum_expected_archive_sha256 "${_vellum_renderer_SHA256}")',
                       f'set(_vellum_expected_archive_sha256 "{self.platform["sha256"]}")')
        self.assert_drift("VellumSkiaDawn.cmake: restates locked digest")

    def test_cmake_that_stops_reading_the_lock_is_drift(self) -> None:
        self.edit_text("cmake/VellumSkiaDawn.cmake", "gpu_runtime_read_renderer_lock(",
                       "message(")
        self.assert_drift("VellumSkiaDawn.cmake: does not read")

    def test_cmake_reading_an_unlocked_archive_is_drift(self) -> None:
        self.edit_text("cmake/VellumSkiaDawn.cmake", "_vellum_renderer_SHA256_libsvg.a",
                       "_vellum_renderer_SHA256_libsvg2.a")
        self.assert_drift("reads libsvg2.a, which the lock does not lock")

    def test_a_milestone_restated_in_the_workflow_is_drift(self) -> None:
        self.edit_text(".github/workflows/gpu-macos.yml", '"$url"',
                       '"https://github.com/danielraffel/skia-builder/releases/download/'
                       'chrome/m158/skia-build-mac-universal-gpu-release.zip"')
        self.assert_drift("gpu-macos.yml: restates milestone chrome/m158")

    def test_an_attestation_record_from_another_release_is_drift(self) -> None:
        self.edit_json(drift.RECORDS[1], lambda record: record["legal_inventory"]["manifest"]
                       .update(url=record["legal_inventory"]["manifest"]["url"]
                               .replace("chrome/m158", "chrome/m157")))
        self.assert_drift("is not an asset of the locked release")

    def test_a_document_with_a_stale_digest_is_drift(self) -> None:
        self.edit_text("DEPENDENCIES.md", self.platform["sha256"], "a" * 64)
        self.assert_drift("DEPENDENCIES.md: quotes skia-build-mac-universal-gpu-release.zip without")
        self.assert_drift("DEPENDENCIES.md: quotes skia-build-mac-universal-gpu-release.zip with stale")

    def test_a_new_hand_copy_anywhere_is_drift(self) -> None:
        (self.root / "cmake/Other.cmake").write_text(
            f'set(_pin "{self.platform["archives"]["libdawn_combined.a"]}")\n', encoding="utf-8")
        self.assert_drift("cmake/Other.cmake: holds locked digest")

    def test_an_uppercase_hand_copy_is_drift(self) -> None:
        digest = self.platform["archives"]["libskia.a"].upper()
        (self.root / "cmake/Other.cmake").write_text(f'set(_pin "{digest}")\n', encoding="utf-8")
        self.assert_drift("cmake/Other.cmake: holds locked digest")
        self.edit_text("cmake/VellumSkiaDawn.cmake",
                       'set(_vellum_expected_archive_sha256 "${_vellum_renderer_SHA256}")',
                       f'set(_vellum_expected_archive_sha256 "{self.platform["sha256"].upper()}")')
        self.assert_drift("VellumSkiaDawn.cmake: restates locked digest")

    def test_recorded_evidence_may_hold_a_digest(self) -> None:
        closure = self.root / "provenance/mirror/closures/x/unit.json"
        closure.parent.mkdir(parents=True)
        closure.write_text(json.dumps({"skia": self.platform["sha256"]}), encoding="utf-8")
        self.assertEqual(drift.check(self.root), [])

    def test_a_malformed_lock_is_refused(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock["platforms"][drift.PLATFORM]
                       .update(sha256="DCA7"))
        self.assert_drift("digests are missing or malformed")


if __name__ == "__main__":
    unittest.main()
