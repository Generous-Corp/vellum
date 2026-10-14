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
        # Keep HEAD's tree, so the receipts still verify; only history is added.
        run(fixture.vellum, "merge", "-q", "--no-edit", "-s", "ours", "extra")
        self.assertEqual(fixture.errors(), [])
        # The unreceipted commit extends the imported history without being a
        # mainline join, so it is refused rather than skipped.
        self.assertRejected(fixture, "joins an imported mirror history but is not a two-parent merge")

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

    @staticmethod
    def rebuild_join(fixture: Fixture, *parents: str) -> None:
        """Replace the join with a commit of the same tree and the given parents."""
        tree = run(fixture.vellum, "rev-parse", "HEAD^{tree}")
        args = [arg for parent in parents for arg in ("-p", parent)]
        joined = run(fixture.vellum, "commit-tree", tree, *args, "-m", "join")
        run(fixture.vellum, "reset", "-q", "--hard", joined)

    def test_octopus_join_is_rejected(self) -> None:
        def octopus(fixture: Fixture) -> None:
            other = run(fixture.vellum, "commit-tree", "4b825dc642cb6eb9a060e54bf8d69288fbee4904", "-m", "other root")
            self.rebuild_join(fixture, fixture.base_sha, fixture.mirror_sha, other)

        self.assertRejected(self.fixture(after_join=octopus), "two-parent merge whose first parent is the mainline")

    def test_join_with_both_parents_imported_is_rejected(self) -> None:
        def both_imported(fixture: Fixture) -> None:
            prelude = run(fixture.vellum, "rev-parse", f"{fixture.mirror_sha}^")
            self.rebuild_join(fixture, prelude, fixture.mirror_sha)

        # With no mainline parent the base is no longer an ancestor of HEAD.
        self.assertRejected(self.fixture(after_join=both_imported), "is not an ancestor of")

    def test_join_with_the_imported_parent_first_is_rejected(self) -> None:
        def imported_first(fixture: Fixture) -> None:
            self.rebuild_join(fixture, fixture.mirror_sha, fixture.base_sha)

        self.assertRejected(self.fixture(after_join=imported_first), "two-parent merge whose first parent is the mainline")

    def test_side_history_rooted_on_the_base_is_rejected(self) -> None:
        def mirror_on_base(fixture: Fixture) -> None:
            # Rebuild the mirror commit on the Vellum base instead of an orphan
            # root, so its first edge would delete the whole observatory.
            tree = run(fixture.vellum, "rev-parse", f"{fixture.mirror_sha}^{{tree}}")
            rooted = run(fixture.vellum, "commit-tree", tree, "-p", fixture.base_sha, "-m", "mirror on base")
            fixture.mirror_sha = rooted
            self.rebuild_join(fixture, fixture.base_sha, rooted)

        fixture = self.fixture(mirror_prelude=[], after_join=mirror_on_base)
        self.assertEqual(fixture.errors(), [])
        self.assertRejected(fixture, "outside the imported history")

    def test_uncommitted_receipts_cannot_approve_an_import(self) -> None:
        fixture = self.fixture()
        write(fixture.vellum, "provenance/mirror/receipts/index.json", dump({
            "schema": "vellum.mirror-receipt-index.v1", "receipts": [fixture.receipt_path],
        }) + "\n")
        self.assertRejected(fixture, "provenance/mirror has uncommitted changes")

    def test_failed_mirror_export_verification_is_reported(self) -> None:
        fixture = self.fixture()
        fixture.reland(mirror_tree="0" * 40)
        with self.assertRaises(observatory.ObservatoryError) as caught:
            self.verify(fixture)
        message = str(caught.exception)
        self.assertIn("observatory cursor must exist on every history edge", message)
        self.assertIn("mirror-export verification failed", message)


if __name__ == "__main__":
    unittest.main()
