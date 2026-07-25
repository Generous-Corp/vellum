#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_ROOT = "https://api.github.com"


REQUIRED_WORKFLOWS = (
    "gpu-macos.yml",
    "product-quality.yml",
    "provenance.yml",
    "readme-quick-start.yml",
)


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


def gate_state(
    runs_by_workflow: dict[str, list[dict[str, Any]]],
    *,
    pull_number: int,
    head_sha: str,
) -> tuple[bool, list[str]]:
    details = []
    ready = True
    for workflow in REQUIRED_WORKFLOWS:
        run = _associated_run(
            runs_by_workflow.get(workflow, []),
            pull_number=pull_number,
            head_sha=head_sha,
        )
        if run is None:
            ready = False
            details.append(f"{workflow}: missing")
            continue
        status = run.get("status")
        conclusion = run.get("conclusion")
        details.append(f"{workflow}: {status}/{conclusion or '-'}")
        if status != "completed" or conclusion != "success":
            ready = False
    return ready, details


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

    runs_by_workflow = {}
    for workflow in REQUIRED_WORKFLOWS:
        response = _api(
            f"repos/{repository}/actions/workflows/{workflow}/runs"
            "?event=pull_request&per_page=100"
        )
        runs_by_workflow[workflow] = response.get("workflow_runs", [])

    ready, details = gate_state(
        runs_by_workflow,
        pull_number=pull_number,
        head_sha=current_head_sha,
    )
    print("\n".join(details))
    if not ready:
        print("merge steward: exact-head gates are not all green")
        return 0

    result = _api(
        f"repos/{repository}/pulls/{pull_number}/merge",
        method="PUT",
        # Decision 0001 rejects squashing because it loses path/commit
        # correspondence, and the observatory depends on that: every
        # observation event is keyed to its source commit, so a squash orphans
        # those commits and the cursor-coverage invariant fails on main.
        body={"sha": current_head_sha, "merge_method": "merge"},
    )
    if result.get("merged") is not True:
        raise MergeError(f"GitHub declined the exact-head merge: {result}")
    print(f"merge steward: merged PR #{pull_number} at {current_head_sha}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge a same-repository Vellum PR after exact-head gates pass."
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
