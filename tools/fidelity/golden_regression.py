#!/usr/bin/env python3
"""Golden regression check for imported design renders.

Compares a current render against a committed baseline, so a code change cannot
silently regress a design that already imported correctly. The comparison is
deliberately TOLERANT and STRUCTURAL, not exact-pixel: a naive equality check
false-positives on GPU antialiasing or dither noise and on legitimate sub-pixel
sizing improvements (a max of 15/255, localized to soft knob shadows, was
measured on an import that had not changed). So pass or fail keys on:

  * the fraction of pixels differing by MORE than `--per-pixel-tol`
    (default 24/255),
  * a coarse edge/structure agreement score (gradient magnitude on luminance),
    which is robust to color and theme and to soft-shadow noise.

The current render comes from one of two places:

  * `--candidate PNG`, a render the caller already made (the default way), or
  * `--render-cmd CMD`, a command the caller supplies. The tool runs it without a
    shell and it must write a PNG to the path the tool passes: either where
    `{output}` appears in CMD, or as an extra last argument.

Proprietary baselines stay local; CI uses sanitized or synthetic baselines.

Usage:
  golden_regression.py --baseline golden/panel.png --candidate current.png \
      [--per-pixel-tol 24 --max-diff-frac 0.01 --min-edge-agree 0.85] \
      [--out-compare out/compare.png]
  golden_regression.py --baseline golden/panel.png \
      --render-cmd "my-render --scene panel.json --out {output}"
Exit 0 = within tolerance (no regression); 3 = regression; 2 = setup error.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bitmap_font import draw_text  # noqa: E402
from fidelity_image import Image, open_image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from vellum_png import RgbaImage, write_png  # noqa: E402


def render(command: str, out_png: str) -> bool:
    """Run the caller's render command so it writes `out_png`."""
    argv = shlex.split(command)
    if not argv:
        print("golden: --render-cmd is empty", file=sys.stderr)
        return False
    if any("{output}" in part for part in argv):
        argv = [part.replace("{output}", out_png) for part in argv]
    else:
        argv.append(out_png)
    try:
        completed = subprocess.run(argv, capture_output=True, text=True)
    except OSError as error:
        print(f"golden: render command could not start: {error}", file=sys.stderr)
        return False
    if completed.returncode != 0:
        print(f"golden: render command failed (exit {completed.returncode})", file=sys.stderr)
        return False
    if not os.path.isfile(out_png):
        print("golden: render command wrote no PNG", file=sys.stderr)
        return False
    return True


def edges(gray: list[list[int]]) -> list[list[bool]]:
    """Binary edge map: |dx| + |dy| of luminance over 32, from signed deltas.

    The deltas are signed before taking magnitudes, so a 255 -> 0 dark-on-light
    edge keeps its full size instead of wrapping to a small value.
    """
    height = len(gray)
    width = len(gray[0]) if height else 0
    out = []
    for y in range(height):
        row = gray[y]
        above = gray[y - 1] if y else row
        out.append([
            (abs(row[x] - row[x - 1]) if x else 0) + abs(row[x] - above[x]) > 32
            for x in range(width)
        ])
    return out


def _dilate(mask: list[list[bool]], r: int = 2) -> list[list[bool]]:
    """Grow a binary mask by r pixels (4-neighbour), so structural agreement
    tolerates sub-pixel edge shifts from GPU AA or legitimate rounding; without
    it a 1px-shifted thin edge wrongly reads as a structural mismatch."""
    out = [list(row) for row in mask]
    height = len(out)
    width = len(out[0]) if height else 0
    for _ in range(r):
        grown = [list(row) for row in out]
        for y in range(height):
            for x in range(width):
                if out[y][x]:
                    continue
                if ((y > 0 and out[y - 1][x]) or (y + 1 < height and out[y + 1][x])
                        or (x > 0 and out[y][x - 1]) or (x + 1 < width and out[y][x + 1])):
                    grown[y][x] = True
        out = grown
    return out


def _rows(image: Image) -> list[list[int]]:
    values = [p[0] for p in image.getdata()]
    return [values[y * image.width:(y + 1) * image.width] for y in range(image.height)]


def _count(mask: list[list[bool]]) -> int:
    return sum(sum(row) for row in mask)


def _both(a: list[list[bool]], b: list[list[bool]]) -> int:
    return sum(x and y for ra, rb in zip(a, b) for x, y in zip(ra, rb))


def compare(baseline_png: str, current_png: str, args) -> int:
    base = open_image(Path(baseline_png), "RGB")
    cur = open_image(Path(current_png), "RGB")
    if base.size != cur.size:
        cur = cur.resize_bicubic(base.size)
    diff = Image(base.width, base.height, [
        tuple(abs(a - b) for a, b in zip(pa, pb))
        for pa, pb in zip(base.getdata(), cur.getdata())
    ], "RGB")
    over = sum(1 for pixel in diff.getdata() if max(pixel) > args.per_pixel_tol)
    frac = float(over) / (base.width * base.height)

    be, ce = edges(_rows(base.convert("L"))), edges(_rows(cur.convert("L")))
    # Shift-tolerant structural agreement: an edge counts as matched if there is
    # an edge within 2px in the other image. Symmetric, so neither side can hide
    # a missing or extra structure.
    be_d, ce_d = _dilate(be), _dilate(ce)
    base_edges, cur_edges = _count(be), _count(ce)
    agree_b = 1.0 if base_edges == 0 else float(_both(be, ce_d)) / float(base_edges)
    agree_c = 1.0 if cur_edges == 0 else float(_both(ce, be_d)) / float(cur_edges)
    edge_agree = min(agree_b, agree_c)

    ok = (frac <= args.max_diff_frac) and (edge_agree >= args.min_edge_agree)
    print(f"golden: pixels>{args.per_pixel_tol}: {frac*100:.3f}% "
          f"(max {args.max_diff_frac*100:.2f}%) | edge-agree: {edge_agree:.4f} "
          f"(min {args.min_edge_agree:.3f}) | {'PASS' if ok else 'REGRESSION'}")

    if args.out_compare:
        _write_compare(base, cur, diff, frac, edge_agree, args.out_compare)
    return 0 if ok else 3


def _autocontrast(gray: Image) -> Image:
    """Pillow's autocontrast with no cutoff: stretch the used range to 0..255."""
    values = [p[0] for p in gray.getdata()]
    lo, hi = min(values), max(values)
    if hi <= lo:
        return gray
    scale = 255.0 / (hi - lo)
    offset = -lo * scale
    lut = [min(255, max(0, int(ix * scale + offset))) for ix in range(256)]
    return Image(gray.width, gray.height, [(lut[v],) for v in values], "L")


def _write_compare(base: Image, cur: Image, diff: Image, frac: float,
                   edge_agree: float, path: str) -> None:
    g = _autocontrast(diff.convert("L"))
    heat = Image(g.width, g.height, [(v, v // 3, 0) for (v,) in g.getdata()], "RGB")
    width = 760

    def fit(im: Image) -> Image:
        return im.resize_bicubic((width, int(im.height * width / im.width)))

    panels = [(fit(base), "BASELINE (GOLDEN)"),
              (fit(cur), "CURRENT RENDER"),
              (fit(heat), f"DIFF (CONTRAST-NORMALIZED) - {frac*100:.3f}% OVER TOL, "
                          f"EDGE-AGREE {edge_agree:.3f}")]
    label_height, pad = 30, 16
    panel_height = panels[0][0].height
    canvas_width = width + pad * 2
    canvas_height = (panel_height + label_height) * 3 + pad * 4
    canvas = bytearray(bytes((22, 22, 26, 255)) * (canvas_width * canvas_height))
    y = pad
    for im, name in panels:
        draw_text(canvas, canvas_width, canvas_height, pad, y + 8, name, (238, 238, 242), 2)
        for row in range(im.height):
            for column in range(im.width):
                offset = ((y + label_height + row) * canvas_width + pad + column) * 4
                canvas[offset:offset + 4] = bytes((*im.getdata()[row * im.width + column], 255))
        y += panel_height + label_height + pad
    write_png(Path(path), RgbaImage(canvas_width, canvas_height, bytes(canvas)))
    print(f"golden: wrote comparison {path}")


def _selftest() -> int:
    """Verify edges() captures strong dark-on-light transitions in both directions."""
    assert edges([[255, 255, 0, 0]])[0][2], "edges() missed a 255->0 dark-on-light transition"
    assert edges([[0, 0, 255, 255]])[0][2], "edges() missed a 0->255 transition"
    print("golden: selftest passed (edges() handles signed luminance deltas)")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return _selftest()
    ap = argparse.ArgumentParser(description="Golden regression check for a design render.")
    ap.add_argument("--baseline", required=True)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate", help="a current render the caller already made")
    source.add_argument("--render-cmd",
                        help="a command that writes the current render to {output}")
    ap.add_argument("--per-pixel-tol", type=int, default=24)
    ap.add_argument("--max-diff-frac", type=float, default=0.01)
    ap.add_argument("--min-edge-agree", type=float, default=0.85)
    ap.add_argument("--out-compare", default="")
    args = ap.parse_args(argv)
    if not os.path.exists(args.baseline):
        print(f"golden: baseline missing: {args.baseline}", file=sys.stderr)
        return 2
    try:
        if args.candidate is not None:
            if not os.path.exists(args.candidate):
                print(f"golden: candidate missing: {args.candidate}", file=sys.stderr)
                return 2
            return compare(args.baseline, args.candidate, args)
        with tempfile.TemporaryDirectory() as td:
            cur = os.path.join(td, "current.png")
            if not render(args.render_cmd, cur):
                return 2
            return compare(args.baseline, cur, args)
    except Exception as error:  # an unreadable PNG is a setup error, not a regression
        print(f"golden: cannot compare: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
