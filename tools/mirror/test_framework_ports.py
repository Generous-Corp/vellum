#!/usr/bin/env python3
"""The ports ledger's reading and replay, each rule with a passing case and a control."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("framework_ports", HERE / "framework_ports.py")
assert spec and spec.loader
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

COMMIT = "a" * 40


def row(path: str = "framework/core/a/src/new.cpp", change: str = "added", **overrides) -> dict:
    base = {"framework_path": path, "change": change,
            "target": "a-target" if change == "added" and fp.compiled(path) else None,
            "origin": "pulp", "pulp_path": path.removeprefix("framework/"), "pulp_commits": [COMMIT],
            "framework_blob": "b" * 40 if change == "added" else None, "reason": "port"}
    return {**base, **overrides}


def ledger(rows: list) -> str:
    return json.dumps({"schema": fp.SCHEMA, "rows": rows})


class RowShapeTest(unittest.TestCase):
    def assertRowRefused(self, candidate, fragment: str) -> None:
        errors = fp.row_errors(candidate, "row")
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_well_formed_rows_pass(self) -> None:
        for candidate in (
            row(),
            row("framework/core/a/include/vellum/a/new.hpp"),
            row("framework/core/a/src/old.cpp", "removed"),
            row(origin="native", pulp_path=None, pulp_commits=[], framework_blob=None),
            row(pulp_commits=[COMMIT, "b" * 40]),
        ):
            self.assertEqual(fp.row_errors(candidate, "row"), [], candidate)

    def test_keys_are_exact(self) -> None:
        self.assertRowRefused({**row(), "extra": 1}, "must have exactly")
        missing = row()
        del missing["reason"]
        self.assertRowRefused(missing, "must have exactly")
        self.assertRowRefused("framework/a.cpp", "must have exactly")

    def test_paths_stay_inside_framework(self) -> None:
        for path in ("graphics/a.cpp", "framework/../graphics/a.cpp", "/framework/a.cpp", "framework/./a.cpp",
                     "framework//a.cpp", "framework/a b.cpp", "framework/", "frameworkx/a.cpp", 7):
            self.assertRowRefused(row(target="t") | {"framework_path": path}, "not a safe path inside framework/")

    def test_third_party_source_is_not_ported_through_the_ledger(self) -> None:
        self.assertRowRefused(row("framework/external/x/x.c"), "third-party source")
        self.assertRowRefused(row("framework/external/x/x.c", "removed"), "third-party source")

    def test_change_origin_and_reason_are_closed_sets(self) -> None:
        self.assertRowRefused(row(change="edited"), "change must be one of")
        self.assertRowRefused(row(origin="upstream"), "origin must be one of")
        self.assertRowRefused(row(reason="  "), "reason must be a non-empty string")

    def test_a_pulp_row_names_full_distinct_commits(self) -> None:
        for commits in ([], [COMMIT[:12]], [COMMIT, COMMIT], COMMIT, ["A" * 40], [None]):
            self.assertRowRefused(row(pulp_commits=commits), "distinct full Pulp commit ids")
        self.assertRowRefused(row(pulp_path="../core/a.cpp"), "not a safe relative path")
        self.assertRowRefused(row(pulp_path=None), "not a safe relative path")

    def test_only_an_added_pulp_row_records_the_joining_blob(self) -> None:
        for blob in (None, "b" * 39, "B" * 40, 7):
            self.assertRowRefused(row(framework_blob=blob), "records the file's git blob id")
        self.assertRowRefused(row("framework/core/a/src/old.cpp", "removed", framework_blob="b" * 40),
                              "framework_blob is null unless")
        self.assertRowRefused(row(origin="native", pulp_path=None, pulp_commits=[]), "framework_blob is null unless")

    def test_a_native_row_names_no_pulp_source(self) -> None:
        self.assertRowRefused(row(origin="native"), "a native row has pulp_path null")
        self.assertRowRefused(row(origin="native", pulp_path=None), "a native row has pulp_path null")

    def test_only_an_added_compilable_source_names_a_target(self) -> None:
        for suffix in sorted(fp.SOURCE_SUFFIXES):
            self.assertRowRefused(row(f"framework/core/a/src/new{suffix}", target=None), "target must name")
        self.assertRowRefused(row(target="bad target"), "target must name")
        self.assertRowRefused(row("framework/core/a/include/vellum/a/new.hpp", target="a-target"),
                              "target is null unless")
        self.assertRowRefused(row("framework/core/a/src/old.cpp", "removed", target="a-target"),
                              "target is null unless")


class LedgerReadTest(unittest.TestCase):
    def test_document_shape(self) -> None:
        self.assertEqual(fp.parse(ledger([row()])), ([row()], []))
        for text in ("[", "[]", json.dumps({"schema": "other", "rows": []}),
                     json.dumps({"schema": fp.SCHEMA, "rows": {}}),
                     json.dumps({"schema": fp.SCHEMA, "rows": [], "note": ""})):
            rows, errors = fp.parse(text)
            self.assertEqual(rows, [])
            self.assertTrue(errors, text)

    def test_one_bad_row_withholds_every_row(self) -> None:
        rows, errors = fp.parse(ledger([row(), row(change="edited")]))
        self.assertEqual(rows, [])
        self.assertIn("rows[1]", errors[0])

    def test_a_missing_ledger_is_empty_and_a_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            self.assertEqual(fp.load(repo), ([], []))
            (repo / fp.LEDGER).parent.mkdir(parents=True)
            (repo / "elsewhere.json").write_text(ledger([row()]), encoding="utf-8")
            (repo / fp.LEDGER).symlink_to(repo / "elsewhere.json")
            self.assertEqual(fp.load(repo), ([], [f"{fp.LEDGER}: is a symlink"]))
            (repo / fp.LEDGER).unlink()
            (repo / fp.LEDGER).write_text(ledger([row()]), encoding="utf-8")
            self.assertEqual(fp.load(repo), ([row()], []))


class ReplayTest(unittest.TestCase):
    TRANSFERRED = {"framework/core/a/src/a.cpp", "framework/core/a/src/b.cpp"}

    def replay(self, rows: list) -> tuple:
        return fp.replay(rows, set(self.TRANSFERRED))

    def test_additions_and_removals_make_the_file_set(self) -> None:
        new, gone = "framework/core/a/src/new.cpp", "framework/core/a/src/a.cpp"
        added, removed, errors = self.replay([row(new), row(gone, "removed")])
        self.assertEqual((sorted(added), removed, errors), ([new], {gone}, []))
        self.assertEqual(added[new]["target"], "a-target")

    def test_a_transferred_file_may_only_be_removed_and_only_once(self) -> None:
        gone = "framework/core/a/src/a.cpp"
        _, _, errors = self.replay([row(gone)])
        self.assertIn("a ledger row may only remove it", errors[0])
        _, _, errors = self.replay([row(gone, "removed"), row(gone)])
        self.assertIn("a ledger row may only remove it", errors[0])
        _, _, errors = self.replay([row(gone, "removed"), row(gone, "removed")])
        self.assertIn("cannot be removed", errors[0])

    def test_a_path_is_added_only_while_absent_and_removed_only_while_present(self) -> None:
        new = "framework/core/a/src/new.cpp"
        _, _, errors = self.replay([row(new), row(new)])
        self.assertIn("already added", errors[0])
        _, _, errors = self.replay([row(new, "removed")])
        self.assertIn("cannot be removed", errors[0])
        # Control: add, remove, add again replays, ending added.
        added, removed, errors = self.replay([row(new), row(new, "removed"), row(new)])
        self.assertEqual((sorted(added), removed, errors), ([new], set(), []))

    def test_a_broken_ledger_never_widens_the_file_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / fp.LEDGER).parent.mkdir(parents=True)
            (repo / fp.LEDGER).write_text(ledger([row(), row("framework/core/a/src/a.cpp", "removed")]),
                                          encoding="utf-8")
            expected, added, removed, errors = fp.file_set(repo, set(self.TRANSFERRED))
            self.assertEqual(errors, [])
            self.assertEqual(expected, {"framework/core/a/src/b.cpp", "framework/core/a/src/new.cpp"})
            (repo / fp.LEDGER).write_text(ledger([row(), row()]), encoding="utf-8")
            expected, added, removed, errors = fp.file_set(repo, set(self.TRANSFERRED))
            self.assertTrue(errors)
            self.assertEqual((expected, added, removed), (self.TRANSFERRED, {}, set()))


if __name__ == "__main__":
    unittest.main()
