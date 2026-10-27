#!/usr/bin/env python3
"""Unit tests for the release-evidence collation behind --release-readiness."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SCRIPT = HERE / "collect_release_evidence.py"
SPEC = importlib.util.spec_from_file_location("collect_release_evidence", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)

import verify_authority_expansion as verifier  # noqa: E402


def sha(character: str) -> str:
    return character * 40


def check_run(name: str, repository: str, run_id: int, **extra: Any) -> dict[str, Any]:
    row = {
        "name": name,
        "details_url": f"https://github.com/{repository}/actions/runs/{run_id}/job/{run_id + 1}",
        "status": "completed",
        "conclusion": "success",
    }
    row.update(extra)
    return row


def pull(number: int, base: str, head: str) -> dict[str, Any]:
    return {"number": number, "base": {"sha": base}, "head": {"sha": head}}


def blob(path: str, object_sha: str, mode: str = "100644") -> dict[str, Any]:
    return {"path": path, "mode": mode, "type": "blob", "sha": object_sha}


def tree(rows: list[dict[str, Any]], *, truncated: Any = False) -> dict[str, Any]:
    value: dict[str, Any] = {"sha": sha("e"), "tree": rows}
    if truncated is not None:
        value["truncated"] = truncated
    return value


def write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def run_cli(*argv: str) -> str:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        collector.main(list(argv))
    return stdout.getvalue()


class SnapshotFixture:
    """The on-disk layout the workflow writes under open-pr-snapshot-input."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for repository in collector.OPEN_PR_REPOSITORIES:
            write(self.repository_root(repository) / "pulls.json", [])

    def repository_root(self, repository: str) -> Path:
        return self.root / repository.replace("/", "__")

    def add_pages(self, repository: str, pages: list[list[dict[str, Any]]]) -> None:
        write(self.repository_root(repository) / "pulls.json", pages)

    def add_pull(
        self,
        repository: str,
        listed: dict[str, Any],
        merge_base_tree: dict[str, Any],
        head_tree: dict[str, Any],
        *,
        merge_base: str = sha("c"),
        detail: dict[str, Any] | None = None,
    ) -> None:
        root = self.repository_root(repository)
        number = listed["number"]
        write(root / "details" / f"{number}.json", detail or listed)
        trees = root / "trees"
        write(trees / f"{number}-compare.json", {"merge_base_commit": {"sha": merge_base}})
        write(
            trees / f"{number}-merge-base-commit.json",
            {"sha": merge_base, "tree": {"sha": sha("1")}},
        )
        write(
            trees / f"{number}-head-commit.json",
            {"sha": listed["head"]["sha"], "tree": {"sha": sha("2")}},
        )
        write(trees / f"{number}-merge-base-tree.json", merge_base_tree)
        write(trees / f"{number}-head-tree.json", head_tree)


class RepositorySetTests(unittest.TestCase):
    def test_audited_repositories_come_from_the_verifier(self) -> None:
        self.assertEqual(
            set(collector.OPEN_PR_REPOSITORIES), verifier.RETIRED_OPEN_PR_REPOSITORIES
        )
        self.assertEqual(
            collector.OPEN_PR_REPOSITORIES, ("Generous-Corp/pulp", "Generous-Corp/vellum")
        )
        self.assertEqual(
            run_cli("repositories"), "Generous-Corp/pulp\nGenerous-Corp/vellum\n"
        )


class CheckRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_paginated_pages_from_both_commits_merge_in_order(self) -> None:
        vellum_pages = [
            {"total_count": 3, "check_runs": [check_run("forbidden-deps", "Generous-Corp/vellum", 10)]},
            {"total_count": 3, "check_runs": [
                check_run("clean-release", "Generous-Corp/vellum", 11),
                check_run("product-quality", "Generous-Corp/vellum", 12),
            ]},
        ]
        pulp_pages = [
            {"total_count": 1, "check_runs": [
                check_run("vellum-routing-contract", "Generous-Corp/pulp", 20)
            ]},
        ]
        output = self.root / "raw.json"
        run_cli(
            "merge-check-runs",
            "--pages", str(write(self.root / "vellum.json", vellum_pages)),
            "--pages", str(write(self.root / "pulp.json", pulp_pages)),
            "--output", str(output),
        )
        expected = {
            "check_runs": [
                *vellum_pages[0]["check_runs"],
                *vellum_pages[1]["check_runs"],
                *pulp_pages[0]["check_runs"],
            ]
        }
        self.assertEqual(
            output.read_bytes(),
            (json.dumps(expected, sort_keys=True) + "\n").encode("utf-8"),
        )

    def test_empty_pages_produce_an_empty_check_run_list(self) -> None:
        output = self.root / "raw.json"
        run_cli(
            "merge-check-runs",
            "--pages", str(write(self.root / "vellum.json", [])),
            "--pages", str(write(self.root / "pulp.json", [{"total_count": 0}])),
            "--output", str(output),
        )
        self.assertEqual(output.read_text(encoding="utf-8"), '{"check_runs": []}\n')

    def test_run_selection_keeps_required_checks_and_successful_pulp_contract(self) -> None:
        rows = [
            check_run("clean-release", "Generous-Corp/vellum", 30),
            # A failed required Vellum check is still collected so the
            # verifier can reject it.
            check_run("gpu-macos-arm64", "Generous-Corp/vellum", 31, conclusion="failure"),
            check_run("unrelated", "Generous-Corp/vellum", 32),
            check_run("vellum-routing-contract", "Generous-Corp/pulp", 40),
            check_run("vellum-routing-contract", "Generous-Corp/pulp", 41, conclusion="failure"),
            check_run("vellum-routing-contract", "Generous-Corp/pulp", 42, status="in_progress"),
            check_run("clean-release", "someone/vellum", 50),
            {"name": "clean-release", "details_url": "https://example.com/x"},
            {"name": "clean-release"},
            # Two jobs of one run collapse to a single fetch.
            check_run("clean-release", "Generous-Corp/vellum", 30),
        ]
        raw = write(self.root / "raw.json", {"check_runs": rows})
        self.assertEqual(
            run_cli("parity-run-ids", "--check-runs", str(raw)),
            "Generous-Corp/pulp 40 vellum-routing-contract\n"
            "Generous-Corp/vellum 30 clean-release\n"
            "Generous-Corp/vellum 31 gpu-macos-arm64\n",
        )

    def test_check_run_evidence_partitions_receipts_by_kind(self) -> None:
        rows = [check_run("clean-release", "Generous-Corp/vellum", 30)]
        raw = write(self.root / "raw.json", {"check_runs": rows})
        runs = self.root / "runs"
        write(runs / "b.json", {"id": 2})
        write(runs / "a.json", {"id": 1})
        attestations = self.root / "attestations"
        (attestations / "empty-download").mkdir(parents=True)
        write(attestations / "x" / "receipt.json", {"kind": "vellum-parity-proof-execution", "n": 1})
        write(attestations / "y" / "nested" / "r.json", {"kind": "pulp-vellum-routing-contract-execution"})
        write(attestations / "z" / "other.json", {"kind": "something-else"})
        output = self.root / "check-runs.json"
        run_cli(
            "check-runs", "--check-runs", str(raw), "--workflow-runs", str(runs),
            "--execution-attestations", str(attestations), "--output", str(output),
        )
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8")),
            {
                "check_runs": rows,
                "workflow_runs": [{"id": 1}, {"id": 2}],
                "parity_proof_execution_receipts": [
                    {"kind": "vellum-parity-proof-execution", "n": 1}
                ],
                "pulp_router_contract_receipts": [
                    {"kind": "pulp-vellum-routing-contract-execution"}
                ],
            },
        )


class OpenPullRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = SnapshotFixture(self.root / "snapshot")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def snapshot(self) -> tuple[Any, Any]:
        output, cursor = self.root / "snapshot.json", self.root / "cursor.json"
        run_cli(
            "open-pr-snapshot", "--snapshot-root", str(self.fixture.root),
            "--output", str(output), "--cursor-output", str(cursor),
        )
        return (
            json.loads(output.read_text(encoding="utf-8")),
            json.loads(cursor.read_text(encoding="utf-8")),
        )

    def test_open_pr_numbers_merge_pages_and_refuse_duplicates(self) -> None:
        pulls = write(self.root / "pulls.json", [[pull(9, sha("a"), sha("b"))], [pull(3, sha("a"), sha("b"))]])
        self.assertEqual(run_cli("open-pr-numbers", "--pulls", str(pulls)), "3\n9\n")
        empty = write(self.root / "empty.json", [])
        self.assertEqual(run_cli("open-pr-numbers", "--pulls", str(empty)), "")
        duplicate = write(self.root / "dup.json", [[pull(3, sha("a"), sha("b"))], [pull(3, sha("a"), sha("b"))]])
        with self.assertRaisesRegex(SystemExit, "duplicate pull request numbers"):
            run_cli("open-pr-numbers", "--pulls", str(duplicate))

    def test_coordinate_lookups_print_the_fields_the_shell_reads(self) -> None:
        detail = write(self.root / "detail.json", pull(3, sha("a"), sha("b")))
        self.assertEqual(
            run_cli("pull-coordinates", "--detail", str(detail)), f"{sha('a')} {sha('b')}\n"
        )
        compare = write(self.root / "compare.json", {"merge_base_commit": {"sha": sha("c")}})
        self.assertEqual(run_cli("merge-base-commit", "--compare", str(compare)), sha("c") + "\n")
        commit = write(self.root / "commit.json", {"sha": sha("c"), "tree": {"sha": sha("d")}})
        self.assertEqual(run_cli("commit-tree", "--commit", str(commit)), sha("d") + "\n")

    def test_changed_paths_cover_added_removed_and_mode_changes(self) -> None:
        listed = pull(7, sha("a"), sha("b"))
        before = tree([
            blob("same.txt", sha("1")),
            blob("edited.txt", sha("2")),
            blob("removed.txt", sha("3")),
            blob("script.sh", sha("4")),
            {"path": "dir", "mode": "040000", "type": "tree", "sha": sha("5")},
        ])
        after = tree([
            blob("same.txt", sha("1")),
            blob("edited.txt", sha("6")),
            blob("script.sh", sha("4"), mode="100755"),
            blob("dir/added.txt", sha("7")),
            {"path": "dir", "mode": "040000", "type": "tree", "sha": sha("8")},
            "not-an-entry",
        ])
        self.fixture.add_pages("Generous-Corp/vellum", [[listed]])
        self.fixture.add_pull("Generous-Corp/vellum", listed, before, after)
        snapshot, _ = self.snapshot()
        row = snapshot["repositories"][1]["pulls"][0]
        self.assertEqual(
            row["paths"], ["dir/added.txt", "edited.txt", "removed.txt", "script.sh"]
        )
        self.assertEqual(row["diff_path_count"], 4)
        self.assertEqual(row["merge_base_commit"], sha("c"))

    def test_paginated_snapshot_and_cursor_are_sorted_and_agree_with_live_cursor(self) -> None:
        pulls = [pull(12, sha("a"), sha("b")), pull(4, sha("a"), sha("d"))]
        pages = [[pulls[0]], [pulls[1]]]
        unchanged = tree([blob("a.txt", sha("1"))])
        self.fixture.add_pages("Generous-Corp/pulp", pages)
        for listed in pulls:
            self.fixture.add_pull("Generous-Corp/pulp", listed, unchanged, unchanged)
        snapshot, cursor = self.snapshot()
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["kind"], "github-open-pull-request-snapshot")
        self.assertEqual(
            [row["repository"] for row in snapshot["repositories"]],
            ["Generous-Corp/pulp", "Generous-Corp/vellum"],
        )
        self.assertEqual([row["number"] for row in snapshot["repositories"][0]["pulls"]], [4, 12])
        self.assertEqual(snapshot["repositories"][0]["pulls"][0]["paths"], [])
        self.assertEqual(snapshot["repositories"][1]["pulls"], [])
        self.assertEqual(
            cursor,
            [
                {"repository": "Generous-Corp/pulp", "pulls": [
                    {"number": 4, "base_commit": sha("a"), "head_commit": sha("d")},
                    {"number": 12, "base_commit": sha("a"), "head_commit": sha("b")},
                ]},
                {"repository": "Generous-Corp/vellum", "pulls": []},
            ],
        )
        # The workflow `cmp`s the snapshot cursor against a fresh live cursor,
        # so identical PR state must produce identical bytes.
        live_root = self.root / "live"
        write(live_root / "Generous-Corp__pulp.json", pages)
        write(live_root / "Generous-Corp__vellum.json", [])
        live = self.root / "live-cursor.json"
        run_cli("live-cursor", "--cursor-root", str(live_root), "--output", str(live))
        self.assertEqual(live.read_bytes(), (self.root / "cursor.json").read_bytes())

    def test_empty_snapshot_lists_every_repository(self) -> None:
        snapshot, cursor = self.snapshot()
        self.assertEqual(
            snapshot["repositories"],
            [{"repository": "Generous-Corp/pulp", "pulls": []},
             {"repository": "Generous-Corp/vellum", "pulls": []}],
        )
        self.assertEqual(cursor, snapshot["repositories"])

    def test_truncated_or_unstated_recursive_tree_fails_closed(self) -> None:
        listed = pull(5, sha("a"), sha("b"))
        complete = tree([blob("a.txt", sha("1"))])
        self.fixture.add_pages("Generous-Corp/pulp", [[listed]])
        for side, bad in (("head", True), ("merge-base", True), ("head", None)):
            with self.subTest(side=side, truncated=bad):
                broken = tree([blob("a.txt", sha("1"))], truncated=bad)
                if side == "head":
                    self.fixture.add_pull("Generous-Corp/pulp", listed, complete, broken)
                else:
                    self.fixture.add_pull("Generous-Corp/pulp", listed, broken, complete)
                with self.assertRaisesRegex(SystemExit, "recursive tree is truncated"):
                    self.snapshot()

    def test_detail_or_commit_lookup_drift_is_refused(self) -> None:
        listed = pull(5, sha("a"), sha("b"))
        complete = tree([])
        self.fixture.add_pages("Generous-Corp/pulp", [[listed]])
        self.fixture.add_pull(
            "Generous-Corp/pulp", listed, complete, complete,
            detail=pull(5, sha("a"), sha("f")),
        )
        with self.assertRaisesRegex(SystemExit, "list/detail head changed"):
            self.snapshot()
        self.fixture.add_pull("Generous-Corp/pulp", listed, complete, complete)
        write(
            self.fixture.repository_root("Generous-Corp/pulp") / "trees" / "5-compare.json",
            {"merge_base_commit": {"sha": sha("9")}},
        )
        with self.assertRaisesRegex(SystemExit, "commit lookup differs"):
            self.snapshot()

    def test_live_cursor_refuses_duplicate_numbers(self) -> None:
        live_root = self.root / "live"
        duplicate = [[pull(3, sha("a"), sha("b"))], [pull(3, sha("a"), sha("b"))]]
        write(live_root / "Generous-Corp__pulp.json", [])
        write(live_root / "Generous-Corp__vellum.json", duplicate)
        with self.assertRaisesRegex(SystemExit, "Generous-Corp/vellum: duplicate open PR numbers"):
            run_cli("live-cursor", "--cursor-root", str(live_root), "--output", str(self.root / "c.json"))


if __name__ == "__main__":
    unittest.main()
