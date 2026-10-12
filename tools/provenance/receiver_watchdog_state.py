#!/usr/bin/env python3
"""Build the receiver watchdog's independent state from fresh checkouts.

Runs inside the watchdog workflow and in tests. It reads the committed cursor
and report from the Vellum checkout, the durable Pulp event range from a fresh
Pulp checkout, and the Vellum cursor's lag against Vellum main, and writes the
state the evaluator consumes. Nothing here consults the GitHub API; the
workflow adds the receiver run list and the evidence-PR flag afterwards.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import receiver  # noqa: E402


def build_state(*, root: Path, pulp_repo: Path) -> dict[str, object]:
    cursor = receiver.load_cursor(root)
    # Decision 0011 freezes Pulp observation at the committed cursor. Do not
    # walk today's Pulp main: its durable event files may have been edited
    # after the freeze, and advancing past the boundary needs a new decision.
    main = cursor
    latest, oldest_event = receiver.durable_event_range(pulp_repo, cursor, main)
    covered = receiver.is_ancestor(pulp_repo, latest, cursor)
    latest_created_at = receiver.git(pulp_repo, "show", "-s", "--format=%cI", oldest_event)
    vellum_cursor = receiver.load_vellum_cursor(root)
    vellum_main = receiver.git(root, "rev-parse", "HEAD")
    vellum_covered = not receiver.vellum_needs_reconcile(root, root, vellum_main)
    vellum_lag_started_at = receiver.vellum_lag_started_at(root, vellum_cursor, vellum_main)
    report = json.loads(
        (root / "provenance/pulp-observatory/reports/current.json").read_text(encoding="utf-8")
    )
    pending = report.get("pending")
    if not isinstance(pending, int) or isinstance(pending, bool) or pending < 0:
        raise receiver.ReceiverError("committed observatory pending count is invalid")
    return {
        "cursor_covers_latest": covered,
        "latest_event_created_at": latest_created_at,
        "latest_target": latest,
        "pending_count": pending,
        "vellum_cursor_covers_main": vellum_covered,
        "vellum_lag_started_at": vellum_lag_started_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pulp-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        state = build_state(root=args.root.resolve(), pulp_repo=args.pulp_repo.resolve())
    except (receiver.ReceiverError, OSError, json.JSONDecodeError) as error:
        print(f"receiver-watchdog-state: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
