#!/usr/bin/env python3
"""Keep one issue current with the pending observation events nearing their deadline.

    deadline_warning.py --summary overdue.json --repo OWNER/NAME [--dry-run]

The summary is overdue_events.py output. Events within 24 hours of their
classification deadline are listed in one open issue, and the issue is closed
once none remains (events that went overdue move to the overdue issue). When an
event comes within 4 hours the issue is marked urgent and a comment mentions the
owning lanes, because editing an issue body does not notify anyone. The issue
carries the label the escalation ladder watches so an agent picks it up. The
owner is mentioned only when an event needs a ruling (schema, API, security or
P0), and only in the body while no event is urgent (the first, 24 hour issue).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TITLE = "Observatory events due for classification within 24 hours"
LABEL = "shipyard:needs-agent"
RULING_OWNER = "@danielraffel"
URGENT_MARK = "URGENT: an event is due within 4 hours."


def lane(row: dict[str, object], lookup=None) -> str:
    """The owning lane: the author of the source commit's pull request."""
    author = lookup(str(row["source_repository"]), str(row["source_commit"])) if lookup else None
    return author or "unresolved"


def render(due_soon: list[dict[str, object]], lookup=None) -> str:
    urgent = any(row["urgent"] for row in due_soon)
    lines = []
    if urgent:
        lines.append(URGENT_MARK)
    lines.append(f"{len(due_soon)} pending observation event(s) reach their classification "
                 "deadline within 24 hours. The owning lane classifies each with a resolution "
                 "event (provenance/pulp-observatory/README.md) before the deadline: once it "
                 "passes, pushes to `main` and release tags fail `provenance-verify`.")
    if any(row["needs_ruling"] and not row["urgent"] for row in due_soon):
        lines.append(f"An event here needs an owner ruling: {RULING_OWNER}")
    lines += ["", "| event | class | lane | deadline | hours left |", "| --- | --- | --- | --- | --- |"]
    for row in due_soon:
        lines.append(f"| {row['event_id']} | {row['class']} | `{lane(row, lookup)}` | "
                     f"{row['deadline']} | {row['hours_left']}{' (urgent)' if row['urgent'] else ''} |")
    return "\n".join(lines) + "\n"


def plan(due_soon: list[dict[str, object]], open_issues: list[dict[str, object]],
         lookup=None) -> list[dict[str, object]]:
    """Actions to take, given the open issues (dicts with number, title, body)."""
    mine = sorted((issue for issue in open_issues if issue["title"] == TITLE),
                  key=lambda issue: issue["number"])
    actions: list[dict[str, object]] = []
    if not due_soon:
        for issue in mine:
            actions.append({"op": "close", "number": issue["number"],
                            "comment": "No pending event is within 24 hours of its classification deadline."})
        return actions
    body = render(due_soon, lookup)
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
        lanes = sorted({lane(row, lookup) for row in due_soon if row["urgent"]})
        actions.append({"op": "comment", "number": keep["number"],
                        "body": f"`{LABEL}`: due within 4 hours. Lanes: " + ", ".join(f"`{x}`" for x in lanes)})
    return actions


def gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=stdin, text=True, check=True,
                          capture_output=True).stdout


def commit_author(repository: str, commit: str) -> str | None:
    """Login of the author of the pull request that carried the commit, else the commit's."""
    for path, query in ((f"repos/{repository}/commits/{commit}/pulls", ".[0].user.login"),
                        (f"repos/{repository}/commits/{commit}", ".author.login")):
        try:
            login = gh("api", path, "--jq", query).strip()
        except subprocess.CalledProcessError:
            continue
        if login and login != "null":
            return login
    return None


def apply(actions: list[dict[str, object]], repo: str, run=gh) -> None:
    for action in actions:
        number = str(action.get("number", ""))
        if action["op"] == "create":
            run("issue", "create", "--repo", repo, "--title", TITLE, "--label", LABEL, "--body-file", "-",
                stdin=str(action["body"]))
        elif action["op"] == "edit":
            run("issue", "edit", number, "--repo", repo, "--add-label", LABEL, "--body-file", "-",
                stdin=str(action["body"]))
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
    actions = plan(due_soon, issues, None if args.dry_run else commit_author)
    print(json.dumps(actions, indent=2))
    if not args.dry_run:
        gh("label", "create", LABEL, "--repo", args.repo, "--force",
           "--description", "Needs an agent to act; watched by the escalation ladder")
        apply(actions, args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
