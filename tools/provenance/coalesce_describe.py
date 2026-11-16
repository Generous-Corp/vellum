#!/usr/bin/env python3
"""Describe a coalescing evidence pull request: a title and a body a person can read.

    coalesce_describe.py --repo . --base HEAD --title-file T --body-file B

Run after a reconcile has rewritten the working tree. `--base` is main, whose
committed cursor and events are the "before". The commits listed are the
landings on main's first-parent history the cursor now covers, so a reviewer
sees which changes the evidence records.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_LISTED = 20
CURSOR = "provenance/pulp-observatory/cursor.json"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def landings(repo: Path, old: str, new: str) -> list[tuple[str, str]]:
    """Short SHA and a readable subject for each first-parent landing in old..new."""
    rows = git(
        repo, "log", "--first-parent", "--reverse", "--format=%h%x00%s%x00%b%x01", f"{old}..{new}"
    ).split("\x01")
    found: list[tuple[str, str]] = []
    for row in rows:
        fields = row.strip("\n").split("\x00")
        if len(fields) != 3 or not fields[0].strip():
            continue
        sha, subject, body = (field.strip() for field in fields)
        # A merge commit's own subject only names the branch; the title is its first body line.
        if subject.startswith("Merge pull request") and body:
            subject = body.splitlines()[0].strip()
        found.append((sha, subject))
    return found


def cursor_commit(repo: Path, revision: str | None, side: str) -> str:
    text = (
        git(repo, "show", f"{revision}:{CURSOR}")
        if revision else (repo / CURSOR).read_text(encoding="utf-8")
    )
    return json.loads(text)[side]["last_scanned_commit"]


def pending_count(repo: Path, revision: str | None) -> int:
    """Pending observations in the working tree, or in the events committed at `revision`."""
    sys.path.append(str(Path(__file__).resolve().parent))
    import observatory  # noqa: E402

    if revision is None:
        return observatory.pending_event_count(repo, None, observatory.load_events(repo), "all")
    with tempfile.TemporaryDirectory() as scratch:
        archive = subprocess.Popen(
            ["git", "archive", "--format=tar", revision, "provenance/pulp-observatory/events"],
            cwd=repo, stdout=subprocess.PIPE,
        )
        subprocess.run(["tar", "-x", "-C", scratch], stdin=archive.stdout, check=True)
        if archive.wait() != 0:
            raise RuntimeError(f"git archive {revision} failed")
        root = Path(scratch)
        return observatory.pending_event_count(root, None, observatory.load_events(root), "all")


def describe(
    *, repo: Path, old: str, new: str, pending_before: int, pending_after: int,
    pulp_old: str, pulp_new: str,
) -> tuple[str, str]:
    commits = landings(repo, old, new)
    count = len(commits)
    noun = "commit" if count == 1 else "commits"
    title = (
        f"Record {count} Vellum {noun} ({old[:9]}..{new[:9]}) as observed; "
        f"pending {pending_before} to {pending_after}"
    )
    lines = [
        f"Evidence only: records {count} Vellum {noun} landed on main as Pulp observatory events.",
        "",
        f"- Vellum cursor: `{old[:9]}` to `{new[:9]}`",
        f"- Pulp cursor: " + (
            f"`{pulp_old[:9]}` to `{pulp_new[:9]}`" if pulp_old != pulp_new
            else f"`{pulp_old[:9]}` (unchanged)"
        ),
        f"- Pending observations: {pending_before} to {pending_after} "
        f"(change {pending_after - pending_before:+d})",
        "",
        "Commits recorded:",
        "",
    ]
    lines += [f"- `{sha}` {subject}" for sha, subject in commits[:MAX_LISTED]]
    if count > MAX_LISTED:
        lines.append(f"- and {count - MAX_LISTED} more")
    if not commits:
        lines.append("- none on the first-parent history (cursor moved within evidence only)")
    lines += [
        "",
        "Lands by merge commit once the required checks pass; never squash or rebase.",
        "",
        "Opened by the Pulp observatory receiver.",
    ]
    return title, "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--base", required=True,
        help="the revision whose committed cursor and events are the 'before' (main)",
    )
    parser.add_argument("--title-file", type=Path, required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    title, body = describe(
        repo=repo,
        old=cursor_commit(repo, args.base, "vellum"),
        new=cursor_commit(repo, None, "vellum"),
        pending_before=pending_count(repo, args.base),
        pending_after=pending_count(repo, None),
        pulp_old=cursor_commit(repo, args.base, "pulp"),
        pulp_new=cursor_commit(repo, None, "pulp"),
    )
    args.title_file.write_text(title + "\n", encoding="utf-8")
    args.body_file.write_text(body, encoding="utf-8")
    print(title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
