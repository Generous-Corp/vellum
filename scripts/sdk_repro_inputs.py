#!/usr/bin/env python3
"""Decide whether a change can alter how the installed SDK is linked.

    sdk_repro_inputs.py --files0-from PATH   # NUL-separated changed paths
    sdk_repro_inputs.py PATH...

Prints `required` or `not-required` and exits 0; exit 2 means it was invoked
wrongly, and a caller treats anything but `not-required` as `required`.

A pull request or queue run builds the SDK once unless a changed path can
change the bytes two builds would disagree on: the CMake and toolchain files,
the Skia/Dawn provider and the renderer lock, the SDK builder and comparer,
the mirror packaging, and the workflow. An empty or unreadable list is
`required`, as is any path this file does not recognise as safe by being
absent from these rules. Every push to main, release tag, nightly and
dispatch builds twice whatever this says.

The workflow runs the copy in the base (the merge ref's first parent, or a
queue entry's `merge_group.base_sha`), never the change's own, so a change
cannot narrow the rules that judge it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED = (
    re.compile(r"CMakeLists\.txt"),
    re.compile(r".*\.cmake"),
    re.compile(r"cmake/.*"),
    re.compile(r"gpu-runtime/.*"),
    re.compile(r"tools/mirror/.*"),
    re.compile(r".*toolchain.*", re.IGNORECASE),
    re.compile(r".*renderer-lock.*", re.IGNORECASE),
    re.compile(r"\.github/.*"),
    re.compile(r"scripts/(build_sdk_artifact|compare_sdk_archives|install_core"
               r"|build_gpu_runtime_artifact|check_renderer_lock|sdk_repro_inputs)\.py"),
)


def required(paths: list[str]) -> tuple[bool, str]:
    """Return (required, reason)."""
    if not paths:
        return True, "no changed paths"
    for path in paths:
        if not path or path.startswith("/") or ".." in path.split("/"):
            return True, f"unrecognised path {path!r}"
        if any(pattern.fullmatch(path) for pattern in REQUIRED):
            return True, f"{path} can change the link or toolchain inputs"
    return False, "no changed path can change the link or toolchain inputs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files0-from", type=Path)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    if args.files0_from is not None:
        try:
            raw = args.files0_from.read_bytes()
            paths = [item.decode("utf-8") for item in raw.split(b"\0") if item]
        except (OSError, UnicodeDecodeError) as error:
            print(f"sdk-repro-inputs: {error}", file=sys.stderr)
            return 2
    else:
        paths = args.paths
    is_required, reason = required(paths)
    print(f"sdk-repro-inputs: {reason}", file=sys.stderr)
    print("required" if is_required else "not-required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
