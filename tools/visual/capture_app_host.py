#!/usr/bin/env python3
"""Headless vellum-app-host capture for the visual runner's command driver.

Runs a bundle offscreen, optionally pressing nodes first (a simulated click on
the node's press action), and writes the result to stdout: a PNG of the final
frame, or the tree dump (node ids and boxes) as JSON.

    capture_app_host.py --bundle BUNDLE [--press NODE]... [--scale 1|2]
                        [--output png|tree]

The host binary comes from the VELLUM_APP_HOST environment variable so a
fixture file stays machine independent. Every path handed to the host is made
absolute first: the host cannot create a capture directory from a bare file
name. A host refusal (an unpressable node, a frame that fails the content
floor) exits non-zero with the host's own message, never an empty image.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOST_ENV = "VELLUM_APP_HOST"
TIMEOUT_SECONDS = 90


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--press", action="append", default=[], metavar="NODE")
    parser.add_argument("--scale", choices=("1", "2"), default="1")
    parser.add_argument("--output", choices=("png", "tree"), default="png")
    return parser.parse_args(argv)


def capture(args: argparse.Namespace) -> bytes:
    host = os.environ.get(HOST_ENV, "")
    if not host or not Path(host).is_file():
        raise SystemExit(f"error: set {HOST_ENV} to a vellum-app-host binary")
    bundle = args.bundle.resolve()
    if not bundle.is_file():
        raise SystemExit(f"error: bundle not found: {bundle}")
    with tempfile.TemporaryDirectory(prefix="vellum-capture-") as scratch:
        out = Path(scratch).resolve() / ("frame.png" if args.output == "png" else "tree.json")
        command = [host, "--bundle", str(bundle), "--no-window"]
        for node in args.press:
            command += ["--press", node]
        if args.output == "png":
            command += ["--capture-scale", args.scale, "--capture", str(out)]
        else:
            command += ["--dump-tree", str(out)]
        environment = dict(os.environ, MTL_DEBUG_LAYER="0")
        try:
            done = subprocess.run(command, check=False, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS, env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SystemExit(f"error: host did not run: {error}") from error
        if done.returncode != 0:
            raise SystemExit(f"error: host exited {done.returncode}: {done.stderr.strip()}")
        if not out.is_file() or out.stat().st_size == 0:
            raise SystemExit("error: host reported success but wrote no capture")
        return out.read_bytes()


def main(argv: list[str]) -> int:
    sys.stdout.buffer.write(capture(parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
