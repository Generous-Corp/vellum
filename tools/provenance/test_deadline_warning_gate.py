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
        summary = {"due_soon": [
            {"event_id": "soon", "hours_left": 5.9},
            {"event_id": "later", "hours_left": 6.1},
        ]}
        self.assertEqual([row["event_id"] for row in deadline_warning_gate.due_within(summary)], ["soon"])

    def test_empty_or_missing_due_soon_passes(self) -> None:
        self.assertEqual(deadline_warning_gate.due_within({}), [])
        self.assertEqual(deadline_warning_gate.due_within({"due_soon": []}), [])

    def test_cli_returns_nonzero_for_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(json.dumps({"due_soon": [{"event_id": "soon", "hours_left": 1}]}),
                            encoding="utf-8")
            result = subprocess.run([sys.executable, str(Path(deadline_warning_gate.__file__)),
                                     "--summary", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("soon", result.stdout)


if __name__ == "__main__":
    unittest.main()
