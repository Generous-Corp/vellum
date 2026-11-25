#!/usr/bin/env python3
"""Unit tests for check_graphite_host.py, one guard at a time.

The qualification itself needs physical Intel macOS, so these tests replace the
platform probes and the subprocess boundary and prove the verdict logic on any
host.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_graphite_host as host  # noqa: E402

PASS_LINE = "adapter=Intel backend=Metal\nPASS: hardware Graphite draw and pixel readback\n"


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class Fixture:
    """Runs qualify() with the platform and subprocess boundaries replaced."""

    def __init__(self, test, *, system="Darwin", machine="x86_64", translated="0\n",
                 outputs=(PASS_LINE, PASS_LINE), codes=(0, 0), raises=None):
        self.test = test
        self.system, self.machine, self.translated = system, machine, translated
        self.outputs, self.codes, self.raises = list(outputs), list(codes), raises
        self.executed: list[str] = []
        self.tool_calls: list[list[str]] = []
        directory = tempfile.TemporaryDirectory()
        test.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.probe = self.root / "probe"
        self.probe.write_bytes(b"probe-bytes")
        self.output = self.root / "proof"

    def run_subprocess(self, args, **kwargs):
        if args[0] == "sysctl":
            return completed(args, 0, self.translated)
        if args[0] in ("xcrun", "codesign"):
            self.tool_calls.append(list(args))
            if args[0] == "xcrun":
                Path(args[args.index("-output") + 1]).write_bytes(b"old-sdk-copy")
            return completed(args)
        self.executed.append(args[0])
        if self.raises is not None and len(self.executed) == 2:
            raise self.raises
        index = len(self.executed) - 1
        return completed(args, self.codes[index], self.outputs[index])

    def qualify(self):
        with mock.patch.object(host.platform, "system", return_value=self.system), \
             mock.patch.object(host.platform, "machine", return_value=self.machine), \
             mock.patch.object(host.platform, "mac_ver", return_value=("15.8.3", ("", "", ""), "")), \
             mock.patch.object(host.subprocess, "run", side_effect=self.run_subprocess):
            return host.qualify(self.probe, self.output)

    def written_results(self):
        found = sorted(self.output.glob("host-*/result.json"))
        return [json.loads(path.read_text(encoding="utf-8")) for path in found]


class QualifyTest(unittest.TestCase):
    def test_two_passing_cases_pass(self) -> None:
        fixture = Fixture(self)
        report = fixture.qualify()
        self.assertTrue(report["pass"])
        self.assertEqual([case["host"] for case in report["cases"]], ["modern_sdk", "sdk_10_12"])
        self.assertEqual(fixture.written_results()[0]["pass"], True)

    def test_the_old_sdk_copy_is_rebuilt_with_a_10_12_sdk_and_resigned(self) -> None:
        fixture = Fixture(self)
        fixture.qualify()
        xcrun, codesign = fixture.tool_calls
        self.assertIn("10.12", xcrun)
        self.assertIn("-set-build-version", xcrun)
        self.assertEqual(codesign[:4], ["codesign", "--force", "--sign", "-"])

    def test_only_a_copy_receives_the_old_sdk_metadata(self) -> None:
        fixture = Fixture(self)
        report = fixture.qualify()
        self.assertEqual(fixture.probe.read_bytes(), b"probe-bytes")
        hashes = {case["executable_sha256"] for case in report["cases"]}
        self.assertEqual(len(hashes), 2)

    def test_apple_silicon_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "physical Intel"):
            Fixture(self, machine="arm64").qualify()

    def test_a_non_macos_host_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "physical Intel"):
            Fixture(self, system="Linux").qualify()

    def test_rosetta_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "Rosetta"):
            Fixture(self, translated="1\n").qualify()

    def test_a_refused_host_runs_nothing(self) -> None:
        fixture = Fixture(self, translated="1\n")
        with self.assertRaises(ValueError):
            fixture.qualify()
        self.assertEqual(fixture.executed, [])
        self.assertFalse(fixture.output.exists())

    def test_a_failing_old_sdk_case_fails_the_run(self) -> None:
        fixture = Fixture(self, outputs=(PASS_LINE, "shader compile failed\n"), codes=(0, 7))
        report = fixture.qualify()
        self.assertFalse(report["pass"])
        self.assertEqual(report["cases"][1]["exit_code"], 7)

    def test_a_zero_exit_without_the_pass_line_fails_the_run(self) -> None:
        fixture = Fixture(self, outputs=(PASS_LINE, "adapter=Intel backend=Metal\n"))
        self.assertFalse(fixture.qualify()["pass"])

    def test_the_pass_line_with_a_nonzero_exit_fails_the_run(self) -> None:
        fixture = Fixture(self, codes=(0, 9))
        self.assertFalse(fixture.qualify()["pass"])

    def test_a_timeout_in_the_second_case_never_records_a_pass(self) -> None:
        fixture = Fixture(self, raises=subprocess.TimeoutExpired("probe", 45))
        with self.assertRaises(subprocess.TimeoutExpired):
            fixture.qualify()
        results = fixture.written_results()
        self.assertEqual(len(results), 1)
        self.assertNotIn("pass", results[0])
        self.assertEqual([case["host"] for case in results[0]["cases"]], ["modern_sdk"])

    def test_failures_are_persisted(self) -> None:
        fixture = Fixture(self, outputs=(PASS_LINE, "boom\n"), codes=(0, 3))
        fixture.qualify()
        saved = fixture.written_results()[0]
        self.assertFalse(saved["pass"])
        self.assertEqual(saved["cases"][1]["stdout"], "boom\n")


if __name__ == "__main__":
    unittest.main()
