#!/usr/bin/env python3
"""Execute browser text semantics against focused and exact Phase-3 fixtures."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument(
        "--fixture", choices=("text", "phase3"), default="text",
    )
    args = parser.parse_args()
    chrome = shutil.which("google-chrome")
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome and mac_chrome.is_file():
        chrome = str(mac_chrome)
    if not chrome:
        raise SystemExit("Google Chrome is required")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node is required")

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
            package = app_root / "package.json"
            manifest = json.loads(package.read_text(encoding="utf-8"))
            manifest["dependencies"].pop("@vellum/ui")
            package.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
            )
            npm = shutil.which("npm")
            if not npm:
                raise SystemExit("npm is required for the exact Phase-3 fixture")
            subprocess.run([
                npm, "install", "--offline", "--ignore-scripts", "--no-audit",
                "--no-fund", "--package-lock=false", "--prefix", str(app_root),
            ], check=True)
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
        subprocess.run([
            node,
            str(args.source_root / "packages/vellum-ui/scripts/build-project.mjs"),
            str(entry),
            str(root / "app.js"),
        ], env=environment, check=True)

        received = threading.Event()
        evidence: dict[str, object] = {}

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *handler_args: object, **handler_kwargs: object) -> None:
                super().__init__(*handler_args, directory=str(root), **handler_kwargs)

            def log_message(self, format: str, *values: object) -> None:
                pass

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
                received.set()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="vellum-text-chrome-") as profile:
                process = subprocess.Popen([
                    chrome, "--headless=new", "--disable-gpu-sandbox", "--no-first-run",
                    "--disable-background-networking", f"--user-data-dir={profile}",
                    f"http://127.0.0.1:{server.server_port}/index.html"
                    "?vellum-scenario=/scenario.json",
                ])
                if not received.wait(20):
                    process.terminate()
                    raise SystemExit("browser text proof timed out")
                process.terminate()
                process.wait(5)
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
            ] or services != [{
                "target": "open", "requested": "files", "supplied": True,
            }] or not isinstance(throws, list) or len(throws) != 1 or \
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
