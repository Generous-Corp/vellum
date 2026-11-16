#!/usr/bin/env python3
"""Keep one issue current with the pending observation events nearing their deadline.

The issue is also kept open while the pending count is at or above the watchdog's
warning level (receiver-policy.json pending_warning, 15 of the 20 limit), listing
every pending event, so a ledger heading for the limit is seen before a push to main
fails on it. Each row names the source commit and the action: a disposition.

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


ACTION = "Classify with a resolution event (a disposition)"


def cell_text(text: str) -> str:
    """Text safe inside one Markdown table cell: no row or column break, no code span."""
    return " ".join(text.split()).replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def commit_cell(row: dict[str, object], subject=None) -> str:
    sha = str(row.get("source_commit") or "")
    text = subject(str(row["source_repository"]), sha) if subject and sha else None
    return f"`{sha[:9] or 'none'}`" + (f" {cell_text(text)}" if text else "")


def render(due_soon: list[dict[str, object]], lookup=None, subject=None,
           pressure: dict[str, int] | None = None) -> str:
    urgent = any(row["urgent"] for row in due_soon)
    lines = []
    if urgent:
        lines.append(URGENT_MARK)
    soon = [row for row in due_soon if row["hours_left"] <= 24]
    if soon:
        lines.append(f"{len(soon)} pending observation event(s) reach their classification "
                     "deadline within 24 hours. The owning lane classifies each with a resolution "
                     "event (provenance/pulp-observatory/README.md) before the deadline: once it "
                     "passes, pushes to `main` and release tags fail `provenance-verify`.")
    if pressure:
        lines.append(f"{pressure['count']} observations are pending, at or above the warning level "
                     f"{pressure['warning']} of the limit {pressure['limit']}. At the limit, pushes to "
                     "`main` fail `provenance-verify`; each needs a disposition, and a coalescing "
                     "evidence PR does not clear them.")
    if any(row["needs_ruling"] and not row["urgent"] for row in due_soon):
        lines.append(f"An event here needs an owner ruling: {RULING_OWNER}")
    lines += ["", "| event | commit | class | lane | deadline | hours left | action |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in due_soon:
        lines.append(f"| {row['event_id']} | {commit_cell(row, subject)} | {row['class']} | "
                     f"`{lane(row, lookup)}` | {row['deadline']} | "
                     f"{row['hours_left']}{' (urgent)' if row['urgent'] else ''} | {ACTION} |")
    return "\n".join(lines) + "\n"


def plan(due_soon: list[dict[str, object]], open_issues: list[dict[str, object]],
         lookup=None, subject=None, pressure_rows: list[dict[str, object]] | None = None,
         pressure: dict[str, int] | None = None) -> list[dict[str, object]]:
    """Actions to take, given the open issues (dicts with number, title, body).

    `pressure_rows` and `pressure` keep the issue open, listing every pending
    event, while the pending count is at the warning level and nothing is due soon.
    """
    if pressure and pressure_rows:
        seen = {row["event_id"] for row in due_soon}
        due_soon = due_soon + [row for row in pressure_rows if row["event_id"] not in seen]
    else:
        pressure = None
    mine = sorted((issue for issue in open_issues if issue["title"] == TITLE),
                  key=lambda issue: issue["number"])
    actions: list[dict[str, object]] = []
    if not due_soon:
        for issue in mine:
            actions.append({"op": "close", "number": issue["number"],
                            "comment": "No pending event is within 24 hours of its classification deadline "
                                       "and the pending count is below the warning level."})
        return actions
    body = render(due_soon, lookup, subject, pressure)
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


def open_issues(repo: str) -> list[dict[str, object]]:
    """Return every open issue, including issues beyond the first API page."""
    pages = json.loads(gh("api", "--paginate", "--slurp",
                          f"repos/{repo}/issues?state=open&per_page=100"))
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub open-issues response is not a list of pages")
    return [issue for page in pages for issue in page
            if isinstance(issue, dict) and "pull_request" not in issue]


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


def commit_subject(repository: str, commit: str) -> str | None:
    """First line of the commit message, else None."""
    try:
        text = gh("api", f"repos/{repository}/commits/{commit}", "--jq", ".commit.message").strip()
    except subprocess.CalledProcessError:
        return None
    return text.splitlines()[0] if text else None


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
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    due_soon = summary["due_soon"]
    count, warning = summary.get("pending_count", 0), summary.get("pending_warning", 10**9)
    pressure = ({"count": count, "warning": warning, "limit": summary["pending_limit"]}
                if count >= warning else None)
    try:
        issues = open_issues(args.repo)
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"could not read open issues; nothing was created or changed: {error}", file=sys.stderr)
        return 1
    actions = plan(due_soon, issues, None if args.dry_run else commit_author,
                   None if args.dry_run else commit_subject, summary.get("pending"), pressure)
    print(json.dumps(actions, indent=2))
    if not args.dry_run:
        gh("label", "create", LABEL, "--repo", args.repo, "--force",
           "--description", "Needs an agent to act; watched by the escalation ladder")
        apply(actions, args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
