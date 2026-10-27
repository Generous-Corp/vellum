#!/usr/bin/env python3
"""Fail when a Mach-O image binds a libc++ symbol its declared minimum macOS lacks.

libc++.dylib exports the floating-point std::to_chars overloads (which
std::format uses for floating-point values) only from macOS 13.3. An image
that declares an older minimum and binds one of them strongly loads on
13.0-13.2 only to fail with dyld "Symbol not found". Defining
_LIBCPP_DISABLE_AVAILABILITY hides exactly this from the compiler, so the
built images have to be read.

Usage: check_libcxx_availability.py IMAGE [IMAGE ...]
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

# std::__1::to_chars(char*, char*, float|double|long double[, ...]): the Itanium
# spelling is __ZNSt3__18to_charsEPcS0_ followed by f, d or e.
FLOAT_TO_CHARS = re.compile(r"^_?_ZNSt3__18to_charsEPcS0_[fde]")
FLOAT_TO_CHARS_MINIMUM = (13, 3)
MINOS = re.compile(r"^\s*minos\s+(\d+)\.(\d+)(?:\.\d+)?\s*$", re.MULTILINE)


def minimum_os(load_commands: str) -> tuple[int, int] | None:
    """The image's LC_BUILD_VERSION minos, or None when it declares none."""
    found = MINOS.search(load_commands)
    return (int(found[1]), int(found[2])) if found else None


def strong_libcxx_imports(listing: str) -> list[str]:
    """Names `nm -m -u` lists as strong (non-weak) imports from libc++."""
    names = []
    for line in listing.splitlines():
        if "(from libc++)" not in line or "weak" in line:
            continue
        fields = line.split()
        if "external" in fields:
            names.append(fields[fields.index("external") + 1])
    return names


def problems(minos: tuple[int, int] | None, imports: list[str]) -> list[str]:
    if minos is None:
        return ["declares no LC_BUILD_VERSION minimum"]
    if minos >= FLOAT_TO_CHARS_MINIMUM:
        return []
    bad = sorted(name for name in imports if FLOAT_TO_CHARS.match(name))
    floor = ".".join(map(str, FLOAT_TO_CHARS_MINIMUM))
    return [f"minos {minos[0]}.{minos[1]} binds {name}, which libc++ exports only from macOS {floor}"
            for name in bad]


def run(command: list[str]) -> str:
    proc = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed: {proc.stderr.strip()}")
    return proc.stdout


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    if not (shutil.which("nm") and shutil.which("otool")):
        print("check_libcxx_availability: nm and otool are required", file=sys.stderr)
        return 2
    failures = []
    for image in argv[1:]:
        try:
            load_commands = run(["otool", "-l", image])
            listing = run(["nm", "-m", "-u", image])
        except RuntimeError as error:
            print(f"check_libcxx_availability: {error}", file=sys.stderr)
            return 2
        failures += [f"{image}: {line}" for line in problems(minimum_os(load_commands),
                                                             strong_libcxx_imports(listing))]
    if failures:
        print("an image binds libc++ symbols newer than its declared minimum macOS:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"{len(argv) - 1} image(s) bind no libc++ symbol newer than their minimum macOS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
