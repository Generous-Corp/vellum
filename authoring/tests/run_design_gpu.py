#!/usr/bin/env python3
"""Materialize a DesignIR document with Node, then draw it on the GPU."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: run_design_gpu.py <node> <tree script> <gpu test>", file=sys.stderr)
        return 2
    node, script, binary = sys.argv[1:]
    with tempfile.TemporaryDirectory() as directory:
        composited = Path(directory) / "composited.json"
        plain = Path(directory) / "plain.json"
        subprocess.run([node, script, str(composited), str(plain)], check=True)
        return subprocess.run([binary, str(composited), str(plain)]).returncode


if __name__ == "__main__":
    sys.exit(main())
