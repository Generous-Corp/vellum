#!/usr/bin/env python3
"""List the pending observation events past their classification deadline.

    overdue_events.py [--root PATH] [--now UTC]

Prints {overdue: [{event_id, class, deadline}], oldest_pending_days,
maximum_pending_event_age_days, pending_age_exceeded, due_soon: [{event_id,
class, deadline, source_repository, source_commit, needs_ruling, hours_left, urgent}]}, overdue oldest first. due_soon
lists pending events not yet overdue whose deadline is within WARN_HOURS, with
urgent set within URGENT_HOURS; the deadline_warning issue is built from it.
The Observatory overdue issue workflow keeps one tracking issue current from it: pull
requests and queue entries only report overdue events (Decision 0013), so the
issue is where the work to classify them is asked for.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import observatory  # noqa: E402


WARN_HOURS = 24
URGENT_HOURS = 4


def overdue(root: Path, now: datetime) -> dict[str, object]:
    budgets = observatory.load_budgets(root / observatory.BUDGETS_PATH)
    rows = []
    soon = []
    oldest = 0.0
    for event in observatory.effective_observations(observatory.load_events(root)):
        if event["disposition"] != "pending":
            continue
        discovered = observatory.parse_utc(event["discovered_at"], "event.discovered_at")
        oldest = max(oldest, (now - discovered).total_seconds() / 86400)
        due = observatory.deadline(event, budgets)
        if now > due:
            rows.append({"event_id": str(event["event_id"]), "class": str(event["class"]),
                         "deadline": observatory.utc_text(due)})
        else:
            hours_left = (due - now).total_seconds() / 3600
            if hours_left <= WARN_HOURS:
                soon.append({"event_id": str(event["event_id"]), "class": str(event["class"]),
                             "deadline": observatory.utc_text(due),
                             "source_repository": str(event.get("source_repository") or ""),
                             "source_commit": str(event.get("source_commit") or ""),
                             "needs_ruling": (event["class"] in {"schema", "security", "api"}
                                              or event.get("severity") == "P0"
                                              or bool(event.get("schema_api_deltas"))),
                             "hours_left": round(hours_left, 1),
                             "urgent": hours_left <= URGENT_HOURS})
    limit = budgets["maximum_pending_event_age_days"]
    return {
        "overdue": sorted(rows, key=lambda row: (row["deadline"], row["event_id"])),
        "oldest_pending_days": round(oldest, 2),
        "maximum_pending_event_age_days": limit,
        "pending_age_exceeded": oldest > limit,
        "due_soon": sorted(soon, key=lambda row: (row["deadline"], row["event_id"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = (observatory.parse_utc(args.now, "--now") if args.now
           else datetime.now(timezone.utc).replace(microsecond=0))
    print(json.dumps(overdue(args.root.resolve(), now), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
