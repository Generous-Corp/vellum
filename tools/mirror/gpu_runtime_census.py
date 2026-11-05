#!/usr/bin/env python3
"""One gpu-runtime per process: census the images an installed package ships.

The mirror links Vellum's in-tree gpu-runtime into vellum-skia-dawn-provider.
Any other image that carries its own copy of the bootstrap coordinator or of
Dawn's proc table would give one process two coordinators and two proc tables,
and the first registration in each would win separately. gpu-runtime links
with hidden symbols, so a second copy shows up only as a local definition:
this census reads every symbol (`nm -P`), not only exported ones.

Exit 0 when each marker is defined in exactly one image, 1 otherwise.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

# Defined only by gpu-runtime's bootstrap coordinator and by Dawn's proc-table
# setter. A marker found in no image means the census read the wrong files.
MARKERS = {
    "gpu-runtime coordinator": "__ZN11gpu_runtime23register_dawn_bootstrap",
    "Dawn proc table": "_dawnProcSetProcs",
}
UNDEFINED = {"U", "u", "w", "v"}


def defined_symbols(listing: str) -> set[str]:
    """Names an `nm -P` listing defines, local or external."""
    names: set[str] = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and len(fields[1]) == 1 and fields[1] not in UNDEFINED:
            names.add(fields[0])
    return names


def run_nm(image: Path) -> str:
    completed = subprocess.run(["nm", "-P", str(image)], capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"nm failed for {image}: {completed.stderr.strip()}")
    return completed.stdout


def census(images: list[Path], nm: Callable[[Path], str] = run_nm) -> list[str]:
    """Findings: each marker must be defined by exactly one image."""
    owners: dict[str, list[str]] = {label: [] for label in MARKERS}
    for image in images:
        names = defined_symbols(nm(image))
        for label, marker in MARKERS.items():
            if any(name == marker or name.startswith(marker) for name in names):
                owners[label].append(str(image))
    findings = []
    for label, found in owners.items():
        if len(found) != 1:
            findings.append(f"{label} is defined by {len(found)} images, expected 1: {found}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args(argv)
    findings = census(args.images)
    for finding in findings:
        print(f"gpu-runtime census: {finding}", file=sys.stderr)
    if not findings:
        print(f"gpu-runtime census: one coordinator and one Dawn proc table in {len(args.images)} images")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
