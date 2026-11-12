#!/usr/bin/env python3
"""Black-box proof that capture steps after `advance` are a function of the
manual clock alone, and that without it they depend on the wall clock."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import time

BUNDLE = r"""
(() => {
  let stamp = Date.now();
  let dirty = true;
  let ticks = 0;
  const tick = () => {
    stamp = Date.now();
    dirty = true;
    if (++ticks < 20) setTimeout(tick, 16);
  };
  setTimeout(tick, 16);
  const tree = () => {
    const level = Math.floor(stamp % 200) + 30;
    const hex = level.toString(16).padStart(2, "0");
    return {
      type: "stack", id: "screen",
      style: { width: 240, height: 120, backgroundColor: "#0F172A", padding: 10 },
      children: [{
        type: "view", id: "bar",
        style: { width: 20 + (stamp % 200), height: 60,
                 backgroundColor: "#" + hex + "90" + hex },
        children: []
      }, {
        type: "view", id: "ramp",
        style: { width: 200, height: 20, backgroundLinearGradient: { angle: 90,
          stops: [{ position: 0.01, color: "#F8FAFC" }, { position: 1, color: "#EF4444" }] } },
        children: []
      }]
    };
  };
  const render = () => { dirty = false; return JSON.stringify({
    protocol: "vellum.authoring-host.v1", tree: tree() }); };
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    hostProtocol: "vellum.authoring-host.v2",
    renderJSON: render, dispatchJSON: render,
    isDirty() { return dirty; },
    pumpJSON() { dirty = false; return JSON.stringify({
      protocol: "vellum.authoring-host.v2", kind: "render-result",
      revision: ticks, tree: tree() }); }
  };
})();
"""

TIMES = (50, 70, 100)  # cumulative 50, 120, 220 ms


def run(host: str, bundle: Path, out: Path, manual: bool) -> list[bytes]:
    arguments = [host, "--bundle", str(bundle), "--capture-dir", str(out)]
    if manual:
        arguments.append("--manual-clock")
    for index, amount in enumerate(TIMES):
        if manual:
            arguments += ["--advance", str(amount)]
        arguments += ["--capture-step", f"t{index}"]
    completed = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if completed.returncode:
        sys.stderr.write(completed.stdout + completed.stderr)
        raise SystemExit(1)
    return [(out / f"t{index}.png").read_bytes() for index in range(len(TIMES))]


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    host = sys.argv[1]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bundle = root / "bundle.js"
        bundle.write_text(BUNDLE, encoding="utf-8")
        first = run(host, bundle, root / "m1", True)
        second = run(host, bundle, root / "m2", True)
        if first != second:
            sys.stderr.write("manual-clock captures differ between runs\n")
            return 1
        if len(set(first)) != len(first):
            sys.stderr.write("frames at different times should differ\n")
            return 1
        # Control: on the wall clock the same scenario shape is not repeatable.
        wall_first = run(host, bundle, root / "w1", False)
        time.sleep(0.01)
        wall_second = run(host, bundle, root / "w2", False)
        if wall_first == wall_second:
            sys.stderr.write("wall-clock control did not differ\n")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
