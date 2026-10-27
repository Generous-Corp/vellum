#!/usr/bin/env python3
"""Fail when a library defines its own copy of a standard exception's typeinfo.

A translation unit compiled with -fno-rtti that throws a standard exception
emits a hidden, private copy of that exception's typeinfo instead of
referencing the C++ library's. Code elsewhere in the image that catches the
exception compares against the library's typeinfo, so the catch can miss.
Pulp builds yogacore with -frtti for this reason, and the mirror build does
the same; this check reads the built archive to prove it took effect.

Usage: check_shared_exception_typeinfo.py LIBRARY [LIBRARY ...]
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

# typeinfo (_ZTI) and typeinfo-name (_ZTS) of the std exception hierarchy a
# library can throw. std::__1::bad_function_call is deliberately absent: libc++
# emits it hidden from its own headers whatever the RTTI setting.
EXCEPTION_TYPEINFO = re.compile(
    r"^_?_ZT[IS]St(?:9exception|11logic_error|13runtime_error|12out_of_range|12length_error"
    r"|16invalid_argument|9bad_alloc|20bad_array_new_length)$"
)


def darwin_definitions(listing: str) -> list[str]:
    """Names `nm -m` lists as defined (not `(undefined)`)."""
    found = []
    for line in listing.splitlines():
        if "(undefined)" in line or not line.strip():
            continue
        name = line.split()[-1]
        if EXCEPTION_TYPEINFO.match(name):
            found.append(line.strip())
    return found


def posix_definitions(listing: str) -> list[str]:
    """Names `nm -P` lists with a defined type (anything but U, w, v)."""
    found = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] not in {"U", "w", "v"} and EXCEPTION_TYPEINFO.match(fields[0]):
            found.append(line.strip())
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    nm = shutil.which("nm")
    if not nm:
        print("check_shared_exception_typeinfo: nm is required", file=sys.stderr)
        return 2
    darwin = sys.platform == "darwin"
    failures = []
    for library in argv[1:]:
        command = [nm, "-m", library] if darwin else [nm, "-P", "-A", library]
        proc = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
        if proc.returncode != 0:
            print(f"check_shared_exception_typeinfo: {' '.join(command)} failed: {proc.stderr.strip()}",
                  file=sys.stderr)
            return 2
        listing = proc.stdout
        if not listing.strip():
            print(f"check_shared_exception_typeinfo: {library} lists no symbols", file=sys.stderr)
            return 2
        if not darwin:
            listing = "\n".join(line.split(": ", 1)[-1] for line in listing.splitlines())
        found = darwin_definitions(listing) if darwin else posix_definitions(listing)
        failures += [f"{library}: {line}" for line in found]
    if failures:
        print("a library defines its own standard exception typeinfo (compiled without RTTI?):", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"no local standard exception typeinfo in {len(argv) - 1} librar{'y' if len(argv) == 2 else 'ies'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
