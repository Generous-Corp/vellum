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
"""

from __future__ import annotations

import argparse
import re
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("plan")
    one.add_argument("--head", required=True)
    one.add_argument("--run-id", required=True)
    one.add_argument("--attempt", required=True)
    two = sub.add_parser("identity")
    two.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
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
    except (DryRunError, rr.ReleaseRequestError, val.VersionError) as error:
        print(f"release dry run refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
