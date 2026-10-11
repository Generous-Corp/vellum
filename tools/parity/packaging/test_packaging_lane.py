#!/usr/bin/env python3
"""Unit tests for the D6 rung-2 packaging lane's comparison, configuration,
justification and single-definition rules. The end-to-end run needs an origin
build and an installed package, so it is exercised by `calibrate` instead."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import packaging_lane as lane  # noqa: E402


class CompareTests(unittest.TestCase):
    def test_identical_passing_populations_match(self) -> None:
        both = {"a::x": "passed", "a::y": "passed"}
        self.assertEqual(lane.compare(both, dict(both)), [])

    def test_population_and_outcome_differences_are_reported(self) -> None:
        reference = {"a::x": "passed", "a::y": "passed"}
        self.assertTrue(lane.compare(reference, {"a::x": "passed"}))
        self.assertTrue(lane.compare(reference, dict(reference, **{"a::z": "passed"})))
        self.assertTrue(lane.compare(reference, {"a::x": "passed", "a::y": "failed"}))
        both_failing = {"a::x": "passed", "a::f": "failed"}
        self.assertIn("a::f: failed on both sides", lane.compare(both_failing, dict(both_failing)))
        self.assertTrue(lane.compare({}, {}))

    def test_identical_skips_are_listed_not_counted(self) -> None:
        reference = {"a::x": "passed", "a::s": "skipped"}
        self.assertEqual(lane.compare(reference, dict(reference)), [])
        self.assertEqual(lane.skipped_on_both(reference, dict(reference)), ["a::s"])
        self.assertTrue(lane.compare({"a::s": "skipped"}, {"a::s": "skipped"}))  # nothing passed


class ConfigurationTests(unittest.TestCase):
    closure = {"configuration": "Release", "compile_definitions": ["PULP_HAS_SHEENBIDI=1"]}

    def test_matching_build_is_accepted(self) -> None:
        cache = {"CMAKE_BUILD_TYPE": "Release", "PULP_HAS_SKIA": "FALSE"}
        self.assertEqual(lane.configuration_errors([self.closure], cache), [])

    def test_build_type_and_skia_mismatches_are_refused(self) -> None:
        self.assertTrue(lane.configuration_errors([self.closure], {"CMAKE_BUILD_TYPE": "Debug",
                                                                   "PULP_HAS_SKIA": "FALSE"}))
        self.assertTrue(lane.configuration_errors([self.closure], {"CMAKE_BUILD_TYPE": "Release",
                                                                   "PULP_HAS_SKIA": "TRUE"}))


@unittest.skipUnless(shutil.which("cc") and shutil.which("ar") and shutil.which("nm"), "needs cc, ar and nm")
class CensusTests(unittest.TestCase):
    def archive(self, root: Path, name: str, code: str) -> Path:
        source = root / f"{name}.c"
        source.write_text(code, encoding="utf-8")
        subprocess.run(["cc", "-c", str(source), "-o", str(root / f"{name}.o")], check=True)
        archive = root / f"lib{name}.a"
        subprocess.run(["ar", "rcs", str(archive), str(root / f"{name}.o")], check=True)
        return archive

    def test_strong_duplicates_fail_and_weak_ones_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = self.archive(root, "one", "int shared(void) { return 1; }\n"
                                            "__attribute__((weak)) int inline_like(void) { return 3; }\n")
            two = self.archive(root, "two", "int other(void) { return 2; }\n"
                                            "__attribute__((weak)) int inline_like(void) { return 3; }\n")
            clean = lane.archive_census([one, two])
            self.assertEqual(clean["duplicate_count"], 0)
            self.assertGreaterEqual(clean["symbols"], 2)
            dup = self.archive(root, "dup", "int shared(void) { return 9; }\n")
            dirty = lane.archive_census([one, two, dup])
            self.assertEqual(dirty["duplicate_count"], 1)
            self.assertIn("_shared", dirty["duplicates"])


class SpecRuleTests(unittest.TestCase):
    def test_unjustified_reclassification_and_exclusion_are_refused(self) -> None:
        spec = {"closures": [], "closure_external_paths": {"build/x.h": ""}}
        with self.assertRaises(lane.LaneError):
            lane.evaluate(spec, Path(tempfile.gettempdir()) / "unused")


if __name__ == "__main__":
    unittest.main()
