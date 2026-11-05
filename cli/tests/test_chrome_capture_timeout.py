from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
import vellum_web_backend as backend  # noqa: E402
from vellum_cdp_client import CdpClientError  # noqa: E402
sys.path.pop(0)


class _Process:
    pid = None

    def poll(self):
        return 0


class _SlowScreenshotClient:
    """A healthy browser whose screenshot answers in 3 s, as on a loaded VM."""

    created: list["_SlowScreenshotClient"] = []

    def __init__(self, endpoint, *, timeout):
        self.timeout = timeout
        self.timeouts = [timeout]
        _SlowScreenshotClient.created.append(self)

    def discover(self):
        return {"Browser": "Chrome/151"}

    def connect(self):
        pass

    def close(self):
        pass

    def set_timeout(self, timeout):
        self.timeout = timeout
        self.timeouts.append(timeout)

    def wait_for_dom(self):
        pass

    def execute_interaction_plan(self, plan):
        return {}

    def settle_idle(self):
        return {}

    def capture_screenshot(self):
        if self.timeout < 3:
            raise CdpClientError(
                f"CDP Page.captureScreenshot got no response within {self.timeout:g} s: timed out"
            )
        raise CdpClientError("stop after the screenshot")


@contextmanager
def _admission(*_args, **_kwargs):
    yield mock.Mock(endpoint=object())


class CaptureTimeoutTests(unittest.TestCase):
    def run_capture(self):
        _SlowScreenshotClient.created.clear()
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "index.html").write_text("<p>x</p>\n")
            with mock.patch.object(backend, "CdpClient", _SlowScreenshotClient), \
                 mock.patch.object(backend, "browser_version", return_value="151"), \
                 mock.patch.object(backend, "CdpAdmission", mock.Mock(
                     side_effect=_admission, wait_for_desktop_port=mock.Mock(return_value=1),
                     chrome_launch_arguments=mock.Mock(return_value=[]))), \
                 mock.patch.object(backend, "stop_browser"):
                with self.assertRaises(backend.BackendFailure) as raised:
                    backend.run_chrome_interaction_capture(
                        build, {"name": "plan"}, chrome="/bin/true",
                        process_factory=lambda *a, **k: _Process(),
                    )
        return raised.exception, _SlowScreenshotClient.created[-1]

    def test_capture_commands_get_the_capture_bound_not_the_readiness_poll(self) -> None:
        error, client = self.run_capture()
        # The readiness poll keeps its short bound; the capture runs under the
        # longer one, so the slow screenshot is answered.
        self.assertEqual(client.timeouts, [2.0, backend.CAPTURE_COMMAND_TIMEOUT])
        self.assertIn("stop after the screenshot", str(error))
        self.assertIn("Browser capture failed", str(error))

    def test_without_the_capture_bound_the_slow_screenshot_fails_and_says_why(self) -> None:
        # The negative control: the pre-fix behaviour, the poll's 2 s bound
        # kept for every capture command, fails on the healthy slow browser.
        with mock.patch.object(_SlowScreenshotClient, "set_timeout", lambda self, timeout: None):
            error, _ = self.run_capture()
        self.assertIn("Page.captureScreenshot got no response within 2 s", str(error))


if __name__ == "__main__":
    unittest.main()
