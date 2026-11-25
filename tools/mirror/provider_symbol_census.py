#!/usr/bin/env python3
"""Check the installed Skia/Dawn provider's export and bind tables."""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
from pathlib import Path
import re
import subprocess
import sys


# Include vendor types embedded in template names, such as libc++ helpers
# instantiated by Tint. Vellum's own API can name vendor types in signatures.
PRIVATE_NAMES = re.compile(r"4tint|4dawn6native|5skgpu|4SkSL|4absl")
OWNED_BY_VENDOR = re.compile(
    r"^__Z(?:N[KRVO]*|T[IVS]N|TTN)(?:4tint|4dawn6native|5skgpu|4SkSL|4absl)"
)
IMPORT = re.compile(r"^\s*0x[0-9a-fA-F]+\s+(\S+)\s+\(from (.+)\)$", re.MULTILINE)
PLANTED = {
    "__ZN4tint7plantedEv",
    "__ZN4dawn6native7plantedEv",
    "__ZN5skgpu7plantedEv",
    "__ZN4SkSL7plantedEv",
    "__ZN4absl7plantedEv",
}


def run(*args: str | Path) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", type=Path)
    parser.add_argument("export_list", type=Path)
    parser.add_argument("--plant", action="store_true")
    args = parser.parse_args()

    patterns = [line.strip() for line in args.export_list.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    exports = set(run("nm", "-j", "-gU", args.provider).splitlines())
    if not patterns or not any("register_native_dawn_bootstrap" in name for name in exports):
        raise RuntimeError("provider export census could not see the allowlist and bootstrap")
    if args.plant:
        exports.update(PLANTED)

    def allowed(name: str) -> bool:
        return not OWNED_BY_VENDOR.search(name) and any(
            fnmatchcase(name, pattern) for pattern in patterns
        )

    unexpected = sorted(name for name in exports if not allowed(name))
    # dyld_info reads both chained fixups and classic bind opcodes. The
    # provider's private definitions require local relocations.
    imports = IMPORT.findall(run("dyld_info", "-imports", args.provider))
    if not imports:
        raise RuntimeError("provider import census could not see any dynamic binds")
    defined = set(run("nm", "-jU", args.provider).splitlines())
    private_binds = sorted(f"{name} (from {owner})" for name, owner in imports
                           if not allowed(name) and
                           (name in defined or PRIVATE_NAMES.search(name)))
    if unexpected or private_binds:
        for label, names in (("exports", unexpected), ("binds", private_binds)):
            if names:
                print(f"provider has {len(names)} unexpected {label}: {', '.join(names[:8])}",
                      file=sys.stderr)
        return 1
    print("installed provider export and bind census: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
