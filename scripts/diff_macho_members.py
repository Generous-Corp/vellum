#!/usr/bin/env python3
"""Say where two builds of the same SDK archive member differ inside the Mach-O.

The SDK release builds its archive twice and requires byte equality. When a
library member differs, the archive diff can only name the member. This
extracts both copies of every differing Mach-O member into OUTDIR (so the job
can keep them) and prints, for each one:

- the load commands whose text differs (LC_UUID first, since a UUID that
  differs while every section matches points at the linker, not the code);
- each section whose bytes differ, by segment, section and size;
- whether the symbol tables list the same names in the same order.

Usage: diff_macho_members.py FIRST.tar.gz SECOND.tar.gz OUTDIR
It exits 0 after reporting; the caller has already decided the build failed.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tarfile
from pathlib import Path

MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf", b"\xbe\xba\xfe\xca"}
SECTION = re.compile(
    r"^\s*sectname (?P<sect>\S+)\n\s*segname (?P<seg>\S+)\n\s*addr \S+\n\s*size (?P<size>0x[0-9a-f]+)\n"
    r"\s*offset (?P<offset>\d+)", re.M)


def members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path) as handle:
        return {m.name: handle.extractfile(m).read() for m in handle.getmembers() if m.isfile()}


def run(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, errors="replace", check=False).stdout


def sections(load_commands: str) -> dict[tuple[str, str], tuple[int, int]]:
    """(segment, section) -> (file offset, size) for sections with file contents."""
    found = {}
    for match in SECTION.finditer(load_commands):
        offset, size = int(match["offset"]), int(match["size"], 16)
        if offset:
            found[(match["seg"], match["sect"])] = (offset, size)
    return found


def load_command_blocks(load_commands: str) -> dict[str, list[str]]:
    """Load-command text grouped by command name, with offsets and sizes left in."""
    blocks: dict[str, list[str]] = {}
    for block in re.split(r"\nLoad command \d+\n", load_commands)[1:]:
        name = re.search(r"cmd (\S+)", block)
        blocks.setdefault(name[1] if name else "?", []).append(block.strip())
    return blocks


def report(name: str, first: Path, second: Path) -> list[str]:
    lines = [f"{name}:"]
    a_lc, b_lc = run("otool", "-l", str(first)), run("otool", "-l", str(second))
    a_blocks, b_blocks = load_command_blocks(a_lc), load_command_blocks(b_lc)
    changed = sorted(cmd for cmd in set(a_blocks) | set(b_blocks) if a_blocks.get(cmd) != b_blocks.get(cmd))
    changed.sort(key=lambda cmd: cmd != "LC_UUID")
    lines.append(f"  load commands that differ: {', '.join(changed) or 'none'}")
    a_bytes, b_bytes = first.read_bytes(), second.read_bytes()
    a_sections, b_sections = sections(a_lc), sections(b_lc)
    differing = []
    for key in sorted(set(a_sections) | set(b_sections)):
        if key not in a_sections or key not in b_sections:
            differing.append(f"{key[0]},{key[1]} (only in one build)")
            continue
        (ao, asz), (bo, bsz) = a_sections[key], b_sections[key]
        if hashlib.sha256(a_bytes[ao:ao + asz]).digest() != hashlib.sha256(b_bytes[bo:bo + bsz]).digest():
            differing.append(f"{key[0]},{key[1]} ({asz} vs {bsz} bytes)")
    lines.append(f"  sections whose bytes differ: {', '.join(differing) or 'none'}")
    a_names, b_names = run("nm", "-j", str(first)).splitlines(), run("nm", "-j", str(second)).splitlines()
    if a_names == b_names:
        lines.append(f"  symbol tables: identical ({len(a_names)} names)")
    elif sorted(a_names) == sorted(b_names):
        lines.append(f"  symbol tables: same {len(a_names)} names, different order")
    else:
        lines.append(f"  symbol tables: different names ({len(set(a_names) ^ set(b_names))} not shared)")
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    first, second, outdir = Path(argv[1]), Path(argv[2]), Path(argv[3])
    a, b = members(first), members(second)
    for name in sorted(set(a) & set(b)):
        if a[name] == b[name] or a[name][:4] not in MACHO_MAGIC:
            continue
        base = outdir / Path(name).name
        base.mkdir(parents=True, exist_ok=True)
        (base / "first").write_bytes(a[name])
        (base / "second").write_bytes(b[name])
        print("\n".join(report(name, base / "first", base / "second")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
