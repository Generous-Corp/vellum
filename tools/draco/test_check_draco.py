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


class SymbolChecks(unittest.TestCase):
    def test_draco_symbols_are_recognised_and_skia_is_not(self) -> None:
        self.assertTrue(cd.is_draco_symbol("__ZN5draco7Decoder18DecodeMeshFromBufferEPNS_13DecoderBufferE"))
        self.assertFalse(cd.is_draco_symbol("__ZN6vellum6render12decode_dracoEPKhm"))

    def test_a_missing_library_is_an_inspection_error_not_an_empty_list(self) -> None:
        with self.assertRaises(cd.InspectionError):
            cd.defined_symbols(Path("/nonexistent/libvellum-skia-dawn-provider.dylib"))


if __name__ == "__main__":
    unittest.main()
