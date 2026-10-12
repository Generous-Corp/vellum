#!/usr/bin/env python3
"""Keep Dawn's proc-table setter confined to the designated app-host bootstrap."""

from __future__ import annotations

import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
ALLOWED = {ROOT / "graphics" / "include" / "vellum" / "graphics" / "dawn_native_bootstrap.hpp"}


def main() -> int:
    offenders: list[pathlib.Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".cpp", ".cc", ".cxx", ".mm", ".hpp", ".h"}:
            continue
        if any(part == ".git" or part.startswith("build") for part in path.parts):
            continue
        contents = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\bdawnProcSetProcs\s*\(", contents) and path not in ALLOWED:
            offenders.append(path.relative_to(ROOT))
    if offenders:
        print("unapproved Dawn proc-table setters:", *offenders, sep="\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
