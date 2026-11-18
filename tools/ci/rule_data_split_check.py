#!/usr/bin/env python3
"""Fail a pull request that adds a rule together with the data it enforces.

The standing review rule: a gate and the data it acts on land in separate
pull requests, the rule first. The rule lands inert without its data and is
judged by the base it replaces; the data follows and is judged by the rule
main then has. One pull request that adds both is judged by a rule that is
not on main yet, so the reviewer holds it and every split costs another
round of release-path approvals. This check raises it when the pull request
opens instead.

A pull request fails when a file it adds as code (`.py`, `.sh`, `.cmake` or
`CMakeLists.txt`, not a test) names a data file it also adds (`.json`,
`.jsonl`, `.yaml`, `.yml`, `.toml`, `.txt`, `.csv` or `.tsv`, outside
`.github/` and outside test fixtures), by its repository path or its file
name. A test and its fixtures may land together; a script that edits an
existing file, or one moved with its data, is not a new rule.

The match reads text, so it can flag a new tool that is no rule at all, for
instance a generator and its template. Such a pull request says so, once
for each file the check names, with a line in any of its commit messages:

  Rule-Data: together rule=<path> reason="<why it is not a rule over that data>"

A line excuses only the file it names, and the reviewer reads its reason
like any other part of the change.

This is a narrow accident guard, and a pass proves nothing. It does not see an
existing gate edited to read new data, a rule written as a workflow or in
another language, or a path the code builds at run time; the reviewer still
applies the rule to those.

  rule_data_split_check.py --base <base sha> --head <head sha> [--repo .]

The diff is `base...head`, what the pull request shows. Exit codes: 0 the
change passes or carries the trailer; 1 it adds a rule with its data; 2 git
could not read the range.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CODE_SUFFIXES = (".py", ".sh", ".cmake")
CODE_NAMES = ("CMakeLists.txt",)
DATA_SUFFIXES = (".json", ".jsonl", ".yaml", ".yml", ".toml", ".txt", ".csv", ".tsv")
# A directory that holds tests or their inputs.
TEST_DIRS = frozenset({"test", "tests", "testdata", "fixtures", "__fixtures__", "golden", "goldens"})
TRAILER = re.compile(r'^Rule-Data:\s*together\s+rule=(\S+)\s+reason="[^"\s][^"]*"\s*$', re.MULTILINE)


class GitError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    rule: str
    data: str

    def message(self) -> str:
        return (f"{self.rule} is a new rule and reads {self.data}, which this pull request also adds. "
                f"Split it, rule first: land {self.rule} inert without its data, then add {self.data} "
                f"in a follow-up pull request that the merged rule judges. If {self.rule} is not a rule "
                f"over {self.data}, add `Rule-Data: together rule={self.rule} reason=\"...\"` to a commit message.")


def escape(text: str, prop: bool = False) -> str:
    """`text` as a workflow command value, so a file name cannot start another command."""
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip() or result.returncode}")
    return result.stdout


def in_tests(path: PurePosixPath) -> bool:
    return any(part in TEST_DIRS for part in path.parts[:-1])


def is_code(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.name not in CODE_NAMES and pure.suffix not in CODE_SUFFIXES:
        return False
    stem = pure.stem
    test = (stem.startswith("test_") or stem.endswith("_test") or stem.endswith("_tests")
            or pure.name == "conftest.py")
    return not test and not in_tests(pure)


def is_data(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.name in CODE_NAMES or pure.suffix not in DATA_SUFFIXES:
        return False
    if pure.parts[0] == ".github" or in_tests(pure):
        return False
    # A Python requirements file is what a tool installs, not what a rule enforces.
    pip = pure.name.startswith("requirements") and pure.suffix == ".txt"
    return not (pip or pure.name.startswith("test_") or "fixture" in pure.name)


def names(text: str, data: str) -> bool:
    """Whether `text` names `data` by its repository path or its file name."""
    for name in (data, PurePosixPath(data).name):
        if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", text):
            return True
    return False


def added(repo: Path, base: str, head: str) -> list[str]:
    """Files the change adds. A moved or copied file is not added: a rule
    moved with its data was already on the base."""
    fields = iter(git(repo, "diff", "--name-status", "--find-renames", "-z", f"{base}...{head}").split("\0"))
    paths: list[str] = []
    for status in fields:
        if not status:
            continue
        path = next(fields)
        if status[0] in "RC":
            next(fields)
        elif status == "A":
            paths.append(path)
    return paths


def findings(repo: Path, base: str, head: str) -> list[Finding]:
    paths = added(repo, base, head)
    data = sorted(path for path in paths if is_data(path))
    if not data:
        return []
    found: list[Finding] = []
    for rule in sorted(path for path in paths if is_code(path)):
        text = git(repo, "show", f"{head}:{rule}")
        found.extend(Finding(rule, item) for item in data if item != rule and names(text, item))
    return found


def excused(repo: Path, base: str, head: str) -> set[str]:
    """The rule files a Rule-Data line in the change's own commits names."""
    return set(TRAILER.findall(git(repo, "log", "--format=%B", f"{base}..{head}")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        found = findings(args.repo, args.base, args.head)
        named = excused(args.repo, args.base, args.head) if found else set()
    except GitError as error:
        print(f"rule-data split: error: {escape(str(error))}", file=sys.stderr)
        return 2
    if not found:
        print("rule-data split: no added rule reads data this pull request adds")
    refused = 0
    for finding in found:
        if finding.rule in named:
            print(f"rule-data split: excused by a Rule-Data trailer: {escape(finding.rule)} "
                  f"reads {escape(finding.data)}")
        else:
            refused += 1
            print(f"::error file={escape(finding.rule, prop=True)}::{escape(finding.message())}")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
