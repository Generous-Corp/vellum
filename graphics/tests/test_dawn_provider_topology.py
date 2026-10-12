#!/usr/bin/env python3
"""Prove Dawn has one definition owner in the Vellum host topology."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def global_symbols(path: Path) -> str:
    return subprocess.run(
        ["nm", "-gU", str(path)], check=True, capture_output=True, text=True
    ).stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_dawn_provider_topology.py <host> <vellum-gpu>")
    host, provider = map(Path, sys.argv[1:])
    host_symbols = global_symbols(host)
    provider_symbols = global_symbols(provider)
    for symbol in ("_dawnProcSetProcs", "_dawnProcGetVersion"):
        require(symbol not in host_symbols,
                f"host unexpectedly defines its own Dawn symbol: {symbol}")
        require(symbol in provider_symbols,
                f"vellum-gpu does not own Dawn symbol: {symbol}")
    require("GetProcs" not in host_symbols,
            "host unexpectedly defines dawn::native::GetProcs")
    require("GetProcs" in provider_symbols,
            "vellum-gpu does not own dawn::native::GetProcs")
    require("register_dawn_bootstrap" not in host_symbols,
            "host unexpectedly owns a second Dawn bootstrap coordinator")
    require("register_dawn_bootstrap" in provider_symbols,
            "vellum-gpu does not own the Dawn bootstrap coordinator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
