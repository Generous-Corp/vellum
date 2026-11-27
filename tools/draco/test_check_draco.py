#!/usr/bin/env python3
"""Negative controls for check_draco.py's source and symbol checks."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_draco as cd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FILES = ("CMakeLists.txt", cd.MODULE, "DEPENDENCIES.md", "NOTICE.md", "provenance/third-party-lock.json")


class SourceChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rel in FILES:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, self.root / rel)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_the_real_tree_is_clean(self) -> None:
        self.assertEqual(cd.source_errors(REPO), [])

    def test_default_on_is_refused(self) -> None:
        text = (self.root / "CMakeLists.txt").read_text(encoding="utf-8")
        start = text.index(f"option({cd.OPTION}")
        end = text.index("OFF)", start)
        (self.root / "CMakeLists.txt").write_text(text[:end] + "ON)" + text[end + 4:], encoding="utf-8")
        self.assertTrue(any("must default to OFF" in e for e in cd.source_errors(self.root)))

    def test_fetch_before_the_early_return_is_refused(self) -> None:
        self.edit(cd.MODULE, "include_guard(GLOBAL)", "include_guard(GLOBAL)\nFetchContent_Declare(x URL y)")
        self.assertTrue(any("before its early return" in e for e in cd.source_errors(self.root)))

    def test_a_drifted_digest_is_refused(self) -> None:
        pin = cd.pins(REPO)["sha256"]
        self.edit(cd.MODULE, pin, "0" * 64)
        errors = cd.source_errors(self.root)
        self.assertTrue(any("DEPENDENCIES.md" in e for e in errors))
        self.assertTrue(any("third-party-lock.json" in e for e in errors))

    def test_a_missing_lock_entry_is_refused(self) -> None:
        path = self.root / "provenance/third-party-lock.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        lock["dependencies"] = [d for d in lock["dependencies"] if d.get("name") != "Draco"]
        path.write_text(json.dumps(lock), encoding="utf-8")
        self.assertTrue(any("exactly one Draco entry" in e for e in cd.source_errors(self.root)))

    def test_a_missing_notice_is_refused(self) -> None:
        self.edit("NOTICE.md", "## Optional native Draco decoding", "## Something else")
        self.assertTrue(any("no Draco section" in e for e in cd.source_errors(self.root)))


    def refused(self, fragment: str) -> None:
        errors = cd.source_errors(self.root)
        self.assertTrue(any(fragment in e for e in errors), (fragment, errors))

    def test_a_digest_changed_everywhere_still_needs_the_module_hash_line(self) -> None:
        self.edit(cd.MODULE, '    URL_HASH "SHA256=${VELLUM_DRACO_SHA256}"\n', "")
        self.refused("URL_HASH")

    def test_a_url_hard_coded_to_another_tag_is_refused(self) -> None:
        self.edit(cd.MODULE, "refs/tags/${VELLUM_DRACO_VERSION}.tar.gz", "refs/tags/1.5.7.tar.gz")
        self.refused("must fetch exactly")

    def test_a_fork_url_is_refused(self) -> None:
        self.edit(cd.MODULE, "https://github.com/google/draco/", "https://github.com/evil/draco/")
        self.refused("must fetch exactly")

    def test_a_floating_branch_url_is_refused(self) -> None:
        self.edit(cd.MODULE, "refs/tags/${VELLUM_DRACO_VERSION}.tar.gz", "refs/heads/main.tar.gz")
        self.refused("must fetch exactly")

    def test_a_git_fetch_is_refused(self) -> None:
        self.edit(cd.MODULE, "    DOWNLOAD_EXTRACT_TIMESTAMP TRUE",
                  "    GIT_REPOSITORY https://github.com/google/draco\n    GIT_TAG main\n    DOWNLOAD_EXTRACT_TIMESTAMP TRUE")
        self.refused("GIT_REPOSITORY")
        self.refused("GIT_TAG")

    def test_a_lock_source_on_a_fork_is_refused(self) -> None:
        path = self.root / "provenance/third-party-lock.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        for d in lock["dependencies"]:
            if d.get("name") == "Draco":
                d["source"] = "https://github.com/evil/draco/archive/refs/tags/1.5.9.tar.gz"
        path.write_text(json.dumps(lock), encoding="utf-8")
        self.refused("lock entry's source")

    def test_a_lock_commit_of_zeros_is_refused(self) -> None:
        path = self.root / "provenance/third-party-lock.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        for d in lock["dependencies"]:
            if d.get("name") == "Draco":
                d["commit"] = "0" * 40
        path.write_text(json.dumps(lock), encoding="utf-8")
        self.refused("lock entry's commit")

    def test_the_transcoder_on_is_refused(self) -> None:
        self.edit(cd.MODULE, "set(DRACO_TRANSCODER_SUPPORTED OFF", "set(DRACO_TRANSCODER_SUPPORTED ON")
        self.refused("DRACO_TRANSCODER_SUPPORTED OFF")

    def test_a_non_apache_row_is_refused(self) -> None:
        text = (self.root / "DEPENDENCIES.md").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| Draco "))
        (self.root / "DEPENDENCIES.md").write_text(
            text.replace(row, row.replace("Apache-2.0", "GPL-3.0")), encoding="utf-8")
        self.refused("must name Apache-2.0")

    def test_an_unpinned_download_before_the_guard_is_refused(self) -> None:
        self.edit(cd.MODULE, "include_guard(GLOBAL)",
                  "include_guard(GLOBAL)\nfile(DOWNLOAD https://example.com/x.tgz x.tgz)")
        self.refused("file(DOWNLOAD")

    def test_a_second_fetch_is_refused(self) -> None:
        self.edit(cd.MODULE, "FetchContent_MakeAvailable(vellum_draco)",
                  "FetchContent_MakeAvailable(vellum_draco)\nExternalProject_Add(x URL https://example.com/y.tgz)")
        self.refused("ExternalProject_Add")

    def test_the_root_forcing_the_option_on_is_refused(self) -> None:
        with open(self.root / "CMakeLists.txt", "a", encoding="utf-8") as handle:
            handle.write(f"\nset({cd.OPTION} ON CACHE BOOL \"\" FORCE)\n")
        self.refused("sets VELLUM_ENABLE_DRACO on or forces it")

    def test_an_encoder_symbol_is_recognised(self) -> None:
        self.assertTrue(cd.is_draco_encoder_symbol("__ZN5draco7Encoder6EncodeEv"))
        self.assertTrue(cd.is_draco_encoder_symbol("__ZN5draco11MeshEncoderC2Ev"))
        self.assertFalse(cd.is_draco_encoder_symbol("__ZN5draco7Decoder18DecodeMeshFromBufferEv"))



class SymbolChecks(unittest.TestCase):
    def test_draco_symbols_are_recognised_and_skia_is_not(self) -> None:
        self.assertTrue(cd.is_draco_symbol("__ZN5draco7Decoder18DecodeMeshFromBufferEPNS_13DecoderBufferE"))
        self.assertFalse(cd.is_draco_symbol("__ZN6vellum6render12decode_dracoEPKhm"))

    def test_a_missing_library_is_an_inspection_error_not_an_empty_list(self) -> None:
        with self.assertRaises(cd.InspectionError):
            cd.defined_symbols(Path("/nonexistent/libvellum-skia-dawn-provider.dylib"))


if __name__ == "__main__":
    unittest.main()
