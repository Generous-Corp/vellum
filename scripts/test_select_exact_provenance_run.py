#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("select_exact_provenance_run.py")
SPEC = importlib.util.spec_from_file_location("select_exact_provenance_run", SCRIPT)
assert SPEC and SPEC.loader
selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selector)


SHA = "a" * 40
TAG = "v1.2.3"


def run(**changes):
    value = {
        "id": 123,
        "head_sha": SHA,
        "head_branch": TAG,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    value.update(changes)
    return value


class Tests(unittest.TestCase):
    def test_selects_only_exact_successful_tag_sha_push(self) -> None:
        selected = selector.select(
            {"workflow_runs": [run(), run(id=456, head_branch="v9.9.9")]},
            head_sha=SHA,
            head_branch=TAG,
        )
        self.assertEqual(
            selected, {"id": 123, "status": "completed", "conclusion": "success"}
        )

    def test_absent_exact_run_is_pending_state(self) -> None:
        self.assertIsNone(
            selector.select(
                {"workflow_runs": [run(head_sha="b" * 40)]},
                head_sha=SHA,
                head_branch=TAG,
            )
        )

    def test_duplicate_exact_runs_fail_closed(self) -> None:
        with self.assertRaisesRegex(selector.SelectionError, "multiple exact"):
            selector.select(
                {"workflow_runs": [run(), run(id=456)]},
                head_sha=SHA,
                head_branch=TAG,
            )

    def test_malformed_matching_run_fails_closed(self) -> None:
        cases = [
            {"id": True},
            {"status": "waiting"},
            {"status": "completed", "conclusion": None},
            {"status": "in_progress", "conclusion": "success"},
            {"conclusion": "unknown"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(selector.SelectionError):
                selector.select(
                    {"workflow_runs": [run(**changes)]},
                    head_sha=SHA,
                    head_branch=TAG,
                )

    def test_all_documented_nonterminal_states_remain_pollable(self) -> None:
        for status in ("requested", "waiting", "pending", "queued", "in_progress"):
            with self.subTest(status=status):
                selected = selector.select(
                    {
                        "workflow_runs": [
                            run(status=status, conclusion=None)
                        ]
                    },
                    head_sha=SHA,
                    head_branch=TAG,
                )
                self.assertEqual(
                    selected, {"id": 123, "status": status, "conclusion": None}
                )

    def test_response_and_coordinates_are_strict(self) -> None:
        with self.assertRaises(selector.SelectionError):
            selector.select([], head_sha=SHA, head_branch=TAG)
        with self.assertRaises(selector.SelectionError):
            selector.select({"workflow_runs": []}, head_sha="short", head_branch=TAG)
        with self.assertRaises(selector.SelectionError):
            selector.select(
                {"workflow_runs": []}, head_sha=SHA, head_branch="refs/tags/v1.2.3"
            )

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with self.assertRaisesRegex(selector.SelectionError, "duplicate JSON key"):
            selector.strict_object([("status", "completed"), ("status", "failure")])


if __name__ == "__main__":
    unittest.main()
