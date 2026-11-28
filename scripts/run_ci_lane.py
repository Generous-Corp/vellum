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


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def error(title: str, message: str) -> None:
    """Annotate a failed check with a GitHub Actions workflow command.

    Silent outside Actions. The title is a command property, so it also
    escapes ':' and ','; the message is command data.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    print(f"::error title={_escape_property(title)}::{_escape_data(message)}", flush=True)

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


def linux_covered(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The entry's `linux_covered` claim, validated, or None when it has none.

    The claim says a required Linux check runs this same test on `events`, so a
    slower runner may skip the entry there. scripts/test_ci_lanes.py proves the
    claim; this only refuses a malformed one.
    """
    if "linux_covered" not in entry:
        return None
    claim = entry["linux_covered"]
    events = claim.get("events") if isinstance(claim, dict) else None
    if (not isinstance(events, list) or not events
            or not all(isinstance(event, str) and event for event in events)):
        raise LaneError(f"{entry}: linux_covered needs a non-empty `events` list of strings")
    named = {"lane"} if "lane" in claim else {"workflow", "job"}
    if set(claim) != {"events"} | named or not all(isinstance(claim[key], str) and claim[key] for key in named):
        raise LaneError(f"{entry}: linux_covered names either a `lane` or a `workflow` and `job`")
    return claim


def skipped_on(entry: dict[str, Any], event: str | None) -> bool:
    """True when `event` is one a required Linux check already covers for this entry."""
    claim = linux_covered(entry)
    return claim is not None and event is not None and event in claim["events"]


def command(entry: dict[str, Any]) -> list[str]:
    """The argv one manifest entry runs, with $VARS in its args expanded."""
    linux_covered(entry)
    keys = set(entry) - {"linux_covered"}
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


def run(lane: str, manifest: dict[str, Any], event: str | None = None) -> int:
    entries = manifest["lanes"].get(lane)
    if not isinstance(entries, list) or not entries:
        raise LaneError(f"no lane named {lane!r}")
    commands = [command(entry) for entry in entries]
    skipped = [argv for entry, argv in zip(entries, commands) if skipped_on(entry, event)]
    if skipped:
        print(f"run-ci-lane: {lane}: skipping {len(skipped)} entries a required Linux check runs on {event}", flush=True)
    commands = [argv for entry, argv in zip(entries, commands) if not skipped_on(entry, event)]
    for argv in commands:
        print(f"::group::{' '.join(argv[1:])}", flush=True)
        result = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        first_output = ""
        assert result.stdout is not None
        for line in result.stdout:
            print(line, end="", flush=True)
            if not first_output and line.strip():
                first_output = line.strip()
        returncode = result.wait()
        print("::endgroup::", flush=True)
        if returncode:
            item = first_output or f"{' '.join(argv[1:])} exited {returncode}"
            error(lane, item)
            print(f"run-ci-lane: {lane}: {' '.join(argv[1:])} exited {returncode}", file=sys.stderr)
            return returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument(
        "--event",
        help="the workflow event; entries marked linux_covered for it are skipped. "
             "Omit it to run every entry (tags, pushes, local runs).",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.lane, load(args.manifest), args.event)
    except LaneError as error:
        print(f"run-ci-lane: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
