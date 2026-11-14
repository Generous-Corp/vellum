#!/usr/bin/env python3
"""Tests for verify_rmlui_sdk_artifact.py and the builder's payload rules.

Each test builds a synthetic RmlUi SDK payload with the builder's own file
rules and archive writer, so no compiler runs. The first test is the positive
control: an archive built the way the builder builds one verifies. Every
other test changes one thing and must be refused for that reason.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_rmlui_sdk_artifact as builder  # noqa: E402
from build_sdk_artifact import ArtifactError, payload_files, write_archive  # noqa: E402
import verify_rmlui_sdk_artifact as verifier  # noqa: E402

VERSION = "0.2.0"
NOTICE = ("# Third-party notices\n\nRmlUi, MIT License.\n\n"
          "Portions of this software are copyright © 2024 The FreeType Project "
          "(https://freetype.org). All rights reserved.\n"
          "FreeType is used under the FreeType License (FTL), electing it over "
          "the GPLv2 alternative.\n")
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person ...\n"


def contents(relative: str) -> bytes:
    if relative.endswith("RmlUi-LICENSE.txt"):
        return MIT.encode()
    if relative.endswith("NOTICE-Native.md"):
        return NOTICE.encode()
    if relative.endswith(".cmake"):
        return b'set(_IMPORT_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../..")\n'
    return f"{relative}\n".encode()


class RmlUiSdkArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = self.root / "payload"
        files = [path for path in builder.required_files() if path != builder.METADATA_NAME]
        files += ["include/RmlUi/Core/Element.h", "include/RmlUi/Core/Types.inl"]
        for relative in files:
            self.write(relative, contents(relative))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, data: bytes) -> None:
        path = self.payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def archive(self, *, version: str = VERSION, metadata_edit=None) -> Path:
        metadata = {
            "schema": builder.METADATA_SCHEMA,
            "name": "vellum-rmlui-sdk",
            "version": VERSION,
            "target": "darwin-arm64",
            "source_commit": "0" * 40,
            "source_tree_clean": True,
            "package": "VellumRmlUiNative",
            "components": ["Native", "Metal"],
            "files": payload_files(self.payload),
        }
        if metadata_edit:
            metadata_edit(metadata)
        (self.payload / builder.METADATA_NAME).write_text(json.dumps(metadata), encoding="utf-8")
        output = self.root / "out" / builder.archive_name(version, "darwin-arm64")
        write_archive(self.payload, output)
        (self.payload / builder.METADATA_NAME).unlink()
        return output

    def assert_refused(self, archive: Path, reason: str) -> None:
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify(archive)
        self.assertIn(reason, str(caught.exception))

    def test_an_archive_built_like_the_builder_verifies(self) -> None:
        result = verifier.verify(self.archive())
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(result["files"], len(builder.required_files()) + 2)

    def test_a_vellum_sdk_rmlui_package_file_is_refused(self) -> None:
        self.write("lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake", b"x\n")
        self.assert_refused(self.archive(), "extra ['lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake']")

    def test_the_core_shared_library_is_refused(self) -> None:
        self.write("lib/libvellum-rmlui-core.dylib", b"x")
        self.assert_refused(self.archive(), "libvellum-rmlui-core.dylib")

    def test_a_missing_required_file_is_refused(self) -> None:
        (self.payload / "lib/libvellum-rmlui-metal.a").unlink()
        self.assert_refused(self.archive(), "missing ['lib/libvellum-rmlui-metal.a']")

    def test_a_non_header_under_the_rmlui_include_root_is_refused(self) -> None:
        self.write("include/RmlUi/Core/notes.txt", b"x\n")
        self.assert_refused(self.archive(), "include/RmlUi/Core/notes.txt")

    def test_a_member_changed_after_the_metadata_is_refused(self) -> None:
        def tamper(metadata: dict) -> None:
            for row in metadata["files"]:
                if row["path"] == "lib/librmlui.a":
                    row["sha256"] = "0" * 64
        self.assert_refused(self.archive(metadata_edit=tamper), "lib/librmlui.a: SHA-256")

    def test_a_metadata_list_that_omits_a_member_is_refused(self) -> None:
        def drop(metadata: dict) -> None:
            metadata["files"] = [row for row in metadata["files"] if row["path"] != "lib/librmlui.a"]
        self.assert_refused(self.archive(metadata_edit=drop), "file list differs")

    def test_an_archive_from_another_commit_or_version_is_refused(self) -> None:
        archive = self.archive()
        verifier.verify(archive, source_commit="0" * 40, expected_version=VERSION)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify(archive, source_commit="1" * 40)
        self.assertIn("was built from", str(caught.exception))
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify(archive, expected_version="0.2.1")
        self.assertIn("not 0.2.1", str(caught.exception))

    def test_the_command_line_writes_a_verification_record(self) -> None:
        archive = self.archive()
        record = self.root / "record.json"
        with open(self.root / "stdout", "w") as sink:
            stdout, sys.stdout = sys.stdout, sink
            try:
                code = verifier.main(["--archive", str(archive), "--source-commit", "0" * 40,
                                      "--version", VERSION, "--output", str(record)])
            finally:
                sys.stdout = stdout
        self.assertEqual(code, 0)
        written = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(written["schema"], "vellum.rmlui-sdk-verification.v1")
        self.assertEqual(written["source_commit"], "0" * 40)

    def test_binding_accepts_the_validated_bytes_and_refuses_others(self) -> None:
        archive = self.archive()
        record = self.root / "record.json"
        with open(self.root / "stdout", "w") as sink:
            stdout, sys.stdout = sys.stdout, sink
            try:
                self.assertEqual(verifier.main(["--archive", str(archive), "--source-commit", "0" * 40,
                                                "--output", str(record)]), 0)
            finally:
                sys.stdout = stdout
        checksum = self.root / "vellum-rmlui-sdk.sha256"
        args = ["--archive", str(archive), "--bind-record", str(record), "--source-commit", "0" * 40,
                "--checksum-output", str(checksum)]
        self.assertEqual(verifier.main(args), 0)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(checksum.read_text(encoding="utf-8"), f"{digest}  {archive.name}\n")
        # Another commit, other bytes, or another record shape is refused.
        with self.assertRaises(verifier.VerificationError):
            verifier.bind(archive, record, "1" * 40)
        archive.write_bytes(archive.read_bytes() + b"\0")
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.bind(archive, record, "0" * 40)
        self.assertIn("bytes differ", str(caught.exception))
        record.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
        with self.assertRaises(verifier.VerificationError):
            verifier.bind(archive, record, "0" * 40)

    def test_the_release_workflow_steps_validate_and_bind_an_archive(self) -> None:
        # The sdk-release.yml steps themselves, as the release runs them.
        from test_release_dry_run import step_script
        hold = step_script("sdk-release.yml", "Hold the RmlUi SDK archive to the release source")
        bind = step_script("sdk-release.yml", "Bind the RmlUi SDK archive to its validated record")
        temp = self.root / "runner-temp"
        for directory in ("gpu", "trusted"):
            (temp / directory).mkdir(parents=True)
        archive = self.archive()
        published = temp / "gpu" / archive.name
        published.write_bytes(archive.read_bytes())
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, RUNNER_TEMP=str(temp), SOURCE_COMMIT="0" * 40,
                   RELEASE_ARCHIVE=f"vellum-sdk-{VERSION}-darwin-arm64.tar.gz")

        def run(script: str, **extra: str) -> subprocess.CompletedProcess:
            return subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=repo,
                                  env={**env, **extra}, capture_output=True, text=True, check=False)

        done = run(hold)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        record = temp / "trusted-rmlui-sdk-verification.json"
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["archive"], archive.name)
        (temp / "trusted" / record.name).write_bytes(record.read_bytes())
        done = run(bind)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        digest = hashlib.sha256(published.read_bytes()).hexdigest()
        self.assertEqual((temp / "trusted/vellum-rmlui-sdk.sha256").read_text(encoding="utf-8"),
                         f"{digest}  {archive.name}\n")
        # The release commit must be the one the archive was built from, and
        # the bytes published must be the bytes validated.
        self.assertNotEqual(run(hold, SOURCE_COMMIT="1" * 40).returncode, 0)
        published.write_bytes(published.read_bytes() + b"\0")
        self.assertNotEqual(run(bind).returncode, 0)

    def test_a_name_that_disagrees_with_the_metadata_is_refused(self) -> None:
        self.assert_refused(self.archive(version="0.2.1"), "differs from the archive name")

    def test_freetype_without_its_licence_is_refused(self) -> None:
        (self.payload / builder.DOC_DIR / "FreeType-FTL.TXT").unlink()
        self.assert_refused(self.archive(), "FreeType-FTL.TXT")

    def test_a_notice_without_the_freetype_credit_is_refused(self) -> None:
        self.write(f"{builder.DOC_DIR}/NOTICE-Native.md", b"RmlUi, MIT License.\n")
        self.assert_refused(self.archive(), "The FreeType Project")

    def test_a_licence_that_is_not_mit_is_refused(self) -> None:
        self.write(f"{builder.DOC_DIR}/RmlUi-LICENSE.txt", b"All rights reserved.\n")
        self.assert_refused(self.archive(), "not RmlUi's MIT licence")

    def test_a_package_file_naming_a_build_path_is_refused(self) -> None:
        self.write(f"{builder.PACKAGE_DIR}/VellumRmlUiNativeTargets.cmake",
                   b'set(_IMPORT_PREFIX "/Users/someone/vellum/build")\n')
        self.assert_refused(self.archive(), "names a build or source path (/Users/)")

    def test_a_non_canonical_tar_stream_is_refused(self) -> None:
        archive = self.archive()
        raw = gzip.decompress(archive.read_bytes())
        rewritten = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source, \
                tarfile.open(fileobj=rewritten, mode="w", format=tarfile.PAX_FORMAT) as target:
            for member in source.getmembers():
                member.mtime = 1700000000
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
        archive.write_bytes(gzip.compress(rewritten.getvalue(), mtime=0))
        self.assert_refused(archive, "not the canonical one")

    def test_an_unsafe_member_path_is_refused(self) -> None:
        archive = self.archive()
        raw = gzip.decompress(archive.read_bytes())
        rewritten = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source, \
                tarfile.open(fileobj=rewritten, mode="w", format=tarfile.PAX_FORMAT) as target:
            for member in source.getmembers():
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
            escape = tarfile.TarInfo("../escape.h")
            escape.size = 1
            target.addfile(escape, io.BytesIO(b"x"))
        archive.write_bytes(gzip.compress(rewritten.getvalue(), mtime=0))
        self.assert_refused(archive, "unsafe path")

    def test_the_main_sdk_build_stays_free_of_rmlui(self) -> None:
        # The Vellum SDK's own build never turns on what the RmlUi SDK
        # carries, and its leak check still names RmlUi's files.
        import build_sdk_artifact as main_sdk
        options = main_sdk.native_configure_options(Path("/skia.tar.gz"))
        for option in ("VELLUM_RMLUI_NATIVE", "VELLUM_RMLUI_METAL", "VELLUM_RMLUI_FONTS",
                       "VELLUM_RMLUI_NATIVE_PACKAGE", "VELLUM_RMLUI_CPU"):
            self.assertFalse(any(item.startswith(f"-D{option}=") for item in options), option)
        for relative in ("include/RmlUi/Core.h", "lib/librmlui.a", "lib/librmlui_debugger.a",
                         "lib/libfreetype.a"):
            self.assertTrue(main_sdk.is_upstream_rmlui_path(relative), relative)

    def test_the_builder_refuses_the_same_payloads(self) -> None:
        # The builder applies the same file rules before it writes an archive.
        files = set(builder.required_files())
        builder.check_payload(files)
        with self.assertRaises(ArtifactError):
            builder.check_payload(files | {"lib/libvellum-rmlui-core.dylib"})
        with self.assertRaises(ArtifactError):
            builder.check_payload(files - {"lib/libvellum-rmlui-native.a"})


if __name__ == "__main__":
    unittest.main()
