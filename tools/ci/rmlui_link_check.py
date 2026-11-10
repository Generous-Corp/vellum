#!/usr/bin/env python3
"""Check what a built RmlUi-module binary loads at run time, with `otool -L`.

VellumRmlUi's Core, Native and Metal targets depend only on RmlUi (built in)
and the operating system. A binary built from them must therefore load
nothing but Apple's system frameworks and libraries: no Vellum, Pulp, Skia,
Dawn or WebGPU library, and nothing from Homebrew, /usr/local or an @rpath.

  rmlui_link_check.py --binary build/tests/vellum-rmlui-metal-filters-test \\
      --require Metal.framework

--require names a library each binary must load. It is the control: a check
that reads no libraries at all, or the wrong binary, cannot pass, because the
Metal renderer's binaries must show Metal.framework.

Exit codes: 0 every binary passes; 1 a binary loads a library it must not, or
lacks a required one; 2 otool could not be run, or listed nothing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

ALLOWED_PREFIXES = ("/System/Library/Frameworks/", "/usr/lib/")
FORBIDDEN_NAME = re.compile(r"vellum|pulp|skia|dawn|webgpu|wgpu", re.IGNORECASE)


class OtoolError(Exception):
    pass


def run_otool(binary: Path) -> str:
    try:
        result = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True,
                                check=False)
    except OSError as error:
        raise OtoolError(f"cannot run otool: {error}") from error
    if result.returncode != 0:
        raise OtoolError(f"otool -L {binary} failed: {result.stderr.strip()}")
    return result.stdout


def loaded_libraries(binary: Path, otool_output: str) -> list[str]:
    """The install names otool lists, without the binary's own header line."""
    libraries = []
    for line in otool_output.splitlines():
        if not line.startswith(("\t", " ")):
            continue  # "<path>:" (and "<path> (architecture ...):") headers
        name = line.strip().split(" (compatibility version", 1)[0].strip()
        if name:
            libraries.append(name)
    return libraries


def problems(libraries: list[str], required: list[str]) -> list[str]:
    found = []
    for library in libraries:
        if FORBIDDEN_NAME.search(library):
            found.append(f"loads {library}, which is not a system library")
        elif not library.startswith(ALLOWED_PREFIXES):
            found.append(f"loads {library}, outside {' and '.join(ALLOWED_PREFIXES)}")
    for name in required:
        if not any(Path(library).name == name or f"/{name}/" in library for library in libraries):
            found.append(f"does not load {name}")
    return found


def check(binaries: list[Path], required: list[str],
          otool: Callable[[Path], str] = run_otool) -> int:
    failures = 0
    for binary in binaries:
        if not binary.is_file():
            print(f"{binary}: not a file", file=sys.stderr)
            return 2
        try:
            libraries = loaded_libraries(binary, otool(binary))
        except OtoolError as error:
            print(f"{binary}: {error}", file=sys.stderr)
            return 2
        if not libraries:
            print(f"{binary}: otool listed no libraries; the instrument read nothing",
                  file=sys.stderr)
            return 2
        print(f"{binary}:")
        for library in libraries:
            print(f"  {library}")
        for problem in problems(libraries, required):
            print(f"  FAIL: {problem}")
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--binary", type=Path, action="append", required=True)
    parser.add_argument("--require", action="append", default=[],
                        help="a library every binary must load, e.g. Metal.framework")
    args = parser.parse_args(argv)
    return check(args.binary, args.require)


if __name__ == "__main__":
    raise SystemExit(main())
