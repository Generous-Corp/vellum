#!/usr/bin/env python3
"""Write the list of names a mirror image leaves unexported.

    third_party_unexports.py --out LIST [--extra PATTERNS] ARCHIVE...

Every external symbol the third-party archives define (`nm -g -m`) is written
to LIST, one name per line, followed by the lines of --extra (patterns for the
mirror's own vendored objects, with `#` comments dropped). ld reads LIST as
`-unexported_symbols_list`. The output is sorted and deduplicated, so it is
stable between builds. A run that reads no archive or finds no symbol exits 1,
because an empty list would unexport nothing and look like success.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

_NM_LINE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<section>[^)]*)\)\s+(?P<attrs>.*?)\s*(?P<name>\S+)$")
# ld reads these as pattern characters, so a name holding one is escaped.
_GLOB = re.compile(r"([*?\[\]\\])")


def run_nm(archive: Path) -> str:
    done = subprocess.run(["nm", "-g", "-m", str(archive)], capture_output=True, text=True, errors="replace",
                          check=False)
    if done.returncode:
        raise RuntimeError(f"nm failed for {archive}: {done.stderr.strip()}")
    return done.stdout


def defined_external(listing: str) -> set[str]:
    """Names an `nm -g -m` listing defines with external visibility."""
    names: set[str] = set()
    for line in listing.splitlines():
        match = _NM_LINE.match(line)
        if match and match["section"] != "undefined" and "external" in match["attrs"].split():
            names.add(match["name"])
    return names


def build(archives: list[Path], extra: str = "", nm: Callable[[Path], str] | None = None) -> list[str]:
    nm = nm or run_nm
    names: set[str] = set()
    for archive in archives:
        names |= {_GLOB.sub(r"\\\1", name) for name in defined_external(nm(archive))}
    patterns = [line.strip() for line in extra.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not names:
        raise RuntimeError("the archives define no external symbol")
    return sorted(names | set(patterns))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--extra", type=Path)
    args = parser.parse_args(argv)
    try:
        lines = build(args.archives, args.extra.read_text() if args.extra else "")
    except (RuntimeError, OSError) as error:
        print(f"third_party_unexports: {error}", file=sys.stderr)
        return 1
    args.out.write_text("\n".join(lines) + "\n")
    print(f"third_party_unexports: {len(lines)} names from {len(args.archives)} archives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
