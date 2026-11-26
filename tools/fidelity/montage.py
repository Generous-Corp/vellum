#!/usr/bin/env python3
"""Labeled comparison montage for design-import renders.

Stacks N images into one montage with a titled bar above each panel, so a
reference-versus-render comparison is self-documenting: each panel says what
it is without an external caption. Labels are on by default; that is the
point.

Usage:
  # path:Label pairs (the label defaults to the file stem)
  montage.py --out compare.png ref.png:"Reference" render.png:"Vellum render"
  montage.py --out compare.png --columns 2 a.png:"A" b.png:"B"   # side by side
  montage.py --out compare.png --no-labels a.png b.png            # opt out
  montage.py --out compare.png --config montage.json ...          # defaults via config

Config (JSON, all optional; CLI overrides): {
  "labels": true, "columns": 1, "title_height": 36, "pad": 14,
  "bg": [20,20,24], "title_bg": [12,12,14], "title_fg": [235,235,240],
  "panel_width": 1000, "font_size": 22
}
Importable: build_montage(panels, out_path, **opts) where panels=[(label, path), ...].

Labels use a built-in 5x7 bitmap font scaled to about font_size, and panels
are scaled to panel_width with nearest-neighbour sampling, so the tool needs
no Pillow and no font files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bitmap_font import GLYPH_HEIGHT, draw_text  # noqa: E402
from fidelity_image import open_image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from vellum_png import RgbaImage, write_png  # noqa: E402


def build_montage(panels, out_path, labels=True, columns=1, title_height=36,
                  pad=14, bg=(20, 20, 24), title_bg=(12, 12, 14),
                  title_fg=(235, 235, 240), panel_width=1000, font_size=22):
    """panels: list of (label, image_path). Returns the output path."""
    if columns < 1:
        raise ValueError("columns must be at least 1")
    if not panels:
        raise ValueError("a montage needs at least one panel")
    scale = max(1, round(font_size / (GLYPH_HEIGHT + 1)))
    cells = []
    for label, path in panels:
        im = open_image(Path(path), "RGBA")
        w, h = im.size
        if w != panel_width:
            im = im.resize((panel_width, max(1, int(h * panel_width / w))))
        cells.append((label, im))
    th = title_height if labels else 0
    col_w = panel_width + pad * 2
    rows = (len(cells) + columns - 1) // columns
    row_heights = []
    for r in range(rows):
        rowcells = cells[r * columns:(r + 1) * columns]
        row_heights.append(max(c[1].height for c in rowcells) + th + pad)
    W = col_w * columns + pad
    H = sum(row_heights) + pad
    canvas = bytearray(bytes((*bg, 255)) * (W * H))

    def fill(x0, y0, x1, y1, color):
        for yy in range(max(0, y0), min(H, y1)):
            start = (yy * W + max(0, x0)) * 4
            stop = (yy * W + min(W, x1)) * 4
            canvas[start:stop] = bytes((*color, 255)) * ((stop - start) // 4)

    y = pad
    for r in range(rows):
        x = pad
        for c in range(columns):
            idx = r * columns + c
            if idx >= len(cells):
                break
            label, im = cells[idx]
            if labels:
                fill(x, y, x + panel_width + 1, y + th + 1, title_bg)
                text_y = y + (th - GLYPH_HEIGHT * scale) // 2
                draw_text(canvas, W, H, x + 10, text_y, label, title_fg, scale)
            for row in range(im.height):
                for column in range(im.width):
                    px, py = x + column, y + th + row
                    if 0 <= px < W and 0 <= py < H:
                        offset = (py * W + px) * 4
                        canvas[offset:offset + 4] = bytes(im.getdata()[row * im.width + column])
            x += col_w
        y += row_heights[r]
    write_png(Path(out_path), RgbaImage(W, H, bytes(canvas)))
    return out_path


def _parse_panel(spec):
    # "path:Label" becomes (label, path). Split on the FIRST ':' so a label that
    # contains colons survives ("x.png:1. Figma: source" keeps "1. Figma:
    # source"). Treat the whole spec as a path when the head is not an existing
    # file (a bare path, or a path with no label).
    if os.path.exists(spec):
        return (os.path.splitext(os.path.basename(spec))[0], spec)
    head, sep, label = spec.partition(":")
    if sep and head and os.path.exists(head):
        return (label or os.path.splitext(os.path.basename(head))[0], head)
    return (os.path.splitext(os.path.basename(spec))[0], spec)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Labeled comparison montage for import renders")
    ap.add_argument("panels", nargs="+", help='image specs: path or "path:Label"')
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-labels", action="store_true", help="disable titles (default: labeled)")
    ap.add_argument("--columns", type=int)
    ap.add_argument("--config", help="JSON defaults (CLI overrides)")
    args = ap.parse_args(argv)
    opts = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as handle:
            opts = json.load(handle)
    for key in ("bg", "title_bg", "title_fg"):
        if key in opts:
            opts[key] = tuple(opts[key])
    if args.no_labels:
        opts["labels"] = False
    if args.columns is not None:
        opts["columns"] = args.columns
    panels = [_parse_panel(s) for s in args.panels]
    out = build_montage(panels, args.out, **opts)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
