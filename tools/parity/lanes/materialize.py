#!/usr/bin/env python3
"""Materialize a staged parity input at the implementation's declared name."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: materialize.py SOURCE DEST", file=sys.stderr)
        return 2
    source, destination = map(Path, sys.argv[1:])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
