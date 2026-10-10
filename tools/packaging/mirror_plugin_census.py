#!/usr/bin/env python3
"""Census archives used by the generated plugin-shaped rehearsal consumer."""
from __future__ import annotations
import pathlib, subprocess, sys
if len(sys.argv) < 2:
    raise SystemExit("usage: mirror_plugin_census.py ARCHIVE...")
archives = [pathlib.Path(p) for p in sys.argv[1:]]
seen = {}
provider_archives = set()
for archive in archives:
    if not archive.exists(): raise SystemExit(f"missing archive: {archive}")
    out = subprocess.check_output(["nm", "-gU", str(archive)], text=True, errors="replace")
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-2] not in {"U", "w", "W"}:
            symbol = fields[-1]
            if "pulp" in symbol: seen.setdefault(symbol, set()).add(str(archive))
            if "dawn" in symbol.lower() or "webgpu" in symbol.lower(): provider_archives.add(str(archive))
for symbol, owners in sorted(seen.items()):
    if len(owners) > 1: raise SystemExit(f"duplicate symbol {symbol}: {sorted(owners)}")
if len(provider_archives) > 1:
    raise SystemExit(f"multiple Dawn/WebGPU provider archives: {sorted(provider_archives)}")
print(f"plugin_census=PASS archives={len(archives)} pulp_symbols={len(seen)} dawn_provider_archives={len(provider_archives)}")
