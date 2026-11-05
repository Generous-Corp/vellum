#!/usr/bin/env python3
"""Name, place and recognise a release dry run.

A dry run sends a signed throwaway tag through the whole release path (the
tag's provenance run, gpu-macos with its sterile consumer, and sdk-release's
finalizer) and stops before publication; the finalizer builds a real draft,
and sdk-release's cleanup job deletes the draft and the tag. Its tag lives in a
namespace no release can use:

    dry-run/v<MAJOR.MINOR.PATCH>/<run id>-<run attempt>

The version is main's project version, so every name, archive and attestation
is the one the next release of that version would carry. The release identity
`v<version>` is what readiness and attestation checks are judged against; the
`dry-run/` tag is what git and the GitHub API are asked about.

  plan      the commit to tag (the newest observatory-reconciled commit on
            main's first-parent history, which a tag's provenance run can pass)
            and the dry-run tag's name.
  identity  for a tag name: `mode=` release or dry-run, `version=`, `identity=`.
            Any other name is refused.
  gate      whether this run of release-tag.yml starts a dry run: always on
            the weekly schedule and by hand; on a push to main only when it is
            an observatory reconcile whose newly reconciled range changes a
            release-path file (RELEASE_PATHS), so the dry run tags a commit
            that contains the change; and never while a release tag's GPU
            proof or provenance run, or any SDK finalizer run, is waiting,
            queued or in progress.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_request as rr  # noqa: E402
import version_at_land as val  # noqa: E402

VERSION = r"(?P<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
RELEASE_TAG = re.compile(rf"v{VERSION}")
DRY_RUN_TAG = re.compile(rf"dry-run/v{VERSION}/[1-9][0-9]*-[1-9][0-9]*")
# How far back on main's first-parent history a reconciled commit is looked for.
SEARCH_LIMIT = 200
# A push to main that changes one of these starts a dry run by itself.
RELEASE_PATHS = (
    ".github/workflows/gpu-macos.yml",
    ".github/workflows/provenance.yml",
    ".github/workflows/release-tag.yml",
    ".github/workflows/sdk-release.yml",
    "scripts/check_release_policy.py",
    "scripts/collect_release_tag_rulesets.py",
    "scripts/pretag_checks.py",
    "scripts/release_dry_run.py",
    "scripts/release_request.py",
    "scripts/select_exact_provenance_run.py",
    "scripts/select_release.py",
    "scripts/verify_exact_provenance_artifact.py",
    "scripts/verify_immutable_release_preflight.py",
    "scripts/verify_release_assets.py",
    "scripts/verify_release_tag.py",
    "scripts/verify_release_tag_ruleset.py",
    "scripts/version_at_land.py",
)
# Run states GitHub reports before a run completes; dry-run-gate reads each.
ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")
# Runs of these, on a release tag (or any finalizer run, whose branch is main),
# mean a real release may be in flight; a dry run then waits for next time.
RELEASE_PIPELINE = ("Locked macOS GPU proof", "Provenance and forbidden dependencies")
FINALIZER = "Finalize immutable SDK release"
ZERO = "0" * 40


class DryRunError(RuntimeError):
    pass


def identity(tag: str) -> tuple[str, str]:
    """(mode, version) for a release tag or a dry-run tag; any other name raises."""
    if (match := RELEASE_TAG.fullmatch(tag)):
        return "release", match["version"]
    if (match := DRY_RUN_TAG.fullmatch(tag)):
        return "dry-run", match["version"]
    raise DryRunError(f"{tag!r} is neither a release tag nor a release dry-run tag")


def tag_name(version: str, run_id: str, attempt: str) -> str:
    name = f"dry-run/v{version}/{run_id}-{attempt}"
    if not DRY_RUN_TAG.fullmatch(name):
        raise DryRunError(f"{name!r} is not a well-formed dry-run tag")
    return name


def reconciled_target(root: Path, head: str) -> str:
    """The newest commit on `head`'s first-parent history whose observatory tail is evidence only."""
    chain = rr.git(root, "rev-list", "--first-parent", f"--max-count={SEARCH_LIMIT}", head).split()
    for commit in chain:
        try:
            cursor = rr.observatory_cursor(root, commit)
            rr.require_evidence_tail(root, cursor, commit)
        except rr.ReleaseRequestError:
            continue
        return commit
    raise DryRunError(f"no observatory-reconciled commit in the last {SEARCH_LIMIT} first-parent commits of {head}")


def busy(runs: object) -> list[str]:
    """Queued or running runs that may belong to a real release, from `gh api --paginate --slurp` pages."""
    rows = [row for page in runs if isinstance(page, dict) for row in page.get("workflow_runs", [])
            if isinstance(row, dict)] if isinstance(runs, list) else None
    if rows is None:
        raise DryRunError("the runs listing must be an array of pages")
    found = []
    for row in rows:
        # Anything not completed may still become part of a release.
        if row.get("status") == "completed":
            continue
        name, branch = row.get("name"), str(row.get("head_branch") or "")
        if (name in RELEASE_PIPELINE and RELEASE_TAG.fullmatch(branch)) or name == FINALIZER:
            found.append(f"{name} on {branch} (run {row.get('id')})")
    return found


def gate(root: Path, event: str, before: str, after: str, runs: object) -> tuple[bool, str]:
    """Whether this release-tag.yml run starts a dry run, and why."""
    in_flight = busy(runs)
    if in_flight:
        return False, "a release run may be in flight: " + "; ".join(in_flight)
    if event in ("schedule", "workflow_dispatch"):
        return True, f"{event} runs a dry run"
    if event != "push":
        return False, f"{event} never runs a dry run"
    if not rr.SHA.fullmatch(after):
        raise DryRunError("the pushed commit must be a full SHA")
    previous = before if rr.SHA.fullmatch(before) and before != ZERO else f"{after}^1"
    # A force push can leave `before` out of the clone: no dry run, said plainly.
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{previous}^{{commit}}"],
                      capture_output=True).returncode != 0:
        return False, f"the push's previous commit {previous[:12]} cannot be read; no dry run"
    try:
        old, new = rr.observatory_cursor(root, previous), rr.observatory_cursor(root, after)
    except rr.ReleaseRequestError as error:
        return False, f"the push is not an observatory reconcile with a readable cursor ({error})"
    except subprocess.CalledProcessError as error:
        return False, f"the cursor cannot be read ({(error.stderr or '').strip()[:200]}); no dry run"
    if old == new:
        return False, "the push reconciles no new Vellum commit"
    # The two trees are compared: a release file changed and then changed back
    # inside the range does not count, which is accepted.
    try:
        changed = rr.git(root, "diff", "--name-only", "--no-renames", old, new).split()
    except subprocess.CalledProcessError as error:
        return False, f"the reconciled range {old[:12]}..{new[:12]} cannot be read ({error.stderr.strip()[:200]})"
    touched = sorted(set(changed) & set(RELEASE_PATHS))
    if not touched:
        return False, f"the reconciled range {old[:12]}..{new[:12]} changes no release-path file"
    return True, f"the reconciled range {old[:12]}..{new[:12]} changes " + ", ".join(touched)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("plan")
    one.add_argument("--head", required=True)
    one.add_argument("--run-id", required=True)
    one.add_argument("--attempt", required=True)
    two = sub.add_parser("identity")
    two.add_argument("--tag", required=True)
    three = sub.add_parser("gate")
    three.add_argument("--event", required=True)
    three.add_argument("--before", default="")
    three.add_argument("--after", required=True)
    three.add_argument("--runs", type=Path, required=True, help="queued and in-progress runs, gh api --slurp pages")
    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            import json  # noqa: PLC0415
            run, reason = gate(ROOT, args.event, args.before, args.after,
                               json.loads(args.runs.read_text(encoding="utf-8")))
            print(f"run={'true' if run else 'false'}")
            print(reason, file=sys.stderr)
            return 0
        if args.command == "identity":
            mode, version = identity(args.tag)
            print(f"mode={mode}\nversion={version}\nidentity=v{version}")
            return 0
        if not rr.SHA.fullmatch(args.head):
            raise DryRunError("the head must be a full SHA")
        target = reconciled_target(ROOT, args.head)
        version = val.project_version(rr.git(ROOT, "show", f"{target}:{rr.CMAKELISTS}"))
        name = tag_name(version, args.run_id, args.attempt)
        print(f"commit={target}\ntag={name}\nidentity=v{version}")
    except (DryRunError, rr.ReleaseRequestError, val.VersionError, OSError, ValueError) as error:
        print(f"release dry run refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
