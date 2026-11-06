#!/usr/bin/env python3
"""Negative controls for the one-gpu-runtime-per-process census."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gpu_runtime_census", HERE / "gpu_runtime_census.py")
assert spec and spec.loader
census = importlib.util.module_from_spec(spec)
sys.modules["gpu_runtime_census"] = census
spec.loader.exec_module(census)

COORDINATOR = "__ZN11gpu_runtime23register_dawn_bootstrapERKNS_14DawnBootstrapEPNSt3__112basic_stringIcEE"
PROVIDER = f"{COORDINATOR} t 100 10\n_dawnProcSetProcs T 200 10\n_unrelated T 300 1\n"
UNIT = "_unit T 10 1\n_dawnProcSetProcs U\n"


class GpuRuntimeCensusTest(unittest.TestCase):
    def run_census(self, listings: dict[str, str]) -> list[str]:
        return census.census([Path(name) for name in listings], nm=lambda image: listings[str(image)])

    def test_one_provider_owning_both_passes(self) -> None:
        self.assertEqual(self.run_census({"provider.dylib": PROVIDER, "unit.dylib": UNIT}), [])

    def test_hidden_second_coordinator_in_a_unit_fails(self) -> None:
        # A unit dylib that linked its own static gpu-runtime: the copy is a
        # local (hidden) definition, which an exported-only census would miss.
        second = UNIT + f"{COORDINATOR} t 40 10\n"
        findings = self.run_census({"provider.dylib": PROVIDER, "unit.dylib": second})
        self.assertEqual(len(findings), 1)
        self.assertIn("gpu-runtime coordinator is defined by 2 images", findings[0])

    def test_second_dawn_proc_table_fails(self) -> None:
        second = UNIT.replace("_dawnProcSetProcs U", "_dawnProcSetProcs t 50 4")
        findings = self.run_census({"provider.dylib": PROVIDER, "unit.dylib": second})
        self.assertTrue(any("Dawn proc table is defined by 2 images" in f for f in findings), findings)

    def test_no_owner_fails_so_a_wrong_image_list_is_visible(self) -> None:
        findings = self.run_census({"unit.dylib": UNIT})
        self.assertEqual(len(findings), 2)

    def test_undefined_references_do_not_count(self) -> None:
        self.assertEqual(census.defined_symbols("_a U\n_b u\n_c w\n_d T 0 1\n"), {"_d"})


if __name__ == "__main__":
    unittest.main()
