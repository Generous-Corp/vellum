#!/usr/bin/env python3
"""Proof that the visual runner drives vellum-app-host end to end.

The app-host fixtures capture a panel headlessly, press a node (the simulated
click), and compare the frame and the tree dump with goldens. Each control
breaks one input and requires the run to fail for its own reason: a changed
golden pixel, a moved node in the tree golden, and a press target that does not
exist.
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
from vellum_png import RgbaImage, decode_png_bytes, encode_png  # noqa: E402

RUNNER = REPO / "tools" / "visual" / "runner.py"
CAPTURE = REPO / "tools" / "visual" / "capture_app_host.py"
BUNDLE = REPO / "apps" / "app-host" / "fixtures" / "counter.js"
VISUAL_ROOT = REPO / "tools" / "visual"


def run(command: list[str], host: str, **extra) -> subprocess.CompletedProcess:
    environment = dict(os.environ, VELLUM_APP_HOST=host, MTL_DEBUG_LAYER="0")
    return subprocess.run(command, cwd=REPO, env=environment, capture_output=True, text=True,
                          check=False, timeout=300, **extra)


def verify(host: str, visual_root: Path) -> subprocess.CompletedProcess:
    return run([sys.executable, str(RUNNER), "--verify", "--surface", "app-host",
                "--visual-root", str(visual_root)], host)


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)


def copy_visual_root(into: Path) -> Path:
    root = into / "visual"
    shutil.copytree(VISUAL_ROOT / "fixtures", root / "fixtures")
    shutil.copytree(VISUAL_ROOT / "goldens", root / "goldens")
    return root


def main(host: str) -> int:
    clean = verify(host, VISUAL_ROOT)
    require(clean.returncode == 0, f"committed goldens do not verify:\n{clean.stdout}{clean.stderr}")
    for fixture in ("counter", "counter-pressed", "counter-pressed-tree"):
        require(f"ok app-host/{fixture}" in clean.stdout, f"{fixture} did not report ok")

    with tempfile.TemporaryDirectory(prefix="app-host-visual-") as scratch:
        scratch_path = Path(scratch)

        # Control 1: one golden pixel changed far beyond the channel tolerance.
        root = copy_visual_root(scratch_path / "pixel")
        golden = root / "goldens" / "app-host" / "counter-pressed.png"
        image = decode_png_bytes(golden.read_bytes())
        pixels = bytearray(image.pixels)
        offset = (5 * image.width + 5) * 4
        pixels[offset] = (pixels[offset] + 128) % 256
        golden.write_bytes(encode_png(RgbaImage(image.width, image.height, bytes(pixels))))
        changed = verify(host, root)
        require(changed.returncode == 1, "a changed golden pixel must fail verification")
        require("FAILED app-host/counter-pressed\n" in changed.stderr,
                f"the failure must name counter-pressed:\n{changed.stderr}")
        require("ok app-host/counter\n" in changed.stdout, "the untouched fixture must still pass")

        # Control 2: the tree golden records a node box the capture does not produce.
        root = copy_visual_root(scratch_path / "tree")
        tree_golden = root / "goldens" / "app-host" / "counter-pressed-tree.json"
        tree = json.loads(tree_golden.read_text(encoding="utf-8"))
        for node in tree["nodes"]:
            if node["id"] == "bar":
                node["box"]["width"] = 40
        tree_golden.write_text(json.dumps(tree, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        moved = verify(host, root)
        require(moved.returncode == 1, "a moved node in the tree golden must fail verification")
        require("FAILED app-host/counter-pressed-tree" in moved.stderr,
                f"the failure must name the tree fixture:\n{moved.stderr}")

        # Control 3: the press target is absent, so the simulated click cannot land.
        broken = scratch_path / "no-press.js"
        broken.write_text(BUNDLE.read_text(encoding="utf-8").replace('events: { press: "inc" }, ', ""),
                          encoding="utf-8")
        require(broken.read_text(encoding="utf-8") != BUNDLE.read_text(encoding="utf-8"),
                "control setup did not remove the press event")
        refused = run([sys.executable, str(CAPTURE), "--bundle", str(broken), "--press", "go"], host)
        require(refused.returncode != 0, "pressing an unpressable node must fail the capture")
        require("not pressable" in refused.stderr, f"the host's refusal must surface:\n{refused.stderr}")
        require(refused.stdout == "", "a refused capture must not write an image")

        # The press moves the tree: the click is observable, not only the golden.
        before = run([sys.executable, str(CAPTURE), "--bundle", str(BUNDLE), "--output", "tree"], host)
        after = run([sys.executable, str(CAPTURE), "--bundle", str(BUNDLE), "--press", "go",
                     "--output", "tree"], host)
        require(before.returncode == 0 and after.returncode == 0, "tree captures failed")

        def width(text: str) -> float:
            return next(n["box"]["width"] for n in json.loads(text)["nodes"] if n["id"] == "bar")

        require(width(after.stdout) > width(before.stdout), "the press must widen the bar")
    print("app-host visual fixtures: all controls behaved")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: test_visual_fixtures.py PATH_TO_VELLUM_APP_HOST", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
