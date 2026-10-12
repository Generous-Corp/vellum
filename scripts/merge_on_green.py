#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_ROOT = "https://api.github.com"


RETRY_DELAYS_SECONDS = (0.0, 1.0, 2.0)




class MergeError(RuntimeError):
    pass


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Drop the credential when a redirect leaves the API host.

    urllib replays every header on a redirect, so a redirect off
    api.github.com would hand this job's write-scoped installation token to
    another host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        following = super().redirect_request(req, fp, code, msg, headers, newurl)
        if following is None:
            return None
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(
            req.full_url
        ).netloc:
            following.remove_header("Authorization")
        return following


_OPENER = urllib.request.build_opener(_SameHostRedirect())


def _api(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    # Talk to the API directly rather than through a CLI: the runner images this
    # steward is scheduled on do not all ship `gh`, and a missing binary would
    # fail every merge attempt. The workflow already mints a scoped token.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise MergeError("GH_TOKEN is required to reach the GitHub API")
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_ROOT}/{path.lstrip('/')}", data=encoded, method=method
    )
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "vellum-merge-steward")
    if encoded is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        raise MergeError(
            f"GitHub API {method} {path} failed: {error.code} {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise MergeError(f"GitHub API {method} {path} failed: {error.reason}") from error
    if not payload.strip():
        return {}
    return json.loads(payload)


# GraphQL is the only API that arms GitHub-native auto-merge. The merge method
# is spelled MERGE here for the same reason the REST fallback spells it
# "merge": Decision 0008 keeps every feature commit reachable on main.
PULL_STATE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      autoMergeRequest { enabledAt mergeMethod }
      mergeQueueEntry { id }
    }
  }
}
"""

ENABLE_AUTO_MERGE_MUTATION = """
mutation($pullRequestId: ID!, $expectedHeadOid: GitObjectID!) {
  enablePullRequestAutoMerge(
    input: {pullRequestId: $pullRequestId, mergeMethod: MERGE, expectedHeadOid: $expectedHeadOid}
  ) {
    pullRequest { autoMergeRequest { mergeMethod } }
  }
}
"""


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    response = _api("graphql", method="POST", body={"query": query, "variables": variables})
    errors = response.get("errors")
    if errors:
        messages = "; ".join(str(error.get("message", error)) for error in errors)
        raise MergeError(f"GitHub GraphQL failed: {messages}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise MergeError(f"GitHub GraphQL returned no data: {response}")
    return data


def _already_mergeable(error: MergeError) -> bool:
    """GitHub refuses to arm auto-merge on a PR it could merge right now.

    That only happens while no merge queue protects main: every requirement is
    already met, so there is nothing for auto-merge to wait on. The steward
    then lands the merge commit itself, exactly as it did before the queue.
    """
    return "clean status" in str(error).lower()


def _associated_run(
    runs: list[dict[str, Any]], *, pull_number: int, head_sha: str
) -> dict[str, Any] | None:
    candidates = []
    for run in runs:
        if run.get("event") != "pull_request":
            continue
        associations = run.get("pull_requests")
        if not isinstance(associations, list):
            continue
        for association in associations:
            if (
                association.get("number") == pull_number
                and association.get("head", {}).get("sha") == head_sha
            ):
                candidates.append(run)
                break
    if not candidates:
        return None
    return max(candidates, key=lambda run: (run.get("created_at", ""), run.get("id", 0)))


def _required_contexts(payload: dict[str, Any]) -> list[str]:
    contexts = payload.get("contexts")
    if isinstance(contexts, list):
        return [context for context in contexts if isinstance(context, str)]
    checks = payload.get("checks")
    if isinstance(checks, list):
        return [
            check["context"]
            for check in checks
            if isinstance(check, dict) and isinstance(check.get("context"), str)
        ]
    return []


def _check_run_state(
    check_runs: list[dict[str, Any]], required_contexts: list[str], head_sha: str
) -> tuple[bool, list[str], bool]:
    """Return (ready, details, retryable) for one live API snapshot."""
    by_name: dict[str, dict[str, Any]] = {}
    for check_run in check_runs:
        if check_run.get("head_sha") != head_sha:
            continue
        name = check_run.get("name")
        if not isinstance(name, str) or name not in required_contexts:
            continue
        previous = by_name.get(name)
        if previous is None or check_run.get("id", 0) > previous.get("id", 0):
            by_name[name] = check_run

    details: list[str] = []
    retryable = False
    ready = True
    for context in required_contexts:
        check_run = by_name.get(context)
        if check_run is None:
            details.append(f"{context}: missing")
            ready = False
            retryable = True
            continue
        status = check_run.get("status")
        conclusion = check_run.get("conclusion")
        details.append(f"{context}: {status}/{conclusion or '-'}")
        if status != "completed" or conclusion != "success":
            ready = False
            if status != "completed" and conclusion is None:
                retryable = True
    return ready, details, retryable


def gate_state(
    check_runs: list[dict[str, Any]], *, required_contexts: list[str], head_sha: str
) -> tuple[bool, list[str]]:
    ready, details, _ = _check_run_state(check_runs, required_contexts, head_sha)
    return ready, details


def live_gate_state(
    repository: str, *, base_ref: str, head_sha: str
) -> tuple[bool, list[str]]:
    """Read effective required contexts and exact-head check runs with retries."""
    details: list[str] = []
    for delay in RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        policy = _api(
            f"repos/{repository}/branches/{urllib.parse.quote(base_ref, safe='')}/"
            "protection/required_status_checks"
        )
        required_contexts = _required_contexts(policy)
        if not required_contexts:
            details = ["required contexts: missing"]
            continue
        checks = _api(
            f"repos/{repository}/commits/{head_sha}/check-runs?per_page=100"
        )
        ready, details, retryable = _check_run_state(
            checks.get("check_runs", []), required_contexts, head_sha
        )
        if ready or not retryable:
            return ready, details
    return False, details



def _candidate(payload: dict[str, Any], repository: str) -> tuple[int, str] | None:
    run = payload.get("workflow_run", {})
    if run.get("event") != "pull_request":
        return None
    head_repository = run.get("head_repository") or {}
    if head_repository.get("full_name") != repository:
        return None
    associations = run.get("pull_requests")
    if not isinstance(associations, list) or len(associations) != 1:
        return None
    pull = associations[0]
    number = pull.get("number")
    head_sha = pull.get("head", {}).get("sha")
    if (
        not isinstance(number, int)
        or not isinstance(head_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None
    ):
        return None
    return number, head_sha


def run(event_path: Path, repository: str) -> int:
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    candidate = _candidate(payload, repository)
    if candidate is None:
        print("merge steward: event is not a same-repository pull-request candidate")
        return 0
    pull_number, event_head_sha = candidate

    pull = _api(f"repos/{repository}/pulls/{pull_number}")
    current_head_sha = pull.get("head", {}).get("sha")
    if (
        pull.get("state") != "open"
        or pull.get("draft") is True
        or pull.get("base", {}).get("ref") != "main"
        or pull.get("head", {}).get("repo", {}).get("full_name") != repository
        or current_head_sha != event_head_sha
    ):
        print("merge steward: pull request is closed, draft, forked, non-main, or stale")
        return 0

    ready, details = live_gate_state(
        repository,
        base_ref=pull.get("base", {}).get("ref", ""),
        head_sha=current_head_sha,
    )
    print("\n".join(details))
    if not ready:
        print("merge steward: exact-head gates are not all green")
        return 0

    # Arm GitHub-native auto-merge rather than merging here. With a merge queue
    # on main the PR enters the queue on green and the queue lands the merge
    # commit; direct merges are refused there. Without a queue GitHub merges the
    # PR itself as soon as protection is satisfied, so the outcome is the same
    # merge commit either way.
    owner, name = repository.split("/", 1)
    state = _graphql(
        PULL_STATE_QUERY, {"owner": owner, "name": name, "number": pull_number}
    )
    pull_state = (state.get("repository") or {}).get("pullRequest") or {}
    node_id = pull_state.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise MergeError(f"GitHub returned no node id for PR #{pull_number}")
    if pull_state.get("mergeQueueEntry"):
        print(f"merge steward: PR #{pull_number} is already in the merge queue")
        return 0
    if pull_state.get("autoMergeRequest"):
        print(f"merge steward: PR #{pull_number} already has auto-merge armed")
        return 0
    try:
        _graphql(
            ENABLE_AUTO_MERGE_MUTATION,
            {"pullRequestId": node_id, "expectedHeadOid": current_head_sha},
        )
    except MergeError as error:
        if not _already_mergeable(error):
            raise
        # Decision 0001 rejects squashing because it loses path/commit
        # correspondence, and the observatory depends on that: every
        # observation event is keyed to its source commit, so a squash orphans
        # those commits and the cursor-coverage invariant fails on main.
        result = _api(
            f"repos/{repository}/pulls/{pull_number}/merge",
            method="PUT",
            body={"sha": current_head_sha, "merge_method": "merge"},
        )
        if result.get("merged") is not True:
            raise MergeError(f"GitHub declined the exact-head merge: {result}") from error
        print(f"merge steward: merged PR #{pull_number} at {current_head_sha}")
        return 0
    print(f"merge steward: armed auto-merge (MERGE) on PR #{pull_number} at {current_head_sha}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Arm auto-merge on a same-repository Vellum PR after exact-head gates pass."
    )
    parser.add_argument(
        "--event",
        type=Path,
        default=Path(os.environ.get("GITHUB_EVENT_PATH", "")),
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    args = parser.parse_args()
    if not args.repository or not args.event.is_file():
        parser.error("--repository and a readable --event are required")
    return run(args.event, args.repository)


if __name__ == "__main__":
    raise SystemExit(main())
