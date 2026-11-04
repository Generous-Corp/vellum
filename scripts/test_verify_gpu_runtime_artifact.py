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

import hashlib  # noqa: E402


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
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
        for member, source in verifier.VERBATIM_MODULES.items():
            if member != verifier.LOCK_MEMBER:
                (self.payload / member).write_bytes((ROOT / source).read_bytes())
        (self.payload / builder.PACKAGE_DIR / "VellumGpuRuntimeTargets-release.cmake").write_text(
            "".join(f'  IMPORTED_LOCATION_RELEASE "${{_IMPORT_PREFIX}}/{library}"\n'
                    for library in builder.LIBRARIES), encoding="utf-8")
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

    def rewrite(self, archive: Path, extra: list[tuple[tarfile.TarInfo, bytes | None]],
                record_rows: list[dict] | None = None) -> None:
        """Rewrite the archive member by member, appending `extra` and, when
        given, record rows the attacker adds to keep the record consistent."""
        with tarfile.open(archive, "r:gz") as source:
            members = [(m, source.extractfile(m).read() if m.isreg() else None)
                       for m in source.getmembers()]
        with tarfile.open(archive, "w:gz") as target:
            for member, data in members:
                if member.name == verifier.METADATA_NAME and record_rows:
                    record = json.loads(data)
                    record["files"].extend(record_rows)
                    data = json.dumps(record).encode()
                    member.size = len(data)
                target.addfile(member, io.BytesIO(data) if data is not None else None)
            for member, data in extra:
                target.addfile(member, io.BytesIO(data) if data is not None else None)

    def test_a_tampered_module_with_a_matching_record_is_refused_against_the_source(self) -> None:
        (self.payload / builder.PACKAGE_DIR / "GpuRuntimeDawn.cmake").write_text(
            "# digest check removed\n", encoding="utf-8")
        archive = self.archive()
        verifier.verify(archive, None)  # self-consistent, which is why --repo exists
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify(archive, ROOT, self.generated())
        self.assertIn("differs from the source", str(caught.exception))

    def test_an_extra_library_is_refused(self) -> None:
        (self.payload / "lib/libevil.a").write_bytes(b"!<arch>\n")
        self.assert_refused(self.archive(), "outside the gpu-runtime package")

    def test_case_colliding_members_are_refused(self) -> None:
        archive = self.archive()
        evil = b"!<arch>\nEVIL DAWN\n"
        info = tarfile.TarInfo("lib/LIBDAWN_COMBINED.a")
        info.size = len(evil)
        self.rewrite(archive, [(info, evil)], [{
            "path": "lib/LIBDAWN_COMBINED.a", "sha256": sha256_bytes(evil),
            "size": len(evil), "executable": False}])
        self.assert_refused(archive, "collide")

    def test_unicode_normalization_collisions_are_refused(self) -> None:
        archive = self.archive()
        decomposed = "include/gpu_runtime/caf\u0065\u0301.hpp"
        composed = "include/gpu_runtime/caf\u00e9.hpp"
        extra = []
        for name in (composed, decomposed):
            info = tarfile.TarInfo(name)
            info.size = 1
            extra.append((info, b"x"))
        self.rewrite(archive, extra)
        # Names outside the header pattern are refused before they can collide;
        # the fold itself must also equate the two spellings.
        self.assert_refused(archive, "")
        self.assertEqual(verifier.fold(composed), verifier.fold(decomposed))
        self.assertEqual(verifier.fold("lib/LIBDAWN_COMBINED.a"), verifier.fold(builder.DAWN_LIBRARY))

    def test_duplicate_members_are_refused(self) -> None:
        archive = self.archive()
        evil = b"!<arch>\nEVIL DAWN\n"
        info = tarfile.TarInfo(builder.DAWN_LIBRARY)
        info.size = len(evil)
        self.rewrite(archive, [(info, evil)])
        self.assert_refused(archive, "collide")

    def test_a_hardlink_member_is_refused(self) -> None:
        archive = self.archive()
        info = tarfile.TarInfo("include/gpu_runtime/linked.hpp")
        info.type = tarfile.LNKTYPE
        info.linkname = builder.DAWN_LIBRARY
        self.rewrite(archive, [(info, None)])
        self.assert_refused(archive, "not a regular file")

    def test_a_non_canonical_stream_is_refused(self) -> None:
        # Same files and record, but a header field the builder never writes.
        archive = self.archive()
        with tarfile.open(archive, "r:gz") as source:
            members = [(m, source.extractfile(m).read() if m.isreg() else None)
                       for m in source.getmembers()]
        with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as target:
            for member, data in members:
                member.pax_headers = {"path": member.name} if member.isreg() else {}
                target.addfile(member, io.BytesIO(data) if data is not None else None)
        self.assert_refused(archive, "canonical tar stream")

    def test_an_executable_or_setuid_member_is_refused(self) -> None:
        (self.payload / builder.DAWN_LIBRARY).chmod(0o4755)
        self.assert_refused(self.archive(), "marked executable")

    def source_files(self) -> dict[str, bytes]:
        """What --repo expects, file for file, built from the source itself."""
        files = {name: b"" for name in builder.expected_files(ROOT)}
        include = ROOT / "gpu-runtime/include"
        for header in (include / "gpu_runtime").rglob("*.hpp"):
            files["include/" + header.relative_to(include).as_posix()] = header.read_bytes()
        for member, source in verifier.VERBATIM_MODULES.items():
            files[member] = (ROOT / source).read_bytes()
        files[verifier.LICENSE_MEMBER] = (ROOT / "LICENSE.md").read_bytes()
        files[verifier.NOTICE_MEMBER] = builder.dawn_notice(ROOT).encode()
        files.update(self.generated())
        return files

    def generated(self) -> dict[str, bytes]:
        """Stand-ins for what configuring the source generates (the unit tests
        do not configure; the real regeneration runs in gpu-macos)."""
        return {member: f"# generated {member}\n".encode() for member in verifier.GENERATED}

    def source_record(self) -> dict:
        head = verifier.git_head(ROOT)
        return {"source_commit": head, "version": VERSION}

    def test_source_comparison_accepts_the_source(self) -> None:
        verifier.verify_against_source(self.source_files(), self.source_record(), ROOT,
                                       self.generated())

    def test_a_record_from_another_commit_is_refused_against_the_source(self) -> None:
        record = dict(self.source_record(), source_commit="b" * 40)
        with self.assertRaisesRegex(verifier.VerificationError, "another source commit"):
            verifier.verify_against_source(self.source_files(), record, ROOT, self.generated())

    def test_a_record_of_another_version_is_refused_against_the_source(self) -> None:
        record = dict(self.source_record(), version="9.9.9")
        with self.assertRaisesRegex(verifier.VerificationError, "another version"):
            verifier.verify_against_source(self.source_files(), record, ROOT, self.generated())

    def test_a_notice_other_than_the_assembled_one_is_refused(self) -> None:
        files = self.source_files()
        files[verifier.NOTICE_MEMBER] += b"\n### Extra\n"
        with self.assertRaisesRegex(verifier.VerificationError, "notice differs"):
            verifier.verify_against_source(files, self.source_record(), ROOT, self.generated())

    def test_every_generated_package_file_is_held_to_the_source(self) -> None:
        injected = b'execute_process(COMMAND /bin/sh -c "touch /tmp/pwned")\n'
        for member in verifier.GENERATED:
            for label, edit in (("prepended", lambda data: injected + data),
                                ("appended", lambda data: data + injected)):
                with self.subTest(member=member, edit=label):
                    files = self.source_files()
                    files[member] = edit(files[member])
                    with self.assertRaisesRegex(verifier.VerificationError,
                                                "differs from what the source generates"):
                        verifier.verify_against_source(files, self.source_record(), ROOT,
                                                       self.generated())

    def test_holding_to_the_source_needs_regenerated_files(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "regenerated package files"):
            verifier.verify(self.archive(), ROOT)

    def test_a_file_set_other_than_the_source_is_refused(self) -> None:
        files = self.source_files()
        del files[sorted(name for name in files if name.startswith("include/"))[0]]
        with self.assertRaisesRegex(verifier.VerificationError, "differ from what the source installs"):
            verifier.verify_against_source(files, self.source_record(), ROOT, self.generated())

    def test_targets_importing_another_library_are_refused(self) -> None:
        path = self.payload / builder.PACKAGE_DIR / "VellumGpuRuntimeTargets-release.cmake"
        path.write_text(path.read_text().replace("libgpu-runtime-device.a", "libdawn_combined.a"),
                        encoding="utf-8")
        self.assert_refused(self.archive(), "import something other")

    def test_an_unlisted_file_is_refused(self) -> None:
        archive = self.archive(metadata_edit=lambda m: m.update(
            files=[row for row in m["files"] if row["path"] != builder.DAWN_LIBRARY]))
        self.assert_refused(archive, "differ from the artifact record")

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
            verifier.verify(self.archive(), ROOT, self.generated())
        self.assertIn("renderer-lock.json differs from the source", str(caught.exception))

    def test_a_dawn_library_the_lock_does_not_name_is_refused(self) -> None:
        (self.payload / builder.DAWN_LIBRARY).write_bytes(b"!<arch>\nanother build\n")
        self.assert_refused(self.archive(), "not the library the renderer lock names")

    def test_a_missing_notice_is_refused(self) -> None:
        (self.payload / builder.DOC_DIR / "NOTICE.md").write_text("### Dawn and Tint\n", encoding="utf-8")
        self.assert_refused(self.archive(), "notice lacks ### Skia and skcms")

    def test_the_notice_carries_every_component_inside_the_dawn_archive(self) -> None:
        notice = builder.dawn_notice(ROOT)
        lock = json.loads((ROOT / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
        inside = [row["name"] for row in
                  lock["runtime_toolchain_dependencies"]["renderer"]["bundled_components"]
                  if row.get("parent_archive") == "libdawn_combined.a"]
        self.assertTrue(inside)
        for name in ["Dawn and Tint", *inside, "Skia and skcms", "Apache License, Version 2.0"]:
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
