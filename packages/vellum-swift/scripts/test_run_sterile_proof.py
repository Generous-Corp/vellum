#!/usr/bin/env python3
"""Unit coverage for the Swift host proof runner's skip and sterility rules."""

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_sterile_proof", HERE / "run_sterile_proof.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
PACKAGE = HERE.parent


class RunnerTest(unittest.TestCase):
    def run_main(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as work, redirect_stdout(out), redirect_stderr(err):
            code = runner.main(["--package", str(PACKAGE), "--work", work, *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_missing_prefix_skips_with_a_clear_message(self) -> None:
        saved = runner.os.environ.pop(runner.PREFIX_VARIABLE, None)
        try:
            code, out, _ = self.run_main("--sdk-prefix", "")
        finally:
            if saved is not None:
                runner.os.environ[runner.PREFIX_VARIABLE] = saved
        self.assertEqual(code, runner.SKIP)
        self.assertIn("SKIP vellum.swift-host-proof: no installed Vellum SDK prefix", out)

    def test_absent_prefix_directory_skips(self) -> None:
        code, out, _ = self.run_main("--sdk-prefix", "/nonexistent/vellum-sdk-prefix")
        self.assertEqual(code, runner.SKIP)
        self.assertIn("not available", out)

    def test_prefix_without_the_c_abi_fails(self) -> None:
        with tempfile.TemporaryDirectory() as prefix:
            code, _, err = self.run_main("--sdk-prefix", prefix)
        self.assertEqual(code, 1)
        self.assertIn("does not ship lib/pkgconfig/vellum-host-c.pc", err)

    def test_installed_layouts_are_located(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            prefix = Path(text)
            sdk = prefix / "lib" / "vellum" / "sdk"
            (sdk / "lib" / "pkgconfig").mkdir(parents=True)
            (sdk / runner.PKG_CONFIG_FILE).write_text("", encoding="utf-8")
            self.assertEqual(runner.locate_sdk(str(prefix)), sdk.resolve())
            self.assertEqual(runner.locate_sdk(str(sdk)), sdk.resolve())

    def test_repository_package_is_sterile(self) -> None:
        self.assertEqual(runner.check_package(PACKAGE), [])

    def test_source_headers_in_the_package_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            package = Path(text) / "package"
            runner.shutil.copytree(PACKAGE, package, ignore=runner.shutil.ignore_patterns(".build"))
            stray = package / "Sources" / "CVellumHostC" / "skia_dawn_surface.hpp"
            stray.write_text("#pragma once\n", encoding="utf-8")
            (package / "Sources" / "CVellumHostC" / "shim.h").write_text(
                "#include <vellum/graphics/scene.hpp>\n", encoding="utf-8")
            problems = runner.check_package(package)
        self.assertTrue(any("skia_dawn_surface.hpp" in item for item in problems))
        self.assertTrue(any("shim" in item for item in problems))

    def test_source_references_outside_allowed_roots_are_detected(self) -> None:
        source = Path("/repo")
        allowed = [Path("/repo/build/swift-host-proof"), Path("/opt/sdk")]
        self.assertFalse(runner.foreign_source_reference(
            "-I/opt/sdk/include /repo/build/swift-host-proof/package", source, allowed))
        self.assertTrue(runner.foreign_source_reference(
            "-I/repo/bindings/c/include", source, allowed))

    def test_toolchain_pin_accepts_only_the_pinned_versions(self) -> None:
        xcode = "Xcode 27.2 Build version 27B52"
        swift = "swift-driver version: 1.168.6 Apple Swift version 6.4.2 (swiftlang-6.4.2.1.9)"
        self.assertEqual(runner.pinned_toolchain_problems(xcode, swift), [])
        self.assertEqual(len(runner.pinned_toolchain_problems(
            "Xcode 27.2 Build version 27B50", swift)), 1)
        self.assertEqual(len(runner.pinned_toolchain_problems(
            xcode, "Apple Swift version 6.4.1 (swiftlang-6.4.1)")), 1)
        self.assertEqual(len(runner.pinned_toolchain_problems("", "")), 2)

    def test_missing_prefix_fails_when_required(self) -> None:
        code, _, err = self.run_main("--sdk-prefix", "", "--require-prefix")
        self.assertEqual(code, 1)
        self.assertIn("no installed Vellum SDK prefix", err)
        code, _, err = self.run_main(
            "--sdk-prefix", "/nonexistent/vellum-sdk-prefix", "--require-prefix")
        self.assertEqual(code, 1)

    def test_toolchain_mismatch_skips_unless_the_pin_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            sdk = Path(text)
            (sdk / "lib" / "pkgconfig").mkdir(parents=True)
            (sdk / runner.PKG_CONFIG_FILE).write_text("", encoding="utf-8")
            saved = runner.PINNED_SWIFT
            runner.PINNED_SWIFT = "0.0.0"
            try:
                code, out, _ = self.run_main("--sdk-prefix", str(sdk))
                self.assertEqual(code, runner.SKIP)
                self.assertIn("Swift is not the pinned 0.0.0", out)
                code, _, err = self.run_main(
                    "--sdk-prefix", str(sdk), "--require-pinned-toolchain")
                self.assertEqual(code, 1)
                self.assertIn("Swift is not the pinned 0.0.0", err)
            finally:
                runner.PINNED_SWIFT = saved

    def test_finder_and_swiftpm_metadata_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            package = Path(text) / "package"
            runner.shutil.copytree(PACKAGE, package, ignore=runner.shutil.ignore_patterns(".build"))
            (package / ".DS_Store").write_bytes(b"\0")
            (package / ".swiftpm" / "xcode").mkdir(parents=True)
            (package / ".swiftpm" / "xcode" / "state.json").write_text("{}", encoding="utf-8")
            self.assertEqual(runner.check_package(package), [])


if __name__ == "__main__":
    unittest.main()
