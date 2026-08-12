#!/usr/bin/env python3
"""Regression coverage for browser proof process cleanup."""

from __future__ import annotations

import subprocess
import os
import signal
import sys
import unittest
from unittest import mock

from run_text_semantics_browser import stop_browser


class FakeProcess:
    def __init__(self, *, running: bool = True, stubborn: bool = False) -> None:
        self.running = running
        self.stubborn = stubborn
        self.terminated = False
        self.killed = False
        self.waits: list[int] = []

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.running = False

    def wait(self, timeout: int) -> int:
        self.waits.append(timeout)
        if self.stubborn and not self.killed:
            raise subprocess.TimeoutExpired("chrome", timeout)
        self.running = False
        return 0


class BrowserProcessCleanupTests(unittest.TestCase):
    def test_already_exited_browser_is_untouched(self) -> None:
        process = FakeProcess(running=False)

        stop_browser(process)  # type: ignore[arg-type]

        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)
        self.assertEqual(process.waits, [])

    def test_normal_shutdown_waits_for_browser(self) -> None:
        process = FakeProcess()

        stop_browser(process, timeout=7)  # type: ignore[arg-type]

        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)
        self.assertEqual(process.waits, [7])

    def test_stubborn_browser_is_killed_and_reaped(self) -> None:
        process = FakeProcess(stubborn=True)

        stop_browser(process, timeout=3)  # type: ignore[arg-type]

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.waits, [3, 3])

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_exited_launcher_still_terminates_its_process_group(self) -> None:
        process = FakeProcess(running=False)

        with mock.patch("run_text_semantics_browser.os.killpg") as killpg:
            stop_browser(process, process_group=417)  # type: ignore[arg-type]

        self.assertEqual(
            killpg.call_args_list,
            [mock.call(417, signal.SIGTERM), mock.call(417, signal.SIGKILL)],
        )

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_descendant_cannot_retain_captured_output_pipe(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys; "
                    "subprocess.Popen([sys.executable,'-c',"
                    "'import os,time; print(os.getpid(),os.getpgrp(),flush=True); time.sleep(30)'])"
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            descendant = process.stdout.readline().strip().split()
            self.assertEqual(len(descendant), 2)
            self.assertEqual(int(descendant[1]), process.pid)
            process.wait(timeout=5)
            stop_browser(process, process_group=process.pid)
            process.communicate(timeout=5)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
