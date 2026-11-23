#!/usr/bin/env python3
"""Prove a generated unexport list invalidates the images that consume it."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("cmake") and shutil.which("ninja"),
                     "needs the Darwin linker, CMake and Ninja")
class UnexportsRelinkTest(unittest.TestCase):
    def test_mirror_consumers_track_generated_unexport_list(self) -> None:
        cmake = (Path(__file__).resolve().parent / "VellumMirror.cmake").read_text(encoding="utf-8")
        self.assertIn(
            'set_property(TARGET "${_library}" APPEND PROPERTY LINK_DEPENDS\n'
            '            "${VELLUM_MIRROR_UNEXPORTS}")', cmake)

    def test_provider_tracks_its_export_list(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        cmake = (repo / "cmake/VellumSkiaDawn.cmake").read_text(encoding="utf-8")
        self.assertIn('"LINKER:-exported_symbols_list,${_vellum_provider_exports}"', cmake)
        self.assertIn(
            'set_property(TARGET vellum-skia-dawn-provider APPEND PROPERTY LINK_DEPENDS\n'
            '    "${_vellum_provider_exports}")', cmake)

    def test_weak_choc_definition_is_hidden_by_mirror_patterns(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=repo) as directory:
            root = Path(directory)
            (root / "mirror.cpp").write_text(
                "namespace choc { namespace javascript { namespace quickjs { "
                "__attribute__((weak)) void planted() {} }}}\n", encoding="utf-8")
            (root / "patterns.list").write_text("__ZN4choc*\n", encoding="utf-8")
            (root / "CMakeLists.txt").write_text(
                """cmake_minimum_required(VERSION 3.24)
project(unexports-choc LANGUAGES CXX)
set(GENERATED "${CMAKE_BINARY_DIR}/unexports.list")
add_custom_command(OUTPUT "${GENERATED}"
    COMMAND "${CMAKE_COMMAND}" -E copy "${CMAKE_SOURCE_DIR}/patterns.list" "${GENERATED}"
    DEPENDS "${CMAKE_SOURCE_DIR}/patterns.list")
add_custom_target(unexports DEPENDS "${GENERATED}")
add_library(mirror SHARED mirror.cpp)
target_link_options(mirror PRIVATE "LINKER:-unexported_symbols_list,${GENERATED}")
add_dependencies(mirror unexports)
set_property(TARGET mirror APPEND PROPERTY LINK_DEPENDS "${GENERATED}")
""", encoding="utf-8")
            build = root / "build"
            subprocess.run(["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["ninja", "-C", str(build), "mirror"], check=True, capture_output=True, text=True)
            image = build / "libmirror.dylib"
            exported = subprocess.run(["nm", "-gU", str(image)], check=True, capture_output=True, text=True).stdout
            self.assertNotIn("choc", exported)

    def test_pattern_only_change_relinks_and_changes_exports(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=repo) as directory:
            root = Path(directory)
            (root / "mirror.c").write_text(
                "void vellum_visible(void) {}\nvoid vendored_symbol(void) {}\n", encoding="utf-8")
            patterns = root / "patterns.list"
            patterns.write_text("_vendored_symbol\n", encoding="utf-8")
            (root / "CMakeLists.txt").write_text(
                """cmake_minimum_required(VERSION 3.24)
project(unexports-relink LANGUAGES C)
set(GENERATED "${CMAKE_BINARY_DIR}/unexports.list")
add_custom_command(OUTPUT "${GENERATED}"
    COMMAND "${CMAKE_COMMAND}" -E copy "${CMAKE_SOURCE_DIR}/patterns.list" "${GENERATED}"
    DEPENDS "${CMAKE_SOURCE_DIR}/patterns.list")
add_custom_target(unexports DEPENDS "${GENERATED}")
add_library(mirror SHARED mirror.c)
target_link_options(mirror PRIVATE "LINKER:-unexported_symbols_list,${GENERATED}")
add_dependencies(mirror unexports)
set_property(TARGET mirror APPEND PROPERTY LINK_DEPENDS "${GENERATED}")
""", encoding="utf-8")
            build = root / "build"
            subprocess.run(["cmake", "-S", str(root), "-B", str(build), "-G", "Ninja"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["ninja", "-C", str(build), "mirror"], check=True, capture_output=True, text=True)
            image = build / "libmirror.dylib"
            first = subprocess.run(["nm", "-gU", str(image)], check=True, capture_output=True, text=True).stdout
            self.assertIn("_vellum_visible", first)
            self.assertNotIn("_vendored_symbol", first)

            patterns.write_text("_vellum_visible\n", encoding="utf-8")
            subprocess.run(["ninja", "-C", str(build), "mirror"], check=True, capture_output=True, text=True)
            second = subprocess.run(["nm", "-gU", str(image)], check=True, capture_output=True, text=True).stdout
            self.assertNotIn("_vellum_visible", second)
            self.assertIn("_vendored_symbol", second)


class GpuRuntimePinnedInterfaceTest(unittest.TestCase):
    """Provider visibility must stay outside the mirror-bound header tree."""

    def test_provider_visibility_contract_is_outside_pinned_headers(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        include = repo / "gpu-runtime/include/gpu_runtime"
        self.assertFalse((include / "export.h").exists())
        for name in ("dawn_native_bootstrap.hpp", "gpu_surface.hpp"):
            text = (include / name).read_text(encoding="utf-8")
            self.assertNotIn("GPU_RUNTIME_API", text)

        cmake = (repo / "gpu-runtime/CMakeLists.txt").read_text(encoding="utf-8")
        provider = cmake[cmake.index("gpu-runtime-device-provider"):]
        self.assertIn("CXX_VISIBILITY_PRESET default", provider)
        self.assertIn("VISIBILITY_INLINES_HIDDEN OFF", provider)
        mirror = (repo / "tools/mirror/VellumMirror.cmake").read_text(encoding="utf-8")
        self.assertIn('list(FILTER _provider_whole EXCLUDE REGEX "^gpu-runtime-device$")', mirror)
        self.assertIn('list(FILTER _provider_unit_links EXCLUDE REGEX "^gpu-runtime-device$")', mirror)
        self.assertIn('list(FILTER _third_party EXCLUDE REGEX "^gpu-runtime-device$")', mirror)
        consumer = (repo / "tools/mirror/RunMirrorConsumer.cmake").read_text(encoding="utf-8")
        self.assertIn('NOT _provider_nm MATCHES "create_dawn"', consumer)
        graphics = (repo / "graphics/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('$<LINK_LIBRARY:WHOLE_ARCHIVE,VellumGpuRuntime::DeviceProvider>', graphics)
        runtime = (repo / "gpu-runtime/CMakeLists.txt").read_text(encoding="utf-8")
        # The bridge keeps the installed package contract (Device for installed
        # consumers) while in-tree mirror builds see only the DeviceApi headers.
        self.assertIn('$<BUILD_INTERFACE:VellumGpuRuntime::DeviceApi>', runtime)
        self.assertIn('$<INSTALL_INTERFACE:VellumGpuRuntime::Device>', runtime)
        self.assertNotIn('$<BUILD_INTERFACE:VellumGpuRuntime::Device>', runtime)
        self.assertIn('"${CMAKE_CURRENT_LIST_DIR}/include"', runtime)
        pin = "14bd4c5e430bc8da6b4204f1daa8a1bdbc6b7f3a"
        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=repo, check=True,
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual(
            git("rev-parse", f"{pin}:gpu-runtime/include"),
            git("rev-parse", "HEAD:gpu-runtime/include"),
        )
        exports = (repo / "cmake/skia_dawn_provider.exports").read_text(encoding="utf-8")
        self.assertIn("__ZN11gpu_runtime*", exports)


if __name__ == "__main__":
    unittest.main()
