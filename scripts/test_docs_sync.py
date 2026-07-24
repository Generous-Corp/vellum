#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).with_name("docs_sync.py")
SPEC = importlib.util.spec_from_file_location("docs_sync", PATH)
assert SPEC and SPEC.loader
docs_sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs_sync)


class Tests(unittest.TestCase):
    def test_repository_readme_is_generated(self) -> None:
        rows = docs_sync.load(docs_sync.SOURCE)
        current = docs_sync.README.read_text(encoding="utf-8")
        self.assertEqual(
            current,
            docs_sync.replace(current, docs_sync.render(rows)),
        )

    def test_stale_generated_table_is_detected(self) -> None:
        current = docs_sync.README.read_text(encoding="utf-8")
        stale = current.replace(
            "| macOS native application | experimental |",
            "| macOS native application | supported |",
            1,
        )
        self.assertNotEqual(
            stale,
            docs_sync.replace(
                stale,
                docs_sync.render(docs_sync.load(docs_sync.SOURCE)),
            ),
        )

    def test_supported_without_evidence_fails(self) -> None:
        value = {
            "schema": docs_sync.SCHEMA,
            "rows": [{
                "id": "test",
                "label": "Test",
                "status": "supported",
                "evidence": [],
                "boundary": "fixture",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.yaml"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(docs_sync.Error, "requires"):
                docs_sync.load(path)

    def test_duplicate_identifier_fails(self) -> None:
        row = {
            "id": "test",
            "label": "Test",
            "status": "planned",
            "evidence": [],
            "boundary": "fixture",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.yaml"
            path.write_text(json.dumps({
                "schema": docs_sync.SCHEMA,
                "rows": [row, row],
            }), encoding="utf-8")
            with self.assertRaisesRegex(docs_sync.Error, "duplicate"):
                docs_sync.load(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
