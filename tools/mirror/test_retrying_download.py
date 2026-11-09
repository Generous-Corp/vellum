#!/usr/bin/env python3
"""retrying_download against a local HTTP server that fails on a script."""

from __future__ import annotations

import http.server
import importlib.util
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retrying_download", HERE / "retrying_download.py")
assert spec and spec.loader
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)

BODY = b"release asset bytes\n" * 64
TRUNCATED = -1


class ScriptedServer:
    """Answers each GET with the next status in `script`, then the last one forever."""

    def __init__(self, script: list[int]):
        self.script = list(script)
        self.requests = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                index = min(outer.requests, len(outer.script) - 1)
                outer.requests += 1
                status = outer.script[index]
                if status == TRUNCATED:
                    # Promise the whole body, send half, hang up.
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY[: len(BODY) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                body = BODY if status == 200 else b"gateway timeout"
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/asset.bin"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeTime:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RetryingDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "asset.bin"
        self.time = FakeTime()

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, url: str, **kw) -> int:
        return rd.download_with_retry(url, self.dest, sleep=self.time.sleep,
                                      clock=self.time.clock, **kw)

    def test_rides_out_a_run_of_504s(self):
        with ScriptedServer([504, 504, 503, 502, 200]) as server:
            attempts = self.fetch(server.url)
        self.assertEqual(attempts, 5)
        self.assertEqual(server.requests, 5)
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(self.time.sleeps, [2.0, 4.0, 8.0, 16.0])

    def test_a_server_that_never_recovers_fails_within_the_budget(self):
        with ScriptedServer([504]) as server:
            with self.assertRaises(rd.DownloadError) as caught:
                self.fetch(server.url, budget=60.0)
        message = str(caught.exception)
        self.assertIn(server.url, message)
        self.assertIn("504", message)
        self.assertRegex(message, r"after \d+ attempts")
        self.assertLessEqual(self.time.now, 60.0)
        self.assertGreater(server.requests, 1)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_a_404_fails_at_once(self):
        with ScriptedServer([404, 200]) as server:
            with self.assertRaises(rd.DownloadError) as caught:
                self.fetch(server.url)
        self.assertEqual(server.requests, 1)
        self.assertEqual(self.time.sleeps, [])
        self.assertIn("not retried", str(caught.exception))
        self.assertIn(server.url, str(caught.exception))

    def test_a_truncated_body_is_retried_and_leaves_no_partial(self):
        with ScriptedServer([TRUNCATED, 200]) as server:
            self.assertEqual(self.fetch(server.url), 2)
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["asset.bin"])

    def test_a_body_that_is_always_truncated_leaves_nothing_behind(self):
        with ScriptedServer([TRUNCATED]) as server:
            with self.assertRaises(rd.DownloadError):
                self.fetch(server.url, budget=10.0)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_429_is_retried(self):
        with ScriptedServer([429, 200]) as server:
            self.assertEqual(self.fetch(server.url), 2)

    def test_a_refused_connection_is_retried_then_reported(self):
        with ScriptedServer([200]) as server:
            dead = server.url
        with self.assertRaises(rd.DownloadError) as caught:
            self.fetch(dead, budget=10.0)
        self.assertIn("attempts", str(caught.exception))
        self.assertGreater(len(self.time.sleeps), 0)

    def test_control_first_try_success_does_not_sleep(self):
        with ScriptedServer([200]) as server:
            self.assertEqual(self.fetch(server.url), 1)
        self.assertEqual(self.time.sleeps, [])
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_cli_exit_codes(self):
        script = str(HERE / "retrying_download.py")
        with ScriptedServer([200]) as server:
            ok = subprocess.run([sys.executable, "-I", script, server.url, str(self.dest)],
                                capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.dest.read_bytes(), BODY)
        with ScriptedServer([404]) as server:
            bad = subprocess.run([sys.executable, "-I", script, server.url, str(self.dest) + ".x"],
                                 capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("404", bad.stderr)


if __name__ == "__main__":
    unittest.main()
