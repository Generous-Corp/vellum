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
  ink      every label, chip, track, tab and the info glyph has ink against
           its own surface (the shared gate, perceptual_gate.js).

Before those, the calibration gate mounts the editor at the native
screenshot's size and device scale with the native capture's field state,
parameter values and clock, and runs the shared gate: SSIM against the
native reference, ink in every region against its surface, and Bypass state
ink. It also checks the frozen clock is deterministic and that a mode tab
switches the field.

The view must also tear down cleanly and no browser process may outlive the
run.

--negative-control move serves a view whose slider tracks sit 4 pixels to
the right and passes only when the bounds check fails.
--negative-control swap serves a view whose Mix and Size parameter ids are
exchanged and passes only when the mapping check fails.
--negative-control missing-chip, missing-label, solid-background and
placeholder serve a view with no Source chip, no source name, nothing but
the ground colour, or a one pixel placeholder for every text, and pass only
when the gate fails.

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
# Native tracked_text's advance of an uppercase glyph, in ems.
ADVANCES = {**dict.fromkeys("IJ", 0.30), "L": 0.52, **dict.fromkeys("SEFPTZ", 0.60),
            **dict.fromkeys("RBKVAXY", 0.66), **dict.fromkeys("CDHNUOQG", 0.73),
            **dict.fromkeys("MW", 0.88), " ": 0.40}

VIEW = "views/superconvolver.js"
SESSION = "session/editor_session.js"
# The regions the gate inks: text boxes, measured without their text, and
# chrome, measured without the shape. Sliders add their name, value and track.
TEXT_REGIONS = ["wordmark", "info/glyph", "source/name", "source/caption", "bypass/text",
                "tab/0/text", "tab/1/text", "tab/2/text"]
CHROME_REGIONS = ["source", "source/orb", "tabs", "tab/0", "info", "bypass"]
SLIDER_TEXT = [f"{key}/{part}" for key in ("mix", "size", "gain") for part in ("name", "value")]
SLIDER_TRACKS = [f"{key}/track" for key in ("mix", "size", "gain")]
ALL_INK = {f"ink:{name}" for name in TEXT_REGIONS + SLIDER_TEXT + CHROME_REGIONS + SLIDER_TRACKS}
ALL_INK.add("ink:bypass/state")
# Each control: the file it alters, the single text it replaces and with what,
# the stage that must fail and, for the gate, exactly the failures it must name.
# The info glyph's 3s wide box holds 4 device pixels of a one pixel
# placeholder, 2.3% of its area, so the placeholder control cannot fail it.
NEGATIVE_CONTROLS = {
    "move": (VIEW, "const track = {x: cell.x + hpad,", "const track = {x: cell.x + hpad + 4,", "bounds",
             None),
    "swap": (VIEW, "PARAMETERS = Object.freeze({mix: 1, size: 2,",
             "PARAMETERS = Object.freeze({mix: 2, size: 1,", "mapping", None),
    "missing-chip": (VIEW, "vector: box => chip(box, radius('source'), CHROME_FILL, CHROME_STROKE)},",
                     "vector: box => ({viewBox: [0, 0, box.width, box.height], ops: []})},", "gate",
                     {"ink:source"}),
    "missing-label": (VIEW, """        {id: 'source/name', kind: 'label', color: 'value',
            format: state => sourceLabel(state.impulseResponseName)},
""", "", "gate", {"ink:source/name"}),
    "missing-field": (SESSION, "        setBackdrop();\n", "", "gate", {"ssim"}),
    "solid-background": (SESSION, "        setBackdrop();\n        if (omit.nodes) {",
                         "        if (false) {", "gate", ALL_INK | {"ssim"}),
    "placeholder": (SESSION, "        if (omit.ids.has(key)) return;\n",
                    "        if (omit.ids.has(key)) return;\n        drawnText[key] = text;\n"
                    "        rect(key, box.x, box.y, 1, 1, 0, ...rgba(color));\n        return;\n", "gate",
                    {f"ink:{name}" for name in TEXT_REGIONS + SLIDER_TEXT if name != "info/glyph"}),
}

# The native capture's state (Pulp examples/super-convolver/screenshot.cpp at
# 3a6f370295), measured from that build: one paint, so the clock is 1/60 s;
# Rooms 16 and Flow 0, so density 16 and no motion; the energy its spectrum
# smoothing reached; Tracers. The plug-in values are the ones screenshot.cpp
# sets. The calibration overrides the web field's inputs with these, which
# the web build cannot otherwise reach (it declares neither Rooms nor Flow).
CALIBRATION = {
    "viewport": (820, 560), "scale": 2, "time": 1 / 60,
    "field": {"density": 16, "flow": 0, "energy": 0.304196358},
    "values": {"1": 45, "2": 1.5, "3": 0, "4": 0},
}
REFERENCE = ROOT / "web/tests/fixtures/superconvolver-native/native-820x560-luma.png"


def gate_regions(bounds: dict[str, Any]) -> list[dict[str, Any]]:
    """Every region the gate inks, with the draw ids its surface leaves out."""
    regions = []
    for key in TEXT_REGIONS + SLIDER_TEXT + CHROME_REGIONS:
        regions.append({"name": key, "box": bounds[key], "omit": [key]})
    for key in SLIDERS:
        regions.append({"name": f"{key}/track", "box": bounds[f"{key}/track"],
                        "omit": [f"{key}/{part}" for part in ("track", "fill", "thumb", "thumb-disc")]})
    return regions


def check_gate(result: dict[str, Any], enforce_ssim: bool = True) -> None:
    failures = [name for name in result["failures"] if enforce_ssim or name != "ssim"]
    check(not failures, "gate", {
        "failures": result["failures"], "ssim": result["similarity"],
        "ink": {name: ink for name, ink in result["ink"].items() if not ink["pass"]}})


def calibrate(cdp: "ViewCdp", width: int, height: int, references: dict[str, str],
              capture_dir: Path, enforce_ssim: bool = True) -> dict[str, Any]:
    """Mounts the editor in the native capture's state at `width` by `height`
    and device scale 2, runs the gate against the staged `references`
    ({"reference", "fieldReference"}, either optional), proves the frozen
    clock and the mode tabs, and saves the capture."""
    cdp.command("Emulation.setDeviceMetricsOverride", {
        "width": PAGE["width"], "height": PAGE["height"],
        "deviceScaleFactor": CALIBRATION["scale"], "mobile": False})
    options = json.dumps({"field": CALIBRATION["field"], "values": CALIBRATION["values"],
                          "time": CALIBRATION["time"]})
    cdp.fixture(f"mount({width}, {height}, {options})")
    report = cdp.fixture("remeasure()")
    check(report["size"]["scale"] == CALIBRATION["scale"]
          and report["size"]["backingWidth"] == CALIBRATION["scale"] * width, "device-scale",
          report["size"])
    request = {"regions": gate_regions(report["bounds"]), "bypassState": "bypass", **references}
    result = cdp.fixture(f"gate({json.dumps(request)})")
    check_gate(result, enforce_ssim)
    captured = cdp.fixture("capture()")
    capture_dir.mkdir(parents=True, exist_ok=True)
    png = capture_dir / f"superconvolver-calibration-{width}x{height}@2x.png"
    png.write_bytes(base64.b64decode(captured["png"].split(",", 1)[1]))

    time_now = CALIBRATION["time"]
    first = cdp.fixture(f"digestAt({time_now})")
    again = cdp.fixture(f"digestAt({time_now})")
    later = cdp.fixture(f"digestAt({time_now + 1 / 60})")
    check(first == again == result["digest"] and later != first, "field-clock",
          {"first": first, "again": again, "later": later, "gate": result["digest"]})
    # A press on each tab switches the field; the clock stays frozen.
    left, top = report["canvas"]["left"], report["canvas"]["top"]
    modes = []
    for index in (1, 2, 0):
        tab = report["bounds"][f"tab/{index}"]
        x, y = round(left + tab["x"] + tab["width"] / 2), round(top + tab["y"] + tab["height"] / 2)
        cdp.mouse("mousePressed", x, y)
        cdp.mouse("mouseReleased", x, y)
        modes.append(cdp.fixture("frozenFrame()"))
    check([mode["mode"] for mode in modes] == [1, 2, 0]
          and len({mode["digest"] for mode in modes}) == 3 and modes[2]["digest"] == later,
          "field-modes", modes)
    return {"ssim": result["similarity"], "fieldSsim": result["fieldSimilarity"],
            "ssimWithoutField": result["withoutField"],
            "capture": str(png), "regions": len(result["ink"]), "time": result["time"],
            "commandCount": result["commandCount"], "modeCommandCounts": [m["commandCount"] for m in modes],
            "lowestInk": min((ink["fraction"], name) for name, ink in result["ink"].items())}


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
    out["bypass/text"] = text(chip["x"], chip["y"] + chip["height"] * 0.62, chip["width"], 12 * s)
    # Info glyph: native tracked_text's end of the wordmark, then 18s.
    ix = 24 * s + sum(11 * s * (ADVANCES.get(ch, 0.64) + 0.24) for ch in "SUPERCONVOLVER") + 18 * s
    iy, ir = 25.5 * s, 9.5 * s
    out["info"] = box(ix - ir - 4 * s, iy - ir - 4 * s, 2 * (ir + 4 * s), 2 * (ir + 4 * s))
    out["info/glyph"] = text(ix - 11 * s * 0.14, iy + 4 * s, 3 * s, 11 * s)
    # Mode tabs.
    tw = min(92 * s, (width - 2 * m) / 3) if narrow else 92 * s
    ty = 50 * s if narrow else 16 * s
    x0 = width / 2 - 1.5 * tw
    out["tabs"] = box(x0 - 3 * s, ty - 3 * s, 3 * tw + 6 * s, 26 * s + 6 * s)
    for index in range(3):
        out[f"tab/{index}"] = box(x0 + index * tw, ty, tw, 26 * s)
        out[f"tab/{index}/text"] = text(x0 + index * tw, ty + 26 * s * 0.64, tw, 10.5 * s)
    fade_top = controls["y"] - (44 if narrow else 26) * s
    out["fade"] = box(controls["x"], fade_top, controls["width"],
                      controls["y"] + controls["height"] - fade_top)
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
    inked = cdp.fixture(f"gate({json.dumps({'regions': gate_regions(chosen['bounds'])})})")
    check(inked["pass"], "ink", {name: ink for name, ink in inked["ink"].items() if not ink["pass"]})
    return {"viewport": [width, height], "capture": str(png), "worstBoxDelta": max(worst.values()),
            "boxes": len(worst), "mapping": {entry["id"]: entry["param"] for entry in report["mapping"]},
            "pointerCalls": len(calls), "values": chosen["values"],
            "text": chosen["text"]}


def verify(cdp: ViewCdp, capture_dir: Path, extra: dict[str, Path]) -> dict[str, Any]:
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
    width, height = CALIBRATION["viewport"]
    references = {"reference": "reference.png"}
    if "field" in extra:
        references["fieldReference"] = "field-reference.png"
    calibration = {"native-screenshot": calibrate(cdp, width, height, references, capture_dir)}
    # DESIGN: the narrow viewport has no committed native reference; one given
    # with --narrow-reference is compared and reported, not held to the floor.
    calibration["narrow"] = calibrate(cdp, 390, 700, {"reference": "narrow-reference.png"}
                                      if "narrow" in extra else {}, capture_dir, enforce_ssim=False)
    cdp.command("Emulation.setDeviceMetricsOverride", {
        "width": PAGE["width"], "height": PAGE["height"], "deviceScaleFactor": 1, "mobile": False})
    evidence = {name: verify_viewport(cdp, name, width, height, capture_dir)
                for name, width, height in VIEWPORTS}
    evidence["calibration"] = calibration
    finished = cdp.fixture("teardown()")
    check(finished == {"canvasRemoved": True, "pendingFrame": False,
                       "paramsHandlerRestored": True, "callsAfterTeardown": 0}, "teardown", finished)
    errors = cdp.fixture("errors")
    check(errors == [], "page-errors", errors)
    return evidence


def stage(core_root: Path, control: str | None, extra: dict[str, Path]) -> dict[str, bytes]:
    files = {name: (core_root / name).read_bytes()
             for name in ("vellum_web_core.js", "vellum_web_core.wasm")}
    fixture = ROOT / "web/tests/fixtures/superconvolver-view"
    for name in ("index.html", "fixture.js"):
        files[name] = (fixture / name).read_bytes()
    for path in sorted((ROOT / "web/session").glob("*.js")):
        files[f"session/{path.name}"] = path.read_bytes()
    # The session draws vector chrome through the shell's vector module.
    files["shell/vector-canvas.js"] = (ROOT / "web/shell/vector-canvas.js").read_bytes()
    for path in sorted((ROOT / "web/views").glob("*.js")):
        files[f"views/{path.name}"] = path.read_bytes()
    files["perceptual_gate.js"] = (ROOT / "web/tests/perceptual_gate.js").read_bytes()
    files["reference.png"] = REFERENCE.read_bytes()
    for name, path in extra.items():
        files[f"{name}-reference.png"] = path.read_bytes()
    if control:
        target, old, new, _, _ = NEGATIVE_CONTROLS[control]
        text = files[target].decode("utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"negative control {control} cannot find its single target")
        files[target] = text.replace(old, new).encode("utf-8")
    return files


def run(chrome: str, core_root: Path, capture_dir: Path, control: str | None,
        extra: dict[str, Path]) -> dict[str, Any]:
    files = stage(core_root, control, extra)
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
                evidence = verify(cdp, capture_dir, extra)
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
    parser.add_argument("--field-reference", type=Path,
                        help="a greyscale native field-only reference, to report the field's own SSIM")
    parser.add_argument("--narrow-reference", type=Path,
                        help="a greyscale native reference at 390 by 700, to report its SSIM")
    args = parser.parse_args()
    chrome = browser()
    if chrome is None:
        print("superconvolver view proof skipped: VELLUM_CHROME_PATH is not set")
        return SKIP
    control = args.negative_control
    try:
        extra = {name: path for name, path in (("field", args.field_reference),
                                               ("narrow", args.narrow_reference)) if path}
        evidence = run(chrome, args.core_root, args.capture_dir, control, extra)
    except CheckFailure as failure:
        _, _, _, stage, required = NEGATIVE_CONTROLS[control] if control else (None,) * 5
        named = None
        if control and required is not None and str(failure).startswith(stage + ":"):
            named = set(json.loads(str(failure)[len(stage) + 1:])["failures"])
        if control and str(failure).startswith(stage + ":") and (required is None or named == required):
            print(f"negative control {control} failed as required: {failure}")
            return 0
        if named is not None:
            print(f"negative control {control} failed at {stage} naming {sorted(named)}, "
                  f"not exactly {sorted(required)}", file=sys.stderr)
            return 1
        print(f"superconvolver view proof failed: {failure}", file=sys.stderr)
        return 1
    if control:
        print(f"negative control {control} passed with the view altered", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
