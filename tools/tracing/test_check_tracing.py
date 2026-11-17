#!/usr/bin/env python3
"""Negative controls for check_tracing.py's symbol inspection."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_tracing as ct  # noqa: E402


def fake_nm(stdout: str = "", returncode: int = 0):
    return mock.patch.object(
        ct.subprocess, "run",
        return_value=subprocess.CompletedProcess([], returncode, stdout, "boom" if returncode else ""))


class SymbolInspection(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.lib = Path(self.dir.name) / "libx.dylib"
        self.lib.write_bytes(b"")

    def test_local_definition_is_seen(self):
        # A hidden (local, lowercase t) runtime must count: nm -gU would drop it.
        with fake_nm("0000000000001000 t __ZN8perfetto10TracingMuxerE"):
            self.assertEqual(ct.defined_symbols(self.lib), ["__ZN8perfetto10TracingMuxerE"])

    def test_perfetto_namespace_is_recognised_and_skia_flag_is_not(self):
        self.assertTrue(ct.is_perfetto_symbol("__ZN8perfetto10TracingMuxerE"))
        self.assertFalse(ct.is_perfetto_symbol(
            "__ZN28SkAndroidFrameworkTraceUtils23gUsePerfettoTrackEventsE"))

    def test_undefined_entries_are_not_definitions(self):
        with fake_nm("                 U _vellum_native_trace_enabled"):
            self.assertEqual(ct.defined_symbols(self.lib), [])

    def test_failed_nm_is_an_error(self):
        with fake_nm("", returncode=1), self.assertRaises(ct.InspectionError):
            ct.defined_symbols(self.lib)

    def test_missing_object_is_an_error(self):
        with self.assertRaises(ct.InspectionError):
            ct.defined_symbols(Path(self.dir.name) / "absent.dylib")
        with self.assertRaises(ct.InspectionError):
            ct.undefined_symbols(Path(self.dir.name) / "absent.o")

    def test_adapter_missing_object_and_failed_nm_report_errors(self):
        ninja = ("\nbuild obj/frame_trace_adapter.cpp.o: CXX_COMPILER__x_ "
                 "/src/frame_trace_adapter.cpp\n  INCLUDES = -I/a\n\n")
        self.assertTrue(ct.adapter_errors(ninja, Path(self.dir.name)))
        obj = Path(self.dir.name) / "obj"
        obj.mkdir()
        (obj / "frame_trace_adapter.cpp.o").write_bytes(b"")
        with fake_nm("", returncode=1):
            errors = ct.adapter_errors(ninja, Path(self.dir.name))
        self.assertTrue(any("failed" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
