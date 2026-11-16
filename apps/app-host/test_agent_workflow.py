#!/usr/bin/env python3
"""End to end agent workflow on a native app and its HTML twin.

An agent runs the app under the manual clock, captures at a fixed time, checks
node boxes against Chrome's layout of the twin, follows a flagged node to its
source line through the tree dump, fixes it there, and re-runs. The three
controls each fail for their own reason: the defect left in, the source
location removed, and the wall clock.

The HTML twin shares the app's absolute geometry. Chrome's boxes come from a
DOMSnapshot of the twin (the layout boxes getBoundingClientRect reports).
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
from vellum_cdp import CdpAdmission  # noqa: E402
from vellum_cdp_client import CdpClient  # noqa: E402
from vellum_web_backend import chrome_path  # noqa: E402
sys.path.pop(0)

TOLERANCE = 0.5
ADVANCE_MS = 200
EPOCH_MS = 1700000000000
# Timer ticks land every 16 ms; the last one at or before 200 ms is the 12th.
LAST_TICK_MS = 192
NODES = {  # data-vellum-id -> (x, y, width, height) in the correct design
    "header": (20, 20, 360, 40),
    "knob": (240, 80, 60, 60),
}
METER = (20, 80, 100, 50)  # width is animated: 100 + (Date.now() % 50)

APP = """\
const sourceFile = "app/App.js";
let width = 100;
const place = (id, x, y, w, h, color, line) => ({
  type: "view", id, anchor: "app:" + id,SOURCE
  style: { x, y, width: w, height: h, backgroundColor: color }, children: [] });
const tree = () => ({
  type: "stack", id: "root", style: { width: 400, height: 300, backgroundColor: "#0F172A" },
  children: [
    place("header", 20, 20, 360, 40, "#1E293B", LN),
    place("meter", 20, 80, width, 50, "#14B8A6", LN),
    place("knob", KNOB_X, 80, 60, 60, "#F59E0B", LN),
    { type: "view", id: "ramp", style: { x: 20, y: 160, width: 200, height: 20,
      backgroundLinearGradient: { angle: 90, stops: [
        { position: 0.01, color: "#F8FAFC" }, { position: 1, color: "#EF4444" }] } },
      children: [] },
  ] });
let ticks = 0;
let dirty = true;
const tick = () => { width = 100 + (Date.now() % 50); dirty = true;
  if (++ticks < 20) setTimeout(tick, 16); };
setTimeout(tick, 16);
const render = () => { dirty = false; return JSON.stringify({
  protocol: "vellum.authoring-host.v1", tree: tree() }); };
globalThis.__vellum = { protocol: "vellum.authoring-host.v1",
  hostProtocol: "vellum.authoring-host.v2", renderJSON: render, dispatchJSON: render,
  isDirty() { return dirty; },
  pumpJSON() { dirty = false; return JSON.stringify({
    protocol: "vellum.authoring-host.v2", kind: "render-result", revision: ticks,
    tree: tree() }); } };
"""
SOURCE_FIELD = ' sourceLocation: { file: sourceFile, line, column: 3 },'


def app_source(knob_x: int, with_source: bool = True) -> str:
    text = APP.replace("KNOB_X", str(knob_x))
    text = text.replace("SOURCE", SOURCE_FIELD if with_source else "")
    return "\n".join(line.replace("LN", str(number))
                     for number, line in enumerate(text.split("\n"), start=1))


def expected_meter_width() -> int:
    return 100 + (EPOCH_MS + LAST_TICK_MS) % 50


def twin_html() -> str:
    boxes = dict(NODES)
    boxes["meter"] = (20, 80, expected_meter_width(), 50)
    parts = "".join(
        f'<div data-vellum-id="{name}" style="position:absolute;left:{x}px;top:{y}px;'
        f'width:{w}px;height:{h}px"></div>' for name, (x, y, w, h) in boxes.items())
    return (f'<!doctype html><body style="margin:0"><div data-vellum-id="root" '
            f'style="position:relative;width:400px;height:300px">{parts}</div></body>')


def chrome_boxes(html: str) -> dict[str, tuple[float, float, float, float]]:
    with tempfile.TemporaryDirectory(prefix="vellum-twin-") as root:
        (Path(root) / "index.html").write_text(html, encoding="utf-8")
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=root, **k)

            def log_message(self, *_a):
                pass

        handler = Quiet
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="vellum-chrome-") as profile:
            os.chmod(profile, 0o700)
            process = subprocess.Popen([
                chrome_path(), "--headless=new", "--no-first-run",
                "--disable-background-networking", *CdpAdmission.chrome_launch_arguments(),
                "--window-size=800,600", f"--user-data-dir={profile}",
                f"http://127.0.0.1:{server.server_port}/index.html"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            try:
                port = CdpAdmission.wait_for_desktop_port(profile)
                with CdpAdmission(("127.0.0.1", port)) as admission:
                    client = CdpClient(admission.endpoint, timeout=10.0)
                    client.discover()
                    client.connect()
                    try:
                        client.wait_for_dom()
                        client.settle_idle()
                        snapshot = client.capture_dom_snapshot(["display"])
                    finally:
                        client.close()
            finally:
                try:
                    os.killpg(process.pid, 15)
                except OSError:
                    pass
                process.wait(timeout=10)
                server.shutdown()
    return boxes_from_snapshot(snapshot)


def boxes_from_snapshot(snapshot: dict) -> dict[str, tuple[float, float, float, float]]:
    strings = snapshot["strings"]
    document = snapshot["documents"][0]
    nodes, layout = document["nodes"], document["layout"]
    wanted = strings.index("data-vellum-id") if "data-vellum-id" in strings else -1
    ids: dict[int, str] = {}
    for index, attributes in enumerate(nodes["attributes"]):
        for key, value in zip(attributes[::2], attributes[1::2]):
            if key == wanted:
                ids[index] = strings[value]
    boxes = {}
    for node_index, bounds in zip(layout["nodeIndex"], layout["bounds"]):
        if node_index in ids:
            boxes[ids[node_index]] = tuple(bounds)
    return boxes


def run_host(host: str, bundle: Path, out: Path, *, manual: bool) -> tuple[bytes, dict]:
    out.mkdir(parents=True, exist_ok=True)
    command = [host, "--bundle", str(bundle), "--capture-dir", str(out),
               "--dump-tree", str(out / "dump.json")]
    if manual:
        command += ["--manual-clock", "--advance", str(ADVANCE_MS)]
    command += ["--capture-step", "frame"]
    done = subprocess.run(command, text=True, capture_output=True, check=False)
    if done.returncode:
        sys.exit(done.stdout + done.stderr)
    return ((out / "frame.png").read_bytes(),
            json.loads((out / "dump.json").read_text(encoding="utf-8")))


def box_check(dump: dict, chrome: dict) -> list[dict]:
    """Nodes whose native box differs from Chrome's by more than the tolerance."""
    native = {n["id"]: n["box"] for n in dump["nodes"]}
    flagged = []
    for node_id, expected in chrome.items():
        if node_id == "root" or node_id not in native:
            continue
        box = native[node_id]
        actual = (box["x"], box["y"], box["width"], box["height"])
        delta = [round(a - e, 3) for a, e in zip(actual, expected)]
        if any(abs(d) > TOLERANCE for d in delta):
            flagged.append({"id": node_id, "delta": delta})
    return flagged


def locate_source(dump: dict, node_id: str) -> tuple[str, int]:
    """The file and line the dump gives for a node; fails when it gives none."""
    node = next(n for n in dump["nodes"] if n["id"] == node_id)
    if not node["source"]:
        raise LookupError(f"dump has no source for {node_id}")
    return node["source"]["file"], node["source"]["line"]


def main() -> int:
    host = sys.argv[1]
    chrome = chrome_boxes(twin_html())
    if chrome.get("meter", (0, 0, 0, 0))[2] != expected_meter_width():
        sys.exit(f"chrome twin did not report the expected meter width: {chrome}")
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)

        def bundle(name: str, source: str) -> Path:
            path = work / name
            path.write_text(source, encoding="utf-8")
            return path

        # 1 to 5: run the defective app and let the agent workflow find it.
        planted = bundle("planted.js", app_source(250))
        _, dump = run_host(host, planted, work / "p1", manual=True)
        flagged = box_check(dump, chrome)
        assert [f["id"] for f in flagged] == ["knob"] and flagged[0]["delta"][0] == 10, flagged
        meter = next(n for n in dump["nodes"] if n["id"] == "meter")["box"]
        assert meter["width"] == expected_meter_width(), meter
        file, line = locate_source(dump, "knob")
        assert file == "app/App.js", file
        # 6: apply the fix at the line the dump names, and nowhere else.
        lines = app_source(250).split("\n")
        assert 'place("knob"' in lines[line - 1] and "250" in lines[line - 1]
        lines[line - 1] = lines[line - 1].replace("250", "240")
        fixed = bundle("fixed.js", "\n".join(lines))
        # 7: re-run twice; the box check reads 0 and the captures are identical.
        png1, dump1 = run_host(host, fixed, work / "f1", manual=True)
        png2, _ = run_host(host, fixed, work / "f2", manual=True)
        assert box_check(dump1, chrome) == [], box_check(dump1, chrome)
        assert png1 == png2, "fixed captures differ between runs"

        # Control (a): with the defect left in, step 7 fails.
        _, left = run_host(host, planted, work / "c1", manual=True)
        assert box_check(left, chrome) != [], "control (a): defect was not flagged"

        # Control (b): without a source location the flagged node cannot be
        # mapped, and the failure is the unmapped source, not the box check.
        unmapped = bundle("unmapped.js", app_source(250, with_source=False))
        _, bare = run_host(host, unmapped, work / "c2", manual=True)
        assert [f["id"] for f in box_check(bare, chrome)] == ["knob"]
        try:
            locate_source(bare, "knob")
        except LookupError as error:
            assert "no source" in str(error)
        else:
            raise AssertionError("control (b): knob mapped to a source without one")

        # Control (c): on the wall clock the same capture is not repeatable.
        wall = {run_host(host, fixed, work / f"w{i}", manual=False)[0] for i in range(4)
                if time.sleep(0.013) is None}
        assert len(wall) > 1, "control (c): wall-clock captures were identical"
    return 0


if __name__ == "__main__":
    sys.exit(main())
