#!/usr/bin/env python3
"""Run an implementation's commands and record how the input was received.

The expected-rejection lane compares HOW two implementations refuse a malformed
input, so a refusal is an observation, not a failed step: this wrapper always
exits 0 after writing outcome.json, unless a command could not be started at
all (exit 77, which the harness reports as blocked, never as a pass).

    outcome_recorder.py --out outcome.json --diagnostics '<json list>' \
        -- cmd1 args... [--then cmd2 args...]

Commands run in order and stop at the first non-zero exit. The outcome is
`reject` (exit > 0), `crash` (killed by a signal), `timeout`, `warn` (every
command succeeded and a declared diagnostic matched), or `accept`. Codes come
from the implementation's own message-to-code map (`--diagnostics`, a JSON list
of {"code", "pattern"}), so both sides report one neutral vocabulary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SKIP_EXIT = 77


def split_commands(argv: list[str]) -> list[list[str]]:
    commands, current = [], []
    for arg in argv:
        if arg == "--then":
            commands.append(current)
            current = []
        else:
            current.append(arg)
    commands.append(current)
    return [c for c in commands if c]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command_argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    commands = split_commands(command_argv)
    if not commands:
        print("outcome_recorder: no command", file=sys.stderr)
        return 2
    patterns = [(d["code"], re.compile(d["pattern"])) for d in json.loads(args.diagnostics)]
    record: dict = {"schema": "vellum.parity.outcome.v1", "commands": []}
    seen_codes: list[str] = []
    outcome = "accept"
    for index, command in enumerate(commands):
        entry: dict = {"index": index, "argv0": Path(command[0]).name}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=args.timeout)
        except FileNotFoundError as error:
            print(f"outcome_recorder: cannot start {command[0]}: {error}", file=sys.stderr)
            return SKIP_EXIT
        except subprocess.TimeoutExpired:
            entry["exit"] = None
            record["commands"].append(entry)
            outcome = "timeout"
            break
        text = result.stderr + result.stdout
        codes = [code for code, pattern in patterns if pattern.search(text)]
        entry.update(exit=result.returncode, codes=codes, stderr_tail=result.stderr[-1500:])
        record["commands"].append(entry)
        seen_codes += [c for c in codes if c not in seen_codes]
        if result.returncode < 0:
            outcome = "crash"
            record["signal"] = -result.returncode
            break
        if result.returncode != 0:
            outcome = "reject"
            seen_codes = codes
            break
    else:
        outcome = "warn" if seen_codes else "accept"
    record["outcome"] = outcome
    record["codes"] = sorted(seen_codes) if outcome in ("warn", "reject") else []
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
