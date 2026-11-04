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
  lint    report `Version-Bump:` trailers in a commit range that plan would
          skip (advisory; release-policy.yml runs it on pull requests).

release_request.py also uses this module to decide that a landed version is a
release at all (verify_bump): only the version-at-land bot's own pull request,
whose diff is exactly the rewrite apply makes and whose version is exactly what
plan derives from the changes before it, can be tagged.

How a landed change counts (the semver heuristic, scaled for 0.x):

- A change counts only when it touches a path that ships in the SDK
  (SHIP_PATHS, minus tests). Workflow, provenance, docs and test changes
  release nothing on their own.
- A `Version-Bump: vellum=<patch|minor|major>` trailer on any commit of the
  change sets its level; `Version-Bump: skip reason="..."` counts it as
  nothing. An explicit level wins over a skip in the same change. A
  `Version-Bump:` line this script cannot read (another project's surface
  name, a typo) is skipped with a warning, so one bad line never stops every
  later release.
- Otherwise its conventional-commit titles decide: a breaking change (`type!:`
  or a `BREAKING CHANGE:` footer) is major, `feat` is minor, anything else
  that ships is patch.
- While the major version is 0, every level moves down one place, as Cargo's
  0.x rule does: breaking is minor, and features and fixes are patch. An
  explicit `major` is minor too, so 1.0.0 is never proposed automatically.
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
# A project(...) call at the start of a line, read only after comments are
# removed, so a commented-out project() line can never declare the version.
PROJECT_VERSION = re.compile(r"^[ \t]*project[ \t]*\(\s*vellum\b[^)]*?\bVERSION\s+([^\s)]+)",
                             re.IGNORECASE | re.MULTILINE)
BRACKET_COMMENT = re.compile(r"#\[(=*)\[.*?\]\1\]", re.DOTALL)
TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
LEVELS = ("none", "patch", "minor", "major")
# What ships in the SDK archive or decides its contents.
SHIP_PATHS = (
    "CMakeLists.txt", "LICENSE.md", "NOTICE.md", "DEPENDENCIES.md",
    "apps/**", "assets/**", "authoring/**", "cli/**", "cmake/**", "components/**", "foundation/**",
    "gpu-runtime/**", "graphics/**", "mirror/**", "packages/**", "rmlui/**", "runtime/**", "templates/**", "web/**",
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
INSTALLER_SUMS: str | None = "scripts/INSTALLER_SHA256SUMS"
TRAILER = re.compile(r"^Version-Bump:[ \t]*(?P<value>.*?)[ \t]*$", re.MULTILINE)
EXPLICIT = re.compile(r"^vellum=(?P<level>patch|minor|major)$")
SKIP = re.compile(r'^skip(?:[ \t]+reason="[^"\n]*")?$')
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)\n]*\))?(?P<bang>!)?:[ \t]")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
QUEUE_MERGE = re.compile(r"^Merge pull request #[0-9]+ ")
BOT_TRAILER = 'Version-Bump: skip reason="the version-at-land bump itself"'


class VersionError(RuntimeError):
    pass


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, input=input_bytes, check=False)
    if result.returncode != 0:
        raise VersionError(f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout.decode("utf-8", "replace")


def parse_version(text: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(text):
        raise VersionError(f"{text!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def render(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def strip_cmake_comments(text: str) -> str:
    """`text` with CMake bracket and line comments removed (a `#` inside a quoted argument is kept)."""
    text = BRACKET_COMMENT.sub("", text)
    lines = []
    for line in text.splitlines():
        quoted, kept = False, []
        for index, char in enumerate(line):
            if char == '"' and (index == 0 or line[index - 1] != "\\"):
                quoted = not quoted
            if char == "#" and not quoted:
                break
            kept.append(char)
        lines.append("".join(kept))
    return "\n".join(lines)


def project_version(cmakelists: str) -> str:
    """The one project(Vellum VERSION ...) the file declares; the parser every release script uses."""
    found = PROJECT_VERSION.findall(strip_cmake_comments(cmakelists))
    if not found:
        raise VersionError("CMakeLists.txt declares no project VERSION")
    if len(found) > 1:
        raise VersionError("CMakeLists.txt declares the project VERSION more than once")
    parse_version(found[0])
    return found[0]


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


WARNINGS: list[str] = []


def trailers(message: str) -> list[str]:
    """The `Version-Bump` values in the message's trailer block only, as git reads trailers.

    A line in the body that merely quotes the syntax is not a trailer.
    """
    result = subprocess.run(["git", "interpret-trailers", "--parse", "--no-divider"], input=message,
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise VersionError(f"git interpret-trailers failed: {result.stderr.strip()}")
    return [match["value"] for match in TRAILER.finditer(result.stdout)]


def explicit_level(messages: list[str], major: int = 1) -> tuple[str | None, bool]:
    """The highest `Version-Bump: vellum=<level>` trailer in the messages, and whether one says skip.

    While the major version is 0, an explicit `major` counts as `minor`.
    Another project's key (Pulp's `sdk=patch`) is ignored. A malformed
    `vellum=` or `skip` value is skipped and recorded in WARNINGS, so one bad
    line never stops every later plan.
    """
    levels, skipped = [], False
    for message in messages:
        parsed = trailers(message)
        if len(TRAILER.findall(message)) > len(parsed):
            WARNINGS.append("ignored a `Version-Bump:` line outside the trailer block; put it in the last "
                            "paragraph, after a blank line, with only trailers")
        for value in parsed:
            if (explicit := EXPLICIT.fullmatch(value)):
                level = explicit["level"]
                levels.append("minor" if major == 0 and level == "major" else level)
            elif SKIP.fullmatch(value):
                skipped = True
            elif re.match(r"(?i)\s*(vellum\b|skip\b)", value):
                WARNINGS.append(f"skipped unreadable trailer `Version-Bump: {one_line(value)[:120]}`; use "
                                'vellum=<patch|minor|major> or skip reason="..."')
    return (max(levels, key=LEVELS.index) if levels else None), skipped


def one_line(text: str) -> str:
    return "".join(ch if ch.isprintable() else "?" for ch in text)


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
    explicit, skipped = explicit_level(messages, major)
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


def version_of(root: Path, commit: str) -> str | None:
    """The project VERSION CMakeLists.txt declares at `commit`, or None when it declares none readably."""
    data = blob(root, commit, CMAKELISTS)
    if data is None:
        return None
    try:
        return project_version(data.decode("utf-8", "replace"))
    except VersionError:
        return None


def version_landing(root: Path, commit: str) -> str:
    """The commit on `commit`'s first-parent chain that landed its project VERSION.

    That is the newest first-parent commit whose project VERSION differs from
    its first parent's. A version bump reaches main through the merge queue, so
    on main this is the queue's merge commit. Commits that change
    CMakeLists.txt without changing the version are passed over.
    """
    chain = git(root, "rev-list", "--first-parent", commit).split()
    if not chain:
        raise VersionError(f"{commit} has no history")
    query = "".join(f"{entry}:{CMAKELISTS}\n{entry}^1:{CMAKELISTS}\n" for entry in chain)
    answer = subprocess.run(["git", "-C", str(root), "cat-file", "--batch-check=%(objectname)"],
                            input=query, capture_output=True, text=True, check=True).stdout.splitlines()
    if len(answer) != 2 * len(chain):
        raise VersionError("git answered a different number of objects than asked")
    for index, entry in enumerate(chain):
        mine, parent = answer[2 * index], answer[2 * index + 1]
        if mine.endswith(" missing"):
            break
        if mine != parent and version_of(root, entry) != version_of(root, f"{entry}^1"):
            return entry
    raise VersionError(f"no commit on the first-parent history of {commit} set its project VERSION")


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
    floor = None
    if parse_version(current) > highest:
        problem = verify_bump(root, version_landing(root, commit), tags, retired_tags)
        if problem is None:
            return Plan("pending", current, f"main declares {current}, newer than every release tag; "
                                            f"release-tag.yml tags v{current} once the observatory reconciles it")
        # A bump that release-tag will refuse (one that lost a race with a
        # change landed ahead of it) must not leave main stuck: it counts as
        # used, and the next version goes past it. Only a number one step
        # above the newest tag may be passed over this way; anything further
        # (a hand-set jump) stops here for a person to revert or retire.
        steps = {bumped(highest, level) for level in (("patch", "minor") if highest[0] == 0 else LEVELS[1:])}
        if parse_version(current) not in steps:
            raise VersionError(f"main declares {current}, which version-at-land did not land and which is not "
                               f"one step above v{render(highest)} ({problem}); revert it or retire v{current}")
        floor = parse_version(current)
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
    target = bumped(highest, level)
    if floor is not None and target <= floor:
        target = bumped(floor, "patch")
    version = render(target)
    return Plan("bump", current, f"{level} from {sum(c.level != 'none' for c in changes)} landed change(s) "
                                 f"since {base_tag}", version, level, base_tag, changes)


TOKEN = r"(?<![0-9.]){}(?![0-9]|\.[0-9])"


def rewrite(read: Any, new: str) -> dict[str, bytes]:
    """Every file a bump to `new` writes, from `read(path) -> bytes | None` of the tree it starts from.

    This is the whole bump: apply writes exactly these bytes, and verify_bump
    requires a landed bump to equal them.
    """
    cmake = read(CMAKELISTS)
    if cmake is None:
        raise VersionError("CMakeLists.txt is missing")
    old = project_version(cmake.decode("utf-8"))
    if parse_version(new) <= parse_version(old):
        raise VersionError(f"{new} is not newer than the current version {old}")
    pattern = re.compile(TOKEN.format(re.escape(old)))
    written: dict[str, bytes] = {}
    for relative in VERSION_SURFACES:
        data = read(relative)
        if data is None:
            raise VersionError(f"version surface {relative} cannot be read")
        updated, count = pattern.subn(new, data.decode("utf-8"))
        if count == 0:
            raise VersionError(f"version surface {relative} does not state the current version {old}; "
                               "update VERSION_SURFACES")
        written[relative] = updated.encode("utf-8")
    if project_version(written[CMAKELISTS].decode("utf-8")) != new:
        raise VersionError(f"CMakeLists.txt does not declare {new} after the rewrite")
    if INSTALLER_SUMS:
        written[INSTALLER_SUMS] = installer_sums(read, written)
    return written


def installer_sums(read: Any, written: dict[str, bytes]) -> bytes:
    """INSTALLER_SHA256SUMS recomputed; it names files relative to scripts/ or the root."""
    sums = read(INSTALLER_SUMS)
    if sums is None:
        raise VersionError(f"{INSTALLER_SUMS} is missing")
    lines = []
    for line in sums.decode("utf-8").splitlines():
        if not line.strip():
            continue
        _, name = line.split(None, 1)
        name = name.strip()
        if ".." in Path(name).parts or name.startswith("/"):
            raise VersionError(f"{INSTALLER_SUMS} names {name}, outside the repository")
        for candidate in (f"scripts/{name}", name):
            data = written.get(candidate, read(candidate))
            if data is not None:
                break
        else:
            raise VersionError(f"{INSTALLER_SUMS} names {name}, which does not exist")
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def apply(root: Path, new: str) -> list[str]:
    """Rewrite every version surface from the current version to `new`; the files written."""
    def read(relative: str) -> bytes | None:
        path = root / relative
        return path.read_bytes() if path.is_file() else None

    written = rewrite(read, new)
    for relative, data in written.items():
        (root / relative).write_bytes(data)
    return list(written)


BOT_NAME = "vellum-version-at-land"
BOT_EMAIL = "vellum-version-at-land@users.noreply.github.com"
BUMP_BRANCH = "automation/version-bump"
BUMP_MERGE = re.compile(r"^Merge pull request #[0-9]+ from Generous-Corp/automation/version-bump$")


def blob(root: Path, commit: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"{commit}:{path}"], capture_output=True,
                            check=False)
    return result.stdout if result.returncode == 0 else None


def verify_bump(root: Path, landing: str, tags: dict[str, str], retired_tags: Any) -> str | None:
    """Why the commit that landed main's version is not a release, or None when it is one.

    It is a release only when all of these hold:
    - it is the merge queue's merge of the version-at-land pull request (the
      merge names the automation/version-bump branch) and that pull request is
      one commit by the version-at-land bot carrying the bot's trailer;
    - its diff is exactly the rewrite apply makes from its first parent's tree;
    - its version is exactly what plan derives, at its first parent, from the
      changes landed since the newest release tag.
    A version changed any other way (by hand, with an extra hunk, a level
    plan would not choose, or a skipped number) is never tagged.
    """
    parents = git(root, "rev-list", "--parents", "-n", "1", landing).split()[1:]
    if len(parents) != 2:
        return f"{landing[:12]} is not a merge queue merge"
    base, head = parents
    if not BUMP_MERGE.match(git(root, "show", "-s", "--format=%s", landing).strip()):
        return f"{landing[:12]} does not merge the {BUMP_BRANCH} branch"
    side = git(root, "rev-list", f"{base}..{head}").split()
    if side != [head]:
        return f"the bump pull request merged at {landing[:12]} is not one commit"
    author = git(root, "show", "-s", "--format=%an%n%ae%n%cn%n%ce", head).splitlines()
    if author != [BOT_NAME, BOT_EMAIL, BOT_NAME, BOT_EMAIL]:
        return f"{head[:12]} is not the version-at-land bot's commit"
    if BOT_TRAILER not in git(root, "show", "-s", "--format=%B", head):
        return f"{head[:12]} lacks the version-at-land trailer"
    version = project_version(git(root, "show", f"{landing}:{CMAKELISTS}"))
    # The bump is judged against the main it was planned from (its own
    # parent), so a change that lands ahead of it in the queue does not make
    # it unreleasable; the byte check below still compares the merged tree.
    # The one-commit rule above already means that parent is on main.
    planned = git(root, "rev-parse", f"{head}^1").strip()
    try:
        expected = plan(root, planned, tags, retired_tags)
    except VersionError as error:
        return f"the version cannot be re-derived at {planned[:12]}: {error}"
    if expected.action != "bump" or expected.version != version:
        return (f"version-at-land derives {expected.version or expected.action} at {planned[:12]}, but "
                f"{landing[:12]} declares {version}")
    try:
        written = rewrite(lambda path: blob(root, base, path), version)
    except VersionError as error:
        return f"the bump cannot be re-derived at {base[:12]}: {error}"
    changed = sorted(path for path in git(root, "diff", "--name-only", "--no-renames", "-z", base, landing)
                     .split("\0") if path)
    if changed != sorted(written):
        extra = sorted(set(changed) - set(written))
        missing = sorted(set(written) - set(changed))
        return f"the bump changes {extra or 'too little'}{f', not {missing}' if missing else ''} beyond the rewrite"
    wrong = [path for path, data in written.items() if blob(root, landing, path) != data]
    if wrong:
        return f"the bump's {wrong[0]} differs from the rewrite apply makes"
    before, after = tree_entries(root, base, written), tree_entries(root, landing, written)
    for path, data in written.items():
        mode = before.get(path, ("100644", ""))[0]
        object_id = git(root, "hash-object", "--stdin", "-t", "blob", input_bytes=data).strip()
        if after.get(path) != (mode, object_id):
            return f"the bump's {path} is {after.get(path)}, not mode {mode} object {object_id[:12]}"
    return None


def tree_entries(root: Path, commit: str, paths: Any) -> dict[str, tuple[str, str]]:
    """(mode, object id) of each path in `commit`'s tree."""
    listing = git(root, "ls-tree", "-z", commit, "--", *sorted(paths))
    found = {}
    for row in listing.split("\0"):
        if row:
            meta, path = row.split("\t", 1)
            mode, _, object_id = meta.split()
            found[path] = (mode, object_id)
    return found


def lint(root: Path, revisions: str) -> list[str]:
    """Trailers in `revisions` that plan would skip."""
    WARNINGS.clear()
    for commit in git(root, "rev-list", revisions).split():
        explicit_level([git(root, "show", "-s", "--format=%B", commit)])
    return list(WARNINGS)


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
    three = sub.add_parser("lint", help="print a ::warning:: for each trailer plan would skip; always exits 0")
    three.add_argument("--revisions", required=True, help="a git revision range, such as BASE..HEAD")
    args = parser.parse_args(argv)
    try:
        if args.command == "lint":
            for warning in lint(ROOT, args.revisions):
                print("::warning::" + warning.replace("%", "%25"))
            return 0
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
    for warning in WARNINGS:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
