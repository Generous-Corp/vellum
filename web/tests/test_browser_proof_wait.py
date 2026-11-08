#!/usr/bin/env python3
"""The browser proofs wait on progress, not on one fixed deadline.

A fake clock drives wait_for_proof through scripted browser behaviour, so the
cases run in milliseconds and need no Chrome. The slow-start case is the one a
loaded CI host produces: Chrome serves its first request after 25 s, which the
former fixed 20 s wait failed although the run was still advancing.
"""

from __future__ import annotations

import unittest

from run_text_semantics_browser import ProofProgress, ProofTimeout, wait_for_proof


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.events: list[tuple[float, object]] = []

    def __call__(self) -> float:
        return self.now

    def at(self, when: float, action: object) -> None:
        self.events.append((when, action))
        self.events.sort(key=lambda event: event[0])

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        while self.events and self.events[0][0] <= self.now:
            _, action = self.events.pop(0)
            action()


class FakeChrome:
    def __init__(self) -> None:
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def run(clock: FakeClock, chrome: FakeChrome, progress: ProofProgress) -> ProofTimeout | None:
    try:
        wait_for_proof(progress, chrome, poll=0.5, clock=clock, sleep=clock.sleep)  # type: ignore[arg-type]
    except ProofTimeout as timeout:
        return timeout
    return None


class ProofWaitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.chrome = FakeChrome()
        self.progress = ProofProgress(clock=self.clock)

    def test_slow_browser_start_still_delivers(self) -> None:
        self.clock.at(25.0, lambda: self.progress.request("/index.html"))
        self.clock.at(27.0, lambda: self.progress.request("/app.js"))
        self.clock.at(33.0, self.progress.deliver)
        self.assertIsNone(run(self.clock, self.chrome, self.progress))
        self.assertGreaterEqual(self.clock.now, 33.0)

    def test_steady_progress_is_not_cut_off_by_idle_limit(self) -> None:
        for second in range(5, 200, 10):
            self.clock.at(float(second), lambda s=second: self.progress.request(f"/r{s}"))
        self.clock.at(201.0, self.progress.deliver)
        self.assertIsNone(run(self.clock, self.chrome, self.progress))

    def test_browser_that_never_requests_fails_in_browser_start(self) -> None:
        timeout = run(self.clock, self.chrome, self.progress)
        self.assertIsNotNone(timeout)
        self.assertEqual(timeout.phase, "browser-start")
        self.assertGreaterEqual(self.clock.now, 60.0)
        self.assertLess(self.clock.now, 61.0)

    def test_chrome_exit_fails_at_once(self) -> None:
        self.clock.at(3.0, lambda: setattr(self.chrome, "exit_code", 1))
        timeout = run(self.clock, self.chrome, self.progress)
        self.assertEqual(timeout.phase, "browser-exited")
        self.assertLess(self.clock.now, 4.0)

    def test_stall_after_page_served_names_the_last_request(self) -> None:
        self.clock.at(2.0, lambda: self.progress.request("/index.html"))
        self.clock.at(3.0, lambda: self.progress.request("/vellum_web_core.wasm"))
        timeout = run(self.clock, self.chrome, self.progress)
        self.assertEqual(timeout.phase, "page-load")
        self.assertIn("/vellum_web_core.wasm", str(timeout))
        self.assertGreaterEqual(self.clock.now, 33.0)
        self.assertLess(self.clock.now, 34.0)

    def test_total_limit_bounds_a_page_that_keeps_requesting(self) -> None:
        for second in range(5, 400, 5):
            self.clock.at(float(second), lambda s=second: self.progress.request(f"/r{s}"))
        timeout = run(self.clock, self.chrome, self.progress)
        self.assertEqual(timeout.phase, "page-load")
        self.assertIn("total limit", str(timeout))
        self.assertGreaterEqual(self.clock.now, 240.0)
        self.assertLess(self.clock.now, 241.0)


if __name__ == "__main__":
    unittest.main()
