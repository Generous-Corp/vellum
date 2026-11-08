#!/usr/bin/env python3
"""Hold the host C ABI dylib to exactly the functions its header declares."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOWED_INCLUDES = {"stddef.h", "stdint.h"}


def declared_functions(header: str) -> set[str]:
    return set(re.findall(r"VELLUM_HOST_API\s+[^;(]*?\b(vellum_host_\w+)\s*\(", header))


def exported_symbols(library: Path) -> set[str]:
    output = subprocess.run(
        ["nm", "-gU", "-j", str(library)], check=True, capture_output=True, text=True
    ).stdout
    return {line.strip().removeprefix("_") for line in output.splitlines() if line.strip()}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check_exports.py LIBRARY HEADER", file=sys.stderr)
        return 2
    library = Path(argv[1])
    header = Path(argv[2]).read_text(encoding="utf-8")
    errors: list[str] = []
    includes = set(re.findall(r"#\s*include\s*[<\"]([^>\"]+)[>\"]", header))
    if includes - ALLOWED_INCLUDES:
        errors.append(f"header includes more than the C standard headers: {sorted(includes)}")
    if re.search(r"\b(class|namespace|template|std::)\b", header):
        errors.append("header contains C++ constructs")
    declared = declared_functions(header)
    if not declared:
        errors.append("header declares no functions")
    exported = exported_symbols(library)
    if exported != declared:
        extra = sorted(exported - declared)
        missing = sorted(declared - exported)
        errors.append(f"exports differ from the header: extra={extra[:20]} missing={missing}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"vellum-host-c exports: {len(exported)} functions, all declared by the header")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
