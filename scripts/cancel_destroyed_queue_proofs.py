#!/usr/bin/env python3
"""Cancel the GPU proofs of merge groups the queue has destroyed.

A merge-queue entry's group lives on refs/heads/gh-readonly-queue/main/
pr-<n>-<sha>. When GitHub rebuilds an entry's group (an entry ahead of it
failed or left the queue), the new group keeps pr-<n> with a new suffix and
the old group is destroyed. GitHub Actions delivers no event for a destroyed
group, so the old group's Locked macOS GPU proof would otherwise keep its
place in the runner queue, or keep building, for a commit that can no longer
land.

Run from a merge_group run of gpu-macos.yml, this cancels every older
queued or running merge_group run of the same workflow, in this repository,
whose ref names the same entry. A pull request is the last entry of at most
one live group, so an older run for the same entry is always a destroyed
group's. A ref that is not a queue entry's cancels nothing. Any error cancels
nothing more and exits 0 with a warning: this job is not a required check,
and the proof jobs never depend on it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Callable

WORKFLOW_PATH = ".github/workflows/gpu-macos.yml"
QUEUE_REF = re.compile(r"^refs/heads/gh-readonly-queue/main/(pr-[1-9][0-9]*)-[0-9a-f]{40}$")
QUEUE_BRANCH = "^gh-readonly-queue/main/{entry}-[0-9a-f]{{40}}$"
# Every state of a run that has not finished.
UNFINISHED = ("queued", "in_progress", "waiting", "pending", "requested")

Api = Callable[[str], Any]
Cancel = Callable[[int], None]


def queue_entry(ref: str) -> str | None:
    """pr-<n> for a merge-queue ref, None for any other ref."""
    match = QUEUE_REF.fullmatch(ref)
    return match.group(1) if match else None


def superseded_runs(api: Api, *, repository: str, entry: str, run_id: int) -> list[int]:
    """Older unfinished merge_group runs of this workflow for the same entry."""
    branch = re.compile(QUEUE_BRANCH.format(entry=re.escape(entry)))
    found: set[int] = set()
    for status in UNFINISHED:
        for page in range(1, 11):
            batch = api(
                f"repos/{repository}/actions/workflows/gpu-macos.yml/runs"
                f"?event=merge_group&status={status}&per_page=100&page={page}"
            ).get("workflow_runs", [])
            for run in batch:
                other = run.get("id")
                if (type(other) is int and 0 < other < run_id
                        and run.get("event") == "merge_group"
                        and run.get("path") == WORKFLOW_PATH
                        and run.get("status") == status
                        and (run.get("repository") or {}).get("full_name") == repository
                        and (run.get("head_repository") or {}).get("full_name") == repository
                        and isinstance(run.get("head_branch"), str)
                        and branch.fullmatch(run["head_branch"])):
                    found.add(other)
            if len(batch) < 100:
                break
    return sorted(found)


def run(*, repository: str, ref: str, run_id: int, api: Api, cancel: Cancel) -> list[int]:
    entry = queue_entry(ref)
    if entry is None:
        print(f"{ref} is not a merge-queue entry; nothing to cancel")
        return []
    stale = superseded_runs(api, repository=repository, entry=entry, run_id=run_id)
    for other in stale:
        cancel(other)
        print(f"cancelled run {other}: an older merge group for {entry}")
    if not stale:
        print(f"no older merge group for {entry} is still running")
    return stale


def gh_api(path: str) -> Any:
    return json.loads(subprocess.run(
        ["gh", "api", path], check=True, text=True, capture_output=True,
    ).stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    args = parser.parse_args(argv)

    def gh_cancel(other: int) -> None:
        subprocess.run(
            ["gh", "api", "--method", "POST",
             f"repos/{args.repository}/actions/runs/{other}/cancel"],
            check=True, text=True, capture_output=True,
        )

    try:
        run(repository=args.repository, ref=args.ref, run_id=args.run_id,
            api=gh_api, cancel=gh_cancel)
    except Exception as error:  # noqa: BLE001 - never fail the queue run over cleanup.
        print(f"::warning::could not cancel destroyed merge groups: {type(error).__name__}: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
