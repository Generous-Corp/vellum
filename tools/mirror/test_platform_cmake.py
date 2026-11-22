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

    def test_linux_record_uses_portable_packaging(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME Linux)\nvellum_mirror_require_packaging()')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_record_uses_portable_packaging(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME Windows)\nvellum_mirror_require_packaging()')
        self.assertEqual(result.returncode, 0, result.stderr)

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


class ConfigureDependsTest(unittest.TestCase):
    """Editing anything the plan is derived from must retrigger configure."""

    def test_every_local_module_mirror_package_imports_is_a_configure_dependency(self) -> None:
        import re
        directory = MODULE.parent
        source = (directory / "mirror_package.py").read_text(encoding="utf-8")
        imported = {name for name in re.findall(r"^import (\w+)", source, re.M)
                    if (directory / f"{name}.py").is_file()}
        self.assertIn("platform_closures", imported)
        cmake = (directory / "VellumMirror.cmake").read_text(encoding="utf-8")
        listed = set(re.findall(r'\$\{PROJECT_SOURCE_DIR\}/tools/mirror/(\w+)\.py"', cmake))
        for name in {"mirror_package"} | imported:
            self.assertIn(name, listed, f"{name}.py is not in CMAKE_CONFIGURE_DEPENDS")

    def test_platform_rule_and_records_are_configure_dependencies(self) -> None:
        cmake = (MODULE.parent / "VellumMirror.cmake").read_text(encoding="utf-8")
        self.assertIn("tools/mirror/VellumMirrorPlatform.cmake", cmake)
        self.assertIn("provenance/framework/platforms/*.json", cmake)
        self.assertRegex(
            cmake,
            r"file\(GLOB_RECURSE\s+_vellum_mirror_platform_records\s+"
            r"CONFIGURE_DEPENDS\s+",
            "the platform-record glob must retrigger configure when a record is added",
        )


if __name__ == "__main__":
    unittest.main()
