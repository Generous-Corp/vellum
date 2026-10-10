#!/usr/bin/env python3
"""Dry-run census: each mirror archive must own each global symbol once."""
from __future__ import annotations
import collections
import pathlib
import subprocess
import sys

archives = [pathlib.Path(p) for p in sys.argv[1:]]
if not archives:
    raise SystemExit("usage: mirror_odr_census.py ARCHIVE...")
owners = collections.defaultdict(list)
for archive in archives:
    if not archive.exists():
        raise SystemExit(f"missing archive: {archive}")
    out = subprocess.check_output(["nm", "-gU", str(archive)], text=True, errors="replace")
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-2] not in {"U", "w", "W"} and "pulp" in fields[-1]:
            owners[fields[-1]].append(str(archive))
duplicates = {name: sorted(set(paths)) for name, paths in owners.items() if len(set(paths)) > 1}
if duplicates:
    for name, paths in sorted(duplicates.items()):
        print(f"duplicate symbol {name}: {paths}", file=sys.stderr)
    raise SystemExit(1)
print(f"odr_census=PASS symbols={len(owners)} archives={len(archives)}")
