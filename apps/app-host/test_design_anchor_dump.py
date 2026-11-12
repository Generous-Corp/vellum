#!/usr/bin/env python3
"""End to end: a design materialized by @vellum/ui, run by the app host, dumps
the design identity and laid-out box of its interactive node."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ENTRY = """
import { createApp, materializeDesign, mount } from '@vellum/ui';
import { importedDesign } from '@vellum/imported';
mount(createApp({
    id: 'anchor-dump', stateVersion: '1', initialState: {},
    actions: { tap: () => ({}) },
    render: () => materializeDesign(importedDesign, {
        actions: { 'main/1:2': { press: 'tap' } },
        viewport: { height: 240, padding: 20, width: 420 },
    }),
}));
"""

NORMALIZE = """
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { decodeFigmaPluginExport, normalizeImport } from
    %s;
const bytes = readFileSync(process.argv[2]);
const hash = createHash('sha256').update(bytes).digest('hex');
process.stdout.write(JSON.stringify(normalizeImport(decodeFigmaPluginExport(
    JSON.parse(bytes.toString('utf8')), { sourceHash: `sha256:${hash}`, sourceKey: 'main' },
))));
"""


def run(arguments: list[str], **options) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, text=True, capture_output=True, check=False, **options)
    if completed.returncode:
        sys.stderr.write(completed.stdout + completed.stderr)
        raise SystemExit(1)
    return completed


def interactive_nodes(dump: dict) -> list[dict]:
    return [node for node in dump["nodes"] if node["id"] == "main/1:2"]


def main() -> int:
    host, source, node = sys.argv[1:4]
    root = Path(source)
    ui = root / "packages/vellum-ui"
    ir = root / "packages/vellum-design-ir/src/index.js"
    fixture = root / "fixtures/design-ir/pulp-emitter-generic.export.json"
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        script = work / "normalize.mjs"
        script.write_text(NORMALIZE % json.dumps(ir.as_uri()), encoding="utf-8")
        design = work / "main.materialized.json"
        design.write_text(run([node, str(script), str(fixture)]).stdout, encoding="utf-8")
        (work / "main.bindings.json").write_text(json.dumps({
            "schema": "vellum.generated-bindings.v1", "bindings": [],
        }), encoding="utf-8")
        entry = work / "main.jsx"
        entry.write_text(ENTRY, encoding="utf-8")
        bundle = work / "bundle.js"
        run([node, "scripts/build-project.mjs", str(entry), str(bundle), str(design)],
            cwd=ui)
        dump_path = work / "dump.json"
        run([host, "--bundle", str(bundle), "--dump-tree", str(dump_path)])
        found = interactive_nodes(json.loads(dump_path.read_text(encoding="utf-8")))
        if len(found) != 1 or found[0]["anchor"] != "figma:1:2":
            sys.stderr.write(f"dump lacks the figma anchor: {found}\n")
            return 1
        box = found[0]["box"]
        if not (box["width"] > 0 and box["height"] > 0):
            sys.stderr.write(f"dump box is empty: {box}\n")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
