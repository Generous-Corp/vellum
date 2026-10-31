#!/usr/bin/env python3
"""check_shared_exception_typeinfo.py tells a local exception typeinfo from a reference."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_shared_exception_typeinfo",
                                              HERE / "check_shared_exception_typeinfo.py")
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

# `nm -m` of a yogacore object compiled with -fno-rtti, then with -frtti.
WITHOUT_RTTI = """\
00000000000012d8 (__DATA,__const) weak private external __ZTISt9exception
00000000000012c0 (__DATA,__const) weak private external __ZTISt11logic_error
0000000000000f10 (__TEXT,__const) weak private external __ZTSSt12out_of_range
0000000000001300 (__DATA,__const) weak private external __ZTINSt3__117bad_function_callE
0000000000000100 (__TEXT,__text) external _YGNodeNew
"""
WITH_RTTI = """\
                 (undefined) external __ZTISt9exception (from libc++)
                 (undefined) external __ZTISt11logic_error (from libc++)
                 (undefined) external __ZTISt12out_of_range (from libc++)
0000000000001300 (__DATA,__const) weak private external __ZTINSt3__117bad_function_callE
0000000000000100 (__TEXT,__text) external _YGNodeNew
"""


class SharedExceptionTypeinfoTest(unittest.TestCase):
    def test_local_copies_are_found(self) -> None:
        found = check.darwin_definitions(WITHOUT_RTTI)
        self.assertEqual(len(found), 3)
        self.assertTrue(all("__ZT" in line and "St" in line for line in found))

    def test_references_pass(self) -> None:
        self.assertEqual(check.darwin_definitions(WITH_RTTI), [])

    def test_libcxx_header_artifacts_are_not_counted(self) -> None:
        self.assertEqual(check.darwin_definitions(
            "0000000000001300 (__DATA,__const) weak private external __ZTINSt3__117bad_function_callE\n"
            "0000000000002a79 (__DATA_CONST,__const) non-external (was a private external) "
            "__ZTINSt3__112format_errorE\n"), [])

    def test_any_std_class_counts_not_only_the_exception_family(self) -> None:
        for name in ("__ZTISt19bad_optional_access", "__ZTINSt3__112system_errorE", "__ZTSSt11range_error"):
            with self.subTest(name=name):
                self.assertTrue(check.is_local_std_typeinfo(name))
        # Template instantiations and non-std classes are not this check's concern.
        for name in ("__ZTINSt3__110__function6__funcIZ1fvE3$_0NS_9allocatorIS2_EEFvvEEE", "__ZTIN8facebook4yoga4NodeE",
                     "__ZTINSt3__110__back_refIcEE", "__ZTINSt3__110__function6__baseIFvvEEE"):
            with self.subTest(name=name):
                self.assertFalse(check.is_local_std_typeinfo(name))

    def test_posix_listing(self) -> None:
        listing = "_ZTISt9exception V 12d8 10\n_ZTISt11logic_error U\n_ZTSSt9bad_alloc R f10 12\n"
        self.assertEqual([line.split()[0] for line in check.posix_definitions(listing)],
                         ["_ZTISt9exception", "_ZTSSt9bad_alloc"])


if __name__ == "__main__":
    unittest.main()
