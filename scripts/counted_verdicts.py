"""Read a counted reviewer verdict from a pull request comment.

One parser serves every rule that counts a fleet reviewer's APPROVE: the
`release-path-review` status (release_path_review.py) and the release
readiness open-PR audit, which counts an open pull request as landable when
it carries one (tools/provenance/collect_release_evidence.py).

A verdict counts on the same terms as the `review-verdict` status: a comment
by the fleet App whose last non-blank line is `verdict: APPROVE head:<sha>`
(or `reviewed: <sha>`), carrying exactly one canonical `ghapp` session stamp
whose session matches the base's `[auto_merge] reviewer_stamp_sessions`, and
signed with one `reviewer: <name>` from the base's `[auto_merge]
reviewer_signers`, which must also be the stamp's agent. An APPROVE naming
any other head never counts.

The module reads files and strings only. It has no network or process access
of its own, so the release evidence collector that imports it stays a pure
reshaping of the API responses the workflow fetched.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHIPYARD_CONFIG = Path(".shipyard/config.toml")
FLEET_APP = "shipyard-local[bot]"
# Same patterns as Shipyard's ghapp_queue_arm_guard.py.
MARKER = re.compile(
    r"^[ \t]*(?:reviewed:[ \t]*([0-9a-f]{40})"
    r"|verdict:[ \t]*(APPROVE|HOLD)[ \t]+head:[ \t]*([0-9a-f]{40}))[ \t]*$"
)
SESSION_STAMP = re.compile(r"<!-- session: ([!-.0-~]+)/([!-~]+?)(?: agent: ([!-~]+?))? -->")
STAMP_VARIANT = re.compile(r"<!--\s*session\s*:", re.IGNORECASE)
SIGNER_LINE = re.compile(r"^[ \t]*reviewer:[ \t]*([A-Za-z0-9._-]+)[ \t]*$", re.MULTILINE)
UNATTRIBUTED = "unattributed"


class ReviewerPolicyError(RuntimeError):
    """The base's reviewer policy cannot be read."""


@dataclass(frozen=True)
class ReviewerPolicy:
    signers: frozenset[str]
    sessions: tuple[str, ...]


@dataclass(frozen=True)
class Approval:
    signer: str
    session: str
    comment: Any


def load_reviewer_policy(root: Path) -> ReviewerPolicy:
    """The counted signers and session globs from `root`'s `.shipyard/config.toml`."""
    try:
        config = tomllib.loads((root / SHIPYARD_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewerPolicyError(f"{SHIPYARD_CONFIG} is unreadable: {error}") from error
    section = config.get("auto_merge")
    section = section if isinstance(section, dict) else {}
    signers = section.get("reviewer_signers")
    sessions = section.get("reviewer_stamp_sessions")
    if not (isinstance(signers, list) and signers and all(isinstance(s, str) and s.strip() for s in signers)):
        raise ReviewerPolicyError(f"{SHIPYARD_CONFIG} [auto_merge] reviewer_signers must be a non-empty list of names")
    if not (isinstance(sessions, list) and sessions and all(isinstance(s, str) and s.strip() for s in sessions)):
        raise ReviewerPolicyError(f"{SHIPYARD_CONFIG} [auto_merge] reviewer_stamp_sessions must be a non-empty list of globs")
    return ReviewerPolicy(
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


def counted_approval(comment: dict[str, Any], head: str, policy: Any) -> Approval | None:
    """The `(signer, session)` of a counted APPROVE naming `head`, or None.

    `policy` is anything with `signers` and `sessions`, as ReviewerPolicy has."""
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
