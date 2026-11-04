#!/usr/bin/env python3
"""Unit tests for verify_gpu_runtime_artifact.py on synthetic archives.

Each case writes an archive the way build_gpu_runtime_artifact.py does (the
same payload rows and normalized tar writer), then breaks one property and
proves the verifier refuses it.
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_gpu_runtime_artifact as builder  # noqa: E402
from build_sdk_artifact import payload_files, sha256, write_archive  # noqa: E402
import verify_gpu_runtime_artifact as verifier  # noqa: E402

VERSION = builder.vellum_version(ROOT)
NAME = builder.archive_name(VERSION, "darwin-arm64")


class GpuRuntimeArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.payload = self.root / "payload"
        include = ROOT / "gpu-runtime/include"
        for header in (include / "gpu_runtime").rglob("*.hpp"):
            target = self.payload / "include" / header.relative_to(include)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(header.read_bytes())
        for library in builder.LIBRARIES:
            path = self.payload / library
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"!<arch>\n")
        for package_file in builder.PACKAGE_FILES:
            path = self.payload / package_file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# package file\n", encoding="utf-8")
        # The real libdawn_combined.a is not in the repository, so the shipped
        # lock names this stand-in's digest; checks against the source lock
        # are exercised on their own below.
        dawn = self.payload / builder.DAWN_LIBRARY
        dawn.write_bytes(b"!<arch>\nstand-in for libdawn_combined.a\n")
        lock = json.loads((ROOT / builder.LOCK).read_text(encoding="utf-8"))
        lock["platforms"]["macos-arm64"]["archives"]["libdawn_combined.a"] = sha256(dawn)
        self.lock_path = self.payload / builder.PACKAGE_DIR / "renderer-lock.json"
        self.lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        docs = self.payload / builder.DOC_DIR
        docs.mkdir(parents=True)
        (docs / "LICENSE.md").write_bytes((ROOT / "LICENSE.md").read_bytes())
        (docs / "NOTICE.md").write_text(builder.dawn_notice(ROOT), encoding="utf-8")
        self.metadata = {
            "schema": verifier.METADATA_SCHEMA,
            "name": "vellum-gpu-runtime",
            "version": VERSION,
            "target": "darwin-arm64",
            "source_commit": "a" * 40,
            "source_tree_clean": True,
            "renderer_lock_sha256": sha256(self.lock_path),
            "renderer_milestone": json.loads((ROOT / builder.LOCK).read_text())["milestone"],
        }

    def archive(self, name: str = NAME, metadata_edit=None) -> Path:
        metadata = dict(self.metadata, files=payload_files(self.payload))
        if metadata_edit is not None:
            metadata_edit(metadata)
        (self.payload / verifier.METADATA_NAME).write_text(json.dumps(metadata), encoding="utf-8")
        output = self.root / name
        write_archive(self.payload, output)
        (self.payload / verifier.METADATA_NAME).unlink()
        return output

    def assert_refused(self, archive: Path, fragment: str) -> None:
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify(archive, None)
        self.assertIn(fragment, str(caught.exception))

    def test_a_well_formed_archive_verifies(self) -> None:
        result = verifier.verify(self.archive(), None)
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(sorted(builder.expected_files(ROOT)),
                         sorted([*(row["path"] for row in payload_files(self.payload)),
                                 verifier.METADATA_NAME]))

    def test_a_tampered_file_is_refused(self) -> None:
        archive = self.archive(metadata_edit=lambda m: m["files"][0].update(sha256="0" * 64))
        self.assert_refused(archive, "digest mismatch")

    def test_an_unlisted_file_is_refused(self) -> None:
        (self.payload / "lib/extra.a").write_bytes(b"!<arch>\n")
        listed_without_extra = self.archive(metadata_edit=lambda m: m.update(
            files=[row for row in m["files"] if row["path"] != "lib/extra.a"]))
        self.assert_refused(listed_without_extra, "differ from the artifact record")

    def test_a_file_outside_the_package_is_refused(self) -> None:
        (self.payload / "bin").mkdir()
        (self.payload / "bin/tool").write_text("x", encoding="utf-8")
        self.assert_refused(self.archive(), "outside the gpu-runtime package")

    def test_a_link_member_is_refused(self) -> None:
        archive = self.archive()
        with tarfile.open(archive, "r:gz") as source:
            members = [(m, source.extractfile(m).read() if m.isreg() else None)
                       for m in source.getmembers()]
        link = tarfile.TarInfo("lib/libgpu-runtime-device.dylib")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        with tarfile.open(archive, "w:gz") as target:
            for member, data in members:
                target.addfile(member, io.BytesIO(data) if data is not None else None)
            target.addfile(link)
        self.assert_refused(archive, "not a regular file")

    def test_an_escaping_path_is_refused(self) -> None:
        archive = self.archive()
        with tarfile.open(archive, "w:gz") as target:
            info = tarfile.TarInfo("../outside")
            info.size = 1
            target.addfile(info, io.BytesIO(b"x"))
        self.assert_refused(archive, "unsafe path")

    def test_a_dirty_build_is_refused(self) -> None:
        self.assert_refused(self.archive(metadata_edit=lambda m: m.update(source_tree_clean=False)),
                            "dirty tree")

    def test_a_record_naming_another_lock_is_refused(self) -> None:
        self.assert_refused(
            self.archive(metadata_edit=lambda m: m.update(renderer_lock_sha256="1" * 64)),
            "different renderer lock")

    def test_a_lock_that_differs_from_the_source_is_refused(self) -> None:
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify(self.archive(), ROOT)
        self.assertIn("shipped renderer lock differs from the source", str(caught.exception))

    def test_a_dawn_library_the_lock_does_not_name_is_refused(self) -> None:
        (self.payload / builder.DAWN_LIBRARY).write_bytes(b"!<arch>\nanother build\n")
        self.assert_refused(self.archive(), "not the library the renderer lock names")

    def test_a_missing_notice_is_refused(self) -> None:
        (self.payload / builder.DOC_DIR / "NOTICE.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_refused(self.archive(), "no Dawn notice")

    def test_the_notice_carries_every_component_inside_the_dawn_archive(self) -> None:
        notice = builder.dawn_notice(ROOT)
        lock = json.loads((ROOT / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
        inside = [row["name"] for row in
                  lock["runtime_toolchain_dependencies"]["renderer"]["bundled_components"]
                  if row.get("parent_archive") == "libdawn_combined.a"]
        self.assertTrue(inside)
        for name in ["Dawn and Tint", *inside, "Apache License, Version 2.0"]:
            self.assertIn(f"### {name}\n", notice)
        sections = builder.notice_sections((ROOT / "NOTICE.md").read_text(encoding="utf-8"))
        self.assertIn(sections["Dawn and Tint"], notice)

    def test_a_component_without_a_notice_section_stops_the_build(self) -> None:
        original = builder.notice_sections
        builder.notice_sections = lambda text: {
            key: value for key, value in original(text).items() if key != "Abseil"}
        try:
            with self.assertRaises(builder.ArtifactError):
                builder.dawn_notice(ROOT)
        finally:
            builder.notice_sections = original

    def test_a_mismatched_name_is_refused(self) -> None:
        self.assert_refused(self.archive(name=builder.archive_name("9.9.9", "darwin-arm64")),
                            "version differs from the name")
        self.assert_refused(self.archive(name="gpu-runtime.tar.gz"), "archive name")

    def test_a_missing_library_is_refused(self) -> None:
        (self.payload / "lib/libgpu-runtime-device.a").unlink()
        self.assert_refused(self.archive(), "libgpu-runtime-device.a")


if __name__ == "__main__":
    unittest.main()
