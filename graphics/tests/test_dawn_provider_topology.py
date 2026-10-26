#!/usr/bin/env python3
"""Prove one provider group owns both Vellum frontends' GPU definitions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def defined_symbols(path: Path, *, exported: bool = False) -> set[str]:
    # Include private definitions: an export list must not hide a second copy.
    output = subprocess.run(
        ["nm", "-gU" if exported else "-U", str(path)], check=True, capture_output=True, text=True
    ).stdout
    return {line.split()[-1] for line in output.splitlines() if line.split()}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    if len(sys.argv) < 4:
        raise SystemExit(
            "usage: test_dawn_provider_topology.py <host> <gpu> <provider> [<image> ...]"
        )
    host, gpu, provider, *mirror = map(Path, sys.argv[1:])
    symbols = {path: defined_symbols(path) for path in (host, gpu, provider, *mirror)}
    # Concrete non-inline entry points from both declaration families. The
    # definitions may remain private to the provider dylib; they must not be
    # required to cross a frontend dylib boundary.
    for symbol in ("_dawnProcSetProcs", "_dawnProcGetVersion", "__ZN6SkFont7setSizeEf"):
        require(symbol in symbols[provider], f"provider does not own {symbol}")
        for path in (host, gpu, *mirror):
            require(symbol not in symbols[path], f"{path.name} defines a second {symbol}")
    for path, definitions in symbols.items():
        owns_native = any("GetProcs" in symbol for symbol in definitions)
        require(owns_native == (path == provider),
                f"{path.name} has incorrect dawn::native::GetProcs ownership")
        owns_bootstrap = any("register_dawn_bootstrap" in symbol for symbol in definitions)
        require(owns_bootstrap == (path == provider),
                f"{path.name} has incorrect Dawn bootstrap coordinator ownership")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
