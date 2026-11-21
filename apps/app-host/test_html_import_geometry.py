#!/usr/bin/env python3
"""HTML import keeps Chrome's layout: every node's native box matches Chrome's.

Runs the HTML import end to end on ``fixtures/html-import``: Chrome capture,
``vellum-backend.js import`` into a minimal project, ``build-project.mjs``,
then the app host's ``--dump-tree``. Chrome's boxes are read from the raw
DOMSnapshot the import retains as capture evidence, not from the lowered
design, so the check cannot agree with itself.

A node is matched to its DOM node through its anchor: ``semantic:<id>`` names
the element carrying ``data-vellum-id``, and ``html:dom-<n>`` names DOM node n.
Every DOM node Chrome gave a box must be rendered at that box, and every node
Chrome gave no box (``display: none``, collapsed whitespace) must not be
rendered.

Tolerance is 1 px. The import carries Chrome's fractional CSS px through
unchanged and native layout adds a parent-relative offset back onto its
parent's absolute origin in single precision, so a correct import differs from
Chrome by float rounding, well under 0.01 px at these coordinates. A real
layout defect moves a box by at least a pixel, so 1 px separates the two
without hiding a misplacement anyone would see.

Controls:
- a copy of the fixture with one element moved in its CSS, imported and
  rendered natively, then checked against Chrome's layout of the original, is
  flagged at exactly that element and by exactly the distance it moved;
- the materialized design with its lowered boxes removed (the import as it was
  before it carried geometry) fails, with every element at 0,0,800,600.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
sys.path.insert(0, str(REPO / "cli/tests"))
from live_browser import live_browser_skip_reason  # noqa: E402
from vellum_cli import default_html_interaction_plan  # noqa: E402
from vellum_html_source import stage_html_source  # noqa: E402
from vellum_png import RgbaImage, encode_png, read_png  # noqa: E402
from vellum_web_backend import BackendFailure, chrome_path  # noqa: E402
from vellum_web_backend import run_chrome_interaction_capture  # noqa: E402
sys.path.pop(0)
sys.path.pop(0)

TOLERANCE = 1.0
FIXTURE = REPO / "fixtures" / "html-import"
BACKEND = REPO / "packages" / "vellum-design-ir" / "bin" / "vellum-backend.js"
BUILD = REPO / "packages" / "vellum-ui" / "scripts" / "build-project.mjs"
PLAN = default_html_interaction_plan()
ENTRY = """
import { createApp, Design, mount } from '@vellum/ui';
import { importedBindings, importedDesign } from '@vellum/imported';
mount(createApp({
    id: 'html-import-geometry', stateVersion: '1', initialState: {}, actions: {},
    render: () => Design({ document: importedDesign, bindings: importedBindings, actions: {} }),
}));
"""
# The control nudges the copy paragraph 30 px right and 20 px down. Relative
# positioning moves only that element's box (and its own text); its siblings
# keep their places, so exactly one origin moves.
MOVED_ID = "html-proof-copy"
MOVE_CSS = f'\n[data-vellum-id="{MOVED_ID}"] {{ position: relative; left: 30px; top: 20px; }}\n'


def run(arguments: list[str], **options) -> str:
    done = subprocess.run(arguments, text=True, capture_output=True, check=False, **options)
    if done.returncode:
        sys.exit(f"command failed ({done.returncode}): {arguments}\n{done.stdout}{done.stderr}")
    return done.stdout


def capture_and_import(source: Path, work: Path, node: str) -> tuple[dict, Path]:
    """Capture the page in Chrome and import it; returns the envelope and the design."""
    staging = work / "staged"
    receipt = stage_html_source(source / "index.html", staging)
    envelope = run_chrome_interaction_capture(staging, PLAN, entry=str(receipt["entry"]),
                                              source_metadata={
        "producer": receipt["producer"], "fingerprint": receipt["fingerprint"],
        "preflightSchema": receipt["schema"], "dependencies": receipt["dependencies"],
        "entry": receipt["entry"],
    })
    envelope_path = work / "capture-envelope.json"
    envelope_path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    project = work / "project"
    project.mkdir()
    (project / "framework.lock").write_text(
        json.dumps({"schema": "vellum.project-lock.v1"}), encoding="utf-8")
    payload = json.loads(run([
        node, str(BACKEND), "import", "--project", str(project), "--json",
        str(staging / str(receipt["entry"])), "--source-type", "html",
        "--capture-envelope", str(envelope_path), "--as", "main"]))
    if payload.get("status") != "imported":
        sys.exit(f"import failed: {payload}")
    return envelope, project / "ui" / "generated" / "main.materialized.json"


def retained_material(node: str, design: Path) -> dict:
    script = """
import { readFileSync } from 'node:fs';
import { createApp, materializeDesign, mount } from './packages/vellum-ui/src/index.js';
const document = JSON.parse(readFileSync(process.argv[1], 'utf8'));
const app = createApp({
    id: 'html-import-material', render: () => materializeDesign(document),
});
console.log(mount(app).renderJSON());
"""
    tree = json.loads(run([node, "--input-type=module", "-e", script, str(design)], cwd=REPO))["tree"]
    pending = [tree]
    by_anchor = {}
    while pending:
        current = pending.pop()
        by_anchor[current.get("anchor")] = current
        pending.extend(current.get("children", []))
    return by_anchor


def material_values(tree: dict) -> dict:
    panel = tree["semantic:html-proof-root"]["style"]
    title = tree["semantic:html-proof-title"]["children"][0]["style"]
    button = tree["semantic:html-proof-button"]["style"]
    document = next(item for item in tree.values() if item.get("accessibilityLabel") == "#document")
    return {
        "pageFill": document["style"].get("backgroundColor"),
        "panelFill": panel.get("backgroundColor"),
        "panelRadius": panel.get("borderRadius"),
        "panelBorderColor": panel.get("borderTopColor"),
        "panelBorderWidth": panel.get("borderTopWidth"),
        "titleColor": title.get("color"),
        "titleFont": title.get("fontFamily"),
        "titleWeight": title.get("fontWeight"),
        "buttonFill": button.get("backgroundColor"),
        "buttonGradients": button.get("backgroundGradients"),
    }


def check_material(node: str, design: Path, work: Path) -> None:
    expected = {
        "pageFill": "#f4f0e8",
        "panelFill": "#ffffff",
        "panelRadius": 12,
        "panelBorderColor": "#475569",
        "panelBorderWidth": 2,
        "titleColor": "#b45309",
        "titleFont": '"Courier New", monospace',
        "titleWeight": 700,
        "buttonFill": "#134e4a",
        "buttonGradients": [
            {"kind": "radial", "centerX": 0.5, "centerY": 0.2, "radiusX": 0.8, "radiusY": 0.6,
             "stops": [{"position": 0, "color": "#000000"}, {"position": 0.6, "color": "#00000000"}]},
            {"kind": "linear", "angle": 0, "stops": [
                {"position": 0, "color": "#14b8a680"}, {"position": 1, "color": "#0f766e"},
            ]},
        ],
    }
    actual = material_values(retained_material(node, design))
    mismatches = {key: (value, actual[key]) for key, value in expected.items() if actual[key] != value}
    assert not mismatches, mismatches

    # Strip exactly the groups the old capture omitted. Raw computed styles
    # stay in the design, so a style assertion cannot pass on evidence alone.
    document = json.loads(design.read_text(encoding="utf-8"))
    pending = [document["root"]]
    while pending:
        current = pending.pop()
        for group in ("paint", "text"):
            current["properties"].pop(group, None)
        pending.extend(current["children"])
    stripped = work / "no-material.json"
    stripped.write_text(json.dumps(document), encoding="utf-8")
    control = material_values(retained_material(node, stripped))
    assert control == dict.fromkeys(expected), control
    print(f"material: {len(expected)} values passed, all absent in the stripped control")


def build_and_dump(host: str, node: str, design: Path, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    entry = work / "main.jsx"
    entry.write_text(ENTRY, encoding="utf-8")
    bundle = work / "bundle.js"
    run([node, str(BUILD), str(entry), str(bundle), str(design)], cwd=BUILD.parents[1])
    dump = work / "dump.json"
    run([host, "--bundle", str(bundle), "--dump-tree", str(dump)])
    return json.loads(dump.read_text(encoding="utf-8"))


def chrome_layout(envelope: dict) -> tuple[dict[str, tuple], dict[str, str], set[str]]:
    """Chrome's absolute box for each DOM node it rendered, keyed by anchor;
    each such node's nearest boxed DOM ancestor; and the anchors of the DOM
    nodes Chrome did not render."""
    snapshot = envelope["root"]["properties"]["captureEvidence"]["settledSnapshot"]
    strings = snapshot["strings"]
    document = snapshot["documents"][0]
    nodes, layout = document["nodes"], document["layout"]
    anchors: dict[int, str] = {}
    for index, node_type in enumerate(nodes["nodeType"]):
        if node_type not in {1, 3}:
            continue
        anchors[index] = f"html:dom-{index}"
        if node_type == 1:
            attributes = nodes["attributes"][index]
            for key, value in zip(attributes[::2], attributes[1::2]):
                if strings[key] == "data-vellum-id":
                    anchors[index] = f"semantic:{strings[value]}"
    boxes: dict[int, tuple] = {}
    for index, rect in zip(layout["nodeIndex"], layout["bounds"]):
        if index not in anchors:
            continue
        if nodes["nodeType"][index] == 3 and not (rect[2] > 0 and rect[3] > 0):
            continue  # collapsed whitespace: a layout object with no box
        # Some pseudo-elements get a second, empty layout row from Chrome.
        if index not in boxes or (rect[2] > 0 and rect[3] > 0):
            boxes[index] = tuple(rect)
    parents: dict[str, str] = {}
    for index in boxes:
        parent = nodes["parentIndex"][index]
        while parent >= 0 and parent not in boxes:
            parent = nodes["parentIndex"][parent]
        if parent in anchors:
            parents[anchors[index]] = anchors[parent]
    by_anchor = {anchors[index]: rect for index, rect in boxes.items()}
    return by_anchor, parents, set(anchors.values()) - set(by_anchor)


def compare(dump: dict, envelope: dict) -> dict:
    """Native boxes against Chrome's.

    ``mismatches`` lists every node whose absolute box is off. ``origins``
    lists only the nodes where an error starts: a node's position delta has its
    parent's subtracted, so a moved element is reported once rather than with
    every descendant it carried along.
    """
    chrome, parents, unboxed = chrome_layout(envelope)
    native = {n["anchor"]: n["box"] for n in dump["nodes"] if n.get("anchor")}
    deltas: dict[str, list[float]] = {}
    mismatches, missing = [], []
    for anchor, expected in sorted(chrome.items()):
        box = native.get(anchor)
        if box is None:
            missing.append(anchor)
            continue
        actual = (box["x"], box["y"], box["width"], box["height"])
        deltas[anchor] = [round(a - e, 3) for a, e in zip(actual, expected)]
        if any(abs(d) > TOLERANCE for d in deltas[anchor]):
            mismatches.append({"anchor": anchor, "chrome": list(expected),
                               "native": list(actual), "delta": deltas[anchor]})
    origins = []
    for anchor, delta in sorted(deltas.items()):
        parent = deltas.get(parents.get(anchor, ""), [0, 0, 0, 0])
        relative = [round(delta[0] - parent[0], 3), round(delta[1] - parent[1], 3), *delta[2:]]
        if any(abs(d) > TOLERANCE for d in relative):
            origins.append({"anchor": anchor, "delta": relative})
    return {
        "compared": len(chrome),
        "maxDelta": max((abs(d) for delta in deltas.values() for d in delta), default=0.0),
        "mismatches": mismatches,
        "origins": origins,
        "missing": missing,
        "renderedWithoutBox": sorted(a for a in unboxed if a in native),
    }


def import_case(name: str, node: str, work: Path, *, css: str = "") -> tuple[dict, Path]:
    case = work / name
    source = case / "source"
    shutil.copytree(FIXTURE, source)
    if css:
        styles = source / "styles.css"
        styles.write_text(styles.read_text(encoding="utf-8") + css, encoding="utf-8")
    return capture_and_import(source, case, node)


def stripped_layout(design: Path) -> Path:
    """The materialized design as the import produced it before it carried
    geometry: no node has a lowered box."""
    document = json.loads(design.read_text(encoding="utf-8"))
    pending = [document["root"]]
    while pending:
        current = pending.pop()
        current.get("properties", {}).pop("layout", None)
        pending.extend(current["children"])
    target = design.parent.parent / "stripped" / design.name
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    shutil.copy(design.with_name("main.bindings.json"), target.with_name("main.bindings.json"))
    return target


def check_canvas(host: str, node: str, work: Path) -> None:
    # Start at the imported design boundary with the same asset table and
    # reference emitted by HTML capture. The pixels are synthetic so this
    # test can exercise bundling and native paint without starting Chrome.
    red = (255, 0, 0, 255)
    row = bytes(red) * 32 + bytes(32 * 4)
    png = encode_png(RgbaImage(64, 64, row * 64))
    digest = hashlib.sha256(png).hexdigest()
    uri = f"assets/{digest}.png"
    asset = {
        "id": f"data-{digest[:12]}", "contentHash": f"sha256:{digest}",
        "mimeType": "image/png", "uri": uri,
    }
    project = work / "project"
    generated = project / "ui" / "generated"
    generated.mkdir(parents=True)
    source_png = project / "assets" / "generated" / "main" / "files" / uri
    source_png.parent.mkdir(parents=True)
    source_png.write_bytes(png)

    def box(node_id, x, y, width, height, color, *, children=()):
        return {
            "id": node_id, "kind": "view", "children": list(children),
            "properties": {"layout": {"x": x, "y": y, "width": width, "height": height},
                           "paint": {"backgroundColor": color}},
        }

    canvas = {
        "id": "main/canvas", "kind": "image", "children": [],
        "properties": {"tagName": "canvas", "assetRefs": [uri],
                       "layout": {"x": 40, "y": 40, "width": 64, "height": 64},
                       "image": {"source": uri, "fit": "fill"}},
    }
    design = generated / "main.materialized.json"
    design.write_text(json.dumps({
        "schemaVersion": 6, "source": {"key": "main", "revision": "canvas"},
        "assets": [asset],
        "root": box("main/root", 0, 0, 800, 600, "#101010", children=[
            box("main/white", 200, 0, 200, 600, "#ffffff"),
            box("main/light", 400, 0, 200, 600, "#888888"),
            box("main/dark", 600, 0, 200, 600, "#444444"),
            canvas,
        ]),
    }), encoding="utf-8")
    (generated / "main.bindings.json").write_text(json.dumps({
        "schema": "vellum.generated-bindings.v1", "sourceKey": "main",
        "revision": "canvas", "bindings": [],
    }), encoding="utf-8")
    entry = work / "entry.jsx"
    entry.write_text(ENTRY, encoding="utf-8")
    bundle = work / "bundle.js"
    run([node, str(BUILD), str(entry), str(bundle), str(design)], cwd=BUILD.parents[1])

    packaged = Path(f"{bundle}.images") / f"{digest}.png"
    assert packaged.is_file(), "the native bundle omitted the captured canvas PNG"
    assert hashlib.sha256(packaged.read_bytes()).hexdigest() == digest

    def sample(path: Path, x: int, y: int) -> tuple[int, ...]:
        image = read_png(path)
        offset = (y * image.width + x) * 4
        return tuple(image.pixels[offset:offset + 4])

    assert sample(packaged, 16, 32) == red
    assert sample(packaged, 48, 32)[3] == 0
    print("canvas: known opaque and transparent pixels survived native bundle packaging")
    if host == "--canvas-package-only":
        return

    painted = work / "painted.png"
    run([host, "--bundle", str(bundle), "--capture", str(painted)])
    assert sample(painted, 56, 72) == red, "the native host did not paint the canvas PNG"
    assert sample(painted, 88, 72) == sample(painted, 120, 72)

    # Run the same bundle without the packaged PNG. The canvas background
    # must show through at the pixel that previously held opaque red.
    packaged.unlink()
    control = work / "missing-asset.png"
    run([host, "--bundle", str(bundle), "--capture", str(control)])
    assert sample(control, 56, 72) == sample(control, 120, 72)
    assert sample(control, 56, 72) != red
    print("canvas: native pixels matched and the missing-file control exposed the background")


def report(name: str, result: dict) -> None:
    print(f"{name}: compared={result['compared']} maxDelta={result['maxDelta']} mismatches={len(result['mismatches'])} "
          f"origins={len(result['origins'])} missing={len(result['missing'])} "
          f"renderedWithoutBox={len(result['renderedWithoutBox'])}")
    print(json.dumps(result))


def main() -> int:
    host = sys.argv[1]
    material_only = host == "--material-only"
    canvas_only = host == "--canvas-package-only" or "--canvas-only" in sys.argv[3:]
    node = sys.argv[2] if len(sys.argv) > 2 else "node"
    if canvas_only:
        with tempfile.TemporaryDirectory(prefix="vellum-canvas-asset-") as temporary:
            check_canvas(host, node, Path(temporary))
        return 0
    reason = live_browser_skip_reason()
    if reason is not None:
        print(f"SKIP: {reason}")
        return 77
    if not os.environ.get("VELLUM_CHROME_PATH"):
        try:
            chrome_path()
        except BackendFailure as error:
            print(f"SKIP: {error}")
            return 77
    with tempfile.TemporaryDirectory(prefix="vellum-html-geometry-") as temporary:
        work = Path(temporary)

        envelope, design = import_case("fixture", node, work)
        check_material(node, design, work)
        if material_only:
            return 0
        clean = compare(build_and_dump(host, node, design, work / "fixture"), envelope)
        report("fixture", clean)
        assert clean["compared"] >= 10, clean
        assert clean["mismatches"] == [] and clean["missing"] == [], clean
        assert clean["renderedWithoutBox"] == [], clean

        # Control: a copy of the fixture with one element moved in its CSS,
        # rendered natively and checked against Chrome's layout of the
        # original, is caught at exactly that element.
        _, moved_design = import_case("moved", node, work, css=MOVE_CSS)
        moved = compare(build_and_dump(host, node, moved_design, work / "moved"), envelope)
        report("moved", moved)
        assert [o["anchor"] for o in moved["origins"]] == [f"semantic:{MOVED_ID}"], moved
        assert moved["origins"][0]["delta"] == [30, 20, 0, 0], moved
        assert moved["missing"] == [], moved

        # Control: without the lowered boxes the check fails, with every element
        # at the default 0,0,800,600 box.
        bare = compare(build_and_dump(host, node, stripped_layout(design), work / "stripped"),
                       envelope)
        report("no-layout", bare)
        assert len(bare["mismatches"]) == clean["compared"], bare
        whole = [m for m in bare["mismatches"] if m["native"] == [0, 0, 800, 600]]
        assert len(whole) >= 9, bare  # every element; text keeps its line height
        assert all(m["native"][:3] == [0, 0, 800] for m in bare["mismatches"]), bare
    return 0


if __name__ == "__main__":
    sys.exit(main())
