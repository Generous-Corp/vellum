#!/usr/bin/env python3
"""Controls for the third-party unexport list."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("third_party_unexports", HERE / "third_party_unexports.py")
assert spec and spec.loader
tpu = importlib.util.module_from_spec(spec)
sys.modules["third_party_unexports"] = tpu
spec.loader.exec_module(tpu)


def row(name: str, attrs: str = "external", section: str = "__TEXT,__text") -> str:
    return f"0000000000001000 ({section}) {attrs} {name}"


class ThirdPartyUnexportsTest(unittest.TestCase):
    def test_external_definitions_are_listed_and_others_are_not(self) -> None:
        listing = "\n".join([row("_SDL_Init"), row("_local", "non-external"), row("_ref", "external", "undefined"),
                             row("_weak", "weak external")]) + "\n"
        self.assertEqual(tpu.defined_external(listing), {"_SDL_Init", "_weak"})

    def test_extra_patterns_are_added_and_comments_dropped(self) -> None:
        lines = tpu.build([Path("a.a")], "# why\n_mz_*\n\n__ZN4pugi*\n", nm=lambda p: row("_SDL_Init"))
        self.assertEqual(lines, ["_SDL_Init", "__ZN4pugi*", "_mz_*"])

    def test_names_with_pattern_characters_are_escaped(self) -> None:
        lines = tpu.build([Path("a.a")], nm=lambda p: row("_a*b?c"))
        self.assertEqual(lines, ["_a\\*b\\?c"])

    def test_output_is_sorted_and_deduplicated_across_archives(self) -> None:
        lib = {"a.a": row("_z") + "\n" + row("_a"), "b.a": row("_a")}
        self.assertEqual(tpu.build([Path("a.a"), Path("b.a")], nm=lambda p: lib[str(p)]), ["_a", "_z"])

    def test_archives_defining_nothing_are_an_error_not_an_empty_list(self) -> None:
        with self.assertRaises(RuntimeError):
            tpu.build([Path("a.a")], "_mz_*\n", nm=lambda p: "")

    def test_mirror_patterns_cover_weak_choc_and_graphite_definitions(self) -> None:
        patterns = (HERE / "third_party_unexports.txt").read_text(encoding="utf-8")
        self.assertIn("__ZN4choc*", patterns)
        self.assertIn("__ZN5skgpu8graphite*", patterns)
        # A function-local static in a const member function has a guard variable
        # spelled __ZGVZNK..., which the non-const __ZGVZN... pattern does not match.
        lines = set(patterns.splitlines())
        for namespace in ("7httplib", "4choc", "5skgpu8graphite"):
            self.assertIn(f"__ZGVZN{namespace}*", lines)
            self.assertIn(f"__ZGVZNK{namespace}*", lines)

    def test_main_writes_the_list_and_fails_without_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "list"
            archive = Path(tmp) / "a.a"
            archive.write_bytes(b"x")
            original = tpu.run_nm
            try:
                tpu.run_nm = lambda p: row("_SDL_Init")
                self.assertEqual(tpu.main(["--out", str(out), str(archive)]), 0)
                self.assertEqual(out.read_text(), "_SDL_Init\n")
                tpu.run_nm = lambda p: ""
                self.assertEqual(tpu.main(["--out", str(out), str(archive)]), 1)
            finally:
                tpu.run_nm = original


if __name__ == "__main__":
    unittest.main()
