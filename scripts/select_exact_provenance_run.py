#!/usr/bin/env python3
"""Select one exact tag/SHA provenance run from a GitHub workflow-runs response."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SHA40 = re.compile(r"^[0-9a-f]{40}$")
STATUSES = {
    "requested", "waiting", "pending", "queued", "in_progress", "completed"
}
CONCLUSIONS = {
    None,
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "stale",
    "startup_failure",
    "success",
    "timed_out",
}


class SelectionError(ValueError):
    pass


def strict_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise SelectionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def select(data: Any, *, head_sha: str, head_branch: str) -> dict[str, Any] | None:
    if not SHA40.fullmatch(head_sha):
        raise SelectionError("head SHA is not a full lowercase SHA")
    if not head_branch or head_branch.startswith("refs/"):
        raise SelectionError("head branch must be the bare release tag name")
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise SelectionError("workflow response must contain workflow_runs array")
    matches = []
    for index, run in enumerate(data["workflow_runs"]):
        if not isinstance(run, dict):
            raise SelectionError(f"workflow_runs[{index}] is not an object")
        if (
            run.get("head_sha") == head_sha
            and run.get("head_branch") == head_branch
            and run.get("event") == "push"
        ):
            matches.append(run)
    if len(matches) > 1:
        raise SelectionError("multiple exact provenance runs matched release tag")
    if not matches:
        return None
    run = matches[0]
    run_id = run.get("id")
    status = run.get("status")
    conclusion = run.get("conclusion")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise SelectionError("matching provenance run has invalid ID")
    if status not in STATUSES:
        raise SelectionError("matching provenance run has invalid status")
    if conclusion not in CONCLUSIONS:
        raise SelectionError("matching provenance run has invalid conclusion")
    if status != "completed" and conclusion is not None:
        raise SelectionError("unfinished provenance run already has a conclusion")
    if status == "completed" and conclusion is None:
        raise SelectionError("completed provenance run lacks a conclusion")
    return {"id": run_id, "status": status, "conclusion": conclusion}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-json", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--head-branch", required=True)
    args = parser.parse_args()
    try:
        data = json.loads(
            args.runs_json.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
        selected = select(data, head_sha=args.head_sha, head_branch=args.head_branch)
    except (OSError, UnicodeError, json.JSONDecodeError, SelectionError) as exc:
        print(f"exact provenance selection failed: {exc}", file=sys.stderr)
        return 2
    if selected is None:
        return 4
    print(
        "\t".join(
            [
                str(selected["id"]),
                selected["status"],
                selected["conclusion"] or "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
