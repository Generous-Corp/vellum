#!/usr/bin/env python3
"""Draw the canvas shell's compiled vector plans in Chrome and check pixels.

The plans come from the shared C++ core (the vector asset GPU test binary
prints them for the compiled SVG fixtures); the page draws each with the
shell's `drawVector`, and the expected pixels below are computed here from
the SVG fixtures' geometry, independently of either renderer.
"""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_text_semantics_browser import (  # noqa: E402
    ProofProgress, ProofTimeout, headless_mode_arguments, stop_browser, wait_for_proof,
)

PAGE = """<!doctype html>
<html><body><script type="module">
import { drawVector } from './shell/vector-canvas.js';
const scenes = await (await fetch('./plans.json')).json();
const samples = await (await fetch('./samples.json')).json();
const result = {};
for (const scene of scenes) {
  const canvas = document.createElement('canvas');
  canvas.width = scene.width;
  canvas.height = scene.height;
  document.body.append(canvas);
  const context = canvas.getContext('2d');
  const [red, green, blue, alpha] = scene.background;
  context.fillStyle = `rgba(${Math.round(red * 255)},${Math.round(green * 255)},${Math.round(blue * 255)},${alpha})`;
  context.fillRect(0, 0, scene.width, scene.height);
  for (const plan of scene.plans) drawVector(context, plan);
  const pixels = context.getImageData(0, 0, scene.width, scene.height).data;
  result[scene.name] = (samples[scene.name] || []).map(([x, y]) => {
    const offset = (y * scene.width + x) * 4;
    return [pixels[offset], pixels[offset + 1], pixels[offset + 2], pixels[offset + 3]];
  });
}
await fetch('/__vellum_proof', { method: 'POST', body: JSON.stringify(result) });
</script></body></html>
"""


def mix(start: tuple[float, ...], end: tuple[float, ...], t: float) -> tuple[float, ...]:
    t = min(1.0, max(0.0, t))
    return tuple(a + (b - a) * t for a, b in zip(start, end))


def hexrgb(value: int) -> tuple[float, ...]:
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def two_circle_t(px: float, py: float, fx: float, fy: float, cx: float, cy: float, r: float) -> float:
    dx, dy, ex, ey = px - fx, py - fy, cx - fx, cy - fy
    a = ex * ex + ey * ey - r * r
    b = -2 * (dx * ex + dy * ey)
    c = dx * dx + dy * dy
    root = math.sqrt(max(0.0, b * b - 4 * a * c))
    return max((-b + root) / (2 * a), (-b - root) / (2 * a))


def expectations() -> dict[str, list[tuple[int, int, tuple[float, ...], float, str]]]:
    red, blue = hexrgb(0xFF0000), hexrgb(0x0000FF)
    background = hexrgb(0xF8FAFC)
    gradients = [(5, 5, background, 2, "background"), (160, 70, hexrgb(0xE11D48), 2, "swatch")]
    for x in (36, 100, 164):
        gradients.append((x, 25, mix(red, blue, (x + 0.5 - 20) / 160), 6, f"ramp x={x}"))
    for y in (55, 80, 105):
        fraction = (y + 0.5 - 50) / 60
        gradients.append((40, y, mix(hexrgb(0x00FF00), blue, (fraction - 0.25) / 0.5), 6, f"column y={y}"))
    for x in (110, 125, 132):
        distance = math.hypot(x + 0.5 - 110, 0.5)
        gradients.append((x, 80, mix(hexrgb(0xFFFFFF), (0, 0, 0), distance / 30), 6, f"glow x={x}"))
    compiled = [(5, 5, background, 2, "background")]
    for x in (10, 100, 190):
        compiled.append((x, 25, mix(red, blue, (x + 0.5 - 40) / 120), 6, f"user-space ramp x={x}"))
    for y in (55, 80, 105):
        compiled.append((40, y, mix(hexrgb(0x00FF00), hexrgb(0xFF00FF), (y + 0.5 - 50) / 60), 6, f"turned ramp y={y}"))
    for x, y in ((105, 80), (178, 80), (130, 60)):
        u, v = (x + 0.5 - 80) / 100, (y + 0.5 - 50) / 60
        t = two_circle_t(u, v, 0.25, 0.5, 0.5, 0.5, 0.5)
        compiled.append((x, y, mix(hexrgb(0xFFFF00), hexrgb(0x000080), t), 6, f"focal radial ({x}, {y})"))
    grid = [(2, 2, background, 2, "background"), (8, 7, hexrgb(0xC81E1E), 2, "cell 0,0"),
            (15, 7, hexrgb(0x1E64C8), 2, "cell 0,1"), (11, 7, background, 2, "gap"),
            (183, 102, hexrgb(0xC81E1E), 2, "cell 19,25")]
    # Built from a rendered Vector node at opacity 0.5 with two overlapping
    # squares, red then blue: drawn as one group, the overlap is blue at half
    # strength; drawn paint by paint, red would show through it.
    white = (255, 255, 255)
    overlap = [(5, 10, mix(white, red, 0.5), 2, "red alone at half opacity"),
               (15, 10, mix(white, blue, 0.5), 2, "the overlap is blue at half opacity, no red"),
               (25, 10, mix(white, blue, 0.5), 2, "blue alone at half opacity"),
               (35, 10, white, 2, "the background")]
    # A group with an empty clip draws nothing; one with no clip draws.
    empty_clip = [(5, 10, white, 2, "a group with an empty clip draws nothing"),
                  (15, 10, red, 2, "a group with no clip draws")]
    return {"gradients-compiled": gradients, "compiled-only": compiled, "over-budget": grid,
            "overlap": overlap, "empty-clip": empty_clip}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", required=True, type=Path, help="the vector asset GPU test binary")
    parser.add_argument("--shell", required=True, type=Path)
    args = parser.parse_args()
    chrome = os.environ.get("VELLUM_CHROME_PATH") or shutil.which("google-chrome")
    if chrome is None:
        application = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        chrome = str(application) if application.is_file() else None
    if chrome is None or not Path(chrome).is_file():
        print("SKIP: Google Chrome is required for the canvas vector oracle")
        return 77
    printed = subprocess.run([str(args.plans), "--canvas-plans"], check=True, capture_output=True, text=True)
    scenes = [json.loads(line) for line in printed.stdout.splitlines() if line.strip()]
    expected = expectations()
    if sorted(scene["name"] for scene in scenes) != sorted(expected):
        raise SystemExit(f"unexpected plan set: {[scene['name'] for scene in scenes]}")
    result: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="vellum-vector-oracle-") as root:
        site = Path(root)
        shutil.copytree(args.shell, site / "shell")
        (site / "index.html").write_text(PAGE, encoding="utf-8")
        (site / "plans.json").write_text(json.dumps(scenes), encoding="utf-8")
        (site / "samples.json").write_text(json.dumps(
            {name: [[x, y] for x, y, *_ in points] for name, points in expected.items()}), encoding="utf-8")
        progress = ProofProgress()

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *handler_args: object, **handler_kwargs: object) -> None:
                super().__init__(*handler_args, directory=str(site), **handler_kwargs)

            def log_message(self, *_: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                progress.request(self.path)
                super().do_GET()

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                length = int(self.headers.get("content-length", "0"))
                result.update(json.loads(self.rfile.read(length)))
                self.send_response(204)
                self.end_headers()
                progress.deliver()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="vellum-chrome-") as profile:
                process = subprocess.Popen(
                    [chrome, *headless_mode_arguments(chrome), "--disable-gpu-sandbox",
                     "--no-first-run",
                     "--disable-background-networking", "--force-device-scale-factor=1",
                     f"--user-data-dir={profile}",
                     f"http://127.0.0.1:{server.server_port}/index.html"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=os.name == "posix")
                try:
                    wait_for_proof(progress, process)
                except ProofTimeout as timeout:
                    raise SystemExit(f"canvas vector oracle: {timeout}") from None
                finally:
                    stop_browser(process, process_group=process.pid if os.name == "posix" else None)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    failures = 0
    for name, points in expected.items():
        actual = result.get(name)
        if not isinstance(actual, list) or len(actual) != len(points):
            print(f"FAIL {name}: no pixels")
            failures += 1
            continue
        for (x, y, rgb, tolerance, where), pixel in zip(points, actual):
            worst = max(abs(pixel[channel] - rgb[channel]) for channel in range(3))
            ok = worst <= tolerance and pixel[3] == 255
            failures += 0 if ok else 1
            print(f"{'PASS' if ok else 'FAIL'} canvas oracle {name} {where}: {pixel} expected "
                  f"{tuple(round(value, 1) for value in rgb)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
