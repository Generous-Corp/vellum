"""cmake -P tests for tools/mirror/VellumMirrorPlatform.cmake."""
from __future__ import annotations

import json
import shutil
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

    def test_linux_record_is_accepted_at_packaging(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME Linux)\nvellum_mirror_require_packaging()')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_record_is_accepted_at_packaging(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME Windows)\nvellum_mirror_require_packaging()')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_undeclared_record_is_refused(self) -> None:
        result = run_cmake('set(CMAKE_SYSTEM_NAME FreeBSD)\nvellum_mirror_require_packaging()')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a verified platform closure", " ".join(result.stderr.split()))

    def records_root(self, tmp: Path, keep: tuple[str, ...], *, schema: str = "") -> Path:
        """A copy of the platform records limited to `keep`, indexed only by them."""
        source = MODULE.parent.parent.parent / "provenance/framework/platforms"
        root = tmp / "platforms"
        index = {"schema": "vellum.framework-platform-index.v1", "platforms": {}}
        for key in keep:
            (root / key).mkdir(parents=True)
            text = (source / key / "record.json").read_text(encoding="utf-8")
            if schema:
                text = text.replace("vellum.framework-platform-closure.v1", schema)
            (root / key / "record.json").write_text(text, encoding="utf-8")
            index["platforms"][key] = f"provenance/framework/platforms/{key}/record.json"
        (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
        return root

    def run_with_root(self, root: Path, system: str) -> subprocess.CompletedProcess:
        return run_cmake(f'set(VELLUM_MIRROR_PLATFORM_RECORD_ROOT "{root}")\n'
                         f'set(CMAKE_SYSTEM_NAME {system})\nvellum_mirror_require_packaging()')

    def test_packaging_allowlist_follows_the_records_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.records_root(Path(tmp), ("windows-x64",))
            windows = self.run_with_root(root, "Windows")
            self.assertEqual(windows.returncode, 0, windows.stderr)
            linux = self.run_with_root(root, "Linux")
            self.assertNotEqual(linux.returncode, 0)
            self.assertIn("requires a verified platform closure", " ".join(linux.stderr.split()))
            both = self.records_root(Path(tmp) / "both", ("linux-x64", "windows-x64"))
            self.assertEqual(self.run_with_root(both, "Linux").returncode, 0)

    def test_a_record_the_index_does_not_list_or_that_is_not_a_closure_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.records_root(Path(tmp), ("linux-x64",))
            unlisted = root / "windows-x64"
            unlisted.mkdir()
            shutil.copy(MODULE.parent.parent.parent / "provenance/framework/platforms/windows-x64/record.json",
                        unlisted / "record.json")
            self.assertNotEqual(self.run_with_root(root, "Windows").returncode, 0)
            wrong = self.records_root(Path(tmp) / "wrong", ("linux-x64",),
                                      schema="vellum.something-else.v1")
            self.assertNotEqual(self.run_with_root(wrong, "Linux").returncode, 0)

    def test_declared_platforms_name_system_and_processor(self) -> None:
        result = run_cmake('vellum_mirror_declared_platforms(_p)\nmessage(STATUS "P=${_p}")')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        for platform in ("Darwin|arm64", "Linux|x86_64", "Windows|AMD64"):
            self.assertIn(platform, output)

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
