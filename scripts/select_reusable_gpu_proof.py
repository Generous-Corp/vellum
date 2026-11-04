#!/usr/bin/env python3
"""Find a pull-request GPU proof that already ran on this merge-queue tree.

A merge-queue entry's synthetic commit usually carries exactly the tree its
pull request's own merge ref did: when no other change landed on main between
the pull request's proof and the queue's, GitHub builds the same merge twice.
The GPU proof is a pure function of that tree (every external input is pinned
by digest inside it), so this selector lets the queue run report the earlier
result instead of rebuilding the same tree.

It prints one workflow run ID, and exits 0, only when every one of these holds:

* HEAD is the queue commit and has exactly two parents; the second is the
  pull request's head.
* A completed, successful ``pull_request`` run of this same workflow file, on
  that head, from this repository (never a fork), exists.
* In that run's latest attempt both proof jobs concluded ``success`` (not
  ``skipped``).
* That attempt uploaded the tree receipt, and the receipt names this exact
  tree, this run, and this attempt.

Anything else, including any API or parsing error, prints a reason on stderr
and exits 3: the caller then runs the full proof. The selector can only ever
decline to reuse; it never fails a run and never widens what may be reused.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable


SHA40 = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_PATH = ".github/workflows/gpu-macos.yml"
RECEIPT_ARTIFACT = "vellum-gpu-proof-tree"
RECEIPT_FILE = "gpu-proof-tree.json"
RECEIPT_SCHEMA = "vellum.gpu-proof-tree.v1"
PROOF_JOBS = ("gpu-macos-arm64", "sterile-consumer")
NO_REUSE = 3


class Decline(Exception):
    """The run must not be reused; the message says why."""


Api = Callable[[str], Any]
Download = Callable[[str], bytes]


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def queue_commit(root: Path, merge_group_sha: str) -> tuple[str, str]:
    """Return (tree, pull request head) for the checked-out queue commit."""
    if not SHA40.fullmatch(merge_group_sha):
        raise Decline("merge-group SHA is not a full lowercase SHA")
    head = git(root, "rev-parse", "HEAD")
    if head != merge_group_sha:
        raise Decline(f"HEAD {head} is not the merge-group commit {merge_group_sha}")
    parents = git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    if len(parents) != 2:
        raise Decline(f"queue commit has {len(parents)} parents, not 2")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    if not SHA40.fullmatch(tree) or not all(SHA40.fullmatch(p) for p in parents):
        raise Decline("git returned a malformed object ID")
    return tree, parents[1]


def read_receipt(archive: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
        if names != [RECEIPT_FILE]:
            raise Decline(f"receipt artifact holds {names}, not [{RECEIPT_FILE!r}]")
        value = json.loads(bundle.read(RECEIPT_FILE).decode("utf-8"))
    if not isinstance(value, dict):
        raise Decline("receipt is not a JSON object")
    return value


def check_run(
    run: dict[str, Any], *, repository: str, head_sha: str, tree: str,
    api: Api, download: Download,
) -> int:
    run_id = run.get("id")
    attempt = run.get("run_attempt")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise Decline("run has an invalid ID")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt <= 0:
        raise Decline(f"run {run_id} has an invalid attempt")
    if run.get("event") != "pull_request":
        raise Decline(f"run {run_id} is a {run.get('event')} run")
    if run.get("path") != WORKFLOW_PATH:
        raise Decline(f"run {run_id} ran {run.get('path')}, not {WORKFLOW_PATH}")
    if run.get("head_sha") != head_sha:
        raise Decline(f"run {run_id} ran head {run.get('head_sha')}")
    if (run.get("head_repository") or {}).get("full_name") != repository:
        raise Decline(f"run {run_id} ran a head outside {repository}")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise Decline(f"run {run_id} is {run.get('status')}/{run.get('conclusion')}")

    jobs = api(f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    by_name: dict[str, list[dict[str, Any]]] = {}
    for job in jobs.get("jobs", []):
        by_name.setdefault(job.get("name"), []).append(job)
    for name in PROOF_JOBS:
        matches = by_name.get(name, [])
        if len(matches) != 1:
            raise Decline(f"run {run_id} has {len(matches)} {name} jobs")
        job = matches[0]
        if job.get("conclusion") != "success" or job.get("run_attempt") != attempt:
            raise Decline(
                f"run {run_id} {name} is {job.get('conclusion')} "
                f"in attempt {job.get('run_attempt')}"
            )

    listing = api(
        f"repos/{repository}/actions/runs/{run_id}/artifacts"
        f"?name={RECEIPT_ARTIFACT}&per_page=100"
    )
    artifacts = [
        artifact for artifact in listing.get("artifacts", [])
        if artifact.get("name") == RECEIPT_ARTIFACT and not artifact.get("expired")
        and (artifact.get("workflow_run") or {}).get("id") == run_id
    ]
    for artifact in artifacts:
        receipt = read_receipt(download(
            f"repos/{repository}/actions/artifacts/{artifact['id']}/zip"
        ))
        if receipt.get("run_attempt") != attempt:
            continue
        expected = {
            "schema": RECEIPT_SCHEMA,
            "event": "pull_request",
            "repository": repository,
            "run_id": run_id,
            "run_attempt": attempt,
            "tree": tree,
        }
        for key, value in expected.items():
            if receipt.get(key) != value:
                raise Decline(
                    f"run {run_id} receipt {key} is {receipt.get(key)!r}, not {value!r}"
                )
        return run_id
    raise Decline(f"run {run_id} attempt {attempt} uploaded no tree receipt")


def select(
    *, repository: str, tree: str, head_sha: str, api: Api, download: Download,
) -> int:
    runs = api(
        f"repos/{repository}/actions/workflows/gpu-macos.yml/runs"
        f"?event=pull_request&head_sha={head_sha}&per_page=20"
    ).get("workflow_runs", [])
    reasons = []
    # Newest first: a later attempt or push of the same head is the one that
    # describes the current merge ref.
    for run in sorted(runs, key=lambda value: value.get("id") or 0, reverse=True):
        try:
            return check_run(
                run, repository=repository, head_sha=head_sha, tree=tree,
                api=api, download=download,
            )
        except Decline as reason:
            reasons.append(str(reason))
    raise Decline("; ".join(reasons) or f"no pull_request run on {head_sha}")


def gh_api(path: str) -> Any:
    return json.loads(subprocess.run(
        ["gh", "api", path], check=True, text=True, capture_output=True,
    ).stdout)


def gh_download(path: str) -> bytes:
    return subprocess.run(["gh", "api", path], check=True, capture_output=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--merge-group-sha", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        tree, head_sha = queue_commit(args.root, args.merge_group_sha)
        run_id = select(
            repository=args.repository, tree=tree, head_sha=head_sha,
            api=gh_api, download=gh_download,
        )
    except Decline as reason:
        print(f"no reusable proof: {reason}", file=sys.stderr)
        return NO_REUSE
    except Exception as error:  # noqa: BLE001 - any doubt means a full proof.
        print(f"no reusable proof: {type(error).__name__}: {error}", file=sys.stderr)
        return NO_REUSE
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
