#!/usr/bin/env python3
"""check_libcxx_availability flags floating-point to_chars below macOS 13.3."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_libcxx_availability as check  # noqa: E402

DOUBLE = "__ZNSt3__18to_charsEPcS0_d"
INTEGER = "__ZNSt3__18to_charsEPcS0_i"  # not an overload this check is about


def listing(*names: str, weak: bool = False) -> str:
    kind = "(undefined) weak external" if weak else "(undefined) external"
    return "".join(f"                 {kind} {name} (from libc++)\n" for name in names)


class Parsing(unittest.TestCase):
    def test_minimum_os_reads_lc_build_version(self) -> None:
        self.assertEqual(check.minimum_os("      cmd LC_BUILD_VERSION\n    minos 13.0\n      sdk 15.2\n"), (13, 0))
        self.assertEqual(check.minimum_os("    minos 13.3.1\n"), (13, 3))
        self.assertIsNone(check.minimum_os("      cmd LC_SEGMENT_64\n"))

    def test_weak_imports_are_not_counted(self) -> None:
        self.assertEqual(check.strong_libcxx_imports(listing(DOUBLE)), [DOUBLE])
        self.assertEqual(check.strong_libcxx_imports(listing(DOUBLE, weak=True)), [])


class Verdicts(unittest.TestCase):
    def test_floating_to_chars_below_13_3_fails(self) -> None:
        self.assertEqual(len(check.problems((13, 0), [DOUBLE, INTEGER])), 1)

    def test_at_13_3_it_passes(self) -> None:
        self.assertEqual(check.problems((13, 3), [DOUBLE]), [])

    def test_integer_to_chars_passes(self) -> None:
        self.assertEqual(check.problems((13, 0), [INTEGER]), [])

    def test_no_minimum_fails(self) -> None:
        self.assertTrue(check.problems(None, []))


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang++"), "needs Apple clang")
class BuiltImage(unittest.TestCase):
    """Compile a dylib that formats a double and read it back."""

    SOURCE = '#include <charconv>\nint f(char* b, double v) { return int(std::to_chars(b, b + 32, v).ptr - b); }\n'

    def build(self, minos: str, tmp: Path) -> Path:
        src, out = tmp / "f.cpp", tmp / f"f-{minos}.dylib"
        src.write_text(self.SOURCE)
        subprocess.run(["clang++", "-std=c++20", "-dynamiclib", "-arch", "arm64",
                        f"-mmacosx-version-min={minos}", "-D_LIBCPP_DISABLE_AVAILABILITY=1",
                        str(src), "-o", str(out)], check=True, capture_output=True)
        return out

    def test_a_13_0_dylib_fails_and_a_13_3_one_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            self.assertEqual(check.main(["x", str(self.build("13.0", tmp))]), 1)
            self.assertEqual(check.main(["x", str(self.build("13.3", tmp))]), 0)


if __name__ == "__main__":
    unittest.main()
