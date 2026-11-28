#!/usr/bin/env python3
"""The superconvolver browser proof's exit status for failed checks.

run() launches Chrome, so these cases replace the browser, the process
listing and verify() with stubs. They pin what main() returns when verify()
raises: a negative control that fails on its own named check is the expected
outcome (0), while an unrelated failure, a failure with no control, or a
control that passes with the view altered must stay non-zero.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import run_superconvolver_view_browser as proof
from run_editor_session_browser import CheckFailure


class FakeAdmission:
    endpoint = "ws://127.0.0.1:0"

    def __init__(self, *_args: object) -> None:
        pass

    def __enter__(self) -> "FakeAdmission":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeCdp:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.closed = False

    def connect(self) -> None:
        pass

    def command(self, *_args: object, **_kwargs: object) -> None:
        pass

    def navigate(self, *_args: object, **_kwargs: object) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    pid = 4242

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass


def run_main(control: str | None, verify: object) -> tuple[int, str, str]:
    argv = ["--core-root", "/unused", "--capture-dir", "/unused"]
    if control:
        argv += ["--negative-control", control]
    out, err = io.StringIO(), io.StringIO()
    listings = iter([["browser"], []])
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("sys.argv", ["run", *argv]))
        stack.enter_context(mock.patch.object(proof, "browser", return_value="/fake/chrome"))
        stack.enter_context(mock.patch.object(proof, "stage", return_value={}))
        stack.enter_context(mock.patch.object(subprocess, "Popen", FakeProcess))
        stack.enter_context(mock.patch.object(proof, "CdpAdmission", FakeAdmission))
        stack.enter_context(mock.patch.object(
            proof.CdpAdmission, "chrome_launch_arguments", return_value=[], create=True))
        stack.enter_context(mock.patch.object(
            proof.CdpAdmission, "wait_for_desktop_port", return_value=1, create=True))
        stack.enter_context(mock.patch.object(proof, "ViewCdp", FakeCdp))
        stack.enter_context(mock.patch.object(proof, "stop_browser"))
        stack.enter_context(mock.patch.object(
            proof, "browser_processes", side_effect=lambda *_a: next(listings)))
        stack.enter_context(mock.patch.object(proof, "verify", verify))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        status = proof.main()
    return status, out.getvalue(), err.getvalue()


def failing(name: str, failures: object = ()):
    def verify(*_args: object) -> dict:
        raise CheckFailure(f"{name}: {json.dumps({'failures': sorted(failures)})}")
    return verify


class NegativeControlExitStatus(unittest.TestCase):
    def test_each_control_failing_on_its_own_check_returns_zero(self) -> None:
        for control, (_target, _old, _new, check_name, required) in proof.NEGATIVE_CONTROLS.items():
            with self.subTest(control=control):
                status, out, _err = run_main(control, failing(check_name, required or ()))
                self.assertEqual(status, 0)
                self.assertIn("failed as required", out)

    def test_gate_control_naming_other_failures_is_a_failure(self) -> None:
        for control, (_target, _old, _new, check_name, required) in proof.NEGATIVE_CONTROLS.items():
            if required is None:
                continue
            with self.subTest(control=control):
                for wrong in (set(), required | {"ink:unrelated"}):
                    status, _out, err = run_main(control, failing(check_name, wrong))
                    self.assertEqual(status, 1)
                    self.assertIn("not exactly", err)

    def test_control_failing_on_another_check_is_a_failure(self) -> None:
        status, _out, err = run_main("move", failing("mapping"))
        self.assertEqual(status, 1)
        self.assertIn("proof failed", err)

    def test_failure_without_a_control_is_a_failure(self) -> None:
        status, _out, err = run_main(None, failing("bounds"))
        self.assertEqual(status, 1)
        self.assertIn("proof failed", err)

    def test_control_that_passes_with_the_view_altered_is_a_failure(self) -> None:
        status, _out, err = run_main("move", lambda *_a: {})
        self.assertEqual(status, 1)
        self.assertIn("passed with the view altered", err)

    def test_clean_run_returns_zero_and_prints_evidence(self) -> None:
        status, out, _err = run_main(None, lambda *_a: {"native-screenshot": {}})
        self.assertEqual(status, 0)
        self.assertIn("browserProcessesBeforeStop", out)


if __name__ == "__main__":
    unittest.main()
