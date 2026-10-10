#!/usr/bin/env python3
import json, sys
from pathlib import Path
p = Path(sys.argv[1]); d = json.loads(p.read_text())
assert d.get("schema") == "pulp.vellum.export-receipt.v2"
assert d.get("dry_run") is True, "mirror package must remain explicitly dry_run"
assert d.get("pulp_sha") == "7ec59a0879147e7ebc3932277c99e7b8acd62336"
assert {u["name"] for u in d.get("units", [])} == {"foundation", "canvas"}
assert len(d.get("files", [])) == 214
assert all(row["mirror_path"].startswith("mirror/") for row in d["files"])
print(f"mirror_receipt=PASS dry_run=true files={len(d['files'])}")
