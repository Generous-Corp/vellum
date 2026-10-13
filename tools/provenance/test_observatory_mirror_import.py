#!/usr/bin/env python3
"""Append-only verification over a range that imports a Pulp mirror history."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from git_test_environment import disable_auto_maintenance  # noqa: E402
from test_verify_mirror_export import Fixture, dump, run, write  # noqa: E402

SPEC = importlib.util.spec_from_file_location("observatory", HERE / "observatory.py")
assert SPEC and SPEC.loader
observatory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observatory)

CURSOR = "provenance/pulp-observatory/cursor.json"
REPORT = "provenance/pulp-observatory/reports/current.json"
BASE_FILES = {
    CURSOR: dump({
        "pulp": {"last_scanned_commit": "a" * 40},
        "vellum": {"last_scanned_commit": "b" * 40},
        "reconciled_at": "2026-10-13T00:00:00Z",
    }).encode(),
    REPORT: dump({"status": "pass"}).encode(),
}


class ObservatoryMirrorImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        disable_auto_maintenance()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, **kwargs: object) -> Fixture:
        kwargs.setdefault("base_files", BASE_FILES)
        kwargs.setdefault("mirror_prelude", [{"mirror/core/canvas/README": b"history\n"}])
        return Fixture(self.base, **kwargs)  # type: ignore[arg-type]

    def verify(self, fixture: Fixture) -> None:
        observatory.verify_append_only(fixture.vellum, fixture.base_sha)

    def assertRejected(self, fixture: Fixture, fragment: str) -> None:
        with self.assertRaises(observatory.ObservatoryError) as caught:
            self.verify(fixture)
        self.assertIn(fragment, str(caught.exception))

    def test_imported_mirror_history_with_receipt_tip_passes(self) -> None:
        fixture = self.fixture()
        self.assertEqual(fixture.errors(), [])
        self.verify(fixture)

    def test_side_commit_touching_the_observatory_is_rejected(self) -> None:
        fixture = self.fixture(mirror_prelude=[{CURSOR: b"{}\n"}])
        self.assertRejected(fixture, "holds paths other than mirror/")

    def test_side_commit_with_a_non_mirror_path_is_rejected(self) -> None:
        fixture = self.fixture(mirror_prelude=[{"tools/stray.py": b"x = 1\n"}])
        self.assertRejected(fixture, "holds paths other than mirror/")

    def test_side_tip_that_is_not_a_receipt_mirror_sha_is_rejected(self) -> None:
        fixture = self.fixture()
        run(fixture.vellum, "checkout", "-q", "-b", "extra", fixture.mirror_sha)
        write(fixture.vellum, "mirror/core/canvas/extra.txt", "unreceipted\n")
        run(fixture.vellum, "add", "-A")
        run(fixture.vellum, "commit", "-q", "-m", "unreceipted mirror commit")
        run(fixture.vellum, "checkout", "-q", "main")
        run(fixture.vellum, "merge", "-q", "--no-edit", "extra")
        self.assertRejected(fixture, "observatory cursor must exist on every history edge")

    def test_join_that_changes_the_cursor_is_rejected(self) -> None:
        def change_cursor(fixture: Fixture) -> None:
            cursor = json.loads((fixture.vellum / CURSOR).read_text())
            cursor["reconciled_at"] = "2026-10-14T00:00:00Z"
            write(fixture.vellum, CURSOR, dump(cursor))
            run(fixture.vellum, "add", "-A")
            run(fixture.vellum, "commit", "-q", "--amend", "--no-edit")

        fixture = self.fixture(after_join=change_cursor)
        self.assertRejected(fixture, "joining an imported mirror history changed")

    def test_mainline_commit_deleting_the_cursor_is_rejected(self) -> None:
        fixture = self.fixture()
        run(fixture.vellum, "rm", "-q", CURSOR)
        run(fixture.vellum, "commit", "-q", "-m", "drop cursor")
        self.assertRejected(fixture, "observatory cursor must exist on every history edge")


if __name__ == "__main__":
    unittest.main()
