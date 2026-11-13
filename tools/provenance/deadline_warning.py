#!/usr/bin/env python3
"""Keep one issue current with the pending observation events nearing their deadline.

    deadline_warning.py --summary overdue.json --repo OWNER/NAME [--dry-run]

The summary is overdue_events.py output. Events within 24 hours of their
classification deadline are listed in one open issue, and the issue is closed
once none remains (events that went overdue move to the overdue issue). When an
event comes within 4 hours the issue is marked urgent and a comment mentions the
event owners, because editing an issue body does not notify anyone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TITLE = "Observatory events due for classification within 24 hours"
URGENT_MARK = "URGENT: an event is due within 4 hours."


def render(due_soon: list[dict[str, object]]) -> str:
    urgent = any(row["urgent"] for row in due_soon)
    owners = sorted({str(row["owner"]) for row in due_soon if row["owner"]})
    lines = []
    if urgent:
        lines.append(URGENT_MARK)
    lines.append(f"{len(due_soon)} pending observation event(s) reach their classification "
                 "deadline within 24 hours. Classify each with a resolution event "
                 "(provenance/pulp-observatory/README.md) before the deadline: once it "
                 "passes, pushes to `main` and release tags fail `provenance-verify`.")
    if owners:
        lines.append("Owners: " + " ".join(owners))
    lines += ["", "| event | class | owner | deadline | hours left |", "| --- | --- | --- | --- | --- |"]
    for row in due_soon:
        lines.append(f"| {row['event_id']} | {row['class']} | {row['owner']} | "
                     f"{row['deadline']} | {row['hours_left']}{' (urgent)' if row['urgent'] else ''} |")
    return "\n".join(lines) + "\n"


def plan(due_soon: list[dict[str, object]], open_issues: list[dict[str, object]]) -> list[dict[str, object]]:
    """Actions to take, given the open issues (dicts with number, title, body)."""
    mine = sorted((issue for issue in open_issues if issue["title"] == TITLE),
                  key=lambda issue: issue["number"])
    actions: list[dict[str, object]] = []
    if not due_soon:
        for issue in mine:
            actions.append({"op": "close", "number": issue["number"],
                            "comment": "No pending event is within 24 hours of its classification deadline."})
        return actions
    body = render(due_soon)
    if not mine:
        actions.append({"op": "create", "body": body})
        return actions
    keep, duplicates = mine[0], mine[1:]
    for issue in duplicates:
        actions.append({"op": "close", "number": issue["number"],
                        "comment": f"Duplicate of #{keep['number']}."})
    if keep["body"].strip() != body.strip():
        actions.append({"op": "edit", "number": keep["number"], "body": body})
    if URGENT_MARK in body and URGENT_MARK not in keep["body"]:
        owners = sorted({str(row["owner"]) for row in due_soon if row["urgent"] and row["owner"]})
        actions.append({"op": "comment", "number": keep["number"],
                        "body": "Due within 4 hours. " + " ".join(owners)})
    return actions


def gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=stdin, text=True, check=True,
                          capture_output=True).stdout


def apply(actions: list[dict[str, object]], repo: str, run=gh) -> None:
    for action in actions:
        number = str(action.get("number", ""))
        if action["op"] == "create":
            run("issue", "create", "--repo", repo, "--title", TITLE, "--body-file", "-",
                stdin=str(action["body"]))
        elif action["op"] == "edit":
            run("issue", "edit", number, "--repo", repo, "--body-file", "-", stdin=str(action["body"]))
        elif action["op"] == "comment":
            run("issue", "comment", number, "--repo", repo, "--body", str(action["body"]))
        else:
            run("issue", "close", number, "--repo", repo, "--comment", str(action["comment"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    due_soon = json.loads(args.summary.read_text(encoding="utf-8"))["due_soon"]
    issues = json.loads(gh("issue", "list", "--repo", args.repo, "--state", "open", "--limit", "200",
                           "--json", "number,title,body"))
    actions = plan(due_soon, issues)
    print(json.dumps(actions, indent=2))
    if not args.dry_run:
        apply(actions, args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
