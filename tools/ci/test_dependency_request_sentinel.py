#!/usr/bin/env python3
"""The sentinel leaves its entry only when injected after the recorder."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORDER = HERE / "dependency_request_recorder.cmake"
SENTINEL = HERE / "dependency_request_sentinel.cmake"


def configure(includes: list[Path]) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "src").mkdir()
        (root / "src" / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.24)\nproject(sentinel_probe NONE)\n", encoding="utf-8")
        log = root / "requests.jsonl"
        subprocess.run(["cmake", "-S", str(root / "src"), "-B", str(root / "build"),
                        "-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=" + ";".join(map(str, includes)),
                        f"-DDEPENDENCY_REQUEST_LOG={log}"],
                       check=True, capture_output=True, text=True)
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


@unittest.skipUnless(shutil.which("cmake"), "needs CMake 3.24 or newer")
class SentinelTests(unittest.TestCase):
    def test_a_project_with_no_requests_logs_the_sentinel(self):
        self.assertEqual(configure([RECORDER, SENTINEL]),
                         [{"method": "SENTINEL", "name": "recorder-loaded"}])

    def test_without_the_sentinel_the_log_is_empty(self):
        # The control: the same project leaves nothing for the checker to read.
        self.assertEqual(configure([RECORDER]), [])

    def test_the_sentinel_alone_writes_nothing(self):
        self.assertEqual(configure([SENTINEL]), [])


if __name__ == "__main__":
    unittest.main()
