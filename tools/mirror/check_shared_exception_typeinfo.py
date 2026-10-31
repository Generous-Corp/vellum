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

# typeinfo (_ZTI) and typeinfo-name (_ZTS) of a non-template, non-nested std
# class, spelled outside libc++'s inline namespace (St<len><name>) or inside it
# (NSt3__1<len><name>E). The name is length-prefixed, so it must end exactly
# where its length says; a template (I...E) or nested name does not qualify.
# libc++ emits std::__1::bad_function_call and std::__1::format_error hidden
# from its own headers whatever the RTTI setting (RTTI-on Pulp code carries
# them too); their bases still resolve to the library's shared typeinfo, so a
# catch of std::exception still matches. Those two are excluded.
STD_TYPEINFO = re.compile(r"^_?_ZT[IS](?P<scope>St|NSt3__1)(?P<length>\d+)(?P<rest>.*)$")
LIBCXX_HIDDEN = frozenset({"bad_function_call", "format_error"})


def is_local_std_typeinfo(name: str) -> bool:
    match = STD_TYPEINFO.match(name)
    if not match:
        return False
    length, rest = int(match["length"]), match["rest"]
    class_name, tail = rest[:length], rest[length:]
    if len(class_name) != length or not re.fullmatch(r"[A-Za-z_]\w*", class_name):
        return False
    if tail != ("" if match["scope"] == "St" else "E"):
        return False
    return class_name not in LIBCXX_HIDDEN


def darwin_definitions(listing: str) -> list[str]:
    """Names `nm -m` lists as defined (not `(undefined)`)."""
    found = []
    for line in listing.splitlines():
        if "(undefined)" in line or not line.strip():
            continue
        name = line.split()[-1]
        if is_local_std_typeinfo(name):
            found.append(line.strip())
    return found


def posix_definitions(listing: str) -> list[str]:
    """Names `nm -P` lists with a defined type (anything but U, w, v)."""
    found = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] not in {"U", "w", "v"} and is_local_std_typeinfo(fields[0]):
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
