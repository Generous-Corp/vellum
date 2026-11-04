#!/usr/bin/env python3
"""release_cadence.py finds a version that should be released by now, and nothing else."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_cadence as cadence  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CMAKE = "project(Vellum\n    VERSION {v}\n    LANGUAGES CXX)\n"
EPOCH = 1790000000
HOUR = 3600


class Repo:
    """A scratch history whose commit and tag times are set to the second."""

    def __init__(self, case: unittest.TestCase, version: str = "0.1.10", retired: list[str] = ()) -> None:
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = EPOCH
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@t")
        self.write("provenance/retired-release-tags.json",
                   json.dumps({"tags": [{"tag": tag} for tag in retired]}))
        self.commit(CMAKE.format(v=version), "root")

    def git(self, *args: str) -> str:
        stamp = f"@{self.clock} +0000"
        environment = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True,
                              env=environment).stdout.strip()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, cmake: str, message: str, after_hours: float = 0) -> str:
        self.clock += int(after_hours * HOUR) + 1
        self.write("CMakeLists.txt", cmake)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def bump(self, version: str, after_hours: float = 0) -> str:
        return self.commit(CMAKE.format(v=version), f"chore(release): bump version to {version}", after_hours)

    def tag(self, name: str, after_hours: float = 0) -> None:
        self.clock += int(after_hours * HOUR) + 1
        self.git("tag", "-a", "-m", name, name)

    def at(self, hours: float) -> datetime:
        return datetime.fromtimestamp(self.clock + hours * HOUR, timezone.utc)

    def find(self, releases: list, hours: float) -> list[dict[str, str]]:
        return cadence.findings(self.root, self.git("rev-parse", "HEAD"), releases, self.at(hours))


def release(tag: str, draft: bool = False) -> dict:
    return {"tag_name": tag, "draft": draft, "published_at": None if draft else "2026-11-04T00:00:00Z"}


PUBLISHED = [[release("v0.1.10")]]


class Untagged(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo(self)
        self.repo.tag("v0.1.10")

    def test_a_released_main_is_clean(self) -> None:
        self.assertEqual(self.repo.find(PUBLISHED, hours=100), [])

    def test_a_bump_without_a_tag_is_found_after_the_grace_period(self) -> None:
        landed = self.repo.bump("0.1.11")
        self.assertEqual(self.repo.find(PUBLISHED, hours=5), [])
        found = self.repo.find(PUBLISHED, hours=7)
        self.assertEqual([(row["kind"], row["tag"]) for row in found], [("untagged", "v0.1.11")])
        self.assertIn(landed[:12], found[0]["detail"])

    def test_the_age_counts_from_the_version_landing_not_later_commits(self) -> None:
        self.repo.bump("0.1.11")
        self.repo.commit(CMAKE.format(v="0.1.11") + "# later\n", "build: later", after_hours=5)
        self.assertEqual([row["kind"] for row in self.repo.find(PUBLISHED, hours=2)], ["untagged"])

    def test_a_tagged_bump_is_not_untagged(self) -> None:
        self.repo.bump("0.1.11")
        self.repo.tag("v0.1.11", after_hours=1)
        self.assertEqual(self.repo.find([[release("v0.1.10"), release("v0.1.11")]], hours=100), [])


class Unpublished(unittest.TestCase):
    """The v0.1.9 shape: tagged and frozen, and then never published."""

    def setUp(self) -> None:
        self.repo = Repo(self)
        self.repo.tag("v0.1.10")

    def test_a_tag_without_a_published_release_is_found_after_the_grace_period(self) -> None:
        self.assertEqual(self.repo.find([], hours=5), [])
        self.assertEqual([(row["kind"], row["tag"]) for row in self.repo.find([], hours=7)],
                         [("unpublished", "v0.1.10")])

    def test_a_draft_is_not_a_published_release(self) -> None:
        self.assertEqual([row["kind"] for row in self.repo.find([[release("v0.1.10", draft=True)]], hours=7)],
                         ["unpublished"])

    def test_only_the_newest_unretired_tag_must_be_published(self) -> None:
        self.repo.bump("0.1.11")
        self.repo.tag("v0.1.11")
        # The older v0.1.10 is unpublished too; only the newest one counts.
        self.assertEqual(self.repo.find([[release("v0.1.11")]], hours=7), [])

    def test_a_retired_tag_is_not_owed_a_release(self) -> None:
        repo = Repo(self, version="0.1.9", retired=["v0.1.9"])
        repo.tag("v0.1.8")
        repo.tag("v0.1.9")
        found = repo.find([[release("v0.1.8")]], hours=7)
        # v0.1.9 is retired: no `unpublished` for it, but main must move past it.
        self.assertEqual([(row["kind"], row["tag"]) for row in found], [("retired", "v0.1.9")])


class DryRunLeftovers(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo(self)
        self.repo.tag("v0.1.10")
        self.repo.tag("dry-run/v0.1.10/41-1")

    def test_a_dry_run_tag_is_found_only_after_the_grace_period(self) -> None:
        self.assertEqual(self.repo.find(PUBLISHED, hours=5), [])
        self.assertEqual([(row["kind"], row["tag"]) for row in self.repo.find(PUBLISHED, hours=7)],
                         [("dry-run-leftover", "dry-run/v0.1.10/41-1")])

    def test_an_orphan_dry_run_draft_is_found_after_the_grace_period(self) -> None:
        repo = Repo(self)
        repo.tag("v0.1.10")
        created = repo.at(0).strftime("%Y-%m-%dT%H:%M:%SZ")
        draft = {"tag_name": "dry-run/v0.1.10/9-1", "draft": True, "published_at": None,
                 "name": "Vellum 0.1.10 experimental SDK (release dry run)", "created_at": created}
        self.assertEqual(repo.find([[release("v0.1.10"), draft]], hours=5), [])
        found = repo.find([[release("v0.1.10"), draft]], hours=7)
        self.assertEqual([(row["kind"], row["tag"]) for row in found], [("dry-run-leftover", "dry-run/v0.1.10/9-1")])

    def test_a_published_dry_run_release_is_found_at_once(self) -> None:
        found = self.repo.find([[release("v0.1.10"), release("dry-run/v0.1.10/41-1")]], hours=0)
        self.assertEqual([row["kind"] for row in found], ["dry-run-leftover"])
        self.assertIn("must never publish", found[0]["detail"])


class Behind(unittest.TestCase):
    def test_main_older_than_its_newest_tag_is_found(self) -> None:
        repo = Repo(self, version="0.1.8")
        repo.tag("v0.1.10")
        self.assertEqual([row["kind"] for row in repo.find(PUBLISHED, hours=0)], ["behind"])


class Cli(unittest.TestCase):
    def test_the_cli_writes_the_findings_and_the_issue_body(self) -> None:
        repo = Repo(self)
        repo.tag("v0.1.10")
        repo.bump("0.1.11")
        script = repo.root / "scripts"
        script.mkdir()
        for name in ("release_cadence.py", "release_request.py", "verify_immutable_release_preflight.py",
                     "version_at_land.py"):
            (script / name).write_bytes((ROOT / "scripts" / name).read_bytes())
        releases = repo.root / "releases.json"
        releases.write_text(json.dumps(PUBLISHED))
        out = repo.root / "findings.json"
        body = repo.root / "body.md"
        now = repo.at(7).strftime("%Y-%m-%dT%H:%M:%SZ")
        head = repo.git("rev-parse", "HEAD")
        done = subprocess.run([sys.executable, "-I", str(script / "release_cadence.py"), "--root", str(repo.root),
                               "--commit", head, "--releases", str(releases), "--now", now, "--output", str(out),
                               "--body", str(body)], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "count=1\n")
        self.assertEqual(json.loads(out.read_text())[0]["kind"], "untagged")
        self.assertIn("**untagged** `v0.1.11`", body.read_text())
        broken = subprocess.run([sys.executable, "-I", str(script / "release_cadence.py"), "--root", str(repo.root),
                                 "--commit", "abc", "--releases", str(releases), "--output", str(out),
                                 "--body", str(body)], capture_output=True, text=True, check=False)
        self.assertEqual(broken.returncode, 1)


class Workflows(unittest.TestCase):
    def test_the_cadence_check_runs_python_without_a_token(self) -> None:
        text = (ROOT / ".github/workflows/release-cadence-check.yml").read_text()
        scan = text[text.index("- name: Find versions"):text.index("- name: Maintain the tracking issue")]
        self.assertIn("python3 -I scripts/release_cadence.py", scan)
        self.assertNotIn("GH_TOKEN", scan)
        issue = text[text.index("- name: Maintain the tracking issue"):]
        self.assertNotIn("python", issue)
        self.assertIn("schedule:", text)

    def test_the_watchdog_watches_every_release_workflow_by_its_name(self) -> None:
        text = (ROOT / ".github/workflows/release-watchdog.yml").read_text()
        names = {}
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            first = next(line for line in path.read_text().splitlines() if line.startswith("name:"))
            names[path.name] = first.split(":", 1)[1].strip()
        for workflow in ("version-at-land.yml", "release-tag.yml", "gpu-macos.yml", "provenance.yml",
                         "sdk-release.yml"):
            with self.subTest(workflow=workflow):
                self.assertIn(f"      - {names[workflow]}\n", text)
                self.assertIn(f"'{names[workflow]}'", text)
        self.assertNotIn("run: python", text)
        # Event fields reach the script only through env, never as expressions in it.
        script = text[text.index("        run: |"):]
        self.assertNotIn("${{", script)


if __name__ == "__main__":
    unittest.main()
