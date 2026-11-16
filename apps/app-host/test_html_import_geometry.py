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


def report(name: str, result: dict) -> None:
    print(f"{name}: compared={result['compared']} maxDelta={result['maxDelta']} mismatches={len(result['mismatches'])} "
          f"origins={len(result['origins'])} missing={len(result['missing'])} "
          f"renderedWithoutBox={len(result['renderedWithoutBox'])}")
    print(json.dumps(result))


def main() -> int:
    host = sys.argv[1]
    node = sys.argv[2] if len(sys.argv) > 2 else "node"
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
