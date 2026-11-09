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

    def test_toolchain_mismatch_fails_only_when_the_pin_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            sdk = Path(text)
            (sdk / "lib" / "pkgconfig").mkdir(parents=True)
            (sdk / runner.PKG_CONFIG_FILE).write_text("", encoding="utf-8")
            saved = runner.PINNED_SWIFT
            runner.PINNED_SWIFT = "0.0.0"
            try:
                with tempfile.TemporaryDirectory() as work:
                    calls = []
                    original = runner.check_package
                    runner.check_package = lambda package: calls.append(package) or ["stop here"]
                    try:
                        out, err = io.StringIO(), io.StringIO()
                        with redirect_stdout(out), redirect_stderr(err):
                            code = runner.main(["--package", str(PACKAGE), "--work", work,
                                                "--sdk-prefix", str(sdk)])
                    finally:
                        runner.check_package = original
                self.assertEqual(code, 1)
                self.assertTrue(calls, "an unpinned toolchain must still run the proof")
                self.assertIn("running on an unpinned toolchain", out.getvalue())
                self.assertNotIn("Swift is not the pinned", err.getvalue())
                code, _, err = self.run_main(
                    "--sdk-prefix", str(sdk), "--require-pinned-toolchain")
                self.assertEqual(code, 1)
                self.assertIn("Swift is not the pinned 0.0.0", err)
            finally:
                runner.PINNED_SWIFT = saved

    def test_missing_swift_fails_when_the_prefix_is_required(self) -> None:
        saved = runner.shutil.which
        runner.shutil.which = lambda name: None
        try:
            with tempfile.TemporaryDirectory() as text:
                sdk = Path(text)
                (sdk / "lib" / "pkgconfig").mkdir(parents=True)
                (sdk / runner.PKG_CONFIG_FILE).write_text("", encoding="utf-8")
                code, out, _ = self.run_main("--sdk-prefix", str(sdk))
                self.assertEqual(code, runner.SKIP)
                code, _, err = self.run_main("--sdk-prefix", str(sdk), "--require-prefix")
                self.assertEqual(code, 1)
                self.assertIn("swift toolchain is not on PATH", err)
        finally:
            runner.shutil.which = saved

    def test_native_hosts_must_pass_when_required(self) -> None:
        guard = "machine in use: HIDIdleTime 5 s is under 180 s"
        passed = {"native_hosts": "passed"}
        silent = {"native_hosts": "skipped: no window-server session"}
        inherited = {"native_hosts": "skipped: inherited"}
        self.assertIsNone(runner.native_problem(passed, True, None))
        self.assertIsNone(runner.native_problem(silent, False, None))
        self.assertIsNotNone(runner.native_problem(silent, True, None))
        self.assertIsNotNone(runner.native_problem(inherited, True, guard))
        self.assertIsNone(runner.native_problem({"native_hosts": f"skipped: {guard}"}, True, guard))

    def test_runner_owns_the_native_skip_variable(self) -> None:
        saved = runner.os.environ.get(runner.NATIVE_SKIP_VARIABLE)
        runner.os.environ[runner.NATIVE_SKIP_VARIABLE] = "inherited"
        try:
            environment = runner.sterile_environment(Path("/opt/sdk"))
        finally:
            if saved is None:
                runner.os.environ.pop(runner.NATIVE_SKIP_VARIABLE, None)
            else:
                runner.os.environ[runner.NATIVE_SKIP_VARIABLE] = saved
        self.assertNotIn(runner.NATIVE_SKIP_VARIABLE, environment)

    def test_ci_flags_parse_only_true_or_one(self) -> None:
        self.assertTrue(runner.running_in_ci({"CI": "true"}))
        self.assertTrue(runner.running_in_ci({"CI": "1"}))
        self.assertTrue(runner.running_in_ci({"GITHUB_ACTIONS": "TRUE"}))
        self.assertFalse(runner.running_in_ci({"CI": "false"}))
        self.assertFalse(runner.running_in_ci({"CI": "0"}))
        self.assertFalse(runner.running_in_ci({}))

    def test_finder_and_swiftpm_metadata_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            package = Path(text) / "package"
            runner.shutil.copytree(PACKAGE, package, ignore=runner.shutil.ignore_patterns(".build"))
            (package / ".DS_Store").write_bytes(b"\0")
            (package / ".swiftpm" / "xcode").mkdir(parents=True)
            (package / ".swiftpm" / "xcode" / "state.json").write_text("{}", encoding="utf-8")
            self.assertEqual(runner.check_package(package), [])

    def test_desk_guard_skips_native_hosts_while_the_machine_is_in_use(self) -> None:
        self.assertIsNone(runner.native_guard_reason(True, 0.0, 99.0))
        self.assertIsNone(runner.native_guard_reason(False, 180.0, 3.99))
        self.assertIn("in use", runner.native_guard_reason(False, 179.0, 0.5))
        self.assertIn("load", runner.native_guard_reason(False, 600.0, 4.0))
        self.assertIn("HIDIdleTime", runner.native_guard_reason(False, None, 0.5))
        self.assertIsNone(runner.desk_guard({"GITHUB_ACTIONS": "true"}))
        self.assertIsNotNone(runner.native_guard_reason(False, 10.0, 0.5))

    def test_no_window_server_is_a_visible_skip_only_in_ci(self) -> None:
        report = {"native_hosts": runner.NO_WINDOW_SERVER}
        self.assertIsNone(runner.native_problem(report, True, None, ci=True))
        self.assertIsNotNone(runner.native_problem(report, True, None, ci=False))
        self.assertIsNotNone(runner.native_problem(
            {"native_hosts": "skipped: inherited"}, True, None, ci=True))
        probe = {"launchctl_command": "launchctl print gui/501",
                 "launchctl_gui": {"ok": False, "exit": 113, "detail": "Bad request."},
                 "cg_session": False}
        summary = runner.native_summary(report, probe)
        self.assertTrue(summary.startswith("SKIP native AppKit and SwiftUI hosts"))
        self.assertIn("failed (exit 113)", summary)
        self.assertIn("CGSessionCopyCurrentDictionary NULL", summary)
        self.assertNotIn("passed", summary)

    def test_window_server_probe_reports_both_signals(self) -> None:
        probe = runner.window_server_probe()
        self.assertIn(probe["cg_session"], (True, False, None))
        self.assertIn("ok", probe["launchctl_gui"])
        self.assertTrue(probe["launchctl_command"].startswith("launchctl print gui/"))

    def test_native_check_reports_pass_skip_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            evidence = Path(text) / "evidence.json"
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(runner.check_native(evidence), runner.SKIP)
            for state, expected in (("passed", 0), (runner.NO_WINDOW_SERVER, runner.SKIP),
                                    ("broken", 1)):
                evidence.write_text(runner.json.dumps({"proof": {"native_hosts": state}}),
                                    encoding="utf-8")
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(runner.check_native(evidence), expected, state)

    def test_step_summary_is_appended_in_ci(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            summary = Path(text) / "summary.md"
            runner.publish_summary({"GITHUB_STEP_SUMMARY": str(summary)}, ["SKIP native"])
            self.assertIn("- SKIP native", summary.read_text(encoding="utf-8"))
            runner.publish_summary({}, ["ignored"])


if __name__ == "__main__":
    unittest.main()
