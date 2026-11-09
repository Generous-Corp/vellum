#!/usr/bin/env python3
"""Decide the `release-path-review` commit status: two independent reviews on release-path files.

A pull request that changes a file `.github/CODEOWNERS` names (the release-signing
policy, the code that enforces it, and the Shipyard review policy, see
check_release_policy.py), or any workflow, local action or file under
tools/mirror/, needs two counted reviewer APPROVEs naming its exact head, from
two different signers in two different reviewer sessions, and no HOLD naming
that head. A pull request that
changes none of those files passes.

A verdict counts on the same terms as the `review-verdict` status: a comment by
the fleet App whose last line is `verdict: APPROVE head:<sha>` (or
`reviewed: <sha>`), carrying exactly one canonical `ghapp` session stamp whose
session matches the base's `[auto_merge] reviewer_stamp_sessions`, and signed
with one `reviewer: <name>` from the base's `[auto_merge] reviewer_signers`. An
APPROVE naming any other head never counts, and nothing carries one forward. A
HOLD naming the head, from the fleet App or a trusted account, as a comment or
a changes-requested review, refuses the head.

The protected set, the signers and the session globs are read from the
default branch's checkout this script runs from, never from the pull request.
Every pull request, comment and review is read from the API.

For a merge-queue build (reached through the trigger workflow's
`workflow_run`), the status goes on the queue's commit, and the range is read
from the local checkout with git, never from the compare API (which truncates
at 300 files and refuses long ranges). Every first-parent commit between the
queue commit and its merge base with the default branch must be a two-parent
queue merge; each second parent is a queued pull request's head. Each entry
is judged by its own net diff (`git diff <first parent>...<entry>`, the same
change its pull request shows): an entry that changes a release-path file
needs the two APPROVEs at its head; one that changes none passes without
another read. The range's net diff must hold no release-path file that no
entry changed, and a merge base, commit or diff git cannot produce is an
`error`. Nothing is judged per commit: a path changed and changed back inside
one pull request is not in its net diff, exactly as on the pull request
itself, because what lands is the pull request's net tree.

The script only decides. It writes `sha`, `state` and `description` to
`$GITHUB_OUTPUT`, and a later step posts them with the Actions token. Once a
head is known, anything it cannot read is an `error` state, never a pass.
Exit status: 0 when it wrote a decision or there was nothing to judge, 1 when
it could not even name a head.

This is an accident guard, not a security boundary (Decision 0014; pulp-planning
architecture/2026-11-05-release-path-gate.md, option A). It stops an honest
release-path change from landing without two reviews. Anyone who holds the
fleet App's key can post the verdict comments it counts, and any workflow with
the Actions token can post the status it decides.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import secrets
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODEOWNERS = Path(".github/CODEOWNERS")
SHIPYARD_CONFIG = Path(".shipyard/config.toml")
CONTEXT = "release-path-review"
# GitHub refuses a status description over 140 characters.
DESCRIPTION_LIMIT = 140
FLEET_APP = "shipyard-local[bot]"
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
REQUIRED_APPROVALS = 2
# GitHub lists at most 3000 files of a pull request.
FILE_LIST_LIMIT = 3000
PAGE = 100
# Same patterns as Shipyard's ghapp_queue_arm_guard.py.
MARKER = re.compile(
    r"^[ \t]*(?:reviewed:[ \t]*([0-9a-f]{40})"
    r"|verdict:[ \t]*(APPROVE|HOLD)[ \t]+head:[ \t]*([0-9a-f]{40}))[ \t]*$"
)
SESSION_STAMP = re.compile(r"<!-- session: ([!-.0-~]+)/([!-~]+?)(?: agent: ([!-~]+?))? -->")
STAMP_VARIANT = re.compile(r"<!--\s*session\s*:", re.IGNORECASE)
SIGNER_LINE = re.compile(r"^[ \t]*reviewer:[ \t]*([A-Za-z0-9._-]+)[ \t]*$", re.MULTILINE)
UNATTRIBUTED = "unattributed"
# Every workflow and local action, and the mirror tooling the framework
# transfer is derived and censused with (rename_mirror_namespace.py and the
# collision and symbol censuses that verify_extraction.py runs).
PROTECTED_PREFIXES = (".github/workflows/", ".github/actions/", "tools/mirror/")
# A queue holding more entries than this is refused rather than judged in
# part. GitHub builds at most 100 entries in one group.
QUEUE_ENTRY_LIMIT = 100
# A merge-queue branch is `gh-readonly-queue/<base branch>/pr-<n>-<sha>`.
QUEUE_BRANCH = "gh-readonly-queue/{branch}/"


class GateError(RuntimeError):
    """The gate cannot read what it needs; it never passes on this."""


@dataclass(frozen=True)
class Policy:
    protected: frozenset[str]
    signers: frozenset[str]
    sessions: tuple[str, ...]


@dataclass(frozen=True)
class Approval:
    signer: str
    session: str
    comment: Any


def protected_paths(codeowners: str) -> frozenset[str]:
    """The files CODEOWNERS names, as repository paths.

    check_release_policy.py keeps CODEOWNERS to anchored file paths; any other
    pattern is refused here rather than matched loosely."""
    paths = set()
    for line in codeowners.splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        pattern = fields[0]
        if not pattern.startswith("/") or pattern.endswith("/") or any(c in pattern for c in "*?[]!\\"):
            raise GateError(f"{CODEOWNERS} entry `{pattern}` is not an anchored file path")
        paths.add(pattern.lstrip("/"))
    if not paths:
        raise GateError(f"{CODEOWNERS} names no files")
    return frozenset(paths)


def load_policy(root: Path) -> Policy:
    try:
        codeowners = (root / CODEOWNERS).read_text(encoding="utf-8")
        config = tomllib.loads((root / SHIPYARD_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GateError(f"the base policy is unreadable: {error}") from error
    section = config.get("auto_merge")
    section = section if isinstance(section, dict) else {}
    signers = section.get("reviewer_signers")
    sessions = section.get("reviewer_stamp_sessions")
    if not (isinstance(signers, list) and signers and all(isinstance(s, str) and s.strip() for s in signers)):
        raise GateError(f"{SHIPYARD_CONFIG} [auto_merge] reviewer_signers must be a non-empty list of names")
    if not (isinstance(sessions, list) and sessions and all(isinstance(s, str) and s.strip() for s in sessions)):
        raise GateError(f"{SHIPYARD_CONFIG} [auto_merge] reviewer_stamp_sessions must be a non-empty list of globs")
    return Policy(
        protected=protected_paths(codeowners),
        signers=frozenset(s.strip().lower() for s in signers),
        sessions=tuple(s.strip().lower() for s in sessions),
    )


def marker_verdict(body: str) -> tuple[str, str] | None:
    """`(APPROVE|HOLD, head)` from a comment's last non-blank line."""
    lines = [line for line in body.splitlines() if line.strip()]
    match = MARKER.match(lines[-1]) if lines else None
    if match is None:
        return None
    return ("APPROVE", match.group(1)) if match.group(1) else (match.group(2), match.group(3))


def is_fleet_app(user: Any) -> bool:
    return isinstance(user, dict) and user.get("login") == FLEET_APP and user.get("type") == "Bot"


def trusted(record: dict[str, Any]) -> bool:
    return is_fleet_app(record.get("user")) or record.get("author_association") in TRUSTED_ASSOCIATIONS


def counted_approval(comment: dict[str, Any], head: str, policy: Policy) -> Approval | None:
    """The `(signer, session)` of a counted APPROVE naming `head`, or None."""
    body = comment.get("body")
    if not isinstance(body, str) or not is_fleet_app(comment.get("user")):
        return None
    found = marker_verdict(body)
    if found != ("APPROVE", head):
        return None
    stamps = SESSION_STAMP.findall(body)
    if len(stamps) != 1 or STAMP_VARIANT.search(SESSION_STAMP.sub("", body)):
        return None
    session, agent = stamps[0][1].lower(), stamps[0][2].lower()
    if session == UNATTRIBUTED or not any(fnmatch.fnmatchcase(session, glob) for glob in policy.sessions):
        return None
    named = {name.lower() for name in SIGNER_LINE.findall(body)}
    # The stamp's agent is the identity ghapp posted as; the signer line must name it.
    if len(named) != 1 or not named <= policy.signers or named != {agent}:
        return None
    return Approval(signer=named.pop(), session=session, comment=comment.get("id"))


def holds_on(head: str, comments: Iterable[dict[str, Any]], reviews: Iterable[dict[str, Any]]) -> list[str]:
    """Every trusted HOLD naming `head`."""
    found = []
    for comment in comments:
        body = comment.get("body")
        if isinstance(body, str) and trusted(comment) and marker_verdict(body) == ("HOLD", head):
            found.append(f"comment {comment.get('id')}")
    for review in reviews:
        if (str(review.get("state") or "").upper() == "CHANGES_REQUESTED" and trusted(review)
                and str(review.get("commit_id") or "").lower() == head):
            found.append(f"review {review.get('id')}")
    return found


def independent_pair(approvals: list[Approval]) -> tuple[Approval, Approval] | None:
    """Two APPROVEs with different signers and different sessions, or None."""
    for index, first in enumerate(approvals):
        for second in approvals[index + 1:]:
            if first.signer != second.signer and first.session != second.session:
                return first, second
    return None


def touched(files: Iterable[dict[str, Any]], protected: frozenset[str]) -> list[str]:
    """The protected paths a pull request's file list adds, changes, removes or renames away.

    Every workflow and local action is protected as well as the CODEOWNERS
    files: a workflow runs with the repository's token and secrets, so one a
    pull request adds could otherwise post a required status itself. So is
    everything under tools/mirror/, which the framework transfer's judges
    load and run."""
    hits = set()
    for entry in files:
        for key in ("filename", "previous_filename"):
            name = entry.get(key)
            if isinstance(name, str) and (name in protected or name.startswith(PROTECTED_PREFIXES)):
                hits.add(name)
    return sorted(hits)


def judge(head: str, files: list[dict[str, Any]], comments: list[dict[str, Any]],
          reviews: list[dict[str, Any]], policy: Policy) -> tuple[bool, str]:
    """`(allowed, why)` for one pull request at `head`."""
    head = head.lower()
    hits = touched(files, policy.protected)
    if not hits:
        return True, "no release-path file changed"
    what = hits[0] + (f" and {len(hits) - 1} more" if len(hits) > 1 else "")
    holds = holds_on(head, comments, reviews)
    if holds:
        return False, f"{what}: a HOLD names head {head[:12]} ({holds[0]})"
    approvals = [a for a in (counted_approval(c, head, policy) for c in comments) if a is not None]
    pair = independent_pair(approvals)
    if pair is None:
        signers = sorted({a.signer for a in approvals})
        return False, (f"{what}: needs {REQUIRED_APPROVALS} counted APPROVEs of head {head[:12]} from different "
                       f"signers and sessions; has {len(approvals)} ({', '.join(signers) or 'none'})")
    return True, f"{what}: approved by {pair[0].signer} and {pair[1].signer} at {head[:12]}"


# A transient failure (a 5xx, a timeout, a reset connection) is retried with
# these waits before it becomes an `error`: one bad read among a queue batch's
# few dozen would otherwise eject the whole batch.
RETRY_WAITS = (2, 5, 10)


def transient(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    return isinstance(error, OSError)  # URLError, timeouts and resets are all OSErrors


class Api:
    """Minimal read-only GitHub REST client."""

    def __init__(self, token: str, base: str = "https://api.github.com",
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.token, self.base = token, base.rstrip("/")
        self.opener, self.sleep = opener, sleep

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{self.base}/{path.lstrip('/')}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        for attempt, wait in enumerate((*RETRY_WAITS, None)):
            try:
                with self.opener(request, timeout=30) as response:
                    return json.loads(response.read() or b"null")
            except ValueError as error:
                raise GateError(f"{method} {path}: {error}") from error
            except OSError as error:
                if wait is None or not transient(error):
                    raise GateError(f"{method} {path}: {error}") from error
                self.sleep(wait)
        raise AssertionError("unreachable")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def pages(self, path: str, limit: int = FILE_LIST_LIMIT) -> list[dict[str, Any]]:
        joiner = "&" if "?" in path else "?"
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.get(f"{path}{joiner}per_page={PAGE}&page={page}")
            if not isinstance(batch, list):
                raise GateError(f"{path} is not a list")
            items.extend(batch)
            if len(batch) < PAGE:
                return items
            if len(items) >= limit:
                raise GateError(f"{path} lists {limit} or more entries; it cannot be read whole")
            page += 1


def judge_pull(api: Api, repo: str, number: int, head: str, policy: Policy) -> tuple[bool, str]:
    pull = api.get(f"repos/{repo}/pulls/{number}")
    if not isinstance(pull, dict):
        raise GateError(f"PR #{number} is unreadable")
    current = str((pull.get("head") or {}).get("sha") or "").lower()
    if current != head.lower():
        raise GateError(f"PR #{number} moved from {head[:12]} to {current[:12]}")
    files = api.pages(f"repos/{repo}/pulls/{number}/files")
    if len(files) != pull.get("changed_files"):
        raise GateError(f"PR #{number} lists {len(files)} files but changes {pull.get('changed_files')}")
    comments = api.pages(f"repos/{repo}/issues/{number}/comments", limit=10**6)
    reviews = api.pages(f"repos/{repo}/pulls/{number}/reviews", limit=10**6)
    return judge(head, files, comments, reviews, policy)


def open_pulls_on(api: Api, repo: str, head: str) -> list[int]:
    """Every open pull request whose head is `head`."""
    pulls = api.get(f"repos/{repo}/commits/{head}/pulls?per_page={PAGE}")
    if not isinstance(pulls, list):
        raise GateError(f"the pull requests on {head[:12]} are unreadable")
    return sorted(p["number"] for p in pulls
                  if isinstance(p, dict) and isinstance(p.get("number"), int) and p.get("state") == "open"
                  and str((p.get("head") or {}).get("sha") or "").lower() == head.lower())


def judge_head(api: Api, repo: str, head: str, numbers: Iterable[int], policy: Policy) -> tuple[bool, str] | None:
    """The decision for a pull request head, or None when no open pull request has it.

    A status belongs to a commit, so it is `success` only when every open pull
    request whose head it is passes."""
    judged = sorted(set(numbers) | set(open_pulls_on(api, repo, head)))
    if not judged:
        return None
    verdicts = [(n, *judge_pull(api, repo, n, head, policy)) for n in judged]
    refused = [(n, why) for n, ok, why in verdicts if not ok]
    if refused:
        return False, (f"PR #{refused[0][0]}: " if len(judged) > 1 else "") + refused[0][1]
    return True, verdicts[0][2] + (f" (PRs {', '.join(f'#{n}' for n in judged)})" if len(judged) > 1 else "")


class Git:
    """Read-only git over the local checkout; any failure is a GateError."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def run(self, *args: str) -> str:
        try:
            done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                                  timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise GateError(f"git {args[0]}: {error}") from error
        if done.returncode:
            raise GateError(f"git {' '.join(args[:2])} failed: {one_line(done.stderr)[:200]}")
        return done.stdout

    def commit(self, ref: str) -> str:
        sha = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GateError(f"{ref} is not a commit in the checkout")
        return sha

    def merge_base(self, first: str, second: str) -> str:
        return self.commit(self.run("merge-base", first, second).strip())

    def changed(self, base: str, head: str) -> list[str]:
        """Every path `head` changes against its merge base with `base`.

        Renames are split into a delete and an add, so both names count."""
        out = self.run("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
        return sorted({name for name in out.split("\0") if name})

    def first_parent_chain(self, base: str, head: str) -> list[tuple[str, list[str]]]:
        """`(commit, parents)` from `head` down the first-parent chain to `base`."""
        rows = self.run("rev-list", "--first-parent", "--parents", f"{base}..{head}").split("\n")
        chain = []
        for row in rows:
            fields = row.split()
            if fields:
                chain.append((fields[0], fields[1:]))
        return chain


def queue_entries(base: str, head: str, chain: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    """`(first parent, entry head)` for each pull request a queue commit adds, oldest first.

    Every commit on the first-parent chain from `head` back to `base` must be a
    two-parent queue merge; its second parent is the entry's head."""
    parents = dict(chain)
    entries, at = [], head
    while at != base:
        if at not in parents:
            raise GateError(f"queue commit {at[:12]} is not between {base[:12]} and {head[:12]}")
        if len(parents[at]) != 2:
            raise GateError(f"queue commit {at[:12]} is not a two-parent merge")
        entries.append((parents[at][0], parents[at][1]))
        if len(entries) > QUEUE_ENTRY_LIMIT:
            raise GateError(f"the queue commit adds more than {QUEUE_ENTRY_LIMIT} entries")
        at = parents[at][0]
    if not entries:
        raise GateError("the queue commit adds no pull request")
    return list(reversed(entries))


def protected_hits(paths: Iterable[str], protected: frozenset[str]) -> list[str]:
    return touched(({"filename": path} for path in paths), protected)


def judge_queue(api: Api, repo: str, branch: str, head: str, policy: Policy, git: Git) -> tuple[bool, str]:
    head = git.commit(head)
    tip = None
    for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        try:
            tip = git.commit(ref)
            break
        except GateError:
            continue
    if tip is None:
        raise GateError(f"the checkout has no {branch} to find the queue's merge base")
    base = git.merge_base(tip, head)
    entries = queue_entries(base, head, git.first_parent_chain(base, head))
    claimed: set[str] = set()
    passed = []
    for previous, entry in entries:
        hits = protected_hits(git.changed(previous, entry), policy.protected)
        if not hits:
            continue
        claimed.update(hits)
        numbers = open_pulls_on(api, repo, entry)
        if len(numbers) != 1:
            raise GateError(f"queue entry {entry[:12]} is the head of {len(numbers)} open pull requests, not one")
        comments = api.pages(f"repos/{repo}/issues/{numbers[0]}/comments", limit=10**6)
        reviews = api.pages(f"repos/{repo}/pulls/{numbers[0]}/reviews", limit=10**6)
        allowed, why = judge(entry, [{"filename": path} for path in hits], comments, reviews, policy)
        if not allowed:
            return False, f"PR #{numbers[0]}: {why}"
        passed.append(f"#{numbers[0]}")
    unclaimed = sorted(set(protected_hits(git.changed(base, head), policy.protected)) - claimed)
    if unclaimed:
        raise GateError(f"the queue range changes {unclaimed[0]}, which no queued pull request changes")
    if not passed:
        return True, f"no release-path file changed in {len(entries)} queued pull request(s)"
    return True, f"every queued release-path pull request is approved ({', '.join(passed)})"


def describe(text: str) -> str:
    return text if len(text) <= DESCRIPTION_LIMIT else text[: DESCRIPTION_LIMIT - 1] + "…"


def target(event_name: str, event: dict[str, Any], api: Api, repo: str) -> tuple[str, str | None, list[int]]:
    """`(kind, head, pull requests)`: kind is `pull`, `queue` or `none`.

    A merge-queue build and a review reach this workflow through the
    permission-less trigger workflow's `workflow_run`, so the rule always runs
    from the default branch."""
    if event_name in ("pull_request_target", "issue_comment"):
        if event_name == "issue_comment":
            issue = event.get("issue") or {}
            if not isinstance(issue.get("pull_request"), dict):
                return "none", None, []
            number = issue.get("number")
        else:
            number = (event.get("pull_request") or {}).get("number")
        if not isinstance(number, int):
            raise GateError("the event names no pull request")
        pull = api.get(f"repos/{repo}/pulls/{number}")
        if not isinstance(pull, dict):
            raise GateError(f"PR #{number} is unreadable")
        if pull.get("state") != "open":
            return "none", None, []
        head = str((pull.get("head") or {}).get("sha") or "").lower()
        if not head:
            raise GateError(f"PR #{number} has no readable head")
        return "pull", head, [number]
    if event_name == "workflow_run":
        run = event.get("workflow_run") or {}
        head = str(run.get("head_sha") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise GateError("the triggering run names no head")
        if run.get("event") == "merge_group":
            branch = str((event.get("repository") or {}).get("default_branch") or "")
            if not branch or not str(run.get("head_branch") or "").startswith(QUEUE_BRANCH.format(branch=branch)):
                raise GateError(f"merge-group branch {run.get('head_branch')!r} is not a queue branch")
            return "queue", head, []
        numbers = [p.get("number") for p in run.get("pull_requests") or []
                   if isinstance(p, dict) and isinstance(p.get("number"), int)]
        return "pull", head, numbers
    raise GateError(f"unsupported event {event_name!r}")


def one_line(text: str) -> str:
    """`text` on one line: a pull request's file names reach the log and the
    status, and a newline in one could start a workflow command."""
    return " ".join(str(text).split())


def decide(event_name: str, event: dict[str, Any], repo: str, api: Api, root: Path,
           log: Callable[[str], None] = print, git: Git | None = None) -> tuple[str, str, str] | None:
    """`(sha, state, description)` to post, or None when there is nothing to judge.

    Never raises for a judgement it cannot make: once a head is known, a
    failure to read anything is an `error` status on it."""
    raw_log = log

    def log(message: str) -> None:
        raw_log(one_line(message))

    # The head the event names, so that a failure before the API names it is
    # still an `error` status on that head rather than a silent exit.
    named = ""
    if event_name == "workflow_run":
        named = str((event.get("workflow_run") or {}).get("head_sha") or "").lower()
    elif event_name == "pull_request_target":
        named = str(((event.get("pull_request") or {}).get("head") or {}).get("sha") or "").lower()
    head = named if re.fullmatch(r"[0-9a-f]{40}", named) else None
    try:
        kind, head, numbers = target(event_name, event, api, repo)
        if kind == "none" or head is None:
            log("release-path-review: nothing to judge")
            return None
        policy = load_policy(root)
        if kind == "queue":
            branch = str((event.get("repository") or {}).get("default_branch") or "")
            if not branch:
                raise GateError("the event names no default branch")
            allowed, why = judge_queue(api, repo, branch, head, policy, git or Git(root))
        else:
            result = judge_head(api, repo, head, numbers, policy)
            if result is None:
                log(f"release-path-review: no open pull request has head {head}")
                return None
            allowed, why = result
    except Exception as error:  # noqa: BLE001 - any failure on a known head is an error status, never silence
        if not isinstance(error, GateError):
            error = GateError(f"{type(error).__name__}: {error}")
        log(f"release-path-review: error: {error}")
        if head is None:
            raise error
        return head, "error", describe(one_line(f"cannot judge: {error}"))
    state = "success" if allowed else "failure"
    log(f"release-path-review: {state} on {head}: {why}")
    return head, state, describe(one_line(why))


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    token = os.environ.get("GH_TOKEN", "")
    output = os.environ.get("GITHUB_OUTPUT", "")
    if not (event_name and repo and event_path and token and output):
        print("release-path-review: GITHUB_EVENT_NAME, GITHUB_REPOSITORY, GITHUB_EVENT_PATH, GH_TOKEN and "
              "GITHUB_OUTPUT are required", file=sys.stderr)
        return 2
    api = Api(token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    # The log carries pull-request file names; the runner reads no workflow
    # command from it until the token, which nothing in it can guess, ends it.
    token_line = secrets.token_hex(16)
    print(f"::stop-commands::{token_line}", flush=True)
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        decision = decide(event_name, event, repo, api, ROOT)
    except (OSError, ValueError, GateError) as error:
        print(one_line(f"release-path-review: error: {error}"), file=sys.stderr)
        return 1
    finally:
        print(f"::{token_line}::", flush=True)
    if decision is None:
        return 0
    sha, state, description = decision
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"sha={sha}\nstate={state}\ndescription={one_line(description)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
