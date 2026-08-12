#!/usr/bin/env python3
"""Regression coverage for browser proof process cleanup."""

from __future__ import annotations

import subprocess
import unittest

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


if __name__ == "__main__":
    unittest.main()
