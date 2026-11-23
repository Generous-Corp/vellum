#!/usr/bin/env python3
"""Extract the Metal source literal used by the desktop fallback renderer."""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: extract_metal_source.py <header> <metal-source>")
    header = Path(sys.argv[1]).read_text()
    start_marker = 'R"msl(\n'
    end_marker = '\n)msl";'
    start = header.index(start_marker) + len(start_marker)
    end = header.index(end_marker, start)
    Path(sys.argv[2]).write_text(header[start:end] + "\n")


if __name__ == "__main__":
    main()
