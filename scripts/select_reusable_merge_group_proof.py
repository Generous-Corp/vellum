#!/usr/bin/env python3
"""Find the merge-queue GPU proof that already ran on this exact main commit.

The merge queue fast-forwards main to the commit its ``merge_group`` run
proved, so the ``push`` run that follows carries the very same commit, and
therefore the same tree. This selector lets that push run report the queue's
proof instead of rebuilding an identical tree.

It prints one workflow run ID, and exits 0, only when every one of these holds:

* HEAD is the pushed commit.
* A completed, successful ``merge_group`` run of this same workflow file, on
  this exact commit, from this repository, on a ``gh-readonly-queue/main/``
  ref, exists.
* In that run's latest attempt both proof jobs concluded ``success`` (not
  ``skipped``), on this same commit.
* That run was a proof of the tree: the proof job either built it or reported
  an identical-tree pull-request proof. A queue run that skipped the proof
  because the base's classifier judged the change unable to affect it does
  not count, unless ``--allow-classifier-skip`` is passed.
* That run started no more than MAX_AGE before now.

Anything else, including any API or parsing error, prints a reason on stderr
and exits 3: the caller then runs the full proof. The selector can only ever
decline to reuse; it never fails a run and never widens what may be reused.
Release tags and dispatches never call it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


SHA40 = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_PATH = ".github/workflows/gpu-macos.yml"
PROOF_JOBS = ("gpu-macos-arm64", "sterile-consumer")
QUEUE_REF_PREFIX = "gh-readonly-queue/main/"
# Steps of the proof job whose conclusions say what kind of run it was.
BUILT_STEP = "Size builds to this runner"
REUSED_STEP = "Re-check the external state a reused proof read"
CLASSIFIER_SKIP_STEP = "Prove a skipped proof was allowed"
NO_REUSE = 3
# The push follows the queue run within minutes. A day is far more than that
# and still bounds how old a result this can describe.
MAX_AGE = timedelta(hours=12)


class Decline(Exception):
    """The run must not be reused; the message says why."""


Api = Callable[[str], Any]
Clock = Callable[[], datetime]


def head_commit(root: Path, sha: str) -> None:
    if not SHA40.fullmatch(sha):
        raise Decline("pushed SHA is not a full lowercase SHA")
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    if head != sha:
        raise Decline(f"HEAD {head} is not the pushed commit {sha}")


def positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def started_at(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value
    ):
        raise Decline(f"run start time {value!r} is not a UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def proof_kind(job: dict[str, Any], *, allow_classifier_skip: bool) -> str:
    """Return built, reused or classifier-skip, or decline."""
    conclusions: dict[str, list[Any]] = {}
    for step in job.get("steps") or []:
        if isinstance(step, dict):
            conclusions.setdefault(step.get("name"), []).append(step.get("conclusion"))

    def ran(name: str) -> bool:
        return conclusions.get(name) == ["success"]

    skipped_by_classifier = ran(CLASSIFIER_SKIP_STEP)
    built, reused = ran(BUILT_STEP), ran(REUSED_STEP)
    if skipped_by_classifier and (built or reused):
        raise Decline("the proof job both ran and skipped the proof")
    if skipped_by_classifier:
        if not allow_classifier_skip:
            raise Decline("the queue run skipped the proof by the classifier's verdict")
        return "classifier-skip"
    if built and reused:
        raise Decline("the proof job both built and reused the proof")
    if built:
        return "built"
    if reused:
        return "reused"
    raise Decline("the proof job shows neither a build nor a reused proof")


def check_run(
    run: dict[str, Any], *, repository: str, sha: str, api: Api, now: Clock,
    allow_classifier_skip: bool,
) -> tuple[int, str]:
    run_id = run.get("id")
    attempt = run.get("run_attempt")
    if not positive_int(run_id):
        raise Decline("run has an invalid ID")
    if not positive_int(attempt):
        raise Decline(f"run {run_id} has an invalid attempt")
    age = now() - started_at(run.get("run_started_at"))
    if not timedelta(0) <= age <= MAX_AGE:
        raise Decline(f"run {run_id} started {age} ago, outside {MAX_AGE}")
    if run.get("event") != "merge_group":
        raise Decline(f"run {run_id} is a {run.get('event')} run")
    if run.get("path") != WORKFLOW_PATH:
        raise Decline(f"run {run_id} ran {run.get('path')}, not {WORKFLOW_PATH}")
    if run.get("head_sha") != sha:
        raise Decline(f"run {run_id} ran commit {run.get('head_sha')}")
    branch = run.get("head_branch")
    if not isinstance(branch, str) or not branch.startswith(QUEUE_REF_PREFIX):
        raise Decline(f"run {run_id} ran ref {branch!r}, not a main queue ref")
    for field in ("repository", "head_repository"):
        if (run.get(field) or {}).get("full_name") != repository:
            raise Decline(f"run {run_id} {field} is outside {repository}")
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
        if (
            job.get("conclusion") != "success"
            or not positive_int(job.get("run_attempt")) or job["run_attempt"] != attempt
            or job.get("head_sha") != sha
        ):
            raise Decline(
                f"run {run_id} {name} is {job.get('conclusion')} "
                f"in attempt {job.get('run_attempt')} on {job.get('head_sha')}"
            )
    kind = proof_kind(by_name["gpu-macos-arm64"][0], allow_classifier_skip=allow_classifier_skip)
    return run_id, kind


def successful_runs(api: Api, repository: str, sha: str) -> list[dict[str, Any]]:
    # Only successful runs can qualify, and every page is read, so other runs
    # on the same commit (a failed attempt) cannot hide the real one.
    runs: list[dict[str, Any]] = []
    for page in range(1, 11):
        batch = api(
            f"repos/{repository}/actions/workflows/gpu-macos.yml/runs"
            f"?event=merge_group&status=success&head_sha={sha}"
            f"&per_page=100&page={page}"
        ).get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            return runs
    raise Decline(f"more than 1000 runs on {sha}")


def select(
    *, repository: str, sha: str, api: Api,
    now: Clock = lambda: datetime.now(timezone.utc),
    allow_classifier_skip: bool = False,
) -> tuple[int, str]:
    reasons = []
    # Newest first: a later attempt of the same commit is the one that stands.
    for run in sorted(
        successful_runs(api, repository, sha),
        key=lambda value: value.get("id") if positive_int(value.get("id")) else 0,
        reverse=True,
    ):
        try:
            return check_run(
                run, repository=repository, sha=sha, api=api, now=now,
                allow_classifier_skip=allow_classifier_skip,
            )
        except Decline as reason:
            reasons.append(str(reason))
    raise Decline("; ".join(reasons) or f"no merge_group run on {sha}")


def gh_api(path: str) -> Any:
    return json.loads(subprocess.run(
        ["gh", "api", path], check=True, text=True, capture_output=True,
    ).stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--allow-classifier-skip", action="store_true")
    args = parser.parse_args(argv)
    try:
        head_commit(args.root, args.sha)
        run_id, kind = select(
            repository=args.repository, sha=args.sha, api=gh_api,
            allow_classifier_skip=args.allow_classifier_skip,
        )
    except Decline as reason:
        print(f"no reusable proof: {reason}", file=sys.stderr)
        return NO_REUSE
    except Exception as error:  # noqa: BLE001 - any doubt means a full proof.
        print(f"no reusable proof: {type(error).__name__}: {error}", file=sys.stderr)
        return NO_REUSE
    print(f"merge-group proof kind: {kind}", file=sys.stderr)
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
