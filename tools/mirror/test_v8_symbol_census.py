#!/usr/bin/env python3
"""Unit tests for v8_symbol_census.py over synthetic `nm -m` listings."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v8_symbol_census as census  # noqa: E402

V8 = """\
0000000000001000 (__TEXT,__text) external __ZN2v82V810GetVersionEv
0000000000001100 (__TEXT,__text) weak external __ZNK2v85Value8IsStringEv
0000000000001200 (__DATA_CONST,__const) external __ZTVN6cppgc4HeapE
0000000000001300 (__TEXT,__text) non-external (was a private external) __ZN6icu_7813UnicodeStringC1Ev
0000000000001400 (__TEXT,__text) non-external __ZN4absl12lts_202501274CordC1Ev
                 (undefined) external _write (from libSystem)
                 (undefined) weak external __ZNSt3__16vectorIiNS_9allocatorIiEEE6resizeEm (from libc++)
"""

PROVIDER = """\
0000000000002000 (__TEXT,__text) external __ZN6vellum4view16create_v8_engineEv
0000000000002100 (__TEXT,__text) weak external __ZNK2v85Value8IsStringEv
0000000000002200 (__TEXT,__text) external __ZN6icu_7813UnicodeStringC1Ev
                 (undefined) external __ZN2v82V810GetVersionEv (from libv8)
                 (undefined) external _malloc (from libSystem)
"""


class ParseTest(unittest.TestCase):
    def test_reads_exports_weakness_and_imports(self) -> None:
        image = census.parse_nm(V8)
        self.assertEqual(image.exported, {
            "__ZN2v82V810GetVersionEv": False,
            "__ZNK2v85Value8IsStringEv": True,
            "__ZTVN6cppgc4HeapE": False,
        })
        self.assertEqual(image.imports["_write"], "libSystem")
        self.assertEqual(image.imports["__ZNSt3__16vectorIiNS_9allocatorIiEEE6resizeEm"], "libc++")


class JudgeTest(unittest.TestCase):
    def judge(self, provider: str = PROVIDER, v8: str = V8) -> tuple[list[str], dict]:
        return census.judge(census.parse_nm(provider), census.parse_nm(v8))

    def test_sealed_pair_passes(self) -> None:
        errors, report = self.judge()
        self.assertEqual(errors, [])
        self.assertEqual(report["shared_weak_definitions"], 1)
        self.assertEqual(report["provider_exports_icu_abseil_zlib"], 1)
        self.assertEqual(report["provider_v8_imports_from_libv8"], 1)

    def test_an_exported_abseil_symbol_in_libv8_fails(self) -> None:
        v8 = census.parse_nm(V8)
        v8.exported[census.PLANTED] = False
        errors, report = census.judge(census.parse_nm(PROVIDER), v8)
        self.assertEqual(report["v8_exports_outside_allow_list"], 1)
        self.assertTrue(any("outside v8:: and cppgc::" in e for e in errors))

    def test_an_exported_icu_symbol_in_libv8_fails(self) -> None:
        v8 = V8 + "0000000000001500 (__TEXT,__text) external __ZN6icu_7813UnicodeStringD1Ev\n"
        errors, _ = self.judge(v8=v8)
        self.assertTrue(any("outside v8:: and cppgc::" in e for e in errors))

    def test_a_second_strong_definition_fails(self) -> None:
        provider = PROVIDER + "0000000000002300 (__TEXT,__text) external __ZN2v82V810GetVersionEv\n"
        errors, report = self.judge(provider=provider)
        self.assertEqual(report["shared_strong_definitions"], 1)
        self.assertTrue(any("strong in one" in e for e in errors))

    def test_a_provider_without_v8_imports_fails(self) -> None:
        provider = "\n".join(line for line in PROVIDER.splitlines() if "from libv8" not in line)
        errors, _ = self.judge(provider=provider)
        self.assertTrue(any("does not link libv8" in e for e in errors))

    def test_v8_bound_elsewhere_fails(self) -> None:
        provider = PROVIDER.replace("(from libv8)", "(from libnode)")
        errors, _ = self.judge(provider=provider)
        self.assertTrue(any("other than libv8" in e for e in errors))

    def test_libv8_importing_a_non_system_image_fails(self) -> None:
        v8 = V8 + "                 (undefined) external __ZN6icu_7813UnicodeStringC1Ev (from libvellum-skia-dawn-provider)\n"
        errors, _ = self.judge(v8=v8)
        self.assertTrue(any("non-system images" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
