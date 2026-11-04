#!/usr/bin/env python3
"""Unit tests for check_device_api_closure.py, one violation at a time."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_device_api_closure as closure  # noqa: E402

REAL_INCLUDE = HERE.parent / "include"
CLEAN = """#pragma once
// Comments may mention Pulp, Vellum, Dawn and SkCanvas freely.
#include <cstdint>
#include <gpu_runtime/other.hpp>
namespace gpu_runtime {
struct Info { const char* vendor = "Pulp"; std::uint32_t id = 0; };
}  // namespace gpu_runtime
namespace gpu_runtime::bench {
}
"""


class ClosureTest(unittest.TestCase):
    def tree(self, body: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "gpu_runtime").mkdir()
        (root / "gpu_runtime/other.hpp").write_text("#pragma once\n", encoding="utf-8")
        (root / "gpu_runtime/api.hpp").write_text(body, encoding="utf-8")
        return root

    def assert_violation(self, body: str, fragment: str) -> None:
        problems = closure.check(self.tree(body))
        self.assertTrue(any(fragment in problem for problem in problems),
                        f"expected {fragment!r} in {problems}")

    def test_the_real_headers_are_closed(self) -> None:
        self.assertEqual(closure.check(REAL_INCLUDE), [])

    def test_the_real_tree_has_all_seven_headers(self) -> None:
        names = {path.relative_to(REAL_INCLUDE / "gpu_runtime").as_posix()
                 for path in (REAL_INCLUDE / "gpu_runtime").rglob("*.hpp")}
        self.assertEqual(names, {
            "bench/perf_counters.hpp", "gpu_compute.hpp", "gpu_diagnostics.hpp",
            "gpu_proc_installer.hpp", "gpu_render_time.hpp",
            "gpu_startup_report.hpp", "gpu_surface.hpp"})

    def test_comments_and_strings_do_not_count(self) -> None:
        self.assertEqual(closure.check(self.tree(CLEAN)), [])

    def test_a_framework_include_is_refused(self) -> None:
        self.assert_violation(CLEAN.replace("<cstdint>", "<pulp/runtime/log.hpp>"),
                              "outside the device API")
        self.assert_violation(CLEAN.replace("<cstdint>", "<vellum/graphics/gpu.hpp>"),
                              "outside the device API")

    def test_a_gpu_library_include_is_refused(self) -> None:
        self.assert_violation(CLEAN.replace("<cstdint>", "<webgpu/webgpu_cpp.h>"),
                              "outside the device API")
        self.assert_violation(CLEAN.replace("<cstdint>", "<include/core/SkCanvas.h>"),
                              "outside the device API")

    def test_a_quoted_include_is_refused(self) -> None:
        self.assert_violation(CLEAN.replace("<gpu_runtime/other.hpp>", '"other.hpp"'),
                              "quoted include")

    def test_a_missing_sibling_header_is_refused(self) -> None:
        self.assert_violation(CLEAN.replace("other.hpp>", "absent.hpp>"),
                              "missing header")

    def test_framework_and_gpu_identifiers_in_code_are_refused(self) -> None:
        for identifier in ("pulp::runtime::Log* log;", "vellum::Gpu* gpu;",
                           "wgpu::Device device;", "WGPUDevice device;",
                           "SkCanvas* canvas;", "dawn::native::Instance* i;"):
            with self.subTest(identifier=identifier):
                self.assert_violation(
                    CLEAN.replace("std::uint32_t id = 0;", identifier),
                    "above the device API")

    def test_a_foreign_namespace_is_refused(self) -> None:
        self.assert_violation(CLEAN.replace("namespace gpu_runtime::bench", "namespace pulp::render"),
                              "opens namespace")

    def test_an_empty_tree_is_refused(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.assertTrue(closure.check(Path(temporary.name)))


if __name__ == "__main__":
    unittest.main()
