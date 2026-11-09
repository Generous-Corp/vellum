#!/usr/bin/env python3
"""Append-only verification over a range that imports a Pulp mirror history."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.append(str(HERE))

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


class MirrorImportCase(unittest.TestCase):
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


class ObservatoryMirrorImportTests(MirrorImportCase):
    def verify(self, fixture: Fixture) -> None:
        observatory.verify_append_only(fixture.vellum, fixture.base_sha, pulp_repo=fixture.pulp)

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

    def test_an_import_is_not_approved_without_pulp(self) -> None:
        fixture = self.fixture()
        with self.assertRaises(observatory.ObservatoryError) as caught:
            observatory.verify_append_only(fixture.vellum, fixture.base_sha)
        self.assertIn("Pulp identity cannot be proved without the Pulp repository", str(caught.exception))

    def test_an_import_whose_receipt_disagrees_with_pulp_is_rejected(self) -> None:
        # The mirror carries a file that is not Pulp's blob at the exported
        # commit, and the receipt records the mirror's blob. The receipt is
        # consistent with the mirror on its own; only Pulp's content refutes it.
        path = "core/canvas/src/canvas.cpp"
        fixture = self.fixture(mirror_overrides={path: b"// edited after export\n"})
        self.assertEqual(fixture.errors(), [])
        self.assertTrue(
            any(f"mirror/{path} is not Pulp {fixture.pulp_sha}:{path}" in error for error in fixture.errors(pulp=True)),
            fixture.errors(pulp=True),
        )
        with self.assertRaises(observatory.ObservatoryError) as caught:
            self.verify(fixture)
        message = str(caught.exception)
        self.assertIn("observatory cursor must exist on every history edge", message)
        self.assertIn(f"mirror/{path} is not Pulp {fixture.pulp_sha}:{path}", message)

    def test_a_receipt_whose_commit_pulp_main_does_not_contain_is_rejected(self) -> None:
        fixture = self.fixture()
        self.assertEqual(fixture.errors(pulp=True), [])
        other = run(fixture.pulp, "commit-tree", "4b825dc642cb6eb9a060e54bf8d69288fbee4904", "-m", "other main")
        run(fixture.pulp, "update-ref", "refs/remotes/origin/main", other)
        with self.assertRaises(observatory.ObservatoryError) as caught:
            self.verify(fixture)
        self.assertIn(f"Pulp commit {fixture.pulp_sha} is not reachable from Pulp main", str(caught.exception))

    def commit_dropping_the_cursor(self, fixture: Fixture) -> str:
        """An ordinary commit, not a mirror-only one, whose tree has no cursor."""
        start = run(fixture.vellum, "rev-parse", "HEAD")
        run(fixture.vellum, "rm", "-q", CURSOR)
        write(fixture.vellum, "tools/stray.py", "x = 1\n")
        run(fixture.vellum, "add", "-A")
        run(fixture.vellum, "commit", "-q", "-m", "drop cursor")
        return start

    def test_a_cursorless_commit_is_refused_when_pulp_refutes_the_receipts(self) -> None:
        path = "core/canvas/src/canvas.cpp"
        fixture = self.fixture(mirror_overrides={path: b"// edited after export\n"})
        start = self.commit_dropping_the_cursor(fixture)
        self.assertFalse(observatory.mirror_only_tree(fixture.vellum, "HEAD")[0])
        with self.assertRaises(observatory.ObservatoryError) as caught:
            observatory.imported_mirror_history(fixture.vellum, start, "HEAD", fixture.pulp)
        message = str(caught.exception)
        self.assertIn("without the observatory cursor", message)
        self.assertIn(f"mirror/{path} is not Pulp {fixture.pulp_sha}:{path}", message)

    def test_a_cursorless_commit_is_refused_when_pulp_is_not_supplied(self) -> None:
        fixture = self.fixture()
        start = self.commit_dropping_the_cursor(fixture)
        with self.assertRaises(observatory.ObservatoryError) as caught:
            observatory.imported_mirror_history(fixture.vellum, start, "HEAD", None)
        self.assertIn("Pulp identity cannot be proved without the Pulp repository", str(caught.exception))

    def test_a_range_with_every_cursor_needs_no_receipt_verification(self) -> None:
        fixture = self.fixture(mirror_overrides={"core/canvas/src/canvas.cpp": b"// edited after export\n"})
        start = run(fixture.vellum, "rev-parse", "HEAD")
        write(fixture.vellum, "README.md", "changed\n")
        run(fixture.vellum, "add", "-A")
        run(fixture.vellum, "commit", "-q", "-m", "ordinary")
        self.assertEqual(observatory.imported_mirror_history(fixture.vellum, start, "HEAD", fixture.pulp), set())

    def test_mirror_that_is_not_a_directory_is_rejected(self) -> None:
        for mode, kind in (("120000", "blob"), ("160000", "commit")):
            with self.subTest(mode=mode):
                def mirror_entry(fixture: Fixture, mode: str = mode, kind: str = kind) -> None:
                    # A side commit whose `mirror` is a symlink or a gitlink
                    # has the one name a name-only check looks for.
                    if kind == "blob":
                        target = subprocess.run(
                            ["git", "hash-object", "-w", "--stdin"], cwd=fixture.vellum,
                            input=b"../elsewhere", capture_output=True, check=True,
                        ).stdout.decode().strip()
                    else:
                        target = fixture.base_sha
                    tree = subprocess.run(
                        ["git", "mktree"], cwd=fixture.vellum,
                        input=f"{mode} {kind} {target}\tmirror\n".encode(),
                        capture_output=True, check=True,
                    ).stdout.decode().strip()
                    odd = run(fixture.vellum, "commit-tree", tree, "-p", fixture.mirror_sha, "-m", "odd mirror")
                    tip = run(fixture.vellum, "commit-tree", f"{fixture.mirror_sha}^{{tree}}", "-p", odd, "-m", "tip")
                    fixture.mirror_sha = tip
                    self.rebuild_join(fixture, fixture.base_sha, tip)

                fixture = self.fixture(after_join=mirror_entry)
                original = observatory.approved_mirror_shas
                observatory.approved_mirror_shas = lambda root, pulp_repo, tip=fixture.mirror_sha: ({tip}, [])
                try:
                    self.assertRejected(fixture, "holds paths other than mirror/")
                finally:
                    observatory.approved_mirror_shas = original
                self.tearDown()
                self.setUp()

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


class ReExportMirrorImportTests(MirrorImportCase):
    """A re-export whose filter-repo history continues an import already landed."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture_ = self.fixture()
        self.vellum = self.fixture_.vellum
        # The first import has landed: judge only what comes after it.
        self.landed = run(self.vellum, "rev-parse", "HEAD")
        self.first_tip = self.fixture_.mirror_sha

    def mirror_commit(self, parent: str, name: str) -> str:
        """A mirror-only commit on ``parent``, as git-filter-repo writes the next export."""
        tree = run(self.vellum, "rev-parse", f"{self.first_tip}^{{tree}}")
        index = self.base / f"{name}.index"
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        subprocess.run(["git", "read-tree", tree], cwd=self.vellum, env=env, check=True)
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.vellum, input=name.encode(),
            capture_output=True, check=True).stdout.decode().strip()
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},mirror/core/canvas/{name}.txt"],
            cwd=self.vellum, env=env, check=True)
        new_tree = subprocess.run(
            ["git", "write-tree"], cwd=self.vellum, env=env, capture_output=True, check=True).stdout.decode().strip()
        return run(self.vellum, "commit-tree", new_tree, "-p", parent, "-m", name)

    def join(self, tip: str) -> None:
        run(self.vellum, "merge", "-q", "--no-edit", "-s", "ours", "--allow-unrelated-histories", tip)

    def approve(self, approved: set[str]) -> None:
        original = observatory.approved_mirror_shas
        observatory.approved_mirror_shas = lambda root, pulp_repo: (approved, [])
        self.addCleanup(setattr, observatory, "approved_mirror_shas", original)

    def verify(self, approved: set[str]) -> None:
        self.approve(approved)
        observatory.verify_append_only(self.vellum, self.landed, pulp_repo=self.fixture_.pulp)

    def test_re_export_continuing_the_landed_import_passes(self) -> None:
        second = self.mirror_commit(self.first_tip, "second")
        self.join(second)
        self.verify({self.first_tip, second})

    def test_tail_names_a_re_export_that_brings_no_root(self) -> None:
        # The no-root branch of imported_mirror_history: a continuing
        # re-export brings no root commit, yet its commits lack the cursor,
        # so the import check must still run and name exactly the new commit.
        second = self.mirror_commit(self.first_tip, "second")
        self.join(second)
        self.approve({self.first_tip, second})
        head = run(self.vellum, "rev-parse", "HEAD")
        self.assertEqual(run(self.vellum, "rev-list", "--max-parents=0", f"{self.landed}..{head}"), "")
        self.assertEqual(
            observatory.imported_mirror_history(self.vellum, self.landed, head, self.fixture_.pulp),
            {second},
        )

    def test_tail_refuses_a_re_export_when_verification_fails(self) -> None:
        # With no root of its own, a re-export's cursor-less commits must not
        # fall back to ordinary history when nothing could be approved.
        second = self.mirror_commit(self.first_tip, "second")
        self.join(second)
        head = run(self.vellum, "rev-parse", "HEAD")
        original = observatory.approved_mirror_shas
        observatory.approved_mirror_shas = lambda root, pulp_repo: (set(), ["Pulp refutes the receipt"])
        self.addCleanup(setattr, observatory, "approved_mirror_shas", original)
        with self.assertRaisesRegex(observatory.ObservatoryError,
                                    "mirror-export verification failed: Pulp refutes the receipt"):
            observatory.imported_mirror_history(self.vellum, self.landed, head, self.fixture_.pulp)

    def test_mainline_commit_in_the_approved_set_is_not_a_landed_import(self) -> None:
        # Even if a mainline commit were approved, a parent outside the new
        # import must itself be a mirror-only commit to count as landed.
        mainline = run(self.vellum, "rev-parse", f"{self.landed}^1")
        second = self.mirror_commit(mainline, "second")
        self.join(second)
        with self.assertRaisesRegex(observatory.ObservatoryError, "outside the imported history"):
            self.verify({self.first_tip, mainline, second})

    def test_approved_tip_outside_the_base_is_not_a_landed_import(self) -> None:
        # A stray mirror commit in the base that no landed receipt names,
        # and an approved tip that descends from it but never landed (it is
        # in no history of this range): the stray commit is still refused.
        stray = self.mirror_commit(self.first_tip, "stray")
        self.join(stray)
        self.landed = run(self.vellum, "rev-parse", "HEAD")
        dangling = self.mirror_commit(stray, "dangling")
        second = self.mirror_commit(stray, "second")
        self.join(second)
        with self.assertRaisesRegex(observatory.ObservatoryError, "outside the imported history"):
            self.verify({self.first_tip, dangling, second})

    def test_landed_parent_whose_mirror_is_not_a_directory_is_rejected(self) -> None:
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.vellum, input=b"../elsewhere",
            capture_output=True, check=True).stdout.decode().strip()
        tree = subprocess.run(
            ["git", "mktree"], cwd=self.vellum, input=f"120000 blob {blob}\tmirror\n".encode(),
            capture_output=True, check=True).stdout.decode().strip()
        # The symlink commit sits in the first import's approved, landed history.
        link = run(self.vellum, "commit-tree", tree, "-p", self.first_tip, "-m", "link")
        landed_tip = self.mirror_commit(link, "landed")
        self.join(landed_tip)
        self.landed = run(self.vellum, "rev-parse", "HEAD")
        second = self.mirror_commit(link, "second")
        self.join(second)
        with self.assertRaisesRegex(observatory.ObservatoryError, "outside the imported history"):
            self.verify({self.first_tip, landed_tip, second})

    def test_re_export_on_a_mainline_commit_is_rejected(self) -> None:
        second = self.mirror_commit(self.landed, "second")
        self.join(second)
        with self.assertRaisesRegex(observatory.ObservatoryError, "outside the imported history"):
            self.verify({self.first_tip, second})

    def test_re_export_on_an_unapproved_mirror_history_is_rejected(self) -> None:
        # A mirror-only history already in the base, but no accepted receipt's.
        stray = self.mirror_commit(self.first_tip, "stray")
        self.join(stray)
        self.landed = run(self.vellum, "rev-parse", "HEAD")
        second = self.mirror_commit(stray, "second")
        self.join(second)
        with self.assertRaisesRegex(observatory.ObservatoryError, "outside the imported history"):
            self.verify({self.first_tip, second})


def mapping_for(vellum_path: str) -> dict[str, object]:
    return {
        "mappings": [{
            "id": "canvas", "pulp_paths": ["core/canvas/"], "vellum_paths": [vellum_path],
            "symbols": [], "targets": [], "schemas": [], "platform_hosts": [],
            "contract_tests": [], "authority": "pulp-authoritative-untransferred",
        }],
        "transitive_path_rules": [],
    }


class CoverageTailMirrorImportTests(MirrorImportCase):
    """The coverage tail of a pull request that imports a mirror history."""

    def gaps(self, fixture: Fixture, vellum_path: str = "graphics/") -> list[dict[str, str]]:
        cursor = {
            "pulp": {
                "repository": "Generous-Corp/pulp",
                "scan_base_commit": fixture.pulp_sha,
                "last_scanned_commit": fixture.pulp_sha,
            },
            "vellum": {
                "repository": "Generous-Corp/vellum",
                "scan_base_commit": fixture.base_sha,
                "last_scanned_commit": fixture.base_sha,
            },
        }
        gaps = observatory.coverage_gaps(
            root=fixture.vellum, mapping=mapping_for(vellum_path), cursor=cursor, events=[],
            pulp_repo=fixture.pulp, vellum_repo=fixture.vellum,
            vellum_target=run(fixture.vellum, "rev-parse", "HEAD"),
            vellum_tail_policy="coverage",
        )
        return [gap for gap in gaps if gap["source"] == "vellum"]

    def test_imported_history_and_its_join_are_covered(self) -> None:
        self.assertEqual(self.gaps(self.fixture()), [])

    def test_join_diff_is_observed_for_mapped_mirror_paths(self) -> None:
        fixture = self.fixture()
        join = run(fixture.vellum, "rev-parse", "HEAD^")
        gaps = self.gaps(fixture, vellum_path="mirror/core/canvas/")
        # The join carries the import's mapped paths; the imported commits do not.
        self.assertEqual(gaps, [{
            "source": "vellum", "commit": join, "reason": "mapped-commit-after-reconciled-cursor",
        }])

    def test_join_that_changes_the_cursor_is_rejected_by_the_tail(self) -> None:
        def change_cursor(fixture: Fixture) -> None:
            cursor = json.loads((fixture.vellum / CURSOR).read_text())
            cursor["reconciled_at"] = "2026-10-14T00:00:00Z"
            write(fixture.vellum, CURSOR, dump(cursor))
            run(fixture.vellum, "add", "-A")
            run(fixture.vellum, "commit", "-q", "--amend", "--no-edit")

        with self.assertRaisesRegex(observatory.ObservatoryError, "joining an imported mirror history changed"):
            self.gaps(self.fixture(after_join=change_cursor))

    def test_unreceipted_unrelated_history_is_rejected_by_the_tail(self) -> None:
        fixture = self.fixture()
        run(fixture.vellum, "checkout", "-q", "--orphan", "stray")
        run(fixture.vellum, "rm", "-rq", "--cached", ".")
        write(fixture.vellum, "mirror/core/canvas/stray.txt", "stray\n")
        run(fixture.vellum, "add", "mirror/core/canvas/stray.txt")
        run(fixture.vellum, "commit", "-q", "-m", "stray root")
        stray = run(fixture.vellum, "rev-parse", "HEAD")
        run(fixture.vellum, "checkout", "-q", "-f", "main")
        run(fixture.vellum, "merge", "-q", "--no-edit", "-s", "ours", "--allow-unrelated-histories", stray)
        with self.assertRaisesRegex(observatory.ObservatoryError, f"unrelated history rooted at {stray}"):
            self.gaps(fixture)

    def test_failed_receipt_verification_is_rejected_by_the_tail(self) -> None:
        fixture = self.fixture()
        fixture.reland(mirror_tree="0" * 40)
        with self.assertRaisesRegex(observatory.ObservatoryError, "mirror-export verification failed"):
            self.gaps(fixture)


if __name__ == "__main__":
    unittest.main()
