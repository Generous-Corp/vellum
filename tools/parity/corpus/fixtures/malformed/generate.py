#!/usr/bin/env python3
"""Regenerate the malformed-resource fixtures for the expected-rejection lane.

Every fixture is derived from three authored inputs in this directory: a small
figma-plugin scene envelope, ../image-asset.ir.json with ../assets/swatch.png,
and a short HTML card. Archives are written with fixed timestamps and member
order, so the output is byte-for-byte reproducible:

    python3 tools/parity/corpus/fixtures/malformed/generate.py          # rewrite
    python3 tools/parity/corpus/fixtures/malformed/generate.py --check  # verify

Nothing here is a real exploit: the archive paths only have to be refused, and
the absolute member targets `/`, which is not writable.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import warnings
import zipfile

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent

SCENE = {
    "$schema": "https://pulp.dev/schemas/figma-plugin-export-v1.json",
    "format_version": "2026.05-figma-plugin-v1",
    "root": {
        "type": "frame", "name": "parity panel",
        "style": {"width": 300, "height": 260, "backgroundColor": "#14171c"},
        "children": [
            {"type": "frame", "name": "Header", "style": {"width": 260, "height": 40, "backgroundColor": "#2a3140"},
             "children": []},
            {"type": "text", "name": "Title", "characters": "Parity", "style": {"width": 120, "height": 24},
             "children": []},
        ],
    },
}

CARD = """<!doctype html>
<html><head><style>
@font-face{font-family:"Broken";src:url("fonts/bad.woff2") format("woff2")}
body{margin:0;background:#101522;color:#eef2ff;font-family:"Broken",Arial,sans-serif}
.card{width:320px;margin:24px;padding:20px;background:#1e293b;border-radius:12px}
</style></head><body><main class="card"><h1>Signal Path</h1><p>Clean input monitoring</p></main></body></html>
"""


def scene_bytes(**rename: str) -> bytes:
    scene = json.loads(json.dumps(SCENE))
    for child in scene["root"]["children"]:
        child["name"] = rename.get(child["name"], child["name"])
    return (json.dumps(scene, indent=1, sort_keys=True) + "\n").encode("utf-8")


def archive(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    # Duplicate members are the point of one fixture.
    warnings.filterwarnings("ignore", message="Duplicate name", category=UserWarning)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buffer.getvalue()


def design_ir(**changes: object) -> dict:
    document = json.loads((FIXTURES / "image-asset.ir.json").read_text(encoding="utf-8"))
    for pointer, value in changes.items():
        node: object = document
        parts = pointer.split("__")
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]  # type: ignore[index]
        last = parts[-1]
        if isinstance(node, list):
            node.append(value) if last == "append" else node.__setitem__(int(last), value)
        else:
            node[last] = value  # type: ignore[index]
    return document


def ir_bytes(document: dict) -> bytes:
    return (json.dumps(document, indent=1, sort_keys=True) + "\n").encode("utf-8")


def fixtures() -> dict[str, bytes]:
    swatch = (FIXTURES / "assets" / "swatch.png").read_bytes()
    scene = scene_bytes()
    wellformed_zip = archive([("scene.pulp.json", scene)])
    duplicate_node = {"type": "image", "name": "Swatch copy", "style": {"width": 32, "height": 32},
                      "attributes": {"asset_ref": "swatch"}, "stable_anchor_id": "swatch-image", "children": []}
    full = ir_bytes(design_ir())
    in_string = full[:full.index(b'"assets/sw') + len(b'"assets/sw')]
    files = {
        "zip-wellformed/design.pulp.zip": wellformed_zip,
        "zip-path-traversal/design.pulp.zip": archive([("scene.pulp.json", scene), ("../escape.txt", b"x")]),
        "zip-absolute-path/design.pulp.zip": archive([("scene.pulp.json", scene),
                                                      ("/vellum-parity-absolute-entry.txt", b"x")]),
        "zip-no-document/design.pulp.zip": archive([("readme.txt", b"no design document here\n")]),
        "zip-truncated/design.pulp.zip": wellformed_zip[:60],
        "zip-duplicate-member/design.pulp.zip": archive([("scene.pulp.json", scene),
                                                         ("scene.pulp.json", scene_bytes(Title="Shadow"))]),
        "ir-wellformed/source.ir.json": ir_bytes(design_ir()),
        "ir-wellformed/assets/swatch.png": swatch,
        "ir-missing-asset/source.ir.json": ir_bytes(design_ir()),
        "ir-truncated/source.ir.json": ir_bytes(design_ir())[:200],
        "ir-truncated/assets/swatch.png": swatch,
        # Cut inside a string literal: a parser that only checks for end of
        # input after a backslash reads past the buffer here.
        "ir-truncated-in-string/source.ir.json": in_string,
        "ir-truncated-in-string/assets/swatch.png": swatch,
        "ir-corrupt-image/source.ir.json": ir_bytes(design_ir()),
        "ir-corrupt-image/assets/swatch.png": swatch[:60],
        "ir-not-an-image/source.ir.json": ir_bytes(design_ir()),
        "ir-not-an-image/assets/swatch.png": b"GIF89a this is not an image\n",
        "ir-unknown-node/source.ir.json": ir_bytes(design_ir(**{"root__children__0__type": "hologram"})),
        "ir-unknown-node/assets/swatch.png": swatch,
        "ir-duplicate-identity/source.ir.json": ir_bytes(design_ir(**{"root__children__append": duplicate_node})),
        "ir-duplicate-identity/assets/swatch.png": swatch,
        "ir-future-version/source.ir.json": ir_bytes(design_ir(version=99)),
        "ir-future-version/assets/swatch.png": swatch,
        "ir-asset-path-escape/source.ir.json": ir_bytes(design_ir(
            **{"assetManifest__assets__0__local_path": "../escape.png"})),
        "ir-asset-path-escape/assets/swatch.png": swatch,
        "html-corrupt-font/font.html": CARD.encode("utf-8"),
        "html-corrupt-font/fonts/bad.woff2": b"wOF2 deliberately not a font\n",
    }
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a committed fixture differs")
    args = parser.parse_args()
    stale = []
    for relative, data in fixtures().items():
        path = HERE / relative
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for relative in stale:
        print(f"stale or missing fixture: {relative}", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
