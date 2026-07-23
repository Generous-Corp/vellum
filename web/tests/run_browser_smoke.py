#!/usr/bin/env python3
"""Serve the built proof and require executable evidence from real Chrome."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    chrome = shutil.which("google-chrome")
    if chrome is None:
        application = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        chrome = str(application) if application.is_file() else None
    if chrome is None:
        raise SystemExit("Google Chrome is required for the browser proof")
    received = threading.Event()
    result: dict[str, object] = {}

    class ProofHandler(SimpleHTTPRequestHandler):
        def __init__(self, *handler_args: object, **handler_kwargs: object) -> None:
            super().__init__(*handler_args, directory=str(args.root), **handler_kwargs)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/__vellum_proof":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("proof must be an object")
                result.update(payload)
                self.send_response(204)
                self.end_headers()
                received.set()
            except (ValueError, json.JSONDecodeError) as error:
                self.send_error(400, str(error))

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProofHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="vellum-chrome-") as profile:
            process = subprocess.Popen(
                [chrome, "--headless=new", "--disable-gpu-sandbox", "--no-first-run",
                 "--disable-background-networking", f"--user-data-dir={profile}",
                 f"http://127.0.0.1:{server.server_port}/index.html"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                if not received.wait(timeout=20):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise SystemExit("browser proof handshake timed out")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        if result.get("backend") != "wasm-shared-cpp-core+canvas2d-shell":
            raise SystemExit(f"unexpected backend evidence: {result}")
        initial = result.get("initial")
        after = result.get("afterAction")
        if not isinstance(initial, dict) or not isinstance(after, dict) or \
                initial != {"commandCount": 5, "digest": 2618880820} or \
                after != {"commandCount": 5, "digest": 1000471619} or \
                result.get("authoringRuntime") != "browser JavaScript" or \
                result.get("embeddedEngineMarkers") is not False or \
                not isinstance(result.get("canvasDataBytes"), int) or \
                result["canvasDataBytes"] < 1000:
            raise SystemExit(f"incomplete browser evidence: {result}")
        print("Chrome executed browser JavaScript -> shared C++ Wasm -> Canvas2D proof")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
