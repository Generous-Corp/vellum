#!/usr/bin/env python3
"""Fail-closed manifest sanity check for the full-extraction rehearsal."""
from __future__ import annotations
import json
import pathlib
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: test_mirror_rehearsal_manifest.py CLOSURE_DIR MIRROR_ROOT")
closure_dir = pathlib.Path(sys.argv[1])
mirror_root = pathlib.Path(sys.argv[2])
manifests = sorted(closure_dir.glob("*.json"))
if not manifests:
    raise SystemExit("no closure manifests found")
results = []
for manifest in manifests:
    data = json.loads(manifest.read_text())
    missing = []
    sources = 0
    for target in data.get("targets", []):
        for source in target.get("sources", []):
            p = pathlib.Path(source)
            if p.is_absolute():
                continue
            if p.suffix in {".c", ".cc", ".cpp", ".cxx", ".m", ".mm"}:
                sources += 1
                if not (mirror_root / p).exists():
                    missing.append(str(p))
    status = "PASS" if sources and not missing else "FAIL"
    results.append((manifest.stem, status, sources, missing))
for unit, status, sources, missing in results:
    print(f"rehearsal_unit={unit} status={status} sources={sources}")
    if missing:
        print("missing=" + ",".join(missing), file=sys.stderr)
if any(status == "FAIL" for _, status, _, _ in results):
    raise SystemExit(1)
