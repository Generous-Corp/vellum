#!/usr/bin/env python3
"""Window and issue lifecycle tests for the observatory deadline warning."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import deadline_warning  # noqa: E402
import observatory  # noqa: E402
import overdue_events  # noqa: E402

LOOKUP = lambda repository, commit: "pr-author"  # noqa: E731
DEADLINE = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
EVENT_ID = "pulp-" + "c" * 40
BUDGETS = """observatory:
  security_or_p0_classification_hours: 24
  ordinary_framework_classification_business_days: 3
  other_classification_days: 7
  maximum_pending_events: 20
  maximum_overdue_events: 0
  maximum_pending_event_age_days: 70
  maximum_repeated_generic_fixes: 2
  maximum_framework_effort_percent: 15
"""


def summary_at(hours_before_deadline: float, event_class: str = "documentation",
               api_deltas: list[str] | None = None) -> dict[str, object]:
    """overdue_events output for one pending event whose deadline is DEADLINE."""
    discovered = DEADLINE - timedelta(days=7 if event_class == "documentation" else 3)
    now = DEADLINE - timedelta(hours=hours_before_deadline)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / observatory.BUDGETS_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / observatory.BUDGETS_PATH).write_text(BUDGETS)
        events = root / observatory.EVENTS_PATH
        events.mkdir(parents=True, exist_ok=True)
        event = {
            "class": event_class, "contract_keys": [], "contract_tests": [], "direction": "Pulp-to-framework",
            "discovered_at": observatory.utc_text(discovered), "disposition": "pending",
            "effort_minutes": 0, "event_id": EVENT_ID, "include_dependency_deltas": [], "kind": "observation",
            "linked_commits": [], "linked_pull_requests": [], "mapped_contracts": [], "mapped_paths": [],
            "owner": "@danielraffel", "patch_id": None, "rationale": "r", "rename_candidates": [],
            "scan_cursor": {"from_commit": "a" * 40, "to_commit": "b" * 40}, "schema_api_deltas": api_deltas or [],
            "schema_version": 1, "severity": None, "shared_contract_release_blocker": False,
            "source_commit": "c" * 40, "source_repository": "Generous-Corp/pulp", "transitive_paths": [],
        }
        (events / f"{EVENT_ID}.yaml").write_text(json.dumps(event) + "\n")
        return overdue_events.overdue(root, now)


class WindowTests(unittest.TestCase):
    def test_no_warning_at_25_hours(self) -> None:
        self.assertEqual(summary_at(25)["due_soon"], [])

    def test_warning_at_23_hours(self) -> None:
        (row,) = summary_at(23)["due_soon"]
        self.assertEqual((row["event_id"], row["source_commit"], row["urgent"]), (EVENT_ID, "c" * 40, False))

    def test_urgent_at_3_hours(self) -> None:
        (row,) = summary_at(3)["due_soon"]
        self.assertTrue(row["urgent"])

    def test_overdue_event_is_not_due_soon(self) -> None:
        summary = summary_at(-1)
        self.assertEqual(summary["due_soon"], [])
        self.assertEqual(len(summary["overdue"]), 1)


class RoutingTests(unittest.TestCase):
    def test_routine_event_never_mentions_the_owner(self) -> None:
        for hours in (23, 3):
            body = deadline_warning.render(summary_at(hours)["due_soon"], LOOKUP)
            self.assertNotIn("@danielraffel", body)
            self.assertIn("`pr-author`", body)

    def test_schema_event_mentions_the_owner_at_24h_only(self) -> None:
        early = summary_at(23, "schema")["due_soon"]
        late = summary_at(3, "schema")["due_soon"]
        self.assertTrue(early[0]["needs_ruling"])
        self.assertIn("@danielraffel", deadline_warning.render(early, LOOKUP))
        self.assertNotIn("@danielraffel", deadline_warning.render(late, LOOKUP))
        for action in deadline_warning.plan(late, [{"number": 7, "title": deadline_warning.TITLE,
                                                    "body": deadline_warning.render(early, LOOKUP)}], LOOKUP):
            self.assertNotIn("@danielraffel", str(action))

    def test_api_change_mentions_the_owner_at_24h_only(self) -> None:
        early = summary_at(23, "correctness", ["Widget::draw"])["due_soon"]
        late = summary_at(3, "correctness", ["Widget::draw"])["due_soon"]
        self.assertTrue(early[0]["needs_ruling"])
        self.assertIn("@danielraffel", deadline_warning.render(early, LOOKUP))
        self.assertNotIn("@danielraffel", deadline_warning.render(late, LOOKUP))

    def test_api_class_needs_a_ruling_even_without_deltas(self) -> None:
        # The loader accepts no "api" class today; this pins the predicate
        # for the day one is added.
        import observatory_common  # noqa: PLC0415
        self.assertNotIn("api", observatory_common.CLASSES)
        source = Path(overdue_events.__file__).read_text()
        self.assertIn('{"schema", "security", "api"}', source)

    def test_issue_is_labelled_for_the_escalation_ladder(self) -> None:
        calls: list[tuple[str, ...]] = []
        deadline_warning.apply([{"op": "create", "body": "b"}], "o/r",
                               run=lambda *a, stdin=None: calls.append(a) or "")
        self.assertIn("shipyard:needs-agent", calls[0])


class IssueTests(unittest.TestCase):
    def issue(self, number: int, due_soon: list[dict[str, object]]) -> dict[str, object]:
        return {"number": number, "title": deadline_warning.TITLE, "body": deadline_warning.render(due_soon, LOOKUP)}

    def test_creates_one_issue_then_nothing_on_rerun(self) -> None:
        soon = summary_at(23)["due_soon"]
        (create,) = deadline_warning.plan(soon, [], LOOKUP)
        self.assertEqual(create["op"], "create")
        self.assertIn("pr-author", create["body"])
        self.assertNotIn("@danielraffel", create["body"])
        self.assertEqual(deadline_warning.plan(soon, [self.issue(7, soon)], LOOKUP), [])

    def test_nothing_to_do_without_warning(self) -> None:
        self.assertEqual(deadline_warning.plan([], []), [])

    def test_escalation_edits_once_and_mentions_owner(self) -> None:
        early, late = summary_at(23)["due_soon"], summary_at(3)["due_soon"]
        edit, comment = deadline_warning.plan(late, [self.issue(7, early)], LOOKUP)
        self.assertEqual((edit["op"], comment["op"]), ("edit", "comment"))
        self.assertIn("pr-author", comment["body"])
        self.assertNotIn("@danielraffel", comment["body"])
        self.assertEqual(deadline_warning.plan(late, [self.issue(7, late)], LOOKUP), [])

    def test_closes_when_event_resolves_or_goes_overdue(self) -> None:
        soon = summary_at(23)["due_soon"]
        (close,) = deadline_warning.plan([], [self.issue(7, soon)])
        self.assertEqual((close["op"], close["number"]), ("close", 7))

    def test_duplicates_are_closed(self) -> None:
        soon = summary_at(23)["due_soon"]
        actions = deadline_warning.plan(soon, [self.issue(9, soon), self.issue(7, soon)], LOOKUP)
        self.assertEqual([(a["op"], a["number"]) for a in actions], [("close", 9)])

    def test_apply_issues_gh_calls(self) -> None:
        calls: list[tuple[str, ...]] = []
        deadline_warning.apply([{"op": "create", "body": "b"}, {"op": "close", "number": 3, "comment": "c"}],
                               "o/r", run=lambda *a, stdin=None: calls.append(a) or "")
        self.assertEqual([c[:2] for c in calls], [("issue", "create"), ("issue", "close")])


if __name__ == "__main__":
    unittest.main()
