#!/usr/bin/env python3
"""Black-box proof for the native host's bounded state-v1 persistence lane."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def contains(value: object, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, list):
        return any(contains(item, expected) for item in value)
    if isinstance(value, dict):
        return any(contains(item, expected) for item in value.values())
    return False


def invoke(host: str, bundle: str, state: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [host, "--bundle", bundle, "--self-test", "--state-file", str(state), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    host, bundle = sys.argv[1:]
    with tempfile.TemporaryDirectory() as temporary:
        state = Path(temporary) / "state.json"
        written = invoke(host, bundle, state, "--input", "native-title-input", "Persistent title")
        if written.returncode or not state.is_file():
            sys.stderr.write(written.stdout + written.stderr)
            return 1
        first = json.loads(state.read_text(encoding="utf-8"))
        if not contains(first, "Persistent title"):
            return 1

        restored = invoke(host, bundle, state, "--key", "native-title-input", "Backspace")
        if restored.returncode:
            sys.stderr.write(restored.stdout + restored.stderr)
            return 1
        second = json.loads(state.read_text(encoding="utf-8"))
        if not contains(second, "Persistent titl"):
            return 1

        state.write_text("{", encoding="utf-8")
        corrupt = invoke(host, bundle, state)
        if corrupt.returncode == 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
