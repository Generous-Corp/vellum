#!/usr/bin/env python3
"""Decide whether a landed version bump may be tagged, and describe its freeze.

A release is a landed change to the project VERSION in CMakeLists.txt that is
newer than every release tag, existing or retired. version-at-land.yml opens
that change as a pull request; a person may open one too. Either way it lands
through the merge queue like any other change. The commit that lands it is
not tagged: a tag's provenance run requires every commit after the Pulp
observatory's Vellum cursor to be observatory evidence, and a commit's tree
can never name that same commit as its cursor. The observatory receiver then
lands a reconcile commit whose cursor covers the bump, and release-tag.yml
tags that commit. This script makes the decision from files the workflow has
already fetched and the local clone, so it needs no token:

  select  whether the pushed commit is the observatory reconcile that covers
          an untagged version bump, and so the commit to tag.
  check   the project version is new, unretired and newer than every release
          tag, the tag does not exist yet, every check the main branch rules
          require concluded success on the commit, and the live answer of
          GET /repos/{repo}/immutable-releases says immutable releases are
          enabled.
  freeze  the per-tag ruleset that verify_release_tag_ruleset.py requires
          before an immutable release is finalized.
  attestation
          the promotion attestation the tag carries as its signed message,
          attested by the release pipeline itself (owner decision 2026-10-31),
          checked with the release-readiness verifier's own validators before
          anything is signed. It binds the parity completion record when one
          exists at the commit, and otherwise is the mirror-promotion
          attestation that only the listed mirror-promotion tags may carry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import version_at_land as val  # noqa: E402
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")


REPOSITORY = "Generous-Corp/vellum"
CMAKELISTS = "CMakeLists.txt"
RETIRED_TAGS = val.retired_record.RELATIVE_PATH
OBSERVATORY = "provenance/pulp-observatory"
CURSOR = f"{OBSERVATORY}/cursor.json"
# What an observatory evidence commit may change, exactly as the tag's
# provenance run (tools/provenance/observatory.py, verify_vellum_observatory_tail)
# accepts it: the cursor and the two reports modified, and new event files.
EVIDENCE_MODIFIED = frozenset({CURSOR, f"{OBSERVATORY}/reports/current.json",
                               f"{OBSERVATORY}/reports/current.md"})
EVENTS_PREFIX = f"{OBSERVATORY}/events/"
EXPANSION = Path("provenance/authority/expansions/full-design-import-render-v1")


class ReleaseRequestError(RuntimeError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseRequestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (OSError, ValueError) as error:
        raise ReleaseRequestError(f"cannot read {path}: {error}") from error


def project_version(cmakelists: str) -> str:
    """The project VERSION, read by the one parser every release script shares."""
    try:
        return val.project_version(cmakelists)
    except val.VersionError as error:
        message = str(error)
        if "MAJOR.MINOR.PATCH" in message:
            message = f"the project VERSION is not MAJOR.MINOR.PATCH ({message})"
        raise ReleaseRequestError(message) from error


def retired(retired_tags: Any) -> set[str]:
    try:
        return val.retired_record.tag_names(retired_tags)
    except val.retired_record.RetiredTagsError as error:
        raise ReleaseRequestError(str(error)) from error


def required_checks(branch_rules: Any, protection: Any) -> list[tuple[str, int | None]]:
    """Every check main requires, as (context, app id or None for any app).

    Required checks can come from classic branch protection (Vellum's do,
    GET /branches/main/protection/required_status_checks) or from a ruleset's
    required_status_checks rule (GET /rules/branches/main). Both are read, and
    each check keeps the app it is bound to.
    """
    if not isinstance(branch_rules, list):
        raise ReleaseRequestError("branch rules must be a list")
    found: dict[str, int | None] = {}

    def add(context: Any, app: Any) -> None:
        if not isinstance(context, str) or not context:
            raise ReleaseRequestError("a required check has no context")
        if app is not None and (not isinstance(app, int) or isinstance(app, bool) or app <= 0):
            raise ReleaseRequestError(f"required check {context} has an invalid app binding: {app!r}")
        app_id = app
        if context in found and found[context] not in (None, app_id) and app_id is not None:
            raise ReleaseRequestError(f"required check {context} is bound to two different apps")
        if found.get(context) is None:
            found[context] = app_id

    for rule in branch_rules:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            params = rule.get("parameters")
            rows = params.get("required_status_checks") if isinstance(params, dict) else None
            if not isinstance(rows, list):
                raise ReleaseRequestError("a required_status_checks rule has no check list")
            for row in rows:
                if not isinstance(row, dict):
                    raise ReleaseRequestError("a required status check row is not an object")
                add(row.get("context"), row.get("integration_id"))
    if protection is not None:
        if not isinstance(protection, dict):
            raise ReleaseRequestError("branch protection required checks must be an object")
        checks = protection.get("checks")
        if isinstance(checks, list) and checks:
            for row in checks:
                if not isinstance(row, dict):
                    raise ReleaseRequestError("a required status check row is not an object")
                add(row.get("context"), row.get("app_id"))
        else:
            for context in protection.get("contexts") or []:
                add(context, None)
    if not found:
        raise ReleaseRequestError("main requires no status check; refusing to tag unchecked")
    return sorted(found.items())


def check_runs(pages: Any) -> list[dict[str, Any]]:
    """Check runs from `gh api --paginate --slurp`, an array of pages."""
    if not isinstance(pages, list):
        raise ReleaseRequestError("check-run pages must be an array")
    runs = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("check_runs"), list):
            raise ReleaseRequestError("a check-run page has no `check_runs` list")
        runs += [run for run in page["check_runs"] if isinstance(run, dict)]
    return runs


ACTIONS_APP_ID = 15368
ACTIONS_BOT = {"login": "github-actions[bot]", "type": "Bot", "id": 41898282}


def exact_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def commit_statuses(pages: Any) -> list[dict[str, Any]]:
    """Commit statuses from `gh api --paginate --slurp`, an array of pages of statuses.

    Fails closed: any entry that is not an object with an integer id, a context,
    a state and a creator object refuses the release, so a malformed file never
    reads as an empty or passing one.
    """
    if not isinstance(pages, list):
        raise ReleaseRequestError("commit-status pages must be an array")
    found = []
    seen: set[int] = set()
    for page in pages:
        if not isinstance(page, list):
            raise ReleaseRequestError("a commit-status page is not a list")
        for row in page:
            creator = row.get("creator") if isinstance(row, dict) else None
            if (not isinstance(row, dict) or not exact_int(row.get("id")) or row["id"] <= 0
                    or not isinstance(row.get("context"), str) or not isinstance(row.get("state"), str)
                    or not isinstance(creator, dict) or not isinstance(creator.get("login"), str)
                    or not isinstance(creator.get("type"), str) or not exact_int(creator.get("id"))
                    or moment(row.get("created_at")) is None):
                raise ReleaseRequestError("a commit status is malformed: it needs a positive integer id, "
                                          "context, state, created_at and a creator with login, type and id")
            if row["id"] in seen:
                raise ReleaseRequestError(f"commit status id {row['id']} appears twice")
            seen.add(row["id"])
            found.append(row)
    return found


def newest_run(context: str, app_id: int | None, runs: list[dict[str, Any]],
               commit: str) -> dict[str, Any] | None:
    """The newest run of `context` on `commit` from the bound app, by id.

    GitHub assigns ids at creation, so a re-run still in progress outranks an
    older success.
    """
    candidates = [run for run in runs
                  if run.get("head_sha") == commit and run.get("name") == context
                  and isinstance(run.get("id"), int)
                  and (app_id is None or (run.get("app") or {}).get("id") == app_id)]
    return max(candidates, key=lambda row: row["id"]) if candidates else None


def newest_status(context: str, app_id: int | None,
                  statuses: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest commit status of `context` that may satisfy a check bound to `app_id`, by id.

    Only a context bound to the Actions app can be met by a status, and only a
    status whose creator is the github-actions bot account (login, type and id
    all match). Anyone with write access can post a status under any context,
    so a status from any other creator is ignored, never counted.
    """
    if app_id != ACTIONS_APP_ID:
        return None
    candidates = [row for row in statuses if row["context"] == context
                  and all(row["creator"].get(key) == value for key, value in ACTIONS_BOT.items())]
    return max(candidates, key=lambda row: row["id"]) if candidates else None


def run_state(run: dict[str, Any]) -> str:
    if run.get("status") != "completed":
        return "pending"
    return "success" if run.get("conclusion") == "success" else "failure"


def status_state(row: dict[str, Any]) -> str:
    return row["state"] if row["state"] in ("success", "pending") else "failure"


def moment(text: Any) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def newest_result(context: str, app_id: int | None, runs: list[dict[str, Any]],
                  statuses: list[dict[str, Any]], commit: str) -> tuple[str, str] | None:
    """(state, description) of the result for `context`, or None when there is none.

    Check run ids and commit status ids are different id spaces and the two
    carry different clocks, so the sources are never ordered against each other.
    Each source contributes its newest result (by id within the source); when a
    context has both, the worse of the two decides, so it is met only when the
    newest of each is success and neither is still running.
    """
    run = newest_run(context, app_id, runs, commit)
    row = newest_status(context, app_id, statuses)
    found = []
    if run is not None:
        found.append((run_state(run), f"{run.get('status')}/{run.get('conclusion')}"))
    if row is not None:
        found.append((status_state(row), f"status {row['state']}"))
    if not found:
        return None
    rank = {"failure": 0, "pending": 1, "success": 2}
    return min(found, key=lambda item: rank[item[0]])


def unmet(required: list[tuple[str, int | None]], runs: list[dict[str, Any]], commit: str,
          statuses: list[dict[str, Any]] | None = None) -> list[str]:
    """Required checks whose newest result on `commit` did not succeed.

    A required check is met by a check run from its bound app, or, for a check
    bound to the Actions app, by a commit status from the github-actions bot:
    release-path-review and framework-trusted-verify are statuses, and main's
    rules accept either.
    """
    missing = []
    for context, app_id in required:
        result = newest_result(context, app_id, runs, statuses or [], commit)
        if result is None:
            source = f" from app {app_id}" if app_id is not None else ""
            missing.append(f"{context}: no run{source} on {commit}")
        elif result[0] != "success":
            missing.append(f"{context}: {result[1]}")
    return missing


def pending(required: list[tuple[str, int | None]], runs: list[dict[str, Any]], commit: str,
            statuses: list[dict[str, Any]] | None = None) -> list[str]:
    """Required checks whose newest result on `commit` has not finished.

    The push that lands the reconcile starts main's checks again on the same
    commit. While the newest result of a required check is still running (a
    run not completed, or a `pending` status), the release job waits instead of
    reading it as unmet. A result not yet created is not waited for: the
    merge-queue run on the same commit decides.
    """
    return [context for context, app_id in required
            if (result := newest_result(context, app_id, runs, statuses or [], commit)) is not None
            and result[0] == "pending"]


def version_key(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)", tag)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def remote_release_tags(root: Path, remote: str = "origin") -> list[str]:
    """Every vMAJOR.MINOR.PATCH tag on `remote`; any lookup failure raises."""
    result = subprocess.run(["git", "-C", str(root), "ls-remote", "--exit-code", "--tags", "--refs", remote,
                             "refs/tags/v*"], capture_output=True, text=True, check=False)
    if result.returncode not in (0, 2):
        raise ReleaseRequestError(f"could not list release tags on {remote} (git exit {result.returncode}): "
                                  f"{result.stderr.strip()}")
    return [line.split("refs/tags/", 1)[1] for line in result.stdout.splitlines() if "refs/tags/" in line]


def immutable_release_record(answer: Any, tag: str, now: datetime,
                             repository: str = REPOSITORY) -> dict[str, Any]:
    """The verified record of the live immutable-release answer; raises when releases are not immutable.

    `answer` is GET /repos/{repository}/immutable-releases as the App token
    read it in this run. sdk-release reads it live again when it finalizes.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import verify_immutable_release_preflight as vip  # noqa: PLC0415

    try:
        return vip.verify_live(answer, repository=repository, release_tag=tag, checked_at=now)
    except vip.ImmutableReleasePreflightError as error:
        raise ReleaseRequestError(f"the live immutable-release answer refuses the release: {error}") from error


def read_time(text: str) -> datetime:
    """An RFC 3339 UTC time such as `date -u +%Y-%m-%dT%H:%M:%SZ` prints, no later than now."""
    try:
        when = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ReleaseRequestError(f"not an RFC 3339 UTC time: {text!r}") from error
    if when > datetime.now(timezone.utc):
        raise ReleaseRequestError(f"the immutable-release read time {text} is in the future")
    return when


def remote_tag_exists(root: Path, tag: str, remote: str = "origin") -> bool:
    """Whether `tag` exists on `remote`; any lookup failure raises rather than reading as absent."""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ReleaseRequestError("the tag must be vMAJOR.MINOR.PATCH")
    result = subprocess.run(["git", "-C", str(root), "ls-remote", "--exit-code", "--tags", remote,
                             f"refs/tags/{tag}"], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 2:  # git: the remote answered and has no matching ref
        return False
    raise ReleaseRequestError(f"could not look up {tag} on {remote} (git exit {result.returncode}): "
                              f"{result.stderr.strip()}")


def newest_known(retired_tags: Any, existing_tags: list[str]) -> tuple[int, int, int] | None:
    """The highest version any release tag has used, existing or retired."""
    known = [key for name in [*existing_tags, *retired(retired_tags)] if (key := version_key(name))]
    return max(known) if known else None


def check(*, cmakelists: str, retired_tags: Any, commit: str, tag_exists: bool,
          branch_rules: Any, pages: Any, immutable: Any = None, now: datetime | None = None,
          protection: Any = None, existing_tags: list[str] | None = None, statuses: Any = None) -> str:
    """The tag to create; raises ReleaseRequestError when the project version may not be tagged."""
    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    version = project_version(cmakelists)
    tag = f"v{version}"
    if tag in retired(retired_tags):
        raise ReleaseRequestError(f"{tag} is retired and is never reused")
    if tag_exists:
        raise ReleaseRequestError(f"{tag} already exists; a release tag is never moved")
    newest = newest_known(retired_tags, existing_tags or [])
    if newest and version_key(tag) <= newest:  # type: ignore[operator]
        raise ReleaseRequestError(f"{tag} is not newer than v{'.'.join(map(str, newest))}; "
                                  "a release only moves forward")
    missing = unmet(required_checks(branch_rules, protection), check_runs(pages), commit,
                    commit_statuses([] if statuses is None else statuses))
    if missing:
        raise ReleaseRequestError("required checks have not passed: " + "; ".join(missing))
    immutable_release_record(immutable, tag, now or datetime.now(timezone.utc))
    return tag


def freeze_ruleset(tag: str) -> dict[str, Any]:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ReleaseRequestError("the tag must be vMAJOR.MINOR.PATCH")
    return {
        "name": f"freeze-{tag}",
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/tags/{tag}"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
        ],
    }


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def is_ancestor(root: Path, ancestor: str, commit: str) -> bool:
    """Whether `ancestor` is `commit` or one of its ancestors; a git failure raises."""
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, commit],
                            capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ReleaseRequestError(f"cannot compare {ancestor} with {commit}: {result.stderr.strip()}")
    return result.returncode == 0


def blob_at(root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], capture_output=True, check=False)
    if result.returncode != 0:
        raise ReleaseRequestError(f"{path} is missing at {commit}")
    return result.stdout


def version_at(root: Path, commit: str) -> str | None:
    """The project VERSION CMakeLists.txt declares at `commit`, or None when it declares none readably."""
    result = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{CMAKELISTS}"], capture_output=True,
                            check=False)
    if result.returncode != 0:
        return None
    try:
        return val.project_version(result.stdout.decode("utf-8", "replace"))
    except val.VersionError:
        return None


def version_landing(root: Path, commit: str) -> str:
    """The commit on `commit`'s first-parent chain that landed its project VERSION (version_at_land's rule)."""
    try:
        return val.version_landing(root, commit)
    except val.VersionError as error:
        raise ReleaseRequestError(str(error)) from error


def observatory_cursor(root: Path, commit: str) -> str:
    """The Vellum commit the observatory cursor at `commit` was reconciled through."""
    try:
        cursor = json.loads(blob_at(root, commit, CURSOR), object_pairs_hook=strict_object)
    except ValueError as error:
        raise ReleaseRequestError(f"{CURSOR} at {commit} is not JSON: {error}") from error
    vellum = cursor.get("vellum") if isinstance(cursor, dict) else None
    if (not isinstance(vellum, dict) or cursor.get("state") != "active"
            or vellum.get("repository") != REPOSITORY):
        raise ReleaseRequestError(f"{CURSOR} at {commit} is not an active cursor for {REPOSITORY}")
    scanned = vellum.get("last_scanned_commit")
    if not isinstance(scanned, str) or not SHA.fullmatch(scanned):
        raise ReleaseRequestError(f"{CURSOR} at {commit} names no full Vellum commit")
    return scanned


def observatory_pulp_cursor(root: Path, commit: str) -> str:
    """The Pulp commit the observatory cursor at `commit` names, which the tag's check reads."""
    cursor = json.loads(blob_at(root, commit, CURSOR), object_pairs_hook=strict_object)
    pulp = cursor.get("pulp") if isinstance(cursor, dict) else None
    scanned = pulp.get("last_scanned_commit") if isinstance(pulp, dict) else None
    if not isinstance(scanned, str) or not SHA.fullmatch(scanned):
        raise ReleaseRequestError(f"{CURSOR} at {commit} names no full Pulp commit")
    return scanned


def require_evidence_tail(root: Path, cursor: str, commit: str) -> None:
    """Raise unless every commit after `cursor` up to `commit` is observatory evidence.

    This is the rule the tag's provenance run applies with
    `--vellum-tail-policy reconciled` (observatory.py,
    verify_vellum_observatory_tail), minus its one-time authority-activation
    exception: a linear chain of commits that each modify only the cursor and
    the reports and add only event files, where a merge counts only when its
    tree equals the commit before it (the merge queue landing that chain).
    """
    if cursor == commit or not is_ancestor(root, cursor, commit):
        raise ReleaseRequestError(f"the cursor {cursor} is not a strict ancestor of {commit}")
    previous = cursor
    for entry in git(root, "rev-list", "--reverse", "--topo-order", f"{cursor}..{commit}").splitlines():
        parents = git(root, "rev-list", "--parents", "-n", "1", entry).split()[1:]
        if (len(parents) == 2 and previous in parents
                and git(root, "rev-parse", f"{entry}^{{tree}}") == git(root, "rev-parse", f"{previous}^{{tree}}")):
            previous = entry
            continue
        if parents != [previous]:
            raise ReleaseRequestError(f"the evidence tail is not one linear chain at {entry}")
        fields = git(root, "diff-tree", "--no-commit-id", "-r", "-z", "--no-renames", "--name-status",
                     previous, entry).split("\0")
        changes = list(zip(fields[0::2], fields[1::2]))
        if not changes:
            raise ReleaseRequestError(f"{entry} changes nothing")
        for status, path in changes:
            event = path[len(EVENTS_PREFIX):] if path.startswith(EVENTS_PREFIX) else ""
            if (status == "M" and path in EVIDENCE_MODIFIED) or (
                    status == "A" and event.endswith(".yaml") and "/" not in event):
                continue
            raise ReleaseRequestError(f"{entry} is not observatory evidence: {status} {path}")
        previous = entry


def one_line(text: str) -> str:
    """`text` as one printable line: the workflow logs it as a ::notice:: command, and a
    newline in a path from a commit must not start a workflow command of its own."""
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in text)


@dataclass(frozen=True)
class Selection:
    """What a push to main means for the release: the tag to make, or None and why not."""
    tag: str | None
    reason: str
    version_commit: str | None = None
    cursor: str | None = None


def tag_commits(root: Path, names: list[str]) -> dict[str, str]:
    """The commit each local release tag names; a tag the clone lacks is left out."""
    found = {}
    for name in names:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/tags/{name}^{{commit}}"],
                                capture_output=True, text=True, check=False)
        if result.returncode == 0 and version_key(name):
            found[name] = result.stdout.strip()
    return found


def require_bot_bump(root: Path, landing: str, tags: dict[str, str], retired_tags: Any) -> None:
    """Raise unless `landing` is the version-at-land bot's own bump, exactly as it would write it."""
    try:
        problem = val.verify_bump(root, landing, tags, retired_tags)
    except val.VersionError as error:
        problem = str(error)
    if problem:
        raise ReleaseRequestError(f"the version was not landed by version-at-land: {problem}")


def require_coverage(root: Path, commit: str, tags: dict[str, str], retired_tags: Any, version: str) -> None:
    """Raise unless `version` covers every shipped change a release at `commit` carries."""
    try:
        problem = val.coverage_problem(root, commit, tags, retired_tags, version)
    except val.VersionError as error:
        problem = str(error)
    if problem:
        raise ReleaseRequestError(f"the version is too low for what it would ship: {problem}")


def select(root: Path, commit: str, tag_exists: bool, existing_tags: list[str] | None = None,
           tags: dict[str, str] | None = None) -> Selection:
    """Whether `commit` is the commit to tag for the project version it declares.

    It is when that version's tag does not exist yet, is not retired, and is
    newer than every release tag; the commit that landed the version is the
    version-at-land bot's own bump, re-derived exactly (require_bot_bump); the
    version covers every shipped change through `commit`, including any that
    landed after the bump (require_coverage); the observatory cursor at `commit` is (or
    descends from) the commit that landed the version; and everything after
    that cursor is observatory evidence. Such a commit passes the tag's
    reconciled observatory check, and its content is the content the cursor
    was reconciled through. Every other push is not a release: the version
    bump's merge itself (its tree cannot name it as the cursor), a reconcile
    whose cursor predates the bump, a reconcile landed behind other changes,
    and any push once the tag exists.
    """
    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    try:
        cmakelists = blob_at(root, commit, CMAKELISTS).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseRequestError(f"{CMAKELISTS} at {commit} is not UTF-8: {error}") from error
    tag = f"v{project_version(cmakelists)}"
    if tag_exists:
        return Selection(None, f"{tag} already exists; no version bump is waiting")
    try:
        retired_tags = json.loads(blob_at(root, commit, RETIRED_TAGS), object_pairs_hook=strict_object)
    except ValueError as error:
        raise ReleaseRequestError(f"{RETIRED_TAGS} at {commit} is not JSON: {error}") from error
    if tag in retired(retired_tags):
        return Selection(None, f"{tag} is retired and is never reused; main must declare a newer version")
    newest = newest_known(retired_tags, existing_tags or [])
    if newest and version_key(tag) <= newest:  # type: ignore[operator]
        return Selection(None, f"{tag} is not newer than v{'.'.join(map(str, newest))}; no version bump is "
                               "waiting")
    landed = version_landing(root, commit)
    known = tags if tags is not None else tag_commits(root, existing_tags or [])
    try:
        require_bot_bump(root, landed, known, retired_tags)
        require_coverage(root, commit, known, retired_tags, tag[1:])
    except ReleaseRequestError as error:
        return Selection(None, f"{tag} is not a release: {error}", landed)
    cursor = observatory_cursor(root, commit)
    if not is_ancestor(root, landed, cursor):
        return Selection(None, f"the observatory cursor {cursor} does not yet contain {landed}, which "
                               f"landed version {tag[1:]}; a later reconcile tags it", landed, cursor)
    try:
        require_evidence_tail(root, cursor, commit)
    except ReleaseRequestError as error:
        return Selection(None, f"{commit} is not an observatory-reconciled commit ({error}); a later "
                               f"reconcile tags {tag}", landed, cursor)
    return Selection(tag, f"{commit} reconciles the observatory through {cursor}, which contains {landed}, "
                          f"the commit that landed version {tag[1:]}", landed, cursor)


def attestation(root: Path, commit: str, attested_at: str, tag: str) -> dict[str, Any]:
    """The promotion attestation for `commit`, validated as the release-readiness gate will.

    With a parity completion record at `commit` it is the authority promotion
    attestation that binds it. Without one it is the mirror-promotion
    attestation (owner decision 2026-10-31, third sitting), which binds the
    newest mirror export receipt instead and states that parity is not yet
    proven; the verifier accepts that shape only for the Phase 4 promotion tag.
    """
    sys.path.insert(0, str(ROOT / "tools/provenance"))
    import verify_authority_expansion as vae  # noqa: PLC0415

    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    tree = git(root, "rev-parse", f"{commit}^{{tree}}")
    try:
        acknowledgement = (root / EXPANSION / "exact-boundary-acknowledgement-1.json").read_bytes()
    except OSError as error:
        raise ReleaseRequestError(f"a promotion record is missing at {commit}: {error}") from error
    amendment = json.loads((root / EXPANSION / "exact-boundary-amendment-1.json").read_text(encoding="utf-8"))
    data: dict[str, Any] = {
        "schema_version": 1,
        "state": "attested",
        "attested_at": attested_at,
        "attested_by": vae.RELEASE_PIPELINE_ATTESTER,
        "authority_repository": "Generous-Corp/vellum",
        "delivery_repository": "danielraffel/vellum",
        "promotion_mode": "repository-transfer",
        "authority_commit": commit,
        "delivery_commit": commit,
        "authority_tree": tree,
        "delivery_tree": tree,
        "exact_boundary_amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
        "exact_boundary_amendment_sha256": vae.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
        "exact_boundary_acknowledgement_id": "full-design-import-render-v1-exact-boundary-acknowledgement-1",
        "exact_boundary_acknowledgement_sha256": hashlib.sha256(acknowledgement).hexdigest(),
    }
    if git_exists(root, commit, vae.PARITY_COMPLETION_PATH.as_posix()):
        completion = git_show(root, commit, vae.PARITY_COMPLETION_PATH.as_posix())
        data.update({
            "kind": vae.AUTHORITY_PROMOTION_KIND,
            "attestation_id": "full-design-import-render-v1-authority-promotion-1",
            "parity_completion_id": "full-design-import-render-v1-parity-completion-1",
            "parity_completion_sha256": hashlib.sha256(completion).hexdigest(),
            "parity_release_source_commit": commit,
        })
        errors = (vae.validate_authority_promotion_attestation(data, amendment, delivery_retired=True)
                  + vae.validate_promotion_completion_digest(root, data, commit))
    else:
        if tag not in vae.MIRROR_PROMOTION_RELEASE_TAGS:
            raise ReleaseRequestError(
                f"{tag} needs a parity completion record at {commit}; only "
                f"{', '.join(sorted(vae.MIRROR_PROMOTION_RELEASE_TAGS))} may ship as a mirror promotion")
        try:
            index = json.loads(git_show(root, commit, vae.MIRROR_RECEIPT_INDEX_PATH))
            receipt = index["receipts"][-1]
            receipt_bytes = git_show(root, commit, receipt)
        except (subprocess.CalledProcessError, ValueError, KeyError, IndexError, TypeError) as error:
            raise ReleaseRequestError(f"no mirror export receipt is recorded at {commit}: {error}") from error
        data.update({
            "kind": vae.MIRROR_PROMOTION_KIND,
            "attestation_id": vae.MIRROR_PROMOTION_ATTESTATION_ID,
            "mirror_receipt": receipt,
            "mirror_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "release_source_commit": commit,
            "parity": vae.MIRROR_PROMOTION_PARITY,
        })
        errors = (vae.validate_mirror_promotion_attestation(data, amendment, delivery_retired=True)
                  + vae.validate_mirror_promotion_release(root, data, commit, tag))
    errors += vae.validate_promotion_acknowledgement_digest(root, data)
    if errors:
        raise ReleaseRequestError("the promotion attestation would not verify: " + "; ".join(errors))
    return data


DRY_RUN_SCHEMA = "vellum.release-dry-run-attestation.v1"
DRY_RUN_TAG = re.compile(r"dry-run/(v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))/[1-9][0-9]*-[1-9][0-9]*")


def dry_run_attestation(inner: dict[str, Any], dry_run_tag: str, identity: str) -> dict[str, Any]:
    """The signed message of a release dry-run tag: the rehearsed release's attestation, wrapped.

    The wrapper has its own schema and names the dry-run tag, so nothing that
    reads a signed tag message can take a dry run for the release it
    rehearses: the release-readiness verifier refuses the wrapper as a
    promotion attestation, and provenance.yml unwraps it only on the dry-run
    tag it names.
    """
    match = DRY_RUN_TAG.fullmatch(dry_run_tag)
    if match is None or match.group(1) != identity:
        raise ReleaseRequestError(f"{dry_run_tag!r} is not a release dry-run tag for {identity}")
    return {"schema": DRY_RUN_SCHEMA, "dry_run": True, "dry_run_tag": dry_run_tag, "release_identity": identity,
            "attestation": inner}


def git_exists(root: Path, commit: str, path: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}:{path}"],
                          capture_output=True).returncode == 0


def git_show(root: Path, commit: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], capture_output=True,
                          check=True).stdout


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("check")
    one.add_argument("--cmakelists", type=Path, default=ROOT / CMAKELISTS)
    one.add_argument("--commit", required=True)
    one.add_argument("--remote", default="origin",
                     help="looked up for the tag; only git's no-match answer reads as absent")
    one.add_argument("--branch-rules", type=Path, required=True)
    one.add_argument("--check-runs", type=Path, required=True)
    one.add_argument("--statuses", type=Path, required=True,
                     help="GET /commits/{sha}/statuses, paginated and slurped")
    one.add_argument("--protection", type=Path, required=True,
                     help="GET /branches/main/protection/required_status_checks")
    one.add_argument("--immutable-releases", type=Path, required=True,
                     help="GET /repos/{repo}/immutable-releases, read live in this run")
    one.add_argument("--immutable-read-at", required=True,
                     help="when that answer was read, as an RFC 3339 UTC time")
    pick = sub.add_parser("select", help="print `tag=` (empty when this push is not a release) and its reason")
    pick.add_argument("--commit", required=True)
    pick.add_argument("--remote", default="origin")
    wait = sub.add_parser("pending", help="print required checks still running on the commit, one per line")
    wait.add_argument("--commit", required=True)
    wait.add_argument("--branch-rules", type=Path, required=True)
    wait.add_argument("--check-runs", type=Path, required=True)
    wait.add_argument("--statuses", type=Path, required=True)
    wait.add_argument("--protection", type=Path, required=True)
    attest = sub.add_parser("attestation")
    attest.add_argument("--commit", required=True)
    attest.add_argument("--attested-at", required=True)
    attest.add_argument("--tag", required=True)
    attest.add_argument("--dry-run-tag", help="wrap the attestation of --tag for this release dry-run tag")
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            print(json.dumps(freeze_ruleset(args.tag), indent=2, sort_keys=True))
            return 0
        if args.command == "attestation":
            data = attestation(ROOT, args.commit, args.attested_at, args.tag)
            if args.dry_run_tag is not None:
                data = dry_run_attestation(data, args.dry_run_tag, args.tag)
            print(json.dumps(data, indent=2, sort_keys=True))
            return 0
        if args.command == "select":
            if not SHA.fullmatch(args.commit):
                raise ReleaseRequestError("the commit must be a full SHA")
            version = project_version(blob_at(ROOT, args.commit, CMAKELISTS).decode("utf-8", "replace"))
            remote_tags = val.remote_release_tags(ROOT, args.remote)
            chosen = select(ROOT, args.commit, remote_tag_exists(ROOT, f"v{version}", args.remote),
                            list(remote_tags), remote_tags)
            print(f"tag={chosen.tag or ''}")
            print(f"version-commit={chosen.version_commit or ''}")
            print(f"cursor={chosen.cursor or ''}")
            print(f"pulp-cursor={observatory_pulp_cursor(ROOT, args.commit) if chosen.tag else ''}")
            print(one_line(chosen.reason), file=sys.stderr)
            return 0
        if args.command == "pending":
            running = pending(required_checks(load(args.branch_rules), load(args.protection)),
                              check_runs(load(args.check_runs)), args.commit,
                              commit_statuses(load(args.statuses)))
            print("\n".join(running))
            return 0
        cmakelists = args.cmakelists.read_text(encoding="utf-8")
        exists = remote_tag_exists(ROOT, f"v{project_version(cmakelists)}", args.remote)
        retired_tags = load(ROOT / RETIRED_TAGS)
        if not SHA.fullmatch(args.commit):
            raise ReleaseRequestError("the commit must be a full SHA")
        if version_at(ROOT, args.commit) != project_version(cmakelists):
            raise ReleaseRequestError(f"{args.cmakelists} does not declare the version at {args.commit}")
        live_tags = val.remote_release_tags(ROOT, args.remote)
        require_bot_bump(ROOT, version_landing(ROOT, args.commit), live_tags, retired_tags)
        require_coverage(ROOT, args.commit, live_tags, retired_tags, project_version(cmakelists))
        tag = check(cmakelists=cmakelists, retired_tags=retired_tags, commit=args.commit,
                    tag_exists=exists, branch_rules=load(args.branch_rules),
                    pages=load(args.check_runs), statuses=load(args.statuses), immutable=load(args.immutable_releases),
                    protection=load(args.protection),
                    existing_tags=remote_release_tags(ROOT, args.remote), now=read_time(args.immutable_read_at))
    except (OSError, ValueError, subprocess.CalledProcessError, ReleaseRequestError, val.VersionError) as error:
        print(one_line(f"release refused: {error}"), file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
