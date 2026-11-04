#!/usr/bin/env python3
"""Derive Vellum's next version from what landed, and write it.

version-at-land.yml runs this on main after every landing. A release is a
landed change to the project VERSION that is newer than every release tag
(release_request.py); this script proposes that change, so a person never has
to. It decides from the local clone and the release tags it is given, and
needs no token:

  plan    what main needs: `bump` (with the next version and why), `none`
          (nothing that ships changed since the newest release tag), or
          `pending` (main already declares an untagged newer version, which
          release-tag.yml tags once the observatory reconciles it).
  apply   rewrite every version surface from the current version to a newer
          one, refusing when a surface no longer holds the current version.

How a landed change counts (the semver heuristic, scaled for 0.x):

- A change counts only when it touches a path that ships in the SDK
  (SHIP_PATHS, minus tests). Workflow, provenance, docs and test changes
  release nothing on their own.
- A `Version-Bump: vellum=<patch|minor|major>` trailer on any commit of the
  change sets its level; `Version-Bump: skip reason="..."` counts it as
  nothing. An explicit level wins over a skip in the same change.
- Otherwise its conventional-commit titles decide: a breaking change (`type!:`
  or a `BREAKING CHANGE:` footer) is major, `feat` is minor, anything else
  that ships is patch.
- While the major version is 0, every level moves down one place, as Cargo's
  0.x rule does: breaking is minor, and features and fixes are patch. An
  explicit trailer level is taken as written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CMAKELISTS = "CMakeLists.txt"
RETIRED_TAGS = "provenance/retired-release-tags.json"
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PROJECT_VERSION = re.compile(r"project\(\s*vellum\b[^)]*\bVERSION\s+([0-9.]+)", re.IGNORECASE | re.DOTALL)
TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
LEVELS = ("none", "patch", "minor", "major")
# What ships in the SDK archive or decides its contents.
SHIP_PATHS = (
    "CMakeLists.txt", "LICENSE.md", "NOTICE.md", "DEPENDENCIES.md",
    "apps/**", "assets/**", "authoring/**", "cli/**", "cmake/**", "components/**", "foundation/**",
    "graphics/**", "mirror/**", "packages/**", "rmlui/**", "runtime/**", "templates/**", "web/**",
    "tools/mirror/**", "tools/agent_instructions/**",
    "scripts/install.sh", "scripts/install.ps1", "scripts/install_core.py", "scripts/INSTALLER_SHA256SUMS",
    "scripts/build_sdk_artifact.py", "scripts/build_web_payload.py",
    "provenance/third-party-lock.json", "provenance/renderer-attestation.json",
)
NOT_SHIPPED = ("**/tests/**", "**/test/**", "**/test_*.py")
# Every file that states the current version. apply rewrites each one and
# refuses when one of them no longer holds the current version, so a surface
# that moves or stops naming the version is found at the next bump, not after.
VERSION_SURFACES = (
    "CMakeLists.txt",
    "README.md",
    "cli/tests/test_agent_instructions_artifact.py",
    "cli/tests/test_cli.py",
    "cli/tests/test_contamination_scanner.py",
    "cli/tests/test_dev.py",
    "cli/tests/test_import_backend.py",
    "cli/tests/test_installer.py",
    "cli/tests/test_sdk_artifact.py",
    "cli/vellum_cli.py",
    "docs/cli/contract.md",
    "docs/cli/install-artifact.md",
    "scripts/build_sdk_artifact.py",
    "scripts/install.ps1",
    "scripts/install.sh",
    "scripts/verify_sdk_artifact.py",
)
# Regenerated after the surfaces are rewritten: it holds the installers' digests.
INSTALLER_SUMS = "scripts/INSTALLER_SHA256SUMS"
TRAILER = re.compile(r"^Version-Bump:[ \t]*(?P<value>.*?)[ \t]*$", re.MULTILINE)
EXPLICIT = re.compile(r"^vellum=(?P<level>patch|minor|major)$")
SKIP = re.compile(r'^skip(?:[ \t]+reason="[^"\n]*")?$')
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)\n]*\))?(?P<bang>!)?:[ \t]")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
QUEUE_MERGE = re.compile(r"^Merge pull request #[0-9]+ ")
BOT_TRAILER = 'Version-Bump: skip reason="the version-at-land bump itself"'


class VersionError(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise VersionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def parse_version(text: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(text):
        raise VersionError(f"{text!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def render(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def project_version(cmakelists: str) -> str:
    found = PROJECT_VERSION.search(cmakelists)
    if not found:
        raise VersionError("CMakeLists.txt declares no project VERSION")
    parse_version(found[1])
    return found[1]


def bumped(version: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if level == "major":
        return (major + 1, 0, 0)
    if level == "minor":
        return (major, minor + 1, 0)
    if level == "patch":
        return (major, minor, patch + 1)
    raise VersionError(f"cannot bump by {level!r}")


def glob_regex(pattern: str) -> re.Pattern[str]:
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


SHIP = tuple(glob_regex(pattern) for pattern in SHIP_PATHS)
EXCLUDED = tuple(glob_regex(pattern) for pattern in NOT_SHIPPED)


def ships(path: str) -> bool:
    return any(rule.match(path) for rule in SHIP) and not any(rule.match(path) for rule in EXCLUDED)


def semantic_level(messages: list[str], major: int) -> str:
    """The level a shipped change's conventional-commit titles give it."""
    titles = []
    for message in messages:
        lines = [line for line in message.splitlines() if line.strip()]
        if not lines:
            continue
        titles.append(lines[0])
        # A queue merge's subject is the merge itself; the pull request title follows.
        if QUEUE_MERGE.match(lines[0]) and len(lines) > 1:
            titles.append(lines[1])
    parsed = [match for title in titles if (match := CONVENTIONAL.match(title))]
    if any(match["bang"] for match in parsed) or any(BREAKING_FOOTER.search(message) for message in messages):
        level = "major"
    elif any(match["type"] == "feat" for match in parsed):
        level = "minor"
    else:
        level = "patch"
    if major == 0:
        level = {"major": "minor", "minor": "patch", "patch": "patch"}[level]
    return level


def explicit_level(messages: list[str]) -> tuple[str | None, bool]:
    """The highest `Version-Bump: vellum=<level>` in the messages, and whether one says skip.

    A trailer this script cannot read is refused rather than ignored, so a
    typo never turns a requested release into none.
    """
    levels, skipped = [], False
    for message in messages:
        for match in TRAILER.finditer(message):
            value = match["value"]
            if (explicit := EXPLICIT.fullmatch(value)):
                levels.append(explicit["level"])
            elif SKIP.fullmatch(value):
                skipped = True
            else:
                raise VersionError(f"unreadable trailer `Version-Bump: {value}`; use vellum=<patch|minor|major> "
                                   'or skip reason="..."')
    return (max(levels, key=LEVELS.index) if levels else None), skipped


@dataclass(frozen=True)
class Contribution:
    commit: str
    title: str
    level: str
    why: str


def landed_change(root: Path, commit: str, major: int) -> Contribution:
    """What one first-parent commit on main (a queue merge, or a direct commit) asks of the version."""
    parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if not parents:
        raise VersionError(f"{commit} has no parent")
    messages = [git(root, "show", "-s", "--format=%B", commit)]
    if len(parents) > 1:
        for side in git(root, "rev-list", f"{parents[0]}..{parents[1]}").split():
            messages.append(git(root, "show", "-s", "--format=%B", side))
    files = [path for path in git(root, "diff", "--name-only", "--no-renames", "-z", parents[0], commit).split("\0")
             if path]
    lines = [line for line in messages[0].splitlines() if line.strip()]
    title = lines[1] if len(lines) > 1 and QUEUE_MERGE.match(lines[0]) else (lines[0] if lines else commit)
    explicit, skipped = explicit_level(messages)
    if explicit:
        return Contribution(commit, title, explicit, "Version-Bump trailer")
    if skipped:
        return Contribution(commit, title, "none", "Version-Bump: skip")
    shipped = [path for path in files if ships(path)]
    if not shipped:
        return Contribution(commit, title, "none", "touches no shipped path")
    return Contribution(commit, title, semantic_level(messages, major), f"ships {shipped[0]}"
                        + (f" and {len(shipped) - 1} more" if len(shipped) > 1 else ""))


def retired(retired_tags: Any) -> set[str]:
    rows = retired_tags.get("tags") if isinstance(retired_tags, dict) else None
    if not isinstance(rows, list):
        raise VersionError("the retired-tags file must hold a `tags` list")
    return {row["tag"] for row in rows if isinstance(row, dict) and isinstance(row.get("tag"), str)}


def tag_version(tag: str) -> tuple[int, int, int] | None:
    match = TAG.fullmatch(tag)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_ancestor(root: Path, ancestor: str, commit: str) -> bool:
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, commit],
                            capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise VersionError(f"cannot compare {ancestor} with {commit}: {result.stderr.strip()}")
    return result.returncode == 0


@dataclass
class Plan:
    action: str
    current: str
    reason: str
    version: str = ""
    level: str = "none"
    since: str = ""
    changes: list[Contribution] = field(default_factory=list)


def plan(root: Path, commit: str, tags: dict[str, str], retired_tags: Any) -> Plan:
    """What main at `commit` needs.

    `tags` maps each existing release tag to the commit it names. The next
    version is one level above the highest version any tag has used, existing
    or retired, so a retired name is never reused and versions only move
    forward. The changes counted are the first-parent commits after the newest
    existing release tag that main contains.
    """
    current = project_version(git(root, "show", f"{commit}:{CMAKELISTS}"))
    known = [key for name in [*tags, *retired(retired_tags)] if (key := tag_version(name))]
    if not known:
        raise VersionError("no release tag exists to count from")
    highest = max(known)
    if parse_version(current) > highest:
        return Plan("pending", current, f"main declares {current}, newer than every release tag; "
                                        f"release-tag.yml tags v{current} once the observatory reconciles it")
    released = sorted(((key, name) for name in tags if (key := tag_version(name))
                       and is_ancestor(root, tags[name], commit)), reverse=True)
    if not released:
        raise VersionError(f"no release tag is an ancestor of {commit}")
    base_tag = released[0][1]
    chain = git(root, "rev-list", "--first-parent", "--reverse", f"{tags[base_tag]}..{commit}").split()
    changes = [landed_change(root, entry, highest[0]) for entry in chain]
    level = max((change.level for change in changes), key=LEVELS.index, default="none")
    if level == "none":
        return Plan("none", current, f"nothing that ships has landed since {base_tag}", since=base_tag,
                    changes=changes)
    version = render(bumped(highest, level))
    return Plan("bump", current, f"{level} from {sum(c.level != 'none' for c in changes)} landed change(s) "
                                 f"since {base_tag}", version, level, base_tag, changes)


TOKEN = r"(?<![0-9.]){}(?![0-9]|\.[0-9])"


def apply(root: Path, new: str) -> list[str]:
    """Rewrite every version surface from the current version to `new`; the files written."""
    cmake = root / CMAKELISTS
    old = project_version(cmake.read_text(encoding="utf-8"))
    if parse_version(new) <= parse_version(old):
        raise VersionError(f"{new} is not newer than the current version {old}")
    pattern = re.compile(TOKEN.format(re.escape(old)))
    rewritten: dict[str, str] = {}
    for relative in VERSION_SURFACES:
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise VersionError(f"version surface {relative} cannot be read: {error}") from error
        updated, count = pattern.subn(new, text)
        if count == 0:
            raise VersionError(f"version surface {relative} does not state the current version {old}; "
                               "update VERSION_SURFACES")
        rewritten[relative] = updated
    if project_version(rewritten[CMAKELISTS]) != new:
        raise VersionError(f"CMakeLists.txt does not declare {new} after the rewrite")
    for relative, text in rewritten.items():
        (root / relative).write_text(text, encoding="utf-8")
    regenerate_installer_sums(root)
    return [*VERSION_SURFACES, INSTALLER_SUMS]


def regenerate_installer_sums(root: Path) -> None:
    """Recompute each digest in INSTALLER_SHA256SUMS; it names files relative to scripts/ or the root."""
    sums = root / INSTALLER_SUMS
    lines = []
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip()
        candidates = [root / "scripts" / name, root / name]
        source = next((path for path in candidates if path.is_file()), None)
        if source is None or ".." in Path(name).parts:
            raise VersionError(f"{INSTALLER_SUMS} names {name}, which does not exist")
        lines.append(f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {name}")
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remote_release_tags(root: Path, remote: str) -> dict[str, str]:
    """Each vMAJOR.MINOR.PATCH tag on `remote` and the commit it names; any lookup failure raises."""
    result = subprocess.run(["git", "-C", str(root), "ls-remote", "--tags", remote, "refs/tags/v*"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise VersionError(f"could not list release tags on {remote}: {result.stderr.strip()}")
    direct, peeled = {}, {}
    for line in result.stdout.splitlines():
        sha, ref = line.split()
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            direct[name] = sha
    return {name: peeled.get(name, sha) for name, sha in direct.items() if TAG.fullmatch(name)}


def describe(result: Plan) -> str:
    lines = [f"{result.action}: {result.reason}"]
    for change in result.changes:
        lines.append(f"- {change.commit[:12]} {change.level:<5} {change.title[:100]} ({change.why})")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("plan", help="print action=, version=, level= and since= lines; the reasons on stderr")
    one.add_argument("--commit", required=True)
    one.add_argument("--remote", default="origin")
    two = sub.add_parser("apply", help="rewrite every version surface to --to")
    two.add_argument("--to", required=True)
    two.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "apply":
            for path in apply(args.root, args.to):
                print(path)
            return 0
        if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
            raise VersionError("the commit must be a full SHA")
        retired_tags = json.loads(git(ROOT, "show", f"{args.commit}:{RETIRED_TAGS}"))
        result = plan(ROOT, args.commit, remote_release_tags(ROOT, args.remote), retired_tags)
    except (OSError, ValueError, VersionError) as error:
        print(f"version-at-land refused: {error}", file=sys.stderr)
        return 1
    print(f"action={result.action}\nversion={result.version}\nlevel={result.level}\nsince={result.since}\n"
          f"current={result.current}")
    print(describe(result), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
