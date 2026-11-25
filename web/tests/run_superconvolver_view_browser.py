#!/usr/bin/env python3
"""Drive the Superconvolver view in headless Chrome with real input.

At two viewports the fixture mounts the view through the editor session.
The run checks, in this order:

  bounds   every named box against the native editor's layout formulas,
           worked independently here, within BOX_TOLERANCE;
  mapping  every bound control against the plug-in's parameter ids and the
           adapter's labels, and that no unregistered id is read;
  labels   the text each box shows;
  pointer  a CDP mouse drag along each slider track and a click on Bypass,
           with the adapter's begin, set and end calls and their values;
  source   a click on the Source chip opens the page's file selection, and
           the chosen file's cleaned name reaches the chip;
  ink      the capture has ink in the chip, every track and every label box.

The view must also tear down cleanly and no browser process may outlive the
run.

--negative-control move serves a view whose slider tracks sit 4 pixels to
the right and passes only when the bounds check fails.
--negative-control swap serves a view whose Mix and Size parameter ids are
exchanged and passes only when the mapping check fails.

The browser is VELLUM_CHROME_PATH and nothing else; an installed browser is
never used. Without it the run exits 77 so ctest reports a skip.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "cli"))

from run_editor_session_browser import (  # noqa: E402
    ORIGIN, CheckFailure, FixtureCdp, browser, browser_processes, check,
)
from run_text_semantics_browser import stop_browser  # noqa: E402
from vellum_cdp import CdpAdmission  # noqa: E402
from vellum_cdp_client import CdpClientError  # noqa: E402

SKIP = 77

# DESIGN: how far a laid-out box may sit from the native formula. Well under
# the 4 pixel move the negative control makes.
BOX_TOLERANCE = 0.5

# 820 by 560 is the native screenshot size (Pulp
# examples/super-convolver/screenshot.cpp, render_to_file). 390 by 700 is
# DESIGN: a phone-shaped canvas that takes the native narrow branch.
VIEWPORTS = (("native-screenshot", 820, 560), ("narrow", 390, 700))
PAGE = {"width": 900, "height": 760}  # DESIGN: holds either viewport at (20, 20).

# The plug-in's web parameter ids (Pulp super_convolver_ui_host.hpp).
EXPECTED_MAPPING = {"mix": 1, "size": 2, "gain": 3, "bypass": 4}
# Native slider table: label, range, decimals, unit, and the fixture's step.
SLIDERS = {
    "mix": ("Mix", 0.0, 100.0, 0, "%", 0.1, 35.0),
    "size": ("Size", 0.05, 4.0, 2, "s", None, 1.5),
    "gain": ("Gain", -24.0, 24.0, 1, "dB", None, 0.0),
}
WEB_IDS = {1, 2, 3, 4}

NEGATIVE_CONTROLS = {
    "move": ("const track = {x: cell.x + hpad,", "const track = {x: cell.x + hpad + 4,", "bounds"),
    "swap": ("PARAMETERS = Object.freeze({mix: 1, size: 2,",
             "PARAMETERS = Object.freeze({mix: 2, size: 1,", "mapping"),
}


class ViewCdp(FixtureCdp):
    """The editor fixture client, plus the page's file chooser.

    Headless Chrome cancels a file chooser it cannot show. Intercepting it
    keeps the request open, as a person choosing a file would, and records
    that the page really asked for one.
    """

    EXTRA = FixtureCdp.EXTRA | {"Page.setInterceptFileChooserDialog"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.choosers: list[dict[str, Any]] = []

    def _read_frame(self, sock: Any) -> tuple[int, bytes]:
        opcode, data = FixtureCdp._read_frame(sock)
        if opcode == 0x1:
            message = json.loads(data)
            if message.get("method") == "Page.fileChooserOpened":
                self.choosers.append(message["params"])
        return opcode, data


def expected_bounds(width: float, height: float) -> dict[str, dict[str, float]]:
    """The native layout (super_convolver_ui.hpp layout(), paint_controls() and
    the Source chip in paint()), plus the view's DESIGN boxes, worked from the
    canvas size alone. The native IR and spectrum slots are laid out but not
    painted, so they appear only through the controls' position."""
    s = max(0.5, height / 560)
    m = 20 * s
    narrow = width < 680 * s
    top = 132 * s if narrow else 64 * s
    avail = height - top - m
    ir_h, spec_h = avail * 0.42, avail * 0.26
    ctl_h = avail - ir_h - spec_h - 24 * s

    def box(x: float, y: float, w: float, h: float) -> dict[str, float]:
        return {"x": x, "y": y, "width": w, "height": h}

    def text(x: float, baseline: float, w: float, size: float) -> dict[str, float]:
        return box(x, baseline - size, w, size * 1.3)

    out = {
        "controls": box(m, top + ir_h + spec_h + 24 * s, width - 2 * m, ctl_h),
        "wordmark": text(24 * s, 30 * s, 160 * s, 11 * s),
        "source": box(m, 92 * s, width - 2 * m, 36 * s) if narrow else box(24 * s, 44 * s, 196 * s, 38 * s),
    }
    chip = out["source"]
    middle = chip["y"] + chip["height"] / 2
    out["source/orb"] = box(chip["x"] + 18 * s - 7 * s, middle - 7 * s, 14 * s, 14 * s)
    out["source/name"] = text(chip["x"] + 34 * s, chip["y"] + chip["height"] * 0.46,
                              chip["width"] - 46 * s, 12 * s)
    out["source/caption"] = text(chip["x"] + 34 * s, chip["y"] + chip["height"] * 0.82,
                                 chip["width"] - 46 * s, 8.5 * s)
    controls = out["controls"]
    cw = controls["width"] / len(SLIDERS)
    for index, key in enumerate(SLIDERS):
        cell = box(controls["x"] + index * cw, controls["y"], cw, controls["height"])
        track = box(cell["x"] + 14 * s, cell["y"] + cell["height"] * 0.54, cw - 28 * s, 4 * s)
        out[key] = cell
        out[f"{key}/track"] = track
        if narrow:
            out[f"{key}/label"] = box(track["x"], track["y"] - 44 * s, track["width"], 44 * s)
            out[f"{key}/name"] = text(track["x"], track["y"] - 30 * s, track["width"], 9.5 * s)
            out[f"{key}/value"] = text(track["x"], track["y"] - 13 * s, track["width"], 13 * s)
        else:
            half = track["width"] / 2
            out[f"{key}/label"] = box(track["x"], track["y"] - 26 * s, track["width"], 26 * s)
            out[f"{key}/name"] = text(track["x"], track["y"] - 12 * s, half, 9.5 * s)
            out[f"{key}/value"] = text(track["x"] + half, track["y"] - 11 * s, half, 14 * s)
    out["bypass"] = box(width - 120 * s, 18 * s, 96 * s, 32 * s)
    chip = out["bypass"]
    out["bypass/text"] = text(chip["x"], chip["y"] + chip["height"] * 0.66, chip["width"], 12 * s)
    return out


def check_bounds(report: dict[str, Any], width: int, height: int) -> dict[str, float]:
    expected = expected_bounds(width, height)
    actual = report["bounds"]
    check(set(actual) == set(expected), "bounds",
          {"missing": sorted(set(expected) - set(actual)), "extra": sorted(set(actual) - set(expected))})
    worst = {}
    for key, want in expected.items():
        deltas = {field: abs(actual[key][field] - want[field]) for field in want}
        worst[key] = max(deltas.values())
        check(worst[key] <= BOX_TOLERANCE, "bounds",
              {"box": key, "viewport": [width, height], "actual": actual[key], "expected": want})
    return worst


def check_mapping(report: dict[str, Any]) -> None:
    mapping = {entry["id"]: entry["param"] for entry in report["mapping"]}
    check(mapping == EXPECTED_MAPPING, "mapping", report["mapping"])
    for entry in report["mapping"]:
        check(entry["name"] == entry["adapterLabel"], "mapping", entry)
    check(set(report["reads"]) == WEB_IDS, "mapping", {"reads": report["reads"]})
    check(not any(word in node.lower() for node in report["nodes"]
                  for word in ("engine", "rooms", "flow")), "mapping", report["nodes"])


def value_text(key: str, value: float) -> str:
    _, _, _, decimals, unit, _, _ = SLIDERS[key]
    return f"{value:.{decimals}f} {unit}"


def check_labels(report: dict[str, Any], source: str, narrow: bool) -> None:
    text = report["text"]
    want = {
        "wordmark": "SUPERCONVOLVER", "source/name": source, "source/caption": "SOURCE",
        "bypass/text": "Bypass",
    }
    for key, (label, *_rest) in SLIDERS.items():
        # Native draws a wide slider's name in tracked capitals and a narrow
        # one centered as written.
        want[f"{key}/name"] = label if narrow else label.upper()
        want[f"{key}/value"] = value_text(key, report["values"][str(EXPECTED_MAPPING[key])])
    check({key: text.get(key) for key in want} == want, "labels", {"drawn": text, "expected": want})


def slider_value(key: str, track: dict[str, float], x: float) -> float:
    _, low, high, _, _, step, _ = SLIDERS[key]
    fraction = min(1.0, max(0.0, (x - track["x"]) / track["width"]))
    value = low + fraction * (high - low)
    if step:
        # Math.round in the session rounds a half up.
        value = low + math.floor((value - low) / step + 0.5) * step
        value = min(high, max(low, value))
    return value


def check_pointer(cdp: FixtureCdp, report: dict[str, Any]) -> list[dict[str, Any]]:
    left, top = report["canvas"]["left"], report["canvas"]["top"]
    bounds = report["bounds"]
    start = len(report["calls"])
    expected: list[dict[str, Any]] = []
    for key in SLIDERS:
        track = bounds[f"{key}/track"]
        param = EXPECTED_MAPPING[key]
        # Whole page pixels, so the expected value is exact.
        y = round(top + track["y"] + track["height"] / 2)
        press = round(left + track["x"] + track["width"] * 0.25)
        drag = round(left + track["x"] + track["width"] * 0.75)
        cdp.mouse("mousePressed", press, y)
        cdp.mouse("mouseMoved", (press + drag) // 2, y)
        cdp.mouse("mouseMoved", drag, y)
        cdp.mouse("mouseReleased", drag, y)
        middle = slider_value(key, track, (press + drag) // 2 - left)
        expected += [{"call": "begin", "param": param},
                     {"call": "set", "param": param, "value": slider_value(key, track, press - left)},
                     {"call": "set", "param": param, "value": middle},
                     {"call": "set", "param": param, "value": slider_value(key, track, drag - left)},
                     {"call": "end", "param": param}]
    chip = bounds["bypass"]
    cx = round(left + chip["x"] + chip["width"] / 2)
    cy = round(top + chip["y"] + chip["height"] / 2)
    for target in (1, 0):
        cdp.mouse("mousePressed", cx, cy)
        cdp.mouse("mouseReleased", cx, cy)
        expected += [{"call": "begin", "param": 4}, {"call": "set", "param": 4, "value": target},
                     {"call": "end", "param": 4}]
    cdp.mouse("mousePressed", cx, cy)
    cdp.mouse("mouseReleased", cx, cy)
    expected += [{"call": "begin", "param": 4}, {"call": "set", "param": 4, "value": 1},
                 {"call": "end", "param": 4}]
    after = cdp.fixture("settle()")
    calls = after["calls"][start:]
    ok = len(calls) == len(expected) and all(
        got["call"] == want["call"] and got["param"] == want["param"]
        and abs(got.get("value", 0) - want.get("value", 0)) < 1e-9
        for got, want in zip(calls, expected))
    check(ok, "pointer", {"calls": calls, "expected": expected})
    final = {str(EXPECTED_MAPPING[key]): expected[5 * index + 3]["value"]
             for index, key in enumerate(SLIDERS)}
    final["4"] = 1
    check(after["values"] == final, "pointer", {"values": after["values"], "expected": final})
    return calls


def verify_viewport(cdp: ViewCdp, name: str, width: int, height: int,
                    capture_dir: Path) -> dict[str, Any]:
    report = cdp.fixture(f"mount({width}, {height})")
    check(report["size"]["cssWidth"] == width and report["size"]["cssHeight"] == height,
          "viewport", report["size"])
    narrow = width < 680 * max(0.5, height / 560)
    worst = check_bounds(report, width, height)
    check_mapping(report)
    check_labels(report, "Synthetic room", narrow)
    calls = check_pointer(cdp, report)
    pointed = cdp.fixture("settle()")
    check_labels(pointed, "Synthetic room", narrow)

    load = report["bounds"]["source"]
    cdp.mouse("mousePressed", round(report["canvas"]["left"] + load["x"] + load["width"] / 2),
              round(report["canvas"]["top"] + load["y"] + load["height"] / 2))
    cdp.mouse("mouseReleased", round(report["canvas"]["left"] + load["x"] + load["width"] / 2),
              round(report["canvas"]["top"] + load["y"] + load["height"] / 2))
    requested = cdp.fixture("settle()")
    choosers = len(cdp.choosers)
    cdp.choosers.clear()
    check(requested["fileRequests"] == [{"accept": ".wav,.aif,.aiff,.flac", "settled": False}]
          and choosers == 1 and len(requested["calls"]) == len(pointed["calls"]), "source",
          {"requests": requested["fileRequests"], "choosersOpened": choosers})
    chosen = cdp.fixture("chooseFile('chapel_st-vitus_48k_stereo.wav')")
    check(chosen is not None and chosen["fileRequests"][0]["settled"]
          and chosen["text"].get("source/name") == "Chapel St Vitus", "source",
          None if chosen is None else {"requests": chosen["fileRequests"], "text": chosen["text"]})

    captured = cdp.fixture("capture()")
    header, encoded = captured["png"].split(",", 1)
    check(header == "data:image/png;base64", "capture-format", header)
    capture_dir.mkdir(parents=True, exist_ok=True)
    png = capture_dir / f"superconvolver-{name}-{width}x{height}.png"
    png.write_bytes(base64.b64decode(encoded))
    inked = ["source", "source/orb", "source/name", "source/caption", "bypass", "wordmark"]
    for key in SLIDERS:
        inked += [f"{key}/track", f"{key}/name", f"{key}/value"]
    for key in inked:
        ink = captured["ink"][key]
        # The plan's 1.0% per-region ink floor.
        check(ink["pixels"] >= 0.01 * ink["area"], "ink", {"box": key, **ink})
    return {"viewport": [width, height], "capture": str(png), "worstBoxDelta": max(worst.values()),
            "boxes": len(worst), "mapping": {entry["id"]: entry["param"] for entry in report["mapping"]},
            "pointerCalls": len(calls), "values": chosen["values"],
            "text": chosen["text"]}


def verify(cdp: ViewCdp, capture_dir: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        try:
            boot = cdp.fixture("booted || __editorFixture.errors")
        except CheckFailure as failure:
            if "is not defined" not in str(failure) or time.monotonic() > deadline:
                raise
            boot = []
        if boot is True:
            break
        check(boot == [] and time.monotonic() < deadline, "fixture-boot", boot)
        time.sleep(0.1)
    cdp.command("Page.setInterceptFileChooserDialog", {"enabled": True})
    cdp.command("Emulation.setDeviceMetricsOverride", {
        "width": PAGE["width"], "height": PAGE["height"], "deviceScaleFactor": 1, "mobile": False})
    evidence = {name: verify_viewport(cdp, name, width, height, capture_dir)
                for name, width, height in VIEWPORTS}
    finished = cdp.fixture("teardown()")
    check(finished == {"canvasRemoved": True, "pendingFrame": False,
                       "paramsHandlerRestored": True, "callsAfterTeardown": 0}, "teardown", finished)
    errors = cdp.fixture("errors")
    check(errors == [], "page-errors", errors)
    return evidence


def stage(core_root: Path, control: str | None) -> dict[str, bytes]:
    files = {name: (core_root / name).read_bytes()
             for name in ("vellum_web_core.js", "vellum_web_core.wasm")}
    fixture = ROOT / "web/tests/fixtures/superconvolver-view"
    for name in ("index.html", "fixture.js"):
        files[name] = (fixture / name).read_bytes()
    for path in sorted((ROOT / "web/session").glob("*.js")):
        files[f"session/{path.name}"] = path.read_bytes()
    # The session draws vector chrome through the shell's vector module.
    files["shell/vector-canvas.js"] = (ROOT / "web/shell/vector-canvas.js").read_bytes()
    files["views/superconvolver.js"] = (ROOT / "web/views/superconvolver.js").read_bytes()
    if control:
        old, new, _ = NEGATIVE_CONTROLS[control]
        text = files["views/superconvolver.js"].decode("utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"negative control {control} cannot find its single target")
        files["views/superconvolver.js"] = text.replace(old, new).encode("utf-8")
    return files


def run(chrome: str, core_root: Path, capture_dir: Path, control: str | None) -> dict[str, Any]:
    files = stage(core_root, control)
    with tempfile.TemporaryDirectory(prefix="vellum-superconvolver-chrome-") as profile:
        os.chmod(profile, 0o700)
        mode = [] if Path(chrome).name == "chrome-headless-shell" else ["--headless=new"]
        process = subprocess.Popen([
            chrome, *mode, "--disable-gpu-sandbox", "--no-first-run",
            "--disable-background-networking", "--disable-crash-reporter", "--mute-audio",
            *CdpAdmission.chrome_launch_arguments(),
            f"--window-size={PAGE['width']},{PAGE['height']}",
            f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cdp: ViewCdp | None = None
        try:
            port = CdpAdmission.wait_for_desktop_port(profile)
            with CdpAdmission(("127.0.0.1", port)) as admission:
                deadline = time.monotonic() + 20
                while cdp is None:
                    candidate = ViewCdp(admission.endpoint, timeout=30, files=files)
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--capture-dir", required=True, type=Path)
    parser.add_argument("--negative-control", choices=sorted(NEGATIVE_CONTROLS))
    args = parser.parse_args()
    chrome = browser()
    if chrome is None:
        print("superconvolver view proof skipped: VELLUM_CHROME_PATH is not set")
        return SKIP
    control = args.negative_control
    try:
        evidence = run(chrome, args.core_root, args.capture_dir, control)
    except CheckFailure as failure:
        if control and str(failure).startswith(NEGATIVE_CONTROLS[control][2] + ":"):
            print(f"negative control {control} failed as required: {failure}")
            return 0
        print(f"superconvolver view proof failed: {failure}", file=sys.stderr)
        return 1
    if control:
        print(f"negative control {control} passed with the view altered", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
