#!/usr/bin/env python3
"""SHIPYARD_APP_PRIVATE_KEY is read only through the main-only `app-key` environment.

The repository-level copy of the key reaches any workflow a pushed branch
starts. The `app-key` environment holds the same key and admits only runs
whose ref is refs/heads/main, so every job that names the key must declare it,
and its workflow may start only from events whose ref is main: a push to main,
a dispatch, a schedule, repository_dispatch or workflow_run. A tag push or a
pull request event would be refused, and widening the environment instead
would reopen the key to whoever can create a matching ref.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SECRET = "secrets.SHIPYARD_APP_PRIVATE_KEY"
MAIN_REF_EVENTS = {"push", "workflow_dispatch", "schedule", "repository_dispatch", "workflow_run"}


def jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


def triggers(text: str) -> set[str]:
    on = re.search(r"(?m)^on:\n((?:[ #].*\n|\n)+)", text).group(1)
    return set(re.findall(r"(?m)^  ([a-z_]+):", on))


def readers() -> list[tuple[str, str, str, str]]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if SECRET not in text:
            continue
        for name, job in jobs(text).items():
            if SECRET in job:
                found.append((path.name, name, job, text))
    return found


class AppKeyEnvironment(unittest.TestCase):
    def test_the_scan_sees_the_known_readers(self) -> None:
        # Control: a renamed secret or a broken split would make the checks
        # below vacuous.
        self.assertGreaterEqual(len(readers()), 9)

    def test_every_reader_declares_app_key(self) -> None:
        offenders = []
        for workflow, job, text, _ in readers():
            if re.search(r"(?m)^    environment: app-key$", text) is None:
                offenders.append(f"{workflow}:{job}")
        self.assertEqual(offenders, [])

    def test_app_key_workflows_start_only_from_main_refs(self) -> None:
        offenders = []
        for workflow, _, _, text in readers():
            extra = triggers(text) - MAIN_REF_EVENTS
            if extra:
                offenders.append(f"{workflow}: {sorted(extra)}")
            push = re.search(r"(?m)^  push:\n((?:    .*\n)+)", text)
            if push and push.group(1).strip() != "branches: [main]":
                offenders.append(f"{workflow}: push must be branches [main] only")
        self.assertEqual(sorted(set(offenders)), [])

    def test_the_parsers_see_a_missing_environment(self) -> None:
        text = "on:\n  push:\n    tags: ['v*']\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: ${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}\n"
        self.assertEqual(list(jobs(text)), ["a"])
        self.assertIsNone(re.search(r"(?m)^    environment: app-key$", jobs(text)["a"]))
        self.assertEqual(triggers(text), {"push"})


if __name__ == "__main__":
    unittest.main()
