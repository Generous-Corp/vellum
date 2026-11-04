#!/usr/bin/env python3
"""Find a version that should be released by now and is not.

release-cadence-check.yml runs this every hour. It judges the outcome, not the
cause: whatever stopped a release (a failed workflow, a lost run, a reconcile
that never landed, a tag whose pipeline failed as v0.1.9's did), the symptom is
one of these, and each is a finding once it is older than the grace period:

  untagged   main declares a version newer than every release tag, and the
             commit that landed it is older than the grace period.
  unpublished
             the newest unretired release tag has no published release, and
             the tag is older than the grace period.
  retired    main declares a version whose tag is retired; nothing can be
             released until main declares a newer one.
  behind     main declares a version older than the newest release tag.
  dry-run-leftover
             a release dry-run tag older than the grace period, or any
             published release on a dry-run tag; sdk-release's clean-dry-run
             job should have deleted it.

It reads the local clone (with its tags) and the releases the workflow
fetched, and needs no token. It writes the findings as JSON and as the
tracking issue's body, and prints `count=`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_request as rr  # noqa: E402

GRACE_HOURS = 6


def committed_at(root: Path, ref: str) -> datetime:
    """The committer time of a commit, or the tagger time of an annotated tag."""
    stamp = rr.git(root, "for-each-ref", "--format=%(taggerdate:unix)%(committerdate:unix)", f"refs/tags/{ref}") \
        if not rr.SHA.fullmatch(ref) else ""
    if not stamp:
        stamp = rr.git(root, "show", "-s", "--format=%ct", ref)
    return datetime.fromtimestamp(int(stamp), timezone.utc)


def local_release_tags(root: Path) -> list[str]:
    return [name for name in rr.git(root, "tag", "--list", "v*").splitlines() if rr.version_key(name)]


def published(releases: Any) -> set[str]:
    """Tags with a published, non-draft release, from `gh api --paginate --slurp`."""
    if not isinstance(releases, list):
        raise rr.ReleaseRequestError("releases must be an array of pages")
    rows = [row for page in releases for row in (page if isinstance(page, list) else [page])]
    return {row["tag_name"] for row in rows if isinstance(row, dict) and isinstance(row.get("tag_name"), str)
            and row.get("draft") is False and row.get("published_at")}


def findings(root: Path, commit: str, releases: Any, now: datetime,
             grace: timedelta = timedelta(hours=GRACE_HOURS)) -> list[dict[str, str]]:
    retired_tags = json.loads(rr.blob_at(root, commit, rr.RETIRED_TAGS))
    retired = rr.retired(retired_tags)
    tags = local_release_tags(root)
    version = rr.project_version(rr.blob_at(root, commit, rr.CMAKELISTS).decode("utf-8"))
    tag = f"v{version}"
    newest = rr.newest_known(retired_tags, tags)
    found = []
    if tag in retired:
        found.append({"kind": "retired", "tag": tag,
                      "detail": f"main declares {version}, whose tag is retired; main must declare a newer version"})
    elif newest and rr.version_key(tag) > newest:  # type: ignore[operator]
        landed = rr.version_landing(root, commit)
        age = now - committed_at(root, landed)
        if age > grace:
            found.append({"kind": "untagged", "tag": tag,
                          "detail": f"{landed[:12]} landed version {version} {age_text(age)} ago, and {tag} "
                                    "does not exist"})
    elif newest and rr.version_key(tag) < newest and tag not in tags:  # type: ignore[operator]
        found.append({"kind": "behind", "tag": tag,
                      "detail": f"main declares {version}, older than v{'.'.join(map(str, newest))}"})
    for name in rr.git(root, "tag", "--list", "dry-run/*").splitlines():
        age = now - committed_at(root, name)
        if age > grace:
            found.append({"kind": "dry-run-leftover", "tag": name,
                          "detail": f"{name} is {age_text(age)} old; delete it and any draft release on it"})
    published_tags = published(releases)
    for name in sorted(tag for tag in published_tags if tag.startswith("dry-run/")):
        found.append({"kind": "dry-run-leftover", "tag": name,
                      "detail": f"a release on the dry-run tag {name} is published; a dry run must never publish"})
    live = sorted((key, name) for name in tags if name not in retired and (key := rr.version_key(name)))
    if live:
        latest = live[-1][1]
        age = now - committed_at(root, latest)
        if latest not in published_tags and age > grace:
            found.append({"kind": "unpublished", "tag": latest,
                          "detail": f"{latest} was tagged {age_text(age)} ago and has no published release"})
    return found


def age_text(age: timedelta) -> str:
    hours = age.total_seconds() / 3600
    return f"{hours:.1f} hours" if hours < 48 else f"{hours / 24:.1f} days"


def body(found: list[dict[str, str]], now: datetime) -> str:
    lines = [f"_Written by `.github/workflows/release-cadence-check.yml` at {now:%Y-%m-%d %H:%M} UTC._", "",
             "A Vellum version should be released by now and is not:", ""]
    lines += [f"- **{row['kind']}** `{row['tag']}`: {row['detail']}" for row in found]
    lines += ["", "Where to look:",
              "- `untagged`: the Release tag run on the newest observatory reconcile (its select job says why "
              "it did not tag), and the observatory receiver.",
              "- `unpublished`: the tag's provenance, Locked macOS GPU proof and Finalize immutable SDK release "
              "runs. A tag whose pipeline cannot pass is retired in `provenance/retired-release-tags.json` and "
              "the next version is released instead; a frozen tag is never moved.",
              "- `retired` or `behind`: main's `project(VERSION)`; the next shipped change bumps it.",
              "- `dry-run-leftover`: the dry run's sdk-release `clean-dry-run` job; delete the `dry-run/` tag and "
              "its draft by hand.",
              "", "This issue closes on the first check that finds nothing."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=rr.ROOT)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--releases", type=Path, required=True, help="gh api --paginate --slurp .../releases")
    parser.add_argument("--now", help="RFC 3339 UTC time (default: now)")
    parser.add_argument("--grace-hours", type=float, default=GRACE_HOURS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--body", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not rr.SHA.fullmatch(args.commit):
            raise rr.ReleaseRequestError("the commit must be a full SHA")
        now = (datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               if args.now else datetime.now(timezone.utc))
        found = findings(args.root, args.commit, json.loads(args.releases.read_text(encoding="utf-8")), now,
                         timedelta(hours=args.grace_hours))
    except (OSError, ValueError, subprocess.CalledProcessError, rr.ReleaseRequestError) as error:
        print(f"release cadence check failed: {error}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    args.body.write_text(body(found, now), encoding="utf-8")
    for row in found:
        print(f"{row['kind']} {row['tag']}: {row['detail']}", file=sys.stderr)
    print(f"count={len(found)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
