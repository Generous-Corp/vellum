#!/usr/bin/env python3
"""Reject accidental embedded JavaScript engines in Vellum's web Wasm."""

from __future__ import annotations

import argparse
from pathlib import Path


FORBIDDEN = (b"JavaScriptCore", b"QuickJS", b"libquickjs", b"v8::Isolate", b"libv8")


def violations(payload: bytes) -> list[str]:
    return [pattern.decode("ascii") for pattern in FORBIDDEN if pattern in payload]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wasm", nargs="?", type=Path)
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    if args.negative_control:
        found = violations(b"wasm fixture embeds JavaScriptCore by mistake")
        if found != ["JavaScriptCore"]:
            raise SystemExit("negative control did not trigger the detector")
        print("negative control rejected JavaScriptCore sentinel")
        return 0
    if args.wasm is None or not args.wasm.is_file():
        raise SystemExit("a built .wasm file is required")
    found = violations(args.wasm.read_bytes())
    if found:
        raise SystemExit(f"embedded JavaScript engine marker(s): {', '.join(found)}")
    print(f"no embedded JavaScript engine markers in {args.wasm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
