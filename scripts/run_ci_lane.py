#!/usr/bin/env python3
"""Run one lane of scripts/ci_lanes.json, in order, stopping at the first failure.

Workflows call this instead of listing test commands by hand, so a test is run
by exactly the lanes the manifest names and scripts/test_ci_lanes.py can prove
that every test file is run somewhere.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/ci_lanes.json"
SCHEMA = "vellum.ci-lanes.v1"


class LaneError(RuntimeError):
    pass


def load(path: Path = MANIFEST) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LaneError(f"{path} is unreadable: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise LaneError(f"{path} does not declare {SCHEMA}")
    if not isinstance(manifest.get("lanes"), dict) or not isinstance(manifest.get("elsewhere"), dict):
        raise LaneError(f"{path} must hold `lanes` and `elsewhere` objects")
    return manifest


def command(entry: dict[str, Any]) -> list[str]:
    """The argv one manifest entry runs, with $VARS in its args expanded."""
    keys = set(entry)
    if keys == {"discover", "pattern"}:
        return [sys.executable, "-m", "unittest", "discover", "-s", entry["discover"], "-p", entry["pattern"]]
    if keys == {"module"}:
        return [sys.executable, "-m", "unittest", entry["module"]]
    if "script" in keys and keys <= {"script", "isolated", "args"}:
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise LaneError(f"{entry}: args must be a list of strings")
        isolated = entry.get("isolated", False)
        if not isinstance(isolated, bool):
            raise LaneError(f"{entry}: isolated must be true or false")
        return [sys.executable, *(["-I"] if isolated else []), entry["script"],
                *(os.path.expandvars(arg) for arg in args)]
    raise LaneError(f"unrecognised lane entry {entry}")


def run(lane: str, manifest: dict[str, Any]) -> int:
    entries = manifest["lanes"].get(lane)
    if not isinstance(entries, list) or not entries:
        raise LaneError(f"no lane named {lane!r}")
    commands = [command(entry) for entry in entries]
    for argv in commands:
        print(f"::group::{' '.join(argv[1:])}", flush=True)
        result = subprocess.run(argv, cwd=ROOT, check=False)
        print("::endgroup::", flush=True)
        if result.returncode:
            print(f"run-ci-lane: {lane}: {' '.join(argv[1:])} exited {result.returncode}", file=sys.stderr)
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)
    try:
        return run(args.lane, load(args.manifest))
    except LaneError as error:
        print(f"run-ci-lane: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
