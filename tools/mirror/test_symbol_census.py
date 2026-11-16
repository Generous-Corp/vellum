#!/usr/bin/env python3
"""Negative controls for the installed-dylib symbol census."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("symbol_census", HERE / "symbol_census.py")
assert spec and spec.loader
sc = importlib.util.module_from_spec(spec)
sys.modules["symbol_census"] = sc
spec.loader.exec_module(sc)

ANCHOR = "_vellum_mirror_forwarding_anchor"
FN = "__ZN6vellum8graphics11clip_extentEv"
PULP_FN = "__ZN4pulp4view4drawEv"


def listing(*rows: str) -> str:
    return "\n".join(rows) + "\n"


def sym(name: str, attrs: str = "external") -> str:
    return f"0000000000001000 (__TEXT,__text) {attrs} {name}"


def demangle(names: list[str]) -> dict[str, str]:
    table = {FN: "vellum::graphics::clip_extent()", PULP_FN: "pulp::view::draw()", ANCHOR: ANCHOR}
    return {n: table.get(n, n) for n in names}


def run(nm_by_image: dict[str, str], otool_by_image: dict[str, str] | None = None, allow=frozenset()):
    images = [Path(name) for name in nm_by_image]
    otool_by_image = otool_by_image or {}
    return sc.census(images, allow, nm=lambda p: nm_by_image[str(p)], otool=lambda p: otool_by_image.get(str(p), ""),
                     demangle=demangle)


def classes(*names: str) -> str:
    rows = ["Contents of (__DATA,__objc_classlist) section"]
    rows += [f"0000000000004000 0x5000 _OBJC_CLASS_$_{n}" for n in names]
    return "\n".join(rows) + "\n"


class SymbolCensusTest(unittest.TestCase):
    def test_clean_images_pass(self) -> None:
        report, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(sym(ANCHOR.replace("anchor", "other")))})
        self.assertEqual(findings, [])
        self.assertEqual(report["vellum_definitions"], 1)

    def test_pulp_definition_fails(self) -> None:
        _, findings = run({"a.dylib": listing(sym(FN), sym(PULP_FN))})
        self.assertEqual(len(findings), 1)
        self.assertIn("pulp::", findings[0])

    def test_hidden_pulp_definition_still_fails(self) -> None:
        # A private-external pulp:: symbol is still left over from the rename.
        _, findings = run({"a.dylib": listing(sym(FN), sym(PULP_FN, "private external"))})
        self.assertEqual(len(findings), 1)

    def test_same_export_in_two_images_fails(self) -> None:
        _, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(sym(FN))})
        self.assertEqual(len(findings), 1)
        self.assertIn("exported by 2 images", findings[0])

    def test_hidden_or_weak_copy_is_not_a_duplicate(self) -> None:
        _, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(sym(FN, "private external")),
                           "c.dylib": listing(sym(FN, "weak external"))})
        self.assertEqual(findings, [])

    def test_allowlisted_anchor_passes_and_other_duplicates_still_fail(self) -> None:
        nm = {"a.dylib": listing(sym(ANCHOR), sym(FN)), "b.dylib": listing(sym(ANCHOR), sym(FN))}
        _, findings = run(nm, allow=frozenset({ANCHOR}))
        self.assertEqual(len(findings), 1)
        self.assertIn("clip_extent", findings[0])
        nm = {"a.dylib": listing(sym(ANCHOR)), "b.dylib": listing(sym(ANCHOR))}
        self.assertEqual(run(nm, allow=frozenset({ANCHOR}))[1], [])
        self.assertEqual(len(run(nm)[1]), 1)

    def test_objc_class_in_two_images_fails(self) -> None:
        nm = {"a.dylib": listing(sym(FN)), "b.dylib": listing(sym("_other"))}
        _, findings = run(nm, {"a.dylib": classes("Menu"), "b.dylib": classes("Menu", "Other")})
        self.assertEqual(len(findings), 1)
        self.assertIn("Objective-C class Menu", findings[0])

    def test_listing_an_image_twice_is_caught(self) -> None:
        # The census's own plant: the same image seen twice.
        images = [Path("a.dylib"), Path("a.dylib")]
        _, findings = sc.census(images, nm=lambda p: listing(sym(FN)), otool=lambda p: "", demangle=demangle)
        self.assertEqual(len(findings), 1)

    def test_undefined_references_are_not_definitions(self) -> None:
        undefined = "                 (undefined) external " + FN
        _, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(undefined)})
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
