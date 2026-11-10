#!/usr/bin/env python3
"""The native SDK build carries the VellumRmlUi package and none of RmlUi's own files."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_sdk_artifact as builder  # noqa: E402


def touch(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(relative, encoding="utf-8")
    return path


class NativeConfigureOptions(unittest.TestCase):
    def test_native_build_enables_the_rmlui_package_and_its_vellum_renderer(self):
        options = builder.native_configure_options(Path("/locked/skia.tar.gz"))
        self.assertIn("-DVELLUM_ENABLE_RMLUI=ON", options)
        self.assertIn("-DVELLUM_RMLUI_VELLUM_RENDERER=ON", options)
        self.assertIn("-DVELLUM_ENABLE_MIRROR=ON", options)
        self.assertIn("-DVELLUM_SKIA_ARCHIVE=/locked/skia.tar.gz", options)

    def test_build_configures_installs_and_checks_through_the_helpers(self):
        source = inspect.getsource(builder.build)
        self.assertIn("native_configure_options(skia_archive)", source)
        self.assertIn("remove_install_component(", source)
        self.assertIn("missing_package_files(install_tree, RMLUI_PACKAGE_FILES)", source)
        self.assertIn("upstream_rmlui_leaks(install_tree)", source)


class PackageFiles(unittest.TestCase):
    def test_the_package_list_names_both_cmake_packages_entry_points(self):
        self.assertIn("lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake", builder.RMLUI_PACKAGE_FILES)
        self.assertIn(
            "lib/cmake/VellumRmlUi/VellumRmlUiVellumRendererTargets.cmake",
            builder.RMLUI_PACKAGE_FILES,
        )

    def test_a_complete_tree_is_missing_nothing(self):
        with tempfile.TemporaryDirectory() as text:
            tree = Path(text)
            for relative in builder.RMLUI_PACKAGE_FILES:
                touch(tree, relative)
            self.assertEqual(builder.missing_package_files(tree, builder.RMLUI_PACKAGE_FILES), [])

    def test_a_tree_without_the_rmlui_package_names_every_missing_file(self):
        with tempfile.TemporaryDirectory() as text:
            tree = Path(text)
            touch(tree, "lib/cmake/Vellum/VellumConfig.cmake")
            self.assertEqual(
                builder.missing_package_files(tree, builder.RMLUI_PACKAGE_FILES),
                list(builder.RMLUI_PACKAGE_FILES),
            )


class UpstreamLeaks(unittest.TestCase):
    def test_rmlui_headers_and_archives_are_leaks(self):
        with tempfile.TemporaryDirectory() as text:
            tree = Path(text)
            touch(tree, "include/RmlUi/Core.h")
            touch(tree, "lib/librmlui.a")
            touch(tree, "lib/cmake/RmlUi/RmlUiConfig.cmake")
            touch(tree, "lib/libvellum-rmlui-core.0.1.dylib")
            self.assertEqual(
                builder.upstream_rmlui_leaks(tree),
                ["include/RmlUi/Core.h", "lib/cmake/RmlUi/RmlUiConfig.cmake", "lib/librmlui.a"],
            )

    def test_lua_archives_are_leaks(self):
        with tempfile.TemporaryDirectory() as text:
            tree = Path(text)
            touch(tree, "lib/liblua.a")
            touch(tree, "lib/libvellum-rmlui-lua.a")
            touch(tree, "include/lua.h")
            self.assertEqual(
                builder.upstream_rmlui_leaks(tree),
                ["include/lua.h", "lib/liblua.a", "lib/libvellum-rmlui-lua.a"],
            )

    def test_upstream_paths_are_recognized_and_vellum_paths_are_not(self):
        for relative in ("include/RmlUi/Core.h", "include/RmlUi/Core/Box.h", "lib/librmlui.a",
                         "lib/librmlui_debugger.a", "lib/cmake/RmlUi/RmlUiConfig.cmake",
                         "lib/cmake/RmlUi/Modules/FindLuaJIT.cmake", "include/freetype2/ft2build.h",
                         "lib/libfreetype.a", "include/luaconf.h", "lib/liblua.a",
                         "lib/libvellum-rmlui-lua.a"):
            self.assertTrue(builder.is_upstream_rmlui_path(relative), relative)
        for relative in ("lib/libvellum-rmlui-core.dylib", "include/vellum/rmlui/backend.hpp",
                         "lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake",
                         "lib/cmake/Vellum/VellumConfig.cmake", "lib/libvellum-gpu.dylib",
                         "share/doc/VellumRmlUi/RmlUi-LICENSE.txt"):
            self.assertFalse(builder.is_upstream_rmlui_path(relative), relative)

    def test_the_vellum_rmlui_package_is_not_a_leak(self):
        with tempfile.TemporaryDirectory() as text:
            tree = Path(text)
            for relative in builder.RMLUI_PACKAGE_FILES:
                touch(tree, relative)
            touch(tree, "include/vellum/rmlui/session.hpp")
            touch(tree, "lib/libvellum-rmlui-core.0.1.dylib")
            self.assertEqual(builder.upstream_rmlui_leaks(tree), [])


class RemoveInstallComponent(unittest.TestCase):
    def fake_install(self, files):
        def run(arguments, *, cwd=None):
            self.assertEqual(arguments[:2], ["cmake", "--install"])
            self.assertIn("--component", arguments)
            prefix = Path(arguments[arguments.index("--prefix") + 1])
            for relative in files:
                touch(prefix, relative)
        return run

    def test_removes_the_component_files_and_the_directories_they_empty(self):
        with tempfile.TemporaryDirectory() as text:
            root = Path(text)
            tree = root / "install"
            upstream = ["include/RmlUi/Core.h", "include/RmlUi/Core/Box.h", "lib/librmlui.a"]
            for relative in upstream:
                touch(tree, relative)
            touch(tree, "lib/libvellum-gpu.dylib")
            touch(tree, "lib/cmake/Vellum/VellumConfig.cmake")
            with mock.patch.object(builder, "run", self.fake_install(upstream)):
                removed = builder.remove_install_component(
                    root / "build", tree, "VellumRmlUiUpstream", root / "scratch")
            self.assertEqual(removed, sorted(upstream))
            self.assertFalse((tree / "include").exists())
            self.assertTrue((tree / "lib/libvellum-gpu.dylib").is_file())
            self.assertTrue((tree / "lib/cmake/Vellum/VellumConfig.cmake").is_file())
            self.assertFalse((root / "scratch").exists())

    def test_an_empty_component_removes_nothing(self):
        with tempfile.TemporaryDirectory() as text:
            root = Path(text)
            tree = root / "install"
            touch(tree, "lib/libvellum-gpu.dylib")
            with mock.patch.object(builder, "run", self.fake_install([])):
                removed = builder.remove_install_component(
                    root / "build", tree, "VellumRmlUiUpstream", root / "scratch")
            self.assertEqual(removed, [])
            self.assertTrue((tree / "lib/libvellum-gpu.dylib").is_file())

    def test_refuses_a_component_that_installs_anything_but_rmlui_files(self):
        for stray in ("lib/libvellum-rmlui-core.dylib", "include/vellum/rmlui/x.hpp"):
            with self.subTest(stray=stray), tempfile.TemporaryDirectory() as text:
                root = Path(text)
                tree = root / "install"
                touch(tree, "include/RmlUi/Core.h")
                touch(tree, stray)
                files = ["include/RmlUi/Core.h", stray]
                with mock.patch.object(builder, "run", self.fake_install(files)):
                    with self.assertRaisesRegex(builder.ArtifactError, stray):
                        builder.remove_install_component(
                            root / "build", tree, "VellumRmlUiUpstream", root / "scratch")
                # Refused before anything is removed.
                self.assertTrue((tree / stray).is_file())
                self.assertTrue((tree / "include/RmlUi/Core.h").is_file())

    def test_refuses_a_component_that_shares_a_package_file(self):
        with tempfile.TemporaryDirectory() as text:
            root = Path(text)
            tree = root / "install"
            shared = "lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake"
            touch(tree, shared)
            with mock.patch.object(builder, "run", self.fake_install([shared])):
                with self.assertRaises(builder.ArtifactError):
                    builder.remove_install_component(
                        root / "build", tree, "VellumRmlUiUpstream", root / "scratch")
            self.assertTrue((tree / shared).is_file())


class NormalizeMachoDylibs(unittest.TestCase):
    def test_each_real_dylib_is_normalized_once_and_symlinks_are_skipped(self):
        with tempfile.TemporaryDirectory() as text:
            tree = Path(text)
            real = touch(tree, "lib/libvellum-rmlui-core.0.1.10.dylib")
            (tree / "lib/libvellum-rmlui-core.0.1.dylib").symlink_to(real.name)
            (tree / "lib/libvellum-rmlui-core.dylib").symlink_to("libvellum-rmlui-core.0.1.dylib")
            touch(tree, "lib/libvellum-gpu.dylib")
            normalized, signed = [], []
            with mock.patch.object(builder.shutil, "which", return_value="/usr/bin/codesign"), \
                 mock.patch.object(builder, "canonicalize_macho_temp_root",
                                   side_effect=lambda path: normalized.append(path.name)), \
                 mock.patch.object(builder, "rewrite_macho_uuid"), \
                 mock.patch.object(builder, "run",
                                   side_effect=lambda arguments, **_: signed.append(Path(arguments[-1]).name)):
                builder.normalize_macho_dylibs(tree, "0" * 40, "darwin-arm64")
            self.assertEqual(normalized, ["libvellum-gpu.dylib", "libvellum-rmlui-core.0.1.10.dylib"])
            self.assertEqual(sorted(set(signed)), normalized)


if __name__ == "__main__":
    unittest.main()
