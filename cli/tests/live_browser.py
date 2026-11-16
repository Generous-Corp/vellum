"""Gate for tests that start a real browser.

chrome_path() falls back to an installed Chrome when VELLUM_CHROME_PATH is
unset. In a sandbox with no window server that browser aborts during
application registration and macOS reports a crash for the user's own Chrome
on every run. Tests that launch a live browser therefore require a pinned
browser named by VELLUM_CHROME_PATH (CI always sets one) and otherwise skip.
A person who wants the installed browser sets VELLUM_TEST_ALLOW_SYSTEM_CHROME=1.
"""
from __future__ import annotations

import functools
import os
import unittest
from typing import Callable, Mapping

PINNED_ENV = "VELLUM_CHROME_PATH"
ESCAPE_ENV = "VELLUM_TEST_ALLOW_SYSTEM_CHROME"


def live_browser_skip_reason(environ: Mapping[str, str] | None = None) -> str | None:
    """Return why a live-browser test must skip, or None when it may run."""
    env = os.environ if environ is None else environ
    if env.get(ESCAPE_ENV) == "1":
        return None
    if env.get(PINNED_ENV):
        # A named browser is never skipped: chrome_path() rejects one that is
        # missing or not executable, so a broken pin fails instead of hiding.
        return None
    return (f"live-browser test skipped: {PINNED_ENV} is unset and the installed "
            f"Chrome is not used by default (set {ESCAPE_ENV}=1 to allow it)")


def requires_pinned_chrome(test: Callable) -> Callable:
    """Skip a test method, at run time, unless a live browser is allowed."""
    @functools.wraps(test)
    def wrapper(*args, **kwargs):
        reason = live_browser_skip_reason()
        if reason is not None:
            raise unittest.SkipTest(reason)
        return test(*args, **kwargs)
    return wrapper
