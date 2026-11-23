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

COPIED = (drift.LOCK, *drift.READERS, *drift.RECORDS, *drift.DOCUMENTS,
          drift.evidence.PLATFORM_FILE)


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

    @staticmethod
    def add_row(lock: dict, key: str, windows: bool = False) -> dict:
        """Add a synthetic non-macOS row; real rows land in a later PR."""
        import hashlib
        def digest(label: str) -> str:
            return hashlib.sha256(f"{key}/{label}".encode()).hexdigest()
        names = ["skia", "dawn_combined", "svg", "skresources", "skshaper", "skparagraph",
                 "skunicode_core", "skunicode_icu"]
        files = [f"{n}.lib" if windows else f"lib{n}.a" for n in names]
        row = {"asset": drift.evidence.PLATFORM_ASSETS[key], "sha256": digest("asset"),
               "configuration": "Release", "archive_root": f"build/{key}/lib/Release",
               "release": "chrome/m158-attested.2", "builder_commit": "a" * 40,
               "archives": {f: digest(f) for f in files}}
        lock["platforms"][key] = row
        return row

    def write_evidence(self) -> None:
        """Pin evidence that agrees with the rows now in the lock."""
        lock = json.loads((self.root / drift.LOCK).read_text(encoding="utf-8"))
        records = {}
        for key, row in lock["platforms"].items():
            if key == drift.PLATFORM:
                continue
            stem = row["asset"].removesuffix(".zip")
            manifest = "1" * 64
            records[key] = {"asset": row["asset"], "sha256": row["sha256"],
                            "legal_manifest": {"name": f"{stem}.legal-manifest.sha256-{manifest}.json",
                                               "sha256": manifest},
                            "configuration": "Release", "archive_root": row["archive_root"],
                            "archives": dict(row["archives"])}
        target = self.root / drift.evidence.EVIDENCE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "schema": drift.evidence.SCHEMA, "release": "chrome/m158-attested.2",
            "builder": {"repository": "https://github.com/danielraffel/skia-builder",
                        "commit": "a" * 40},
            "attestation": {"sha256": "2" * 64}, "platforms": records}), encoding="utf-8")

    def with_rows(self, change=None) -> None:
        def edit(lock):
            self.add_row(lock, "linux-x64")
            self.add_row(lock, "linux-arm64")
            self.add_row(lock, "windows-x64", windows=True)
        self.edit_json(drift.LOCK, edit)
        self.write_evidence()
        if change:
            self.edit_json(drift.LOCK, change)

    def test_a_row_added_to_the_lock_is_checked_and_agrees(self) -> None:
        self.with_rows()
        self.assertEqual(drift.check(self.root), [])

    def test_a_zeroed_digest_in_a_non_macos_row_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["linux-x64"]["archives"]
                       .update({"libskia.a": "0" * 64}))
        self.assert_drift("linux-x64 libskia.a digest is an all-zero placeholder")

    def test_a_zeroed_asset_digest_in_a_non_macos_row_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["linux-arm64"].update(sha256="0" * 64))
        self.assert_drift("linux-arm64 asset digest is an all-zero placeholder")

    def test_swapped_asset_digests_across_rows_are_drift(self) -> None:
        def swap(lock):
            rows = lock["platforms"]
            rows["linux-arm64"]["sha256"] = rows["linux-x64"]["sha256"]
        self.with_rows(swap)
        self.assert_drift("asset digest repeats linux-x64 asset")

    def test_a_row_without_a_release_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["linux-x64"].pop("release"))
        self.assert_drift("linux-x64 release is not")

    def test_a_windows_row_must_lock_lib_names(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["windows-x64"]["archives"].pop("skia.lib"))
        self.assert_drift("windows-x64 does not lock skia.lib")

    def test_a_repeated_asset_name_is_drift(self) -> None:
        def repeat(lock):
            lock["platforms"]["linux-arm64"]["asset"] = lock["platforms"]["linux-x64"]["asset"]
        self.with_rows(repeat)
        self.assert_drift("asset skia-build-linux-x64-gpu-release.zip repeats linux-x64")

    def test_a_bad_archive_root_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["linux-x64"].update(archive_root="/etc"))
        self.assert_drift("linux-x64 archive_root is not a build/ relative path")

    def test_a_row_release_of_another_milestone_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["linux-x64"]
                       .update(release="chrome/m999-attested.7"))
        self.assert_drift("linux-x64 release chrome/m999-attested.7 is not of milestone chrome/m158")

    def test_a_mutable_release_on_a_non_macos_row_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["linux-x64"].update(release="chrome/m158"))
        self.assert_drift("linux-x64 release is not chrome/mNNN-attested.N")

    def test_a_malformed_release_on_a_non_macos_row_is_drift(self) -> None:
        self.with_rows(lambda lock: lock["platforms"]["windows-x64"].update(release="latest"))
        self.assert_drift("windows-x64 release is not chrome/mNNN-attested.N")

    def test_a_macos_release_other_than_the_milestone_is_drift(self) -> None:
        for release in ("chrome/m999-attested.7", "chrome/m158-attested.2", "latest"):
            with self.subTest(release=release):
                self.setUp()
                self.edit_json(drift.LOCK, lambda lock: lock["platforms"][drift.PLATFORM]
                               .update(release=release))
                self.assert_drift(f"{drift.PLATFORM} release is {release!r}")

    def test_a_macos_release_equal_to_the_milestone_agrees(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock["platforms"][drift.PLATFORM]
                       .update(release=lock["milestone"]))
        self.assertEqual(drift.check(self.root), [])

    def test_an_attested_top_level_milestone_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock.update(milestone="chrome/m158-attested.1"))
        self.assert_drift("milestone is not chrome/mNNN")

    def test_a_row_without_a_builder_commit_is_drift(self) -> None:
        for bad in (None, "z" * 40, "a" * 39):
            with self.subTest(builder_commit=bad):
                self.setUp()
                def change(lock, bad=bad):
                    row = lock["platforms"]["linux-arm64"]
                    row.pop("builder_commit") if bad is None else row.update(builder_commit=bad)
                self.with_rows(change)
                self.assert_drift("linux-arm64 builder_commit is not a 40-character commit")

    def test_a_non_release_asset_name_is_drift(self) -> None:
        for asset in ("evil.zip", "skia-build-linux-x64-gpu-debug.zip", "../skia-build-x-gpu-release.zip"):
            with self.subTest(asset=asset):
                self.setUp()
                self.with_rows(lambda lock: lock["platforms"]["linux-x64"].update(asset=asset))
                self.assert_drift("linux-x64 asset name is not a skia-builder GPU release zip")

    def test_a_non_macos_digest_held_by_another_file_is_drift(self) -> None:
        """The no-other-copies scan covers every row, not just macOS."""
        self.with_rows()
        copy = self.root / "docs" / "hand-copy.md"
        copy.parent.mkdir(parents=True, exist_ok=True)
        lock = json.loads((self.root / drift.LOCK).read_text(encoding="utf-8"))
        digest = lock["platforms"]["windows-x64"]["archives"]["skia.lib"]
        copy.write_text(f"skia.lib {digest}\n", encoding="utf-8")
        self.assert_drift(f"docs/hand-copy.md: holds locked digest {digest}")

    def test_a_macos_digest_held_by_another_file_is_still_drift(self) -> None:
        copy = self.root / "docs" / "hand-copy.md"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(f"{self.platform['sha256']}\n", encoding="utf-8")
        self.assert_drift("docs/hand-copy.md: holds locked digest")

    def test_a_deleted_macos_row_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock["platforms"].pop(drift.PLATFORM))
        self.assert_drift(f"platform {drift.PLATFORM} is missing")

    def test_an_unchanged_copy_agrees(self) -> None:
        self.assertEqual(drift.check(self.root), [])

    def test_bumping_the_lock_alone_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock.update(milestone="chrome/m159"))
        self.assert_drift("third-party-lock.json: renderer artifact release_tag")
        self.assert_drift("renderer-attestation.json: artifact release_tag")
        self.assert_drift("README.md: names chrome/m158")

    def test_one_changed_archive_digest_is_drift(self) -> None:
        self.edit_json(drift.LOCK, lambda lock: lock["platforms"][drift.PLATFORM]["archives"]
                       .update({"libskia.a": "1" * 64}))
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
        self.edit_text("cmake/VellumSkiaDawn.cmake", 'set(_vellum_svg_name "libsvg.a")',
                       'set(_vellum_svg_name "libsvg2.a")')
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
        self.assert_drift("asset digest is missing or malformed")


if __name__ == "__main__":
    unittest.main()
