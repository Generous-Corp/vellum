#!/usr/bin/env python3
"""Keep the cheap pull-request lane honest: one retry for infrastructure, three alarms.

    gpu_proof_guard.py retry  --repository R --run-id N
    gpu_proof_guard.py alarms --repository R [--hours 24] [--issue]

A pull request proves the cheap lane and the merge group proves the full GPU
proof, so two things need watching. First, an infrastructure failure (a lost
runner, a 5xx from a download) must not eject a queue entry or redden a pull
request: `retry` re-runs the failed jobs of a Locked macOS GPU proof run once,
only when every failed job failed for an infrastructure reason, and never
again (it declines any run past its first attempt). Second, a defect that only
the GPU can catch now reaches the queue, so `alarms` reports when, over the
last day, more than one queue entry was ejected, any queue or main run failed
at a GPU-only step, or more than one in ten completed queue runs failed. With
`--issue` it opens or updates one tracking issue and closes it when the alarms
clear. Rolling back (VELLUM_PR_GPU_PROOF=full) is documented in
docs/ci/local-first-actions.md.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

WORKFLOW_FILE = "gpu-macos.yml"
WORKFLOW_PATH = ".github/workflows/gpu-macos.yml"
ISSUE_TITLE = "Cheap pull-request lane: alarm"

# Evidence that the job died for a reason no change to the code could cause.
INFRA_SIGNATURES = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"The runner has received a shutdown signal",
    r"lost communication with the server",
    r"returned error: 50[0-9]\b",
    r"\bHTTP(?: Error)? 50[0-9]\b",
    r"\bHTTPError 50[0-9]\b",
    r"\b50[0-4] (?:Server Error|Bad Gateway|Service Unavailable|Gateway Timeout)\b",
    r"cannot download [^\n]*<urlopen error",
    r"downloading '[^']+' failed[\s\S]{0,2000}returned error: (?:403|429|50[0-9])",
    r"Could not resolve host",
    r"TLS handshake timeout",
    r"Connection (?:reset by peer|timed out)",
))
# A real defect named in the same log keeps the failure a real one.
DEFECT_SIGNATURES = tuple(re.compile(pattern, re.MULTILINE) for pattern in (
    r"^(?:FAIL|ERROR): test\w* ",
    r"\*\*\*(?:Failed|Exception)",
    r"\b[1-9][0-9]* tests? failed out of",
    r"\.(?:c|cc|cpp|mm|h|hpp):\d+:\d+: (?:fatal )?error:",
    r"\bduplicate symbols?\b",
    r"Undefined symbols for architecture",
    r"AssertionError",
))
TAIL_LINES = 120

# Steps that only a GPU (or the sterile-consumer journey) can fail: the pull
# request's cheap lane never runs them, so a queue or main run failing at one
# is a defect the cheap lane could not see.
GPU_ONLY_STEPS = frozenset({
    "Run native, capture, and sterile-consumer tests",
    "Execute every declared parity proof",
    "Prove the installed SDK artifact is reproducible",
    "Smoke every public template through the installed SDK",
    "Prove installed HTML import through DesignIR and GPU capture",
    "Stress parallel text shaping in Release",
    "Stress parallel text shaping with AddressSanitizer",
    "Build the source-matched browser payload",
    "Run the complete installed journey without a framework checkout",
})
GPU_ONLY_JOBS = frozenset({"sterile-consumer"})
# The required proof jobs; the only jobs whose failure the retry judges.
PROOF_JOBS = frozenset({"gpu-macos-arm64", "sterile-consumer"})

Api = Callable[[str], Any]
Post = Callable[[str, dict[str, Any] | None], Any]
Text = Callable[[str], str]


def failure_tail(log: str) -> str:
    """The lines leading up to the first error annotation of a job log."""
    lines = log.splitlines()
    for index, line in enumerate(lines):
        if "##[error]" in line:
            return "\n".join(lines[max(0, index - TAIL_LINES):index + 1])
    return "\n".join(lines[-TAIL_LINES:])


def is_infrastructure_failure(job: dict[str, Any], log: str) -> bool:
    """Whether a failed job died for an infrastructure reason.

    A job that failed with no step marked failed lost its runner. Otherwise
    the tail of the log must carry an infrastructure signature and no
    signature of a defect.
    """
    steps = job.get("steps") or []
    if not any(step.get("conclusion") == "failure" for step in steps):
        return True
    tail = failure_tail(log)
    if any(rule.search(tail) for rule in DEFECT_SIGNATURES):
        return False
    return any(rule.search(tail) for rule in INFRA_SIGNATURES)


def retry(*, repository: str, run_id: int, api: Api, text: Text, post: Post) -> str:
    """Re-run the failed jobs of one run, once, if all of them failed on infrastructure."""
    run = api(f"repos/{repository}/actions/runs/{run_id}")
    if run.get("path") != WORKFLOW_PATH:
        return "declined: not the GPU proof workflow"
    if (run.get("repository") or {}).get("full_name") != repository or \
            (run.get("head_repository") or {}).get("full_name") != repository:
        return "declined: not a first-party run"
    if run.get("event") not in ("pull_request", "merge_group"):
        return f"declined: a {run.get('event')} run is never retried"
    if run.get("conclusion") != "failure":
        return f"declined: the run concluded {run.get('conclusion')}"
    if run.get("run_attempt") != 1:
        return "declined: a retry was already used"
    jobs = api(f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100").get("jobs", [])
    # Only the proof jobs: a failed helper (decide is continue-on-error and
    # changes nothing a proof concludes) must not decline the one retry.
    failed = [job for job in jobs if job.get("conclusion") == "failure" and job.get("name") in PROOF_JOBS]
    if not failed:
        return "declined: no failed job"
    for job in failed:
        try:
            log = text(f"repos/{repository}/actions/jobs/{job['id']}/logs")
        except Exception as error:  # an unreadable log is never evidence of infrastructure
            return f"declined: the log of {job.get('name')} is unreadable ({error})"
        if not is_infrastructure_failure(job, log):
            return f"declined: {job.get('name')} failed on the change, not on infrastructure"
    post(f"repos/{repository}/actions/runs/{run_id}/rerun-failed-jobs", None)
    return f"retried: {len(failed)} failed job(s) failed on infrastructure"


def gpu_only_failures(jobs: list[dict[str, Any]]) -> list[str]:
    """The failed jobs and steps of one run that only a GPU can fail."""
    found: list[str] = []
    for job in jobs:
        if job.get("conclusion") != "failure":
            continue
        if job.get("name") in GPU_ONLY_JOBS:
            found.append(str(job.get("name")))
        found += [f"{job.get('name')} / {step.get('name')}" for step in job.get("steps") or []
                  if step.get("conclusion") == "failure" and step.get("name") in GPU_ONLY_STEPS]
    return found


def completed_runs(api: Api, repository: str, event: str, since: datetime) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page in range(1, 11):
        batch = api(
            f"repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs"
            f"?event={event}&status=completed&created=%3E%3D{since:%Y-%m-%dT%H:%M:%SZ}"
            f"&per_page=100&page={page}").get("workflow_runs", [])
        found += [run for run in batch if run.get("path") == WORKFLOW_PATH]
        if len(batch) < 100:
            break
    return found


def evaluate(queue_runs: list[dict[str, Any]], main_runs: list[dict[str, Any]],
             jobs_of: Callable[[int], list[dict[str, Any]]]) -> dict[str, Any]:
    """The three alarms over completed queue runs and completed pushes to main."""
    decided = [run for run in queue_runs if run.get("conclusion") in ("success", "failure")]
    ejected = [run for run in decided if run["conclusion"] == "failure"]
    escapes: list[str] = []
    for run in [*ejected, *(r for r in main_runs if r.get("conclusion") == "failure")]:
        found = gpu_only_failures(jobs_of(run["id"]))
        if found:
            escapes.append(f"run {run['id']}: {found[0]}")
    alarms = []
    if len(ejected) > 1:
        alarms.append(f"{len(ejected)} queue entries were ejected in the window (limit 1)")
    if escapes:
        alarms.append("a GPU-only failure reached the queue or main: " + "; ".join(escapes))
    if len(decided) >= 5 and len(ejected) * 10 > len(decided):
        alarms.append(f"{len(ejected)} of {len(decided)} queue runs failed (limit 1 in 10)")
    return {"queue_runs": len(decided), "ejected": len(ejected), "gpu_only_escapes": escapes,
            "alarms": alarms}


def alarms(*, repository: str, hours: int, api: Api, now: datetime | None = None) -> dict[str, Any]:
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    queue = completed_runs(api, repository, "merge_group", since)
    main = [run for run in completed_runs(api, repository, "push", since)
            if run.get("head_branch") == "main"]
    return evaluate(queue, main,
                    lambda run_id: api(f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100").get("jobs", []))


def sync_issue(repository: str, report: dict[str, Any], api: Api, post: Post) -> str:
    """Open or update the one tracking issue when alarms fire; close it when they clear."""
    issues = [issue for issue in api(f"repos/{repository}/issues?state=open&per_page=100")
              if not issue.get("pull_request") and issue.get("title") == ISSUE_TITLE]
    body = ("The cheap pull-request lane's alarms fired:\n\n"
            + "\n".join(f"- {line}" for line in report["alarms"])
            + "\n\nRollback: set the repository variable VELLUM_PR_GPU_PROOF to `full` "
              "(docs/ci/local-first-actions.md).\n\n```json\n"
            + json.dumps(report, indent=2, sort_keys=True) + "\n```\n")
    if report["alarms"]:
        if issues:
            post(f"repos/{repository}/issues/{issues[0]['number']}/comments", {"body": body})
            return f"updated issue {issues[0]['number']}"
        post(f"repos/{repository}/issues", {"title": ISSUE_TITLE, "body": body})
        return "opened an issue"
    for issue in issues:
        post(f"repos/{repository}/issues/{issue['number']}",
             {"state": "closed", "state_reason": "completed"})
    return "no alarms" + (f"; closed issue {issues[0]['number']}" if issues else "")


def gh_api(path: str) -> Any:
    result = subprocess.run(["gh", "api", "--method", "GET", path], check=True, text=True,
                            capture_output=True)
    return json.loads(result.stdout)


def gh_text(path: str) -> str:
    return subprocess.run(["gh", "api", "--method", "GET", path], check=True, text=True,
                          capture_output=True).stdout


def gh_post(path: str, payload: dict[str, Any] | None) -> Any:
    command = ["gh", "api", "--method", "PATCH" if payload and "state" in payload else "POST", path]
    if payload:
        command += ["--input", "-"]
    result = subprocess.run(command, check=True, text=True, capture_output=True,
                            input=json.dumps(payload) if payload else None)
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("retry")
    one.add_argument("--repository", required=True)
    one.add_argument("--run-id", type=int, required=True)
    two = sub.add_parser("alarms")
    two.add_argument("--repository", required=True)
    two.add_argument("--hours", type=int, default=24)
    two.add_argument("--issue", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "retry":
            print(retry(repository=args.repository, run_id=args.run_id, api=gh_api,
                        text=gh_text, post=gh_post))
            return 0
        report = alarms(repository=args.repository, hours=args.hours, api=gh_api)
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.issue:
            print(sync_issue(args.repository, report, gh_api, gh_post))
        return 1 if report["alarms"] else 0
    except Exception as error:  # a monitor that cannot read must say so, never pass quietly
        print(f"gpu-proof-guard: {error}", file=sys.stderr)
        return 0 if args.command == "retry" else 2


if __name__ == "__main__":
    sys.exit(main())
