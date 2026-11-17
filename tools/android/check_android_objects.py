#!/usr/bin/env python3
"""Check that an Android build produced AArch64 objects and the probe symbols.

A build that silently used the host compiler would also "succeed", so the check
reads the ELF header of every object and archive member in the build directory
and refuses anything that is not a 64-bit little-endian AArch64 object. It then
requires one object per source the CMake file selected, and the probe symbols
named with --require-symbol (read with llvm-nm).

Usage: check_android_objects.py --build-dir DIR --expect-sources N
           [--nm PATH --require-symbol NAME ...]
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
ELFCLASS64 = 2
ELFDATA2LSB = 1
EM_AARCH64 = 183
EM_X86_64 = 62


def classify(header: bytes) -> str:
    """Return "ok" for an AArch64 ELF64 LE object, else a description."""
    if len(header) < 20:
        return "shorter than an ELF header"
    if header[:4] != ELF_MAGIC:
        return f"not ELF (magic {header[:4].hex()})"
    if header[4] != ELFCLASS64:
        return "not ELF64"
    if header[5] != ELFDATA2LSB:
        return "not little-endian"
    (machine,) = struct.unpack_from("<H", header, 18)
    if machine != EM_AARCH64:
        name = "x86_64" if machine == EM_X86_64 else f"e_machine {machine}"
        return f"wrong architecture ({name})"
    return "ok"


def object_files(build_dir: Path) -> list[Path]:
    """Objects of build targets (inside a <target>.dir), not CMake's compiler probes."""
    return sorted(path for path in build_dir.rglob("*.o")
                  if any(part.endswith(".dir") for part in path.relative_to(build_dir).parts))


def check_objects(build_dir: Path, expected: int) -> list[str]:
    problems: list[str] = []
    objects = object_files(build_dir)
    if len(objects) != expected:
        problems.append(f"expected {expected} objects, found {len(objects)}")
    for path in objects:
        verdict = classify(path.read_bytes()[:20])
        if verdict != "ok":
            problems.append(f"{path.relative_to(build_dir)}: {verdict}")
    return problems


def check_symbols(build_dir: Path, nm: str, required: list[str]) -> list[str]:
    objects = object_files(build_dir)
    defined: set[str] = set()
    for path in objects:
        out = subprocess.run(
            [nm, "--defined-only", str(path)], capture_output=True, text=True, check=False
        )
        if out.returncode != 0:
            return [f"{nm} failed on {path.name}: {out.stderr.strip()}"]
        defined.update(line.split()[-1] for line in out.stdout.splitlines() if line.strip())
    return [f"symbol {name} is not defined by any object" for name in required
            if name not in defined]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--expect-sources", type=int, required=True)
    parser.add_argument("--nm")
    parser.add_argument("--require-symbol", action="append", default=[])
    args = parser.parse_args(argv)
    if args.require_symbol and not args.nm:
        parser.error("--require-symbol needs --nm")
    problems = check_objects(args.build_dir, args.expect_sources)
    if args.require_symbol:
        problems += check_symbols(args.build_dir, args.nm, args.require_symbol)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK {args.expect_sources} AArch64 objects"
          + (f", {len(args.require_symbol)} symbols" if args.require_symbol else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
