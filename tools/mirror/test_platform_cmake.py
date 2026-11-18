"""cmake -P tests for tools/mirror/VellumMirrorPlatform.cmake."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "VellumMirrorPlatform.cmake"

# The flags the Darwin configure has always passed, byte for byte.
DARWIN_PREFIX_FLAGS = ("-ffile-prefix-map=/src=vellum;-ffile-prefix-map=/bld=vellum-build")


def run_cmake(body: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "case.cmake"
        script.write_text(f'include("{MODULE}")\n{body}\n', encoding="utf-8")
        return subprocess.run(["cmake", "-P", str(script)], capture_output=True, text=True)


class PlatformCmakeTests(unittest.TestCase):
    def test_darwin_packaging_passes_silently(self) -> None:
        result = run_cmake('set(APPLE TRUE)\nset(CMAKE_SYSTEM_NAME Darwin)\n'
                           'vellum_mirror_require_packaging()')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_linux_record_stops_with_a_clear_error_at_packaging(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME Linux)\nvellum_mirror_require_packaging()')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mirror packaging for Linux is not implemented yet; "
                      "it arrives with that platform's record PR",
                      " ".join(result.stderr.split()))

    def test_windows_record_stops_with_a_clear_error_at_packaging(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME Windows)\nvellum_mirror_require_packaging()')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mirror packaging for Windows is not implemented yet", result.stderr)

    def prefix_flags(self, c_id: str, cxx_id: str, variant: str = "") -> str:
        result = run_cmake(
            f'set(CMAKE_C_COMPILER_ID {c_id})\nset(CMAKE_CXX_COMPILER_ID {cxx_id})\n'
            f'set(CMAKE_C_COMPILER_FRONTEND_VARIANT "{variant}")\n'
            f'set(CMAKE_CXX_COMPILER_FRONTEND_VARIANT "{variant}")\n'
            'vellum_mirror_prefix_map_options(flags "/src=vellum" "/bld=vellum-build")\n'
            'message(STATUS "FLAGS=[${flags}]")')
        self.assertEqual(result.returncode, 0, result.stderr)
        line = [l for l in (result.stdout + result.stderr).splitlines() if "FLAGS=[" in l][0]
        return line.split("FLAGS=[", 1)[1].rstrip("]")

    def test_darwin_clang_flags_are_unchanged(self) -> None:
        self.assertEqual(self.prefix_flags("AppleClang", "AppleClang", "GNU"), DARWIN_PREFIX_FLAGS)

    def test_gnu_emits_the_flag(self) -> None:
        self.assertIn("-ffile-prefix-map=", self.prefix_flags("GNU", "GNU"))

    def test_msvc_does_not_emit_the_flag(self) -> None:
        self.assertEqual(self.prefix_flags("MSVC", "MSVC"), "")

    def test_clang_cl_does_not_emit_the_flag(self) -> None:
        self.assertEqual(self.prefix_flags("Clang", "Clang", "MSVC"), "")


if __name__ == "__main__":
    unittest.main()
