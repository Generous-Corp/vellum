from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import deadline_warning_gate


class DeadlineWarningGateTests(unittest.TestCase):
    def test_only_events_within_six_hours_fail(self) -> None:
        summary = {"pending": [], "pending_count": 0, "pending_limit": 20,
                   "pending_warning": 16, "overdue": [], "due_soon": [
            {"event_id": "soon", "hours_left": 5.9},
            {"event_id": "later", "hours_left": 6.1},
        ]}
        self.assertEqual([row["event_id"] for row in deadline_warning_gate.due_within(summary)], ["soon"])

    def test_empty_or_missing_due_soon_passes(self) -> None:
        self.assertEqual(deadline_warning_gate.due_within({"due_soon": []}), [])

    def test_count_pressure_names_oldest_five(self) -> None:
        summary = {"pending_count": 16, "pending_limit": 20,
                   "pending_warning": 16,
                   "pending": [{"event_id": str(i), "deadline": f"2026-11-2{i}T00:00:00Z"}
                                for i in range(6)]}
        pressured, oldest = deadline_warning_gate.count_pressure(summary)
        self.assertTrue(pressured)
        self.assertEqual(oldest, ["0", "1", "2", "3", "4"])

    def test_count_below_warning_passes(self) -> None:
        pressured, oldest = deadline_warning_gate.count_pressure({"pending_count": 15,
                                                                    "pending_warning": 16,
                                                                    "pending": []})
        self.assertFalse(pressured)
        self.assertEqual(oldest, [])

    def test_summary_with_overdue_event_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "overdue"):
            deadline_warning_gate.validate_summary({
                "pending": [], "pending_count": 0, "pending_limit": 20,
                "pending_warning": 16, "due_soon": [], "overdue": [{"event_id": "x"}],
            })

    def test_summary_missing_key_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required keys"):
            deadline_warning_gate.validate_summary({})

    def test_cli_returns_nonzero_for_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(json.dumps({"pending": [], "pending_count": 0,
                                        "pending_limit": 20, "pending_warning": 16,
                                        "due_soon": [{"event_id": "soon", "hours_left": 1,
                                                       "deadline": "2026-11-23T00:00:00Z"}],
                                        "overdue": []}),
                            encoding="utf-8")
            result = subprocess.run([sys.executable, str(Path(deadline_warning_gate.__file__)),
                                     "--summary", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("soon", result.stdout)


if __name__ == "__main__":
    unittest.main()
