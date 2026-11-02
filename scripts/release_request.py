#!/usr/bin/env python3
"""Decide whether a merged release request may be tagged, and describe its freeze.

A release starts as a reviewed pull request that changes
provenance/release-request.json. The commit that lands it on main is not
tagged: a tag's provenance run requires every commit after the Pulp
observatory's Vellum cursor to be observatory evidence, and a commit's tree
can never name that same commit as its cursor. The observatory receiver then
lands a reconcile commit whose cursor covers the request, and release-tag.yml
tags that commit. This script makes the decision from files the workflow has
already fetched and the local clone, so it needs no token:

  select  whether the pushed commit is the observatory reconcile that covers
          an untagged release request, and so the commit to tag.
  check   the request names a new, unretired version that matches the
          project version, the tag does not exist yet, every check the main
          branch rules require concluded success on the commit, and the live
          answer of GET /repos/{repo}/immutable-releases says immutable
          releases are enabled.
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
REQUEST_SCHEMA = "vellum.release-request.v1"
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
PROJECT_VERSION = re.compile(r"project\(\s*vellum\b[^)]*\bVERSION\s+([0-9.]+)", re.IGNORECASE | re.DOTALL)


REPOSITORY = "Generous-Corp/vellum"
REQUEST = "provenance/release-request.json"
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


def request_version(request: Any) -> str:
    if not isinstance(request, dict) or set(request) != {"schema", "version"}:
        raise ReleaseRequestError("the request must hold exactly `schema` and `version`")
    if request["schema"] != REQUEST_SCHEMA:
        raise ReleaseRequestError(f"the request schema must be {REQUEST_SCHEMA}")
    version = request["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseRequestError("the requested version must be MAJOR.MINOR.PATCH")
    return version


def project_version(cmakelists: str) -> str:
    found = PROJECT_VERSION.search(cmakelists)
    if not found:
        raise ReleaseRequestError("CMakeLists.txt declares no project VERSION")
    return found[1]


def retired(retired_tags: Any) -> set[str]:
    rows = retired_tags.get("tags") if isinstance(retired_tags, dict) else None
    if not isinstance(rows, list):
        raise ReleaseRequestError("the retired-tags file must hold a `tags` list")
    return {row["tag"] for row in rows if isinstance(row, dict) and isinstance(row.get("tag"), str)}


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
        app_id = app if isinstance(app, int) and app > 0 else None
        if context in found and found[context] not in (None, app_id) and app_id is not None:
            raise ReleaseRequestError(f"required check {context} is bound to two different apps")
        if found.get(context) is None:
            found[context] = app_id

    for rule in branch_rules:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            for row in (rule.get("parameters") or {}).get("required_status_checks", []):
                if isinstance(row, dict):
                    add(row.get("context"), row.get("integration_id"))
    if protection is not None:
        if not isinstance(protection, dict):
            raise ReleaseRequestError("branch protection required checks must be an object")
        checks = protection.get("checks")
        if isinstance(checks, list) and checks:
            for row in checks:
                if isinstance(row, dict):
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


def unmet(required: list[tuple[str, int | None]], runs: list[dict[str, Any]], commit: str) -> list[str]:
    """Required checks whose newest run on `commit`, from the bound app, did not conclude success."""
    missing = []
    for context, app_id in required:
        run = newest_run(context, app_id, runs, commit)
        if run is None:
            source = f" from app {app_id}" if app_id is not None else ""
            missing.append(f"{context}: no run{source} on {commit}")
        elif run.get("status") != "completed" or run.get("conclusion") != "success":
            missing.append(f"{context}: {run.get('status')}/{run.get('conclusion')}")
    return missing


def pending(required: list[tuple[str, int | None]], runs: list[dict[str, Any]], commit: str) -> list[str]:
    """Required checks whose newest run on `commit` has not finished.

    The push that lands a request starts main's checks again on the same
    commit. While the newest run of a required check is still running, the
    release job waits instead of reading it as unmet. A run not yet created
    is not waited for: the merge-queue run on the same commit decides.
    """
    return [context for context, app_id in required
            if (run := newest_run(context, app_id, runs, commit)) is not None
            and run.get("status") != "completed"]


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


def check(*, request: Any, cmakelists: str, retired_tags: Any, commit: str, tag_exists: bool,
          branch_rules: Any, pages: Any, immutable: Any = None, now: datetime | None = None,
          protection: Any = None, existing_tags: list[str] | None = None) -> str:
    """The tag to create; raises ReleaseRequestError when the request may not be tagged."""
    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    version = request_version(request)
    tag = f"v{version}"
    if project_version(cmakelists) != version:
        raise ReleaseRequestError(f"the request names {version}, but CMakeLists.txt declares {project_version(cmakelists)}")
    if tag in retired(retired_tags):
        raise ReleaseRequestError(f"{tag} is retired and is never reused")
    if tag_exists:
        raise ReleaseRequestError(f"{tag} already exists; a release tag is never moved")
    known = [key for name in [*(existing_tags or []), *retired(retired_tags)] if (key := version_key(name))]
    if known and version_key(tag) <= max(known):  # type: ignore[operator]
        newest = "v" + ".".join(map(str, max(known)))
        raise ReleaseRequestError(f"{tag} is not newer than {newest}; a release only moves forward")
    missing = unmet(required_checks(branch_rules, protection), check_runs(pages), commit)
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


def request_landing(root: Path, commit: str) -> str:
    """The commit on `commit`'s first-parent chain that landed its release request.

    That is the newest first-parent commit whose request file differs from its
    first parent's. A request reaches main through the merge queue, so on main
    this is the queue's merge commit, the one the release used to tag.
    """
    chain = git(root, "rev-list", "--first-parent", commit).splitlines()
    if not chain:
        raise ReleaseRequestError(f"{commit} has no history")
    query = "".join(f"{entry}:{REQUEST}\n{entry}^1:{REQUEST}\n" for entry in chain)
    answer = subprocess.run(["git", "-C", str(root), "cat-file", "--batch-check=%(objectname)"],
                            input=query, capture_output=True, text=True, check=True).stdout.splitlines()
    if len(answer) != 2 * len(chain):
        raise ReleaseRequestError("git answered a different number of objects than asked")
    for index, entry in enumerate(chain):
        mine, parent = answer[2 * index], answer[2 * index + 1]
        if mine.endswith(" missing"):
            break
        if mine != parent:
            return entry
    raise ReleaseRequestError(f"no commit on the first-parent history of {commit} landed {REQUEST}")


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


@dataclass(frozen=True)
class Selection:
    """What a push to main means for the release: the tag to make, or None and why not."""
    tag: str | None
    reason: str
    request_commit: str | None = None
    cursor: str | None = None


def select(root: Path, commit: str, tag_exists: bool) -> Selection:
    """Whether `commit` is the commit to tag for the release request it carries.

    It is when the request's tag does not exist yet, the observatory cursor at
    `commit` is (or descends from) the commit that landed the request, and
    everything after that cursor is observatory evidence. Such a commit passes
    the tag's reconciled observatory check, and its content is the content the
    cursor was reconciled through. Every other push is not a release: the
    request merge itself (its tree cannot name it as the cursor), a reconcile
    whose cursor predates the request, a reconcile landed behind other changes,
    and any push once the tag exists.
    """
    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    try:
        request = json.loads(blob_at(root, commit, REQUEST), object_pairs_hook=strict_object)
    except ValueError as error:
        raise ReleaseRequestError(f"{REQUEST} at {commit} is not JSON: {error}") from error
    tag = f"v{request_version(request)}"
    if tag_exists:
        return Selection(None, f"{tag} already exists; no release request is waiting")
    landed = request_landing(root, commit)
    cursor = observatory_cursor(root, commit)
    if not is_ancestor(root, landed, cursor):
        return Selection(None, f"the observatory cursor {cursor} does not yet contain {landed}, which "
                               f"landed the {tag} request; a later reconcile tags it", landed, cursor)
    try:
        require_evidence_tail(root, cursor, commit)
    except ReleaseRequestError as error:
        return Selection(None, f"{commit} is not an observatory-reconciled commit ({error}); a later "
                               f"reconcile tags {tag}", landed, cursor)
    return Selection(tag, f"{commit} reconciles the observatory through {cursor}, which contains {landed}, "
                          f"the commit that landed the {tag} request", landed, cursor)


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
    one.add_argument("--request", type=Path, default=ROOT / "provenance/release-request.json")
    one.add_argument("--cmakelists", type=Path, default=ROOT / "CMakeLists.txt")
    one.add_argument("--commit", required=True)
    one.add_argument("--remote", default="origin",
                     help="looked up for the tag; only git's no-match answer reads as absent")
    one.add_argument("--branch-rules", type=Path, required=True)
    one.add_argument("--check-runs", type=Path, required=True)
    one.add_argument("--protection", type=Path, required=True,
                     help="GET /branches/main/protection/required_status_checks")
    one.add_argument("--immutable-releases", type=Path, required=True,
                     help="GET /repos/{repo}/immutable-releases, read live in this run")
    pick = sub.add_parser("select", help="print `tag=` (empty when this push is not a release) and its reason")
    pick.add_argument("--commit", required=True)
    pick.add_argument("--remote", default="origin")
    wait = sub.add_parser("pending", help="print required checks still running on the commit, one per line")
    wait.add_argument("--commit", required=True)
    wait.add_argument("--branch-rules", type=Path, required=True)
    wait.add_argument("--check-runs", type=Path, required=True)
    wait.add_argument("--protection", type=Path, required=True)
    attest = sub.add_parser("attestation")
    attest.add_argument("--commit", required=True)
    attest.add_argument("--attested-at", required=True)
    attest.add_argument("--tag", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            print(json.dumps(freeze_ruleset(args.tag), indent=2, sort_keys=True))
            return 0
        if args.command == "attestation":
            print(json.dumps(attestation(ROOT, args.commit, args.attested_at, args.tag), indent=2, sort_keys=True))
            return 0
        if args.command == "select":
            request = json.loads(blob_at(ROOT, args.commit, REQUEST), object_pairs_hook=strict_object)
            chosen = select(ROOT, args.commit,
                            remote_tag_exists(ROOT, f"v{request_version(request)}", args.remote))
            print(f"tag={chosen.tag or ''}")
            print(f"request-commit={chosen.request_commit or ''}")
            print(f"cursor={chosen.cursor or ''}")
            print(chosen.reason, file=sys.stderr)
            return 0
        if args.command == "pending":
            running = pending(required_checks(load(args.branch_rules), load(args.protection)),
                              check_runs(load(args.check_runs)), args.commit)
            print("\n".join(running))
            return 0
        request = load(args.request)
        exists = remote_tag_exists(ROOT, f"v{request_version(request)}", args.remote)
        tag = check(request=request, cmakelists=args.cmakelists.read_text(encoding="utf-8"),
                    retired_tags=load(ROOT / "provenance/retired-release-tags.json"), commit=args.commit,
                    tag_exists=exists, branch_rules=load(args.branch_rules),
                    pages=load(args.check_runs), immutable=load(args.immutable_releases),
                    protection=load(args.protection),
                    existing_tags=remote_release_tags(ROOT, args.remote))
    except (OSError, ValueError, ReleaseRequestError) as error:
        print(f"release request refused: {error}", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
