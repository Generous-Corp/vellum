#!/usr/bin/env python3
"""Fail a non-required warning check when an event is within six hours of due."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def due_within(summary: dict, hours: float = 6.0) -> list[dict]:
    """Return pending events whose classification deadline is within ``hours``."""
    rows = summary.get("due_soon", [])
    return [row for row in rows if float(row.get("hours_left", float("inf"))) <= hours]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=6.0)
    args = parser.parse_args(argv)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    rows = due_within(summary, args.hours)
    if not rows:
        print(f"observatory due-soon warning: none within {args.hours:g} hours")
        return 0
    print(f"observatory due-soon warning: {len(rows)} event(s) within {args.hours:g} hours")
    for row in rows:
        print(f"{row.get('event_id')} deadline={row.get('deadline')} "
              f"hours_left={row.get('hours_left')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
