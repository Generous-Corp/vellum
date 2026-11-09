#!/usr/bin/env python3
"""The framework/native symbol-intersection gate: zero passes, a shared name or a blind side fails."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mirror_symbol_intersection", HERE / "mirror_symbol_intersection.py")
assert spec and spec.loader
si = importlib.util.module_from_spec(spec)
spec.loader.exec_module(si)


class SymbolIntersectionTest(unittest.TestCase):
    def run_gate(self, symbols: dict[str, set[str]], *extra: str,
                 classes: dict[str, set[str]] | None = None) -> int:
        """Run main() over fake archives whose (already demangled) defined names are `symbols`."""
        classes = classes or {}
        argv = ["mirror_symbol_intersection.py", "--old", "pulp", "--new", "vellum",
                "--namespace", "vellum::runtime", "--mirror", "mirror.dylib", "--native", "native.a", *extra]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(si, "defined_symbols", lambda a: symbols.get(a, set())), \
                mock.patch.object(si, "demangle", lambda names: set(names)), \
                mock.patch.object(si, "private_externs", lambda a: 0), \
                mock.patch.object(si, "objc_classes", lambda a: classes.get(a, set())), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return si.main()

    def test_disjoint_sides_pass(self) -> None:
        self.assertEqual(self.run_gate({"mirror.dylib": {"vellum::runtime::log(int)"},
                                        "native.a": {"vellum::runtime::Kernel::run()"}}), 0)

    def test_a_shared_defined_name_fails(self) -> None:
        self.assertEqual(self.run_gate({"mirror.dylib": {"vellum::runtime::Kernel::run()", "vellum::runtime::log(int)"},
                                        "native.a": {"vellum::runtime::Kernel::run()"}}), 1)

    def test_a_pre_rename_mirror_name_is_respelled_before_comparing(self) -> None:
        self.assertEqual(self.run_gate({"mirror.dylib": {"pulp::runtime::Kernel::run()"},
                                        "native.a": {"vellum::runtime::Kernel::run()"}}), 1)

    def test_the_plant_control_makes_a_clean_run_fail(self) -> None:
        clean = {"mirror.dylib": {"vellum::runtime::log(int)"}, "native.a": {"vellum::runtime::Kernel::run()"}}
        self.assertEqual(self.run_gate(clean), 0)
        self.assertEqual(self.run_gate(clean, "--plant"), 1)

    def test_a_blind_side_fails_instead_of_passing(self) -> None:
        # Names outside the namespace do not count, so each of these sees nothing on one side.
        self.assertEqual(self.run_gate({"mirror.dylib": {"vellum::runtime::log(int)"}, "native.a": set()}), 3)
        self.assertEqual(self.run_gate({"mirror.dylib": set(), "native.a": {"vellum::runtime::Kernel::run()"}}), 3)
        self.assertEqual(self.run_gate({"mirror.dylib": {"vellum::graphics::Surface::paint()"},
                                        "native.a": {"vellum::runtime::Kernel::run()"}}), 3)
        # A blind side stays a failure even with the plant, which has nothing to plant from.
        self.assertEqual(self.run_gate({"mirror.dylib": set(), "native.a": {"vellum::runtime::Kernel::run()"}},
                                       "--plant"), 3)

    def test_a_shared_objective_c_class_fails(self) -> None:
        names = {"mirror.dylib": {"vellum::runtime::log(int)"}, "native.a": {"vellum::runtime::Kernel::run()"}}
        self.assertEqual(self.run_gate(names, "--old-class-prefix", "Pulp", "--new-class-prefix", "VellumMirror",
                                       classes={"mirror.dylib": {"PulpView"}, "native.a": {"VellumMirrorView"}}), 1)

    def test_the_defined_symbol_reader_keeps_only_definitions(self) -> None:
        nm = "__ZN6vellum7runtime3logEi T 10 4\n__ZN6vellum7runtime6KernelC1Ev U\n_local t 0 0\n"
        result = mock.Mock(stdout=nm)
        with mock.patch.object(si.subprocess, "run", return_value=result):
            self.assertEqual(si.defined_symbols("x.a"), {"__ZN6vellum7runtime3logEi", "_local"})


if __name__ == "__main__":
    unittest.main()
