#!/usr/bin/env python3
"""Drive the Vellum editor session in headless Chrome with real input.

The fixture mounts a knob and a label. CDP sends a touch drag that the browser
cancels and a mouse drag that completes, resizes the container and then the
device pixel ratio, exercises the state, feed and file bridges, captures the
canvas and tears the session down. The run fails unless the gesture edges
arrive in order, each resize reaches the backing store and the layout, the
capture has ink in both controls, and no browser process outlives the run.

--negative-control serves a session whose resize dispatch is removed and
passes only when the resize check fails.

The browser is VELLUM_CHROME_PATH and nothing else; an installed browser is
never used. Without it the run exits 77 so ctest reports a skip.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cli"))

from vellum_browser import configured_provenance  # noqa: E402
from vellum_cdp import CdpAdmission  # noqa: E402
from vellum_cdp_client import ALLOWED_COMMANDS, CdpClient, CdpClientError  # noqa: E402
from run_text_semantics_browser import stop_browser  # noqa: E402

SKIP = 77
RESIZE_DISPATCH = "applySize(measureEntry(entry))"
# Requests to this origin never leave the browser; the client fulfils them.
ORIGIN = "http://127.0.0.1:9/"


class CheckFailure(AssertionError):
    pass


def check(condition: bool, name: str, detail: object) -> None:
    if not condition:
        raise CheckFailure(f"{name}: {json.dumps(detail, sort_keys=True)}")


class FixtureCdp(CdpClient):
    """The capture client with the input and emulation commands this test needs.

    The fixture is served from memory through request interception, so the
    run needs no local server or port.
    Runtime.evaluate is limited to calls on the fixture's own object.
    """

    EXTRA = {"Fetch.enable", "Input.dispatchTouchEvent", "Emulation.setDeviceMetricsOverride"}
    TYPES = {".html": "text/html", ".js": "text/javascript", ".wasm": "application/wasm"}

    def __init__(self, *args: Any, files: dict[str, bytes], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.files = files

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in ALLOWED_COMMANDS and method not in self.EXTRA:
            raise CdpClientError(f"CDP command is not allowed: {method}")
        if method == "Runtime.evaluate" and not str(
                (params or {}).get("expression", "")).startswith("__editorFixture."):
            raise CdpClientError("Runtime.evaluate is limited to the editor fixture")
        if self._socket is None:
            raise CdpClientError("CDP client is not connected")
        command_id = self._send(method, params or {})
        while True:
            opcode, data = self._read_frame(self._socket)
            if opcode == 0x9:
                self._send_frame(self._socket, 0xA, data)
                continue
            if opcode == 0x8:
                raise CdpClientError("CDP websocket closed before command response")
            if opcode != 0x1:
                continue
            message = json.loads(data)
            if message.get("method") == "Fetch.requestPaused":
                self._fulfil(message["params"])
            elif message.get("id") == command_id:
                if "error" in message:
                    raise CdpClientError(f"CDP command failed: {message['error']}")
                return message.get("result") or {}

    def _send(self, method: str, params: dict[str, Any]) -> int:
        self._next_id += 1
        payload = json.dumps({"id": self._next_id, "method": method, "params": params},
                             separators=(",", ":")).encode()
        self._send_frame(self._socket, 0x1, payload)
        return self._next_id

    def _fulfil(self, paused: dict[str, Any]) -> None:
        name = urlsplit(paused["request"]["url"]).path.lstrip("/")
        body = self.files.get(name)
        headers = [{"name": "Content-Type",
                    "value": self.TYPES.get(Path(name).suffix, "application/octet-stream")}]
        self._send("Fetch.fulfillRequest", {
            "requestId": paused["requestId"], "responseCode": 200 if body is not None else 404,
            "responseHeaders": headers,
            "body": base64.b64encode(body or b"").decode("ascii"),
        })

    def fixture(self, call: str) -> Any:
        result = self.command("Runtime.evaluate", {
            "expression": f"__editorFixture.{call}",
            "awaitPromise": True, "returnByValue": True,
        })
        if "exceptionDetails" in result:
            raise CheckFailure(f"fixture call {call} threw: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def mouse(self, kind: str, x: float, y: float) -> None:
        self.command("Input.dispatchMouseEvent", {
            "type": kind, "x": x, "y": y, "button": "left",
            "buttons": 0 if kind == "mouseReleased" else 1, "clickCount": 1,
        })

    def touch(self, kind: str, points: list[tuple[float, float]]) -> None:
        self.command("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [{"x": x, "y": y, "id": 0} for x, y in points],
        })


def browser() -> str | None:
    chrome = os.environ.get("VELLUM_CHROME_PATH")
    if not chrome:
        return None
    if not Path(chrome).is_file() or not os.access(chrome, os.X_OK):
        raise SystemExit(f"VELLUM_CHROME_PATH does not name an executable browser: {chrome}")
    if os.environ.get("VELLUM_REQUIRE_CHROME_PROVENANCE") == "1":
        valid, detail = configured_provenance(Path(chrome))
        if not valid:
            raise SystemExit(f"pinned Chrome lacks valid provenance: {detail}")
    return chrome


def stage(core_root: Path, negative_control: bool) -> dict[str, bytes]:
    files = {name: (core_root / name).read_bytes()
             for name in ("vellum_web_core.js", "vellum_web_core.wasm")}
    fixture = ROOT / "web/tests/fixtures/editor-session"
    for name in ("index.html", "fixture.js"):
        files[name] = (fixture / name).read_bytes()
    for path in sorted((ROOT / "web/session").glob("*.js")):
        files[f"session/{path.name}"] = path.read_bytes()
    if negative_control:
        text = files["session/editor_session.js"].decode("utf-8")
        if text.count(RESIZE_DISPATCH) != 1:
            raise SystemExit("negative control cannot find the single resize dispatch")
        files["session/editor_session.js"] = text.replace(
            RESIZE_DISPATCH, "void entry").encode("utf-8")
    return files


def browser_processes(group: int, profile: str) -> list[str]:
    listing = subprocess.run(["ps", "-axww", "-o", "pid=,pgid=,command="],
                             text=True, capture_output=True, check=True).stdout
    found = []
    for line in listing.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and (fields[1] == str(group) or profile in fields[2]):
            found.append(line.strip())
    return found


def centre(snapshot: dict[str, Any], node: str) -> tuple[float, float]:
    box = snapshot["bounds"][node]
    canvas = snapshot["canvas"]
    return canvas["left"] + box["x"] + box["width"] / 2, canvas["top"] + box["y"] + box["height"] / 2


def verify(cdp: FixtureCdp, capture_dir: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        try:
            boot = cdp.fixture("booted || __editorFixture.errors")
        except CheckFailure as failure:
            # The page has not run its first script yet.
            if "is not defined" not in str(failure) or time.monotonic() > deadline:
                raise
            boot = []
        if boot is True:
            break
        check(boot == [] and time.monotonic() < deadline, "fixture-boot", boot)
        time.sleep(0.1)
    first = cdp.fixture("ready()")
    check(first["stats"]["frames"] >= 1, "initial-frame", first["stats"])
    x, y = centre(first, "mix")

    # A touch drag the browser cancels, then a mouse drag that completes.
    cdp.touch("touchStart", [(x, y)])
    cdp.touch("touchMove", [(x, y - 20)])
    cdp.touch("touchMove", [(x, y - 40)])
    cdp.touch("touchCancel", [])
    cdp.mouse("mousePressed", x, y)
    for step in (1, 2, 3):
        cdp.mouse("mouseMoved", x, y + 15 * step)
    cdp.mouse("mouseReleased", x, y + 45)
    dragged = cdp.fixture("settle()")
    kinds = [event["type"] for event in dragged["gestures"]]
    cancelled = kinds.index("cancel") if "cancel" in kinds else -1
    check(cancelled > 1 and kinds[0] == "begin" and set(kinds[1:cancelled]) == {"set"}
          and kinds[cancelled + 1] == "begin" and kinds[-1] == "end"
          and set(kinds[cancelled + 2:-1]) == {"set"}, "gesture-order", kinds)
    sets = [event for event in dragged["gestures"] if event["type"] == "set"]
    adapter_sets = [call for call in dragged["calls"] if call["call"] == "set"]
    check([call["value"] for call in adapter_sets] == [event["value"] for event in sets],
          "parameter-writes", {"session": sets, "adapter": adapter_sets})
    check([call["call"] for call in dragged["calls"] if call["call"] != "set"]
          == ["begin", "end", "begin", "end"], "adapter-gesture-edges", dragged["calls"])
    check(abs(dragged["value"] - sets[-1]["value"]) < 1e-9, "value-retained", dragged["value"])

    # Two resizes: the container's CSS size, then the device pixel ratio.
    resized = cdp.fixture("resizeContainer(480, 300)")
    size = resized["size"]
    check(size["cssWidth"] == 480 and size["cssHeight"] == 300
          and resized["canvas"]["width"] == 480 * first["size"]["scale"]
          and resized["bounds"]["mix"] != dragged["bounds"]["mix"]
          and resized["stats"]["frames"] > dragged["stats"]["frames"]
          and resized["stats"]["lastFrame"]["cssWidth"] == 480,
          "resize-css", {"size": size, "canvas": resized["canvas"], "stats": resized["stats"]})
    cdp.command("Emulation.setDeviceMetricsOverride", {
        "width": 800, "height": 600, "deviceScaleFactor": 2, "mobile": False})
    dense = cdp.fixture("settle()")
    check(dense["size"]["scale"] == 2 and dense["canvas"]["width"] == 960
          and dense["canvas"]["height"] == 600
          and dense["stats"]["frames"] > resized["stats"]["frames"]
          and dense["stats"]["lastFrame"]["scale"] == 2,
          "resize-device-pixels", {"size": dense["size"], "canvas": dense["canvas"]})

    bridges = cdp.fixture("exerciseBridges()")
    check(bridges == {
        "spectrumRepainted": True, "irName": "Hall.wav", "loadedValue": 0.25,
        "restoredValue": round(sets[-1]["value"] * 100) / 100, "selectedFile": "Room.wav",
        "fileInputRemoved": True, "absentParameterRefused": True,
    }, "bridges", bridges)

    captured = cdp.fixture("capture()")
    header, encoded = captured["png"].split(",", 1)
    check(header == "data:image/png;base64", "capture-format", header)
    capture_dir.mkdir(parents=True, exist_ok=True)
    png = capture_dir / "editor-session.png"
    png.write_bytes(base64.b64decode(encoded))
    for node in ("title", "mix"):
        ink = captured["ink"][node]
        check(ink["pixels"] >= 0.01 * ink["area"], f"ink-{node}", ink)

    finished = cdp.fixture("teardown()")
    check(finished == {
        "canvasRemoved": True, "fileInputRemoved": True, "fileRequestSettled": True,
        "pendingFrame": False, "framesAfterResize": 0, "paramsHandlerRestored": True,
        "irHandlerRestored": True, "callsAfterTeardown": len(dragged["calls"]),
    }, "teardown", finished)
    return {"gestures": kinds, "size": dense["size"], "ink": captured["ink"],
            "capture": str(png), "bridges": bridges, "teardown": finished}


def run(chrome: str, core_root: Path, capture_dir: Path, negative_control: bool) -> dict[str, Any]:
    files = stage(core_root, negative_control)
    with tempfile.TemporaryDirectory(prefix="vellum-editor-chrome-") as profile:
        os.chmod(profile, 0o700)
        process = subprocess.Popen([
            chrome, "--headless=new", "--disable-gpu-sandbox", "--no-first-run",
            "--disable-background-networking", "--disable-crash-reporter", "--mute-audio",
            *CdpAdmission.chrome_launch_arguments(), "--window-size=800,600",
            f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cdp: FixtureCdp | None = None
        try:
            port = CdpAdmission.wait_for_desktop_port(profile)
            with CdpAdmission(("127.0.0.1", port)) as admission:
                deadline = time.monotonic() + 20
                while cdp is None:
                    candidate = FixtureCdp(admission.endpoint, timeout=30, files=files)
                    try:
                        candidate.connect()
                        cdp = candidate
                    except (CdpClientError, OSError):
                        candidate.close()
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.1)
                cdp.command("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
                cdp.navigate(ORIGIN + "index.html")
                evidence = verify(cdp, capture_dir)
        finally:
            if cdp is not None:
                cdp.close()
            running = browser_processes(process.pid, profile)
            stop_browser(process, process_group=process.pid)
            leaked = browser_processes(process.pid, profile)
        # The control proves the listing can see this browser at all.
        check(len(running) > 0, "process-listing-control", running)
        check(leaked == [], "leaked-browser-process", leaked)
        evidence["browserProcessesBeforeStop"] = len(running)
        return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--capture-dir", required=True, type=Path)
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    chrome = browser()
    if chrome is None:
        print("editor session proof skipped: VELLUM_CHROME_PATH is not set")
        return SKIP
    try:
        evidence = run(chrome, args.core_root, args.capture_dir, args.negative_control)
    except CheckFailure as failure:
        if args.negative_control and str(failure).startswith("resize-css"):
            print(f"negative control failed as required: {failure}")
            return 0
        print(f"editor session proof failed: {failure}", file=sys.stderr)
        return 1
    if args.negative_control:
        print("negative control passed with the resize dispatch removed", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
