#!/usr/bin/env python3
"""Fail a non-required warning check before observatory budgets are breached."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def due_within(summary: dict, hours: float = 6.0) -> list[dict]:
    """Return pending events whose classification deadline is within ``hours``."""
    rows = summary["due_soon"]
    return [row for row in rows if float(row["hours_left"]) <= hours]


def count_pressure(summary: dict, warning: int | None = None) -> tuple[bool, list[str]]:
    """Return whether pending count is at warning level and the oldest five IDs."""
    count = int(summary["pending_count"])
    warning = summary["pending_warning"] if warning is None else warning
    rows = sorted(summary["pending"], key=lambda row: row["deadline"])
    return count >= warning, [str(row["event_id"]) for row in rows[:5]]


def validate_summary(summary: dict) -> None:
    """Reject malformed summaries and any overdue event (fail closed)."""
    required = ("pending", "pending_count", "pending_limit", "pending_warning",
                "due_soon", "overdue")
    missing = [key for key in required if key not in summary]
    if missing:
        raise ValueError("summary missing required keys: " + ", ".join(missing))
    if not isinstance(summary["pending"], list) or not isinstance(summary["due_soon"], list):
        raise ValueError("summary pending and due_soon must be lists")
    if not isinstance(summary["overdue"], list):
        raise ValueError("summary overdue must be a list")
    if summary["overdue"]:
        raise ValueError(f"summary contains {len(summary['overdue'])} overdue event(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--warning-count", type=int)
    args = parser.parse_args(argv)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    try:
        validate_summary(summary)
    except (TypeError, ValueError) as error:
        print(f"observatory warning summary rejected: {error}")
        return 2
    rows = due_within(summary, args.hours)
    pressured, oldest = count_pressure(summary, args.warning_count)
    if not rows and not pressured:
        print(f"observatory due-soon warning: none within {args.hours:g} hours")
        return 0
    if pressured:
        warning = summary["pending_warning"] if args.warning_count is None else args.warning_count
        print(f"observatory pending-count warning: {summary['pending_count']} pending "
              f"(warning threshold {warning}, enforced limit {summary['pending_limit']}); "
              "oldest five: " + ", ".join(oldest))
    if rows:
        print(f"observatory due-soon warning: {len(rows)} event(s) within {args.hours:g} hours")
    for row in rows:
        print(f"{row['event_id']} deadline={row['deadline']} hours_left={row['hours_left']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
