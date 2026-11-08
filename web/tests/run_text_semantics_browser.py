#!/usr/bin/env python3
"""Execute browser text semantics against focused and exact Phase-3 fixtures."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable


def stop_browser(
    process: subprocess.Popen[bytes], timeout: int = 5, *,
    process_group: int | None = None,
) -> None:
    """Stop Chrome and every helper that could retain inherited descriptors."""
    if process_group is None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout)
        return

    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process_group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if process.poll() is None:
        process.wait(timeout)


class ProofTimeout(Exception):
    """The browser did not deliver its proof; names the phase that stalled."""

    def __init__(self, phase: str, detail: str) -> None:
        super().__init__(f"browser proof timed out in phase {phase}: {detail}")
        self.phase = phase


class ProofProgress:
    """What the browser has done so far, fed by the HTTP handler's threads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.started = clock()
        self.requests = 0
        self.last_path = ""
        self.last_activity = self.started
        self.received = False

    def request(self, path: str) -> None:
        with self._lock:
            self.requests += 1
            self.last_path = path
            self.last_activity = self._clock()

    def deliver(self) -> None:
        with self._lock:
            self.received = True
            self.last_activity = self._clock()

    def snapshot(self) -> tuple[int, str, float, bool]:
        with self._lock:
            return self.requests, self.last_path, self.last_activity, self.received


def wait_for_proof(
    progress: ProofProgress, process: subprocess.Popen[bytes], *,
    start_timeout: float = 60.0, idle_timeout: float = 30.0,
    total_timeout: float = 240.0, poll: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for the browser's proof on progress, not on one fixed deadline.

    A loaded host can take a long time to start Chrome and fetch the page, so
    one short deadline fails runs that were still advancing. The wait is in
    phases: browser-start (no request served yet; also fails at once if Chrome
    exits), then page-load (requests served, no proof yet; fails after
    idle_timeout without a new request). total_timeout bounds the whole wait.
    A timeout names its phase and what was last served.
    """
    while True:
        requests, last_path, last_activity, received = progress.snapshot()
        if received:
            return
        now = clock()
        elapsed = now - progress.started
        phase = "browser-start" if requests == 0 else "page-load"
        exited = process.poll()
        if exited is not None:
            raise ProofTimeout(
                "browser-exited", f"Chrome exited with {exited} after {elapsed:.1f}s, "
                f"{requests} requests served")
        if elapsed >= total_timeout:
            raise ProofTimeout(
                phase, f"no proof after {elapsed:.1f}s (total limit {total_timeout:.0f}s), "
                f"{requests} requests served, last {last_path!r}")
        if requests == 0 and elapsed >= start_timeout:
            raise ProofTimeout(
                phase, f"Chrome requested nothing in {elapsed:.1f}s "
                f"(limit {start_timeout:.0f}s)")
        if requests and now - last_activity >= idle_timeout:
            raise ProofTimeout(
                phase, f"no request or proof for {now - last_activity:.1f}s after "
                f"{last_path!r} (limit {idle_timeout:.0f}s), {requests} requests served")
        sleep(poll)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument(
        "--fixture", choices=("text", "phase3"), default="text",
    )
    parser.add_argument("--node", type=Path)
    parser.add_argument("--build-script", type=Path)
    args = parser.parse_args()
    chrome = os.environ.get("VELLUM_CHROME_PATH") or shutil.which("google-chrome")
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome and mac_chrome.is_file():
        chrome = str(mac_chrome)
    if not chrome or not Path(chrome).is_file():
        raise SystemExit("Google Chrome is required")
    node = str(args.node.resolve()) if args.node else shutil.which("node")
    if not node:
        raise SystemExit("Node is required")
    build_script = (
        args.build_script.resolve()
        if args.build_script
        else args.source_root / "packages/vellum-ui/scripts/build-project.mjs"
    )
    if not build_script.is_file():
        raise SystemExit(f"Vellum UI build script is missing: {build_script}")

    with tempfile.TemporaryDirectory(prefix="vellum-text-browser-") as temporary:
        root = Path(temporary)
        for name in ("vellum_web_core.js", "vellum_web_core.wasm"):
            shutil.copy2(args.core_root / name, root / name)
        consumer = args.source_root / "web/consumer"
        for name in ("vellum_host.js", "text_semantics.js", "style.css"):
            shutil.copy2(consumer / name, root / name)
        index = (consumer / "index.html").read_text(encoding="utf-8")
        (root / "index.html").write_text(
            index.replace("{{APP_NAME}}", "Text semantics proof"),
            encoding="utf-8",
        )
        (root / "vellum_components.json").write_text(
            '{"schema":"vellum.web-components.v1","components":[]}\n',
            encoding="utf-8",
        )
        if args.fixture == "phase3":
            app_root = root / "phase3-source"
            shutil.copytree(
                args.source_root / "fixtures/authoring-phase3", app_root,
            )
            modules = app_root / "node_modules/@vellum"
            modules.mkdir(parents=True)
            for name in ("pure-esm-root", "pure-esm-leaf"):
                shutil.copytree(
                    app_root / f"vendor/{name}",
                    modules / f"fixture-{name}",
                )
            entry = app_root / "src/App.tsx"
            scenario = app_root / "scenarios/phase3.json"
        else:
            app_root = args.source_root / "web/tests/fixtures"
            entry = app_root / "text-semantics-app.tsx"
            scenario = app_root / "text-semantics-scenario.json"
        shutil.copy2(scenario, root / "scenario.json")
        environment = dict(os.environ)
        environment["VELLUM_BUILD_FORMAT"] = "esm"
        environment["VELLUM_PROJECT_ROOT"] = str(app_root)
        built = subprocess.run([
            node,
            str(build_script),
            str(entry),
            str(root / "app.js"),
        ], env=environment, text=True, capture_output=True, check=False)
        if built.returncode:
            raise SystemExit(
                "browser fixture build failed:\n"
                f"{built.stdout}{built.stderr}"
            )

        evidence: dict[str, object] = {}

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *handler_args: object, **handler_kwargs: object) -> None:
                super().__init__(*handler_args, directory=str(root), **handler_kwargs)

            def log_message(self, format: str, *values: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                progress.request(self.path)
                super().do_GET()

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/__vellum_proof":
                    self.send_error(404)
                    return
                length = int(self.headers.get("content-length", "0"))
                value = json.loads(self.rfile.read(length))
                if isinstance(value, dict):
                    evidence.update(value)
                self.send_response(204)
                self.end_headers()
                progress.deliver()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="vellum-text-chrome-") as profile:
                progress = ProofProgress()
                process = subprocess.Popen([
                    chrome, "--headless=new", "--disable-gpu-sandbox", "--no-first-run",
                    "--disable-background-networking", f"--user-data-dir={profile}",
                    f"http://127.0.0.1:{server.server_port}/index.html"
                    "?vellum-scenario=/scenario.json",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=os.name == "posix")
                try:
                    wait_for_proof(progress, process)
                except ProofTimeout as timeout:
                    raise SystemExit(f"browser text proof: {timeout}") from None
                finally:
                    stop_browser(
                        process,
                        process_group=process.pid if os.name == "posix" else None,
                    )
        finally:
            server.shutdown()
            thread.join(5)

        if evidence.get("bootError"):
            raise SystemExit(f"browser text proof failed: {evidence}")
        composition = evidence.get("compositions")
        accessibility = evidence.get("accessibility")
        expected_composition = [{
            "target": "title-input",
            "value": "GPU Notes日本語",
            "selection": {"start": 12, "end": 12},
        }]
        expected_accessibility = [{
            "target": "title-input",
            "label": "Board title",
            "role": "text-field",
            "browserRole": "textbox",
            "value": "GPU Notes日本語",
        }]
        if composition != expected_composition or \
                accessibility != expected_accessibility:
            raise SystemExit(f"incomplete text/IME/accessibility evidence: {evidence}")
        if args.fixture == "phase3":
            assertions = evidence.get("assertions")
            services = evidence.get("services")
            throws = evidence.get("throws")
            scroll = evidence.get("scrollContainers")
            touches = evidence.get("touches")
            if assertions != [
                {"action": "assert-text", "target": "item-list", "passed": True},
                {"action": "command", "target": "item.add", "passed": True},
            ] or services != [
                {"target": "open", "requested": "files", "supplied": True},
                {"target": "copy", "requested": "clipboard", "supplied": True},
                {"target": "docs", "requested": "open_url", "supplied": True},
            ] or not isinstance(throws, list) or len(throws) != 1 or \
                    throws[0].get("target") != "mapped-error" or \
                    "vellum://app/src/App.tsx" not in json.dumps(throws[0]) or \
                    scroll != [{"id": "item-list", "direction": "vertical"}] or \
                    not isinstance(touches, list) or len(touches) != 1 or \
                    touches[0].get("changed") is not True:
                raise SystemExit(f"incomplete exact Phase-3 evidence: {evidence}")
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
