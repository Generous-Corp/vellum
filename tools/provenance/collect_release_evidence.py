#!/usr/bin/env python3
"""Collate GitHub API pages into the evidence files the authority verifier reads.

The provenance workflow fetches every GitHub API response with `gh api` and
writes it to disk; this script only reshapes those recorded responses. It never
calls the network, so each collation is reproducible from the recorded pages.

`verify_authority_expansion.py --release-readiness` trusts the files written
here (`--check-runs-json`, `--open-pr-snapshot-json`), and the live open-PR
cursor written here is compared byte-for-byte with `cmp` before and after the
verifier runs. Output bytes are therefore part of the contract: JSON is written
with sorted keys and a trailing newline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from verify_authority_expansion import RETIRED_OPEN_PR_REPOSITORIES

# The verifier accepts exactly these repositories in the open-PR snapshot once
# the temporary delivery repository is retired, so the snapshot and the live
# cursor are collected for this set and nothing else. Sorted order is the
# order the snapshot and cursor list them in.
OPEN_PR_REPOSITORIES = tuple(sorted(RETIRED_OPEN_PR_REPOSITORIES))

REQUIRED_VELLUM_CHECKS = frozenset(
    {
        "clean-release", "forbidden-deps", "gpu-macos-arm64",
        "product-quality", "sterile-consumer",
    }
)
CHECK_RUN_DETAILS_URL = re.compile(
    r"https://github\.com/(Generous-Corp/(?:vellum|pulp))/actions/runs/([0-9]+)/job/[0-9]+"
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def repository_slug(repository: str) -> str:
    return repository.replace("/", "__")


def merge_check_run_pages(page_sets: Iterable[list[Any]]) -> dict[str, Any]:
    """Flatten `gh api --paginate --slurp` check-run pages into one list."""
    pages = [page for page_set in page_sets for page in page_set]
    return {"check_runs": [row for page in pages for row in page.get("check_runs", [])]}


def parity_run_ids(check_runs: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    """Select the workflow runs whose metadata and receipts must be fetched.

    Vellum's required release checks are taken whatever their state, so the
    verifier can reject a failed one; Pulp's routing contract is taken only
    when it completed successfully.
    """
    ids = set()
    for row in check_runs:
        match = CHECK_RUN_DETAILS_URL.fullmatch(row.get("details_url", ""))
        if not match:
            continue
        repository = match.group(1)
        name = row.get("name")
        if (
            (
                repository == "Generous-Corp/pulp"
                and name == "vellum-routing-contract"
                and row.get("status") == "completed"
                and row.get("conclusion") == "success"
            )
            or (repository == "Generous-Corp/vellum" and name in REQUIRED_VELLUM_CHECKS)
        ):
            ids.add((repository, int(match.group(2)), name))
    return sorted(ids)


def check_runs_evidence(
    check_runs: list[Any], workflow_runs_dir: Path, attestations_dir: Path
) -> dict[str, Any]:
    runs = [read_json(path) for path in sorted(workflow_runs_dir.glob("*.json"))]
    receipts = [read_json(path) for path in sorted(attestations_dir.rglob("*.json"))]
    return {
        "check_runs": check_runs,
        "workflow_runs": runs,
        "parity_proof_execution_receipts": [
            row for row in receipts
            if row.get("kind") == "vellum-parity-proof-execution"
        ],
        "pulp_router_contract_receipts": [
            row for row in receipts
            if row.get("kind") == "pulp-vellum-routing-contract-execution"
        ],
    }


def open_pull_numbers(pages: list[list[dict[str, Any]]]) -> list[int]:
    numbers = sorted(pull["number"] for page in pages for pull in page)
    if len(numbers) != len(set(numbers)):
        raise SystemExit("open PR API returned duplicate pull request numbers")
    return numbers


def tree_leaves(tree: dict[str, Any]) -> dict[str, tuple[Any, Any, Any]]:
    return {
        row["path"]: (row.get("mode"), row.get("type"), row.get("sha"))
        for row in tree.get("tree", [])
        if isinstance(row, dict) and row.get("type") != "tree"
    }


def changed_paths(merge_base_tree: dict[str, Any], head_tree: dict[str, Any]) -> list[str]:
    """Paths whose blob identity differs between two recursive trees.

    A path counts as changed when it was added, removed, or changed mode,
    type or object id.
    """
    before, after = tree_leaves(merge_base_tree), tree_leaves(head_tree)
    return sorted(
        path for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def snapshot_pull(
    repository: str, repository_root: Path, pull: dict[str, Any]
) -> dict[str, Any]:
    number = pull["number"]
    detail = read_json(repository_root / "details" / f"{number}.json")
    if (
        detail.get("number") != number
        or detail.get("head", {}).get("sha") != pull["head"]["sha"]
        or detail.get("base", {}).get("sha") != pull["base"]["sha"]
    ):
        raise SystemExit(f"{repository}#{number}: list/detail head changed")
    trees = repository_root / "trees"
    comparison = read_json(trees / f"{number}-compare.json")
    merge_base_commit = read_json(trees / f"{number}-merge-base-commit.json")
    head_commit = read_json(trees / f"{number}-head-commit.json")
    merge_base_sha = comparison.get("merge_base_commit", {}).get("sha")
    if (
        merge_base_commit.get("sha") != merge_base_sha
        or head_commit.get("sha") != detail["head"]["sha"]
    ):
        raise SystemExit(f"{repository}#{number}: commit lookup differs")
    merge_base_tree = read_json(trees / f"{number}-merge-base-tree.json")
    head_tree = read_json(trees / f"{number}-head-tree.json")
    # GitHub truncates large recursive trees; a truncated listing would make
    # the changed-path set silently incomplete, so anything but an explicit
    # `false` is refused.
    if merge_base_tree.get("truncated") is not False or head_tree.get("truncated") is not False:
        raise SystemExit(f"{repository}#{number}: recursive tree is truncated")
    paths = changed_paths(merge_base_tree, head_tree)
    return {
        "number": number,
        "base_commit": detail["base"]["sha"],
        "merge_base_commit": merge_base_sha,
        "head_commit": detail["head"]["sha"],
        "paths": paths,
        "diff_path_count": len(paths),
    }


def open_pr_snapshot(snapshot_root: Path) -> dict[str, Any]:
    repositories = []
    for repository in OPEN_PR_REPOSITORIES:
        repository_root = snapshot_root / repository_slug(repository)
        pulls = [pull for page in read_json(repository_root / "pulls.json") for pull in page]
        rows = [
            snapshot_pull(repository, repository_root, pull)
            for pull in sorted(pulls, key=lambda row: row["number"])
        ]
        repositories.append({"repository": repository, "pulls": rows})
    return {
        "schema_version": 2,
        "kind": "github-open-pull-request-snapshot",
        "repositories": repositories,
    }


def snapshot_cursor(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "repository": row["repository"],
            "pulls": [
                {"number": pull["number"], "base_commit": pull["base_commit"],
                 "head_commit": pull["head_commit"]}
                for pull in row["pulls"]
            ],
        }
        for row in snapshot["repositories"]
    ]


def live_cursor(cursor_root: Path) -> list[dict[str, Any]]:
    repositories = []
    for repository in OPEN_PR_REPOSITORIES:
        pages = read_json(cursor_root / (repository_slug(repository) + ".json"))
        pulls = [pull for page in pages for pull in page]
        rows = sorted(
            ({"number": pull["number"], "base_commit": pull["base"]["sha"],
              "head_commit": pull["head"]["sha"]}
             for pull in pulls),
            key=lambda row: row["number"],
        )
        if len(rows) != len({row["number"] for row in rows}):
            raise SystemExit(f"{repository}: duplicate open PR numbers")
        repositories.append({"repository": repository, "pulls": rows})
    return repositories


def command_repositories(_: argparse.Namespace) -> None:
    for repository in OPEN_PR_REPOSITORIES:
        print(repository)


def command_merge_check_runs(args: argparse.Namespace) -> None:
    write_json(args.output, merge_check_run_pages(read_json(path) for path in args.pages))


def command_parity_run_ids(args: argparse.Namespace) -> None:
    for repository, run_id, name in parity_run_ids(read_json(args.check_runs)["check_runs"]):
        print(repository, run_id, name)


def command_check_runs(args: argparse.Namespace) -> None:
    write_json(
        args.output,
        check_runs_evidence(
            read_json(args.check_runs)["check_runs"],
            args.workflow_runs,
            args.execution_attestations,
        ),
    )


def command_open_pr_numbers(args: argparse.Namespace) -> None:
    for number in open_pull_numbers(read_json(args.pulls)):
        print(number)


def command_pull_coordinates(args: argparse.Namespace) -> None:
    detail = read_json(args.detail)
    print(detail["base"]["sha"], detail["head"]["sha"])


def command_merge_base_commit(args: argparse.Namespace) -> None:
    print(read_json(args.compare)["merge_base_commit"]["sha"])


def command_commit_tree(args: argparse.Namespace) -> None:
    print(read_json(args.commit)["tree"]["sha"])


def command_open_pr_snapshot(args: argparse.Namespace) -> None:
    snapshot = open_pr_snapshot(args.snapshot_root)
    write_json(args.output, snapshot)
    write_json(args.cursor_output, snapshot_cursor(snapshot))


def command_live_cursor(args: argparse.Namespace) -> None:
    write_json(args.output, live_cursor(args.cursor_root))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = root.add_subparsers(dest="command", required=True)

    command = commands.add_parser(
        "repositories", help="print the repositories whose open PRs are audited"
    )
    command.set_defaults(handler=command_repositories)

    command = commands.add_parser(
        "merge-check-runs",
        help="flatten slurped check-run pages into {check_runs: [...]}",
    )
    command.add_argument("--pages", type=Path, action="append", required=True)
    command.add_argument("--output", type=Path, required=True)
    command.set_defaults(handler=command_merge_check_runs)

    command = commands.add_parser(
        "parity-run-ids",
        help="print 'repository run_id check_name' for runs to fetch",
    )
    command.add_argument("--check-runs", type=Path, required=True)
    command.set_defaults(handler=command_parity_run_ids)

    command = commands.add_parser(
        "check-runs", help="write the verifier's --check-runs-json evidence"
    )
    command.add_argument("--check-runs", type=Path, required=True)
    command.add_argument("--workflow-runs", type=Path, required=True)
    command.add_argument("--execution-attestations", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.set_defaults(handler=command_check_runs)

    command = commands.add_parser(
        "open-pr-numbers", help="print the open PR numbers from slurped pulls pages"
    )
    command.add_argument("--pulls", type=Path, required=True)
    command.set_defaults(handler=command_open_pr_numbers)

    command = commands.add_parser(
        "pull-coordinates", help="print '<base sha> <head sha>' of a PR detail"
    )
    command.add_argument("--detail", type=Path, required=True)
    command.set_defaults(handler=command_pull_coordinates)

    command = commands.add_parser(
        "merge-base-commit", help="print the merge-base commit of a compare response"
    )
    command.add_argument("--compare", type=Path, required=True)
    command.set_defaults(handler=command_merge_base_commit)

    command = commands.add_parser(
        "commit-tree", help="print the tree SHA of a git commit response"
    )
    command.add_argument("--commit", type=Path, required=True)
    command.set_defaults(handler=command_commit_tree)

    command = commands.add_parser(
        "open-pr-snapshot",
        help="write the verifier's --open-pr-snapshot-json and its cursor",
    )
    command.add_argument("--snapshot-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--cursor-output", type=Path, required=True)
    command.set_defaults(handler=command_open_pr_snapshot)

    command = commands.add_parser(
        "live-cursor", help="write the open-PR cursor from freshly slurped pulls pages"
    )
    command.add_argument("--cursor-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.set_defaults(handler=command_live_cursor)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
