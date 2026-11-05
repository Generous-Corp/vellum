#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_release_tag_rulesets as collector  # noqa: E402


class Tests(unittest.TestCase):
    def test_complete_detail_set_is_sorted(self) -> None:
        result = collector.collect(
            [[{"id": 2}], [{"id": 1}]],
            [{"id": 1, "rules": []}, {"id": 2, "rules": []}],
        )
        self.assertEqual([row["id"] for row in result], [1, 2])

    def test_missing_duplicate_or_unindexed_detail_fails(self) -> None:
        cases = (
            ([[{"id": 1}]], []),
            ([[{"id": 1}, {"id": 1}]], [{"id": 1}]),
            ([[{"id": 1}]], [{"id": 1}, {"id": 2}]),
        )
        for index, details in cases:
            with self.subTest(index=index, details=details), self.assertRaises(ValueError):
                collector.collect(index, details)


if __name__ == "__main__":
    unittest.main()
