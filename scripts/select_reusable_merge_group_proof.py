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
  an identical-tree pull-request proof.
* A queue run that skipped the proof because the base's classifier judged the
  change unable to affect it counts only under proven equivalence (see
  ``verify_equivalence``): the push's tree, base and changed paths are those
  the queue run classified, the classifier script is the same blob, the
  classifier says ``not-required`` again when run now, and the push requires
  nothing the queue run did not.
* That run started no more than MAX_AGE before now.
* The pushed tree needs no receipt that only a push run writes (the parity
  proof execution receipt), whatever the kind of proof being reused.

Anything else, including any API or parsing error, prints a reason on stderr
and exits 3: the caller then runs the full proof. The selector can only ever
decline to reuse; it never fails a run and never widens what may be reused.
Release tags and dispatches never call it.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile
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
SKIP_RECEIPT_ARTIFACT = "vellum-gpu-classifier-skip"
SKIP_RECEIPT_FILE = "gpu-classifier-skip.json"
SKIP_RECEIPT_SCHEMA = "vellum.gpu-classifier-skip.v1"
CLASSIFIER_PATH = "scripts/classify_gpu_proof.py"
# Present at the pushed commit, this file makes gpu-macos.yml execute the
# parity proofs and upload a receipt that release readiness binds to the push
# run (tools/provenance/verify_authority_expansion.py). A reused run uploads
# none, so a push of such a tree always proves.
PUSH_ONLY_RECEIPT_TRIGGERS = (
    "provenance/authority/expansions/full-design-import-render-v1/parity-completion-1.json",
)
NO_REUSE = 3
# The push follows the queue run within minutes. A day is far more than that
# and still bounds how old a result this can describe.
MAX_AGE = timedelta(hours=12)


class Decline(Exception):
    """The run must not be reused; the message says why."""


Api = Callable[[str], Any]
Clock = Callable[[], datetime]
Download = Callable[[str], bytes]
Equivalence = Callable[[int, int], list[str]]


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


def proof_kind(job: dict[str, Any]) -> str:
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
    equivalence: Equivalence | None = None,
) -> tuple[int, str, list[str]]:
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
    kind = proof_kind(by_name["gpu-macos-arm64"][0])
    evidence = [
        f"merge-group run: {run_id} (attempt {attempt}), commit {sha}",
        f"proof kind: {kind}",
    ]
    if kind == "classifier-skip":
        if equivalence is None:
            raise Decline("the queue run skipped the proof and no equivalence check was given")
        evidence += equivalence(run_id, attempt)
    return run_id, kind, evidence


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
    equivalence: Equivalence | None = None,
) -> tuple[int, str, list[str]]:
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
                equivalence=equivalence,
            )
        except Decline as reason:
            reasons.append(str(reason))
    raise Decline("; ".join(reasons) or f"no merge_group run on {sha}")


def git_text(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def git_bytes(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True,
    ).stdout


def require_no_push_only_receipt(root: Path, sha: str) -> None:
    """Decline when the pushed tree makes the push run upload a receipt a reuse cannot."""
    listing = git_bytes(root, "ls-tree", "--name-only", "-z", sha, "--", *PUSH_ONLY_RECEIPT_TRIGGERS)
    if listing:
        raise Decline(
            "the pushed tree has "
            + ", ".join(sorted(name for name in listing.decode("utf-8").split("\0") if name))
            + ", so its push run writes a receipt a reuse cannot"
        )


def read_skip_receipt(archive: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
        if names != [SKIP_RECEIPT_FILE]:
            raise Decline(f"receipt artifact holds {names}, not [{SKIP_RECEIPT_FILE!r}]")
        value = json.loads(bundle.read(SKIP_RECEIPT_FILE).decode("utf-8"))
    if not isinstance(value, dict):
        raise Decline("receipt is not a JSON object")
    return value


def verify_equivalence(
    *, root: Path, repository: str, sha: str, before: str, api: Api, download: Download,
    run_id: int, attempt: int,
) -> list[str]:
    """Prove the push is the change the queue run classified; return the evidence.

    Every fact is recomputed here from git. The queue run's receipt must agree
    with it, field by field; a field that is missing, mistyped or different
    declines. The classifier is run again, from the copy in ``before`` (main's
    tip the push replaced, the base the queue run judged against), over the
    paths the push changed.
    """
    for value, name in ((sha, "pushed commit"), (before, "previous main tip")):
        if not SHA40.fullmatch(value) or set(value) == {"0"}:
            raise Decline(f"{name} {value!r} is not a full commit SHA")
    tree = git_text(root, "rev-parse", sha + "^{tree}")
    classifier_blob = git_text(root, "rev-parse", f"{before}:{CLASSIFIER_PATH}")
    changed = git_bytes(root, "diff", "--name-only", "-z", "--no-renames", before, sha)
    if not SHA40.fullmatch(tree) or not SHA40.fullmatch(classifier_blob) or not changed:
        raise Decline("git returned a malformed object ID or an empty change")
    paths_digest = hashlib.sha256(changed).hexdigest()

    listing = api(
        f"repos/{repository}/actions/runs/{run_id}/artifacts"
        f"?name={SKIP_RECEIPT_ARTIFACT}&per_page=100"
    )
    artifacts = [
        artifact for artifact in listing.get("artifacts", [])
        if artifact.get("name") == SKIP_RECEIPT_ARTIFACT
        and artifact.get("expired") is False
        and positive_int(artifact.get("id"))
        and (artifact.get("workflow_run") or {}).get("id") == run_id
    ]
    if len(artifacts) != 1:
        raise Decline(f"run {run_id} has {len(artifacts)} classifier-skip receipts")
    receipt = read_skip_receipt(download(
        f"repos/{repository}/actions/artifacts/{artifacts[0]['id']}/zip"
    ))
    expected = {
        "schema": SKIP_RECEIPT_SCHEMA,
        "event": "merge_group",
        "repository": repository,
        "run_id": run_id,
        "run_attempt": attempt,
        "commit": sha,
        "tree": tree,
        "base_sha": before,
        "classifier_blob": classifier_blob,
        "changed_paths_sha256": paths_digest,
        "verdict": "not-required",
        "required_contexts": list(PROOF_JOBS),
    }
    for key, value in expected.items():
        # type() as well as value: JSON true must not stand in for 1.
        if type(receipt.get(key)) is not type(value) or receipt.get(key) != value:
            raise Decline(f"receipt {key} is {receipt.get(key)!r}, not {value!r}")
    if receipt.get("classifier_config") != "embedded-in-classifier":
        raise Decline(f"receipt classifier_config is {receipt.get('classifier_config')!r}")

    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "classify.py"
        script.write_bytes(git_bytes(root, "show", f"{before}:{CLASSIFIER_PATH}"))
        paths = Path(directory) / "paths"
        paths.write_bytes(changed)
        verdict = subprocess.run(
            [sys.executable, "-I", str(script), "--files0-from", str(paths)],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
    if verdict != "not-required":
        raise Decline(f"the classifier now says {verdict!r}")
    return [
        f"tree: {tree} (same as the queue run's)",
        f"base: {before} (the queue run's base and main's previous tip)",
        f"changed paths sha256: {paths_digest}",
        f"classifier blob: {classifier_blob} (same at base and in the receipt); config: embedded",
        f"classifier re-run at the base over the changed paths: {verdict}",
        f"required contexts: {', '.join(PROOF_JOBS)} (both reported success by the queue run)",
        "push-only requirements: none (no parity completion record in the tree)",
    ]


def gh_api(path: str) -> Any:
    return json.loads(subprocess.run(
        ["gh", "api", path], check=True, text=True, capture_output=True,
    ).stdout)


def gh_download(path: str) -> bytes:
    return subprocess.run(["gh", "api", path], check=True, capture_output=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--before", default="", help="main's tip before the push")
    parser.add_argument("--summary", type=Path, help="append the equivalence evidence here")
    args = parser.parse_args(argv)

    def equivalence(run_id: int, attempt: int) -> list[str]:
        return verify_equivalence(
            root=args.root, repository=args.repository, sha=args.sha, before=args.before,
            api=gh_api, download=gh_download, run_id=run_id, attempt=attempt,
        )

    try:
        head_commit(args.root, args.sha)
        require_no_push_only_receipt(args.root, args.sha)
        run_id, kind, evidence = select(
            repository=args.repository, sha=args.sha, api=gh_api, equivalence=equivalence,
        )
        if args.summary is not None:
            with args.summary.open("a", encoding="utf-8") as handle:
                handle.write("Reused merge-queue proof, equivalence evidence:\n")
                handle.writelines(f"- {line}\n" for line in evidence)
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
