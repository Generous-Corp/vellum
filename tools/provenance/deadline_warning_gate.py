#!/usr/bin/env python3
"""Fail a non-required warning check before observatory budgets are breached."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def due_within(summary: dict, hours: float = 6.0) -> list[dict]:
    """Return pending events whose classification deadline is within ``hours``."""
    rows = summary.get("due_soon", [])
    return [row for row in rows if float(row.get("hours_left", float("inf"))) <= hours]


def count_pressure(summary: dict, warning: int = 16) -> tuple[bool, list[str]]:
    """Return whether pending count is at warning level and the oldest five IDs."""
    count = int(summary.get("pending_count", 0))
    rows = sorted(summary.get("pending", []), key=lambda row: row.get("deadline", ""))
    return count >= warning, [str(row.get("event_id")) for row in rows[:5]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--warning-count", type=int, default=16)
    args = parser.parse_args(argv)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    rows = due_within(summary, args.hours)
    pressured, oldest = count_pressure(summary, args.warning_count)
    if not rows and not pressured:
        print(f"observatory due-soon warning: none within {args.hours:g} hours")
        return 0
    if pressured:
        limit = summary.get("pending_limit", "unknown")
        print(f"observatory pending-count warning: {summary.get('pending_count', 0)} pending "
              f"(warning threshold {args.warning_count}, enforced limit {limit}); "
              "oldest five: " + ", ".join(oldest))
    if rows:
        print(f"observatory due-soon warning: {len(rows)} event(s) within {args.hours:g} hours")
    for row in rows:
        print(f"{row.get('event_id')} deadline={row.get('deadline')} "
              f"hours_left={row.get('hours_left')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
