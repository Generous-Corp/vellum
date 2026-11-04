#!/usr/bin/env python3
"""version_at_land.py proposes the next version only from shipped changes, and writes every surface."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import version_at_land as val  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CMAKE = "cmake_minimum_required(VERSION 3.24)\nproject(Vellum\n    VERSION {v}\n    LANGUAGES CXX)\n"
NO_RETIRED = {"schema": "vellum.retired-release-tags.v1", "tags": []}


class Repo:
    """A scratch repository shaped like Vellum's main: every change lands as a queue merge."""

    def __init__(self, case: unittest.TestCase, version: str = "0.1.10") -> None:
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tick = 0
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@t")
        self.write("CMakeLists.txt", CMAKE.format(v=version))
        self.write("foundation/src/core.cpp", "int x;\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "root")
        self.main = self.git("rev-parse", "HEAD")
        self.tags: dict[str, str] = {}

    def git(self, *args: str) -> str:
        self.tick += 1
        stamp = f"@{1790000000 + self.tick} +0000"
        environment = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True,
                              env=environment).stdout.strip()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def land(self, title: str, files: dict[str, str], body: str = "", branch: str = "topic",
             identity: tuple[str, str] = ("t", "t@t"), extra_commits: int = 0) -> str:
        """A pull request with one commit titled `title`, landed by the queue's merge commit."""
        self.git("checkout", "-q", "--detach", self.main)
        for index in range(extra_commits):
            self.git("commit", "-q", "--allow-empty", "-m", f"chore: extra {index}")
        for path, text in files.items():
            self.write(path, text)
        self.git("add", "-A")
        name, email = identity
        self.git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "--allow-empty", "-m",
                 title + ("\n\n" + body if body else ""))
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.main)
        number = self.tick
        self.git("merge", "-q", "--no-ff", "-m",
                 f"Merge pull request #{number} from Generous-Corp/{branch}\n\n{title}", head)
        self.main = self.git("rev-parse", "HEAD")
        return self.main

    def bot_bump(self, version: str, extra: dict[str, str] | None = None, **overrides) -> str:
        """The version-at-land pull request: the bot's one commit rewriting CMakeLists.txt, merged."""
        files = {"CMakeLists.txt": CMAKE.format(v=version), **(extra or {})}
        options = dict(body=val.BOT_TRAILER, branch=val.BUMP_BRANCH, identity=(val.BOT_NAME, val.BOT_EMAIL))
        options.update(overrides)
        return self.land(f"chore(release): bump version to {version}", files, **options)

    def tag(self, name: str) -> None:
        self.tags[name] = self.main

    def plan(self, retired: dict = NO_RETIRED) -> val.Plan:
        return val.plan(self.root, self.main, self.tags, retired)


class Paths(unittest.TestCase):
    def test_shipped_paths_count_and_the_rest_do_not(self) -> None:
        for path in ("CMakeLists.txt", "foundation/src/core.cpp", "cli/vellum_cli.py", "mirror/core/x.hpp",
                     "scripts/install.sh", "provenance/renderer-attestation.json", "packages/vellum-ui/index.js",
                     "LICENSE.md", "tools/mirror/VellumMirror.cmake", "gpu-runtime/renderer-lock.json"):
            with self.subTest(path=path):
                self.assertTrue(val.ships(path))
        for path in ("cli/tests/test_cli.py", "web/tests/a.mjs", ".github/workflows/release-tag.yml",
                     "docs/ci/releasing.md", "provenance/pulp-observatory/cursor.json", "scripts/release_request.py",
                     "scripts/test_version_at_land.py", "README.md", "CMakeLists.txt.bak", "tools/provenance/x.py"):
            with self.subTest(path=path):
                self.assertFalse(val.ships(path))


class Levels(unittest.TestCase):
    def test_conventional_titles_under_the_0x_rule(self) -> None:
        cases = {
            "fix: a": "patch", "feat: a": "patch", "feat(ui): a": "patch", "refactor: a": "patch",
            "feat!: a": "minor", "fix(core)!: a": "minor", "no prefix at all": "patch",
        }
        for title, level in cases.items():
            with self.subTest(title=title):
                self.assertEqual(val.semantic_level([title], major=0), level)
        self.assertEqual(val.semantic_level(["fix: a\n\nBREAKING CHANGE: the API moved"], major=0), "minor")

    def test_conventional_titles_from_1_0(self) -> None:
        self.assertEqual(val.semantic_level(["fix: a"], major=1), "patch")
        self.assertEqual(val.semantic_level(["feat: a"], major=1), "minor")
        self.assertEqual(val.semantic_level(["feat!: a"], major=1), "major")

    def test_a_queue_merge_is_read_by_its_pull_request_title(self) -> None:
        merge = "Merge pull request #12 from Generous-Corp/x\n\nfeat!: replace the scene API"
        self.assertEqual(val.semantic_level([merge], major=1), "major")
        # The merge subject itself is not a conventional title.
        self.assertEqual(val.semantic_level(["Merge pull request #12 from Generous-Corp/x"], major=1), "patch")

    def test_trailers_set_or_skip_the_level(self) -> None:
        self.assertEqual(val.explicit_level(["fix: a\n\nVersion-Bump: vellum=minor"]), ("minor", False))
        self.assertEqual(val.explicit_level(['fix: a\n\nVersion-Bump: skip reason="docs only"']), (None, True))
        self.assertEqual(val.explicit_level(["a\n\nVersion-Bump: skip", "b\n\nVersion-Bump: vellum=patch"]),
                         ("patch", True))
        self.assertEqual(val.explicit_level(["a\n\nVersion-Bump: vellum=patch", "Version-Bump: vellum=major"]),
                         ("major", False))
        self.assertEqual(val.explicit_level(["fix: no trailer"]), (None, False))

    def test_an_explicit_major_is_minor_under_0x(self) -> None:
        # 1.0.0 is never proposed automatically.
        self.assertEqual(val.explicit_level(["a\n\nVersion-Bump: vellum=major"], major=0), ("minor", False))
        self.assertEqual(val.explicit_level(["a\n\nVersion-Bump: vellum=major"], major=1), ("major", False))
        repo = Repo(self)
        repo.tag("v0.1.10")
        repo.land("fix: a", {"runtime/s.cpp": "s\n"}, body="Version-Bump: vellum=major")
        self.assertEqual(repo.plan().version, "0.2.0")

    def test_an_unreadable_trailer_is_skipped_with_a_warning(self) -> None:
        # Must-pass: one bad line never stops every later plan. Must-fail: it is
        # still named, and it never counts as a level or a skip.
        for value in ("sdk=minor", "vellum=huge", "vellum = minor", "skip because", "Minor"):
            with self.subTest(value=value):
                val.WARNINGS.clear()
                self.assertEqual(val.explicit_level([f"fix: a\n\nVersion-Bump: {value}"]), (None, False))
                self.assertEqual(len(val.WARNINGS), 1)
                self.assertIn("unreadable trailer", val.WARNINGS[0])
        repo = Repo(self)
        repo.tag("v0.1.10")
        repo.land("fix: a", {"runtime/s.cpp": "s\n"}, body="Version-Bump: sdk=patch")
        self.assertEqual(repo.plan().version, "0.1.11")
        self.assertEqual(len(val.lint(repo.root, f"{repo.tags['v0.1.10']}..{repo.main}")), 1)
        self.assertEqual(val.lint(repo.root, f"{repo.tags['v0.1.10']}..{repo.tags['v0.1.10']}"), [])


class Plans(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo(self)
        self.repo.tag("v0.1.10")

    def test_nothing_shipped_proposes_nothing(self) -> None:
        self.repo.land("docs: explain", {"docs/x.md": "x\n"})
        self.repo.land("ci: tune", {".github/workflows/x.yml": "on: push\n"})
        self.repo.land("chore(provenance): coalesce Pulp observatory evidence",
                       {"provenance/pulp-observatory/cursor.json": "{}\n"})
        self.repo.land("fix: a test-only fix", {"cli/tests/test_x.py": "pass\n"})
        result = self.repo.plan()
        self.assertEqual((result.action, result.version, result.since), ("none", "", "v0.1.10"))
        self.assertEqual(len(result.changes), 4)

    def test_a_shipped_change_proposes_the_next_patch(self) -> None:
        self.repo.land("docs: explain", {"docs/x.md": "x\n"})
        self.repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"})
        result = self.repo.plan()
        self.assertEqual((result.action, result.version, result.level), ("bump", "0.1.11", "patch"))
        self.repo.land("feat: add a widget", {"components/w.cpp": "w\n"})
        self.assertEqual(self.repo.plan().version, "0.1.11")
        self.repo.land("feat!: replace the scene API", {"runtime/s.cpp": "s\n"})
        result = self.repo.plan()
        self.assertEqual((result.version, result.level), ("0.2.0", "minor"))

    def test_only_changes_after_the_newest_tag_count(self) -> None:
        self.repo.land("feat!: an old break", {"runtime/s.cpp": "s\n"})
        self.repo.land("chore(release): bump version to 0.2.0", {"CMakeLists.txt": CMAKE.format(v="0.2.0")},
                       body=val.BOT_TRAILER)
        self.repo.tag("v0.2.0")
        self.assertEqual(self.repo.plan().action, "none")
        self.repo.land("fix: after the release", {"runtime/s.cpp": "t\n"})
        result = self.repo.plan()
        self.assertEqual((result.version, result.since), ("0.2.1", "v0.2.0"))

    def test_an_untagged_newer_version_on_main_is_pending(self) -> None:
        self.repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"})
        self.repo.land("chore(release): bump version to 0.1.11", {"CMakeLists.txt": CMAKE.format(v="0.1.11")},
                       body=val.BOT_TRAILER)
        self.repo.land("feat!: lands while the release waits", {"runtime/s.cpp": "s\n"})
        result = self.repo.plan()
        self.assertEqual((result.action, result.version), ("pending", ""))
        self.assertIn("newer than every release tag", result.reason)

    def test_a_retired_version_is_never_proposed(self) -> None:
        self.repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"})
        retired = {"tags": [{"tag": "v0.1.11"}, {"tag": "v0.1.12"}]}
        self.assertEqual(self.repo.plan(retired).version, "0.1.13")
        # Retired names count as used even when they are higher than any tag.
        self.assertEqual(self.repo.plan({"tags": [{"tag": "v0.3.4"}]}).version, "0.3.5")

    def test_a_main_behind_its_newest_tag_counts_from_the_tag(self) -> None:
        repo = Repo(self, version="0.1.9")
        repo.tag("v0.1.10")
        repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"})
        self.assertEqual(repo.plan().version, "0.1.11")

    def test_trailers_override_the_heuristic(self) -> None:
        self.repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"},
                       body='Version-Bump: skip reason="no user-visible change"')
        self.assertEqual(self.repo.plan().action, "none")
        self.repo.land("ci: a workflow-only change", {".github/workflows/x.yml": "x\n"},
                       body="Version-Bump: vellum=minor")
        self.assertEqual(self.repo.plan().version, "0.2.0")

    def test_the_bump_itself_is_never_counted(self) -> None:
        # A bump branch that also ships an edit still counts as nothing: the
        # trailer marks the bot's own commit.
        self.repo.land("chore(release): bump version to 0.1.11",
                       {"CMakeLists.txt": CMAKE.format(v="0.1.10") + "# touched\n"}, body=val.BOT_TRAILER)
        self.assertEqual(self.repo.plan().action, "none")

    def test_without_a_reachable_tag_it_refuses(self) -> None:
        with self.assertRaisesRegex(val.VersionError, "no release tag exists"):
            val.plan(self.repo.root, self.repo.main, {}, NO_RETIRED)
        # A tag off main's history, or one whose commit is not in the clone, refuses too.
        self.repo.git("checkout", "-q", "--detach", self.repo.main)
        self.repo.git("commit", "-q", "--allow-empty", "-m", "side")
        side = self.repo.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(val.VersionError, "is an ancestor"):
            val.plan(self.repo.root, self.repo.main, {"v0.1.10": side}, NO_RETIRED)
        with self.assertRaisesRegex(val.VersionError, "cannot compare"):
            val.plan(self.repo.root, self.repo.main, {"v0.1.10": "f" * 40}, NO_RETIRED)


class Apply(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in val.VERSION_SURFACES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"version 0.1.10; not 0.1.100, 10.1.10 or 0.1.10.5\n")
        (self.root / "CMakeLists.txt").write_text(CMAKE.format(v="0.1.10"))
        (self.root / "scripts/install_core.py").write_text("core\n")
        (self.root / val.INSTALLER_SUMS).write_text(f"{'0' * 64}  install_core.py\n{'0' * 64}  install.sh\n")

    def snapshot(self) -> dict[str, str]:
        return {str(p.relative_to(self.root)): p.read_text() for p in self.root.rglob("*") if p.is_file()}

    def test_every_surface_moves_and_the_installer_sums_follow(self) -> None:
        written = val.apply(self.root, "0.1.11")
        self.assertEqual(written, [*val.VERSION_SURFACES, val.INSTALLER_SUMS])
        self.assertEqual(val.project_version((self.root / "CMakeLists.txt").read_text()), "0.1.11")
        self.assertEqual((self.root / "README.md").read_text(),
                         "version 0.1.11; not 0.1.100, 10.1.10 or 0.1.10.5\n")
        expected = (f"{hashlib.sha256(b'core' + bytes([10])).hexdigest()}  install_core.py\n"
                    f"{hashlib.sha256((self.root / 'scripts/install.sh').read_bytes()).hexdigest()}  install.sh\n")
        self.assertEqual((self.root / val.INSTALLER_SUMS).read_text(), expected)

    def test_a_surface_without_the_version_refuses_and_writes_nothing(self) -> None:
        (self.root / "docs/cli/contract.md").write_text("no version here\n")
        before = self.snapshot()
        with self.assertRaisesRegex(val.VersionError, "docs/cli/contract.md does not state the current version"):
            val.apply(self.root, "0.1.11")
        self.assertEqual(self.snapshot(), before)

    def test_only_a_newer_version_is_written(self) -> None:
        for version in ("0.1.10", "0.1.9", "0.1", "v0.1.11"):
            with self.subTest(version=version), self.assertRaises(val.VersionError):
                val.apply(self.root, version)


class Repository(unittest.TestCase):
    def test_every_version_surface_states_the_current_version(self) -> None:
        # The surfaces apply rewrites must all name main's version today, or
        # the next bump refuses; this says so at the change that broke it.
        current = val.project_version((ROOT / "CMakeLists.txt").read_text())
        for relative in val.VERSION_SURFACES:
            with self.subTest(surface=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(text, val.TOKEN.format(current.replace(".", r"\.")))

    def test_a_bump_of_the_repository_surfaces_applies(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sums = (ROOT / val.INSTALLER_SUMS).read_text().split()
        for relative in {*val.VERSION_SURFACES, val.INSTALLER_SUMS, "scripts/install_core.py",
                         *(f"scripts/{name}" for name in sums[1::2])}:
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, root / relative)
        current = val.parse_version(val.project_version((ROOT / "CMakeLists.txt").read_text()))
        new = val.render(val.bumped(current, "patch"))
        val.apply(root, new)
        result = subprocess.run(["shasum", "-a", "256", "-c", "INSTALLER_SHA256SUMS"], cwd=root / "scripts",
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class Workflow(unittest.TestCase):
    """version-at-land.yml plans with no write token and pushes with one, in separate jobs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (ROOT / ".github/workflows/version-at-land.yml").read_text()
        _, _, jobs = cls.text.partition("\njobs:\n")
        cls.plan_job, _, cls.push_job = jobs.partition("\n  push:\n")

    def step(self, job: str, title: str) -> str:
        start = job.index(f"- name: {title}")
        following = job.find("\n      - name:", start + 1)
        return job[start:following if following != -1 else None]

    def test_only_main_is_counted_and_nothing_is_tagged(self) -> None:
        self.assertIn("on:\n  push:\n    branches: [main]\n", self.text)
        self.assertNotIn("pull_request", self.text.split("\njobs:\n")[0])
        self.assertNotIn("git tag", self.text)
        self.assertNotIn("environment:", self.text)
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read\n", self.text)
        self.assertIn(f"BUMP_BRANCH: {val.BUMP_BRANCH}\n", self.text)

    def test_the_plan_job_holds_no_secret_and_the_push_job_runs_no_python(self) -> None:
        self.assertNotIn("secrets.", self.plan_job)
        self.assertNotIn("outputs.token", self.plan_job)
        self.assertNotIn("python", self.push_job)
        self.assertIn("needs: plan", self.push_job)
        self.assertIn("ref: ${{ needs.plan.outputs.main }}", self.push_job)
        order = [self.push_job.index(f"- name: {name}") for name in (
            "Check out the planned main commit", "Fetch the planned bump", "Apply the planned bump",
            "Mint the bump token", "Push the bump branch and open its pull request")]
        self.assertEqual(order, sorted(order))
        apply = self.step(self.push_job, "Apply the planned bump")
        self.assertIn("-c core.hooksPath=/dev/null", apply)
        push = self.step(self.push_job, "Push the bump branch and open its pull request")
        self.assertIn("env -i PATH=/usr/bin:/bin", push)
        for command in push.split("\n"):
            if command.strip().startswith(("git ", "gh ")):
                self.fail(f"a token-holding command runs outside the clean environment: {command.strip()}")
        # main is only read, never pushed, by the bump job.
        self.assertNotIn(":refs/heads/main", push)

    def test_the_push_job_applies_only_version_surfaces(self) -> None:
        apply = self.step(self.push_job, "Apply the planned bump")
        listed = apply.split("allowed=(\n", 1)[1].split("\n          )", 1)[0].split()
        self.assertEqual(listed, [*val.VERSION_SURFACES, val.INSTALLER_SUMS])

    def test_the_planned_outputs_are_checked_before_use(self) -> None:
        decide = self.step(self.plan_job, "Decide the next version")
        self.assertIn('[[ "$version" =~ ^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$ ]]', decide)
        self.assertIn('[[ "$main" =~ ^[0-9a-f]{40}$ ]]', decide)
        self.assertIn('python3 -I scripts/version_at_land.py plan --commit "$main"', decide)
        write = self.step(self.plan_job, "Write the version bump")
        self.assertIn('python3 -I scripts/version_at_land.py apply --to "$VERSION"', write)
        self.assertIn(val.BOT_TRAILER, write)
        self.assertIn(f"user.email={val.BOT_EMAIL}", write)


class Parser(unittest.TestCase):
    def test_comments_never_declare_the_version(self) -> None:
        text = CMAKE.format(v="0.1.10")
        self.assertEqual(val.project_version(text), "0.1.10")
        self.assertEqual(val.project_version("# project(Vellum VERSION 9.9.9)\n" + text), "0.1.10")
        self.assertEqual(val.project_version("#[[\nproject(Vellum VERSION 9.9.9)\n]]\n" + text), "0.1.10")
        self.assertEqual(val.project_version(text.replace("LANGUAGES CXX", 'DESCRIPTION "a # b" LANGUAGES CXX')),
                         "0.1.10")
        with self.assertRaisesRegex(val.VersionError, "more than once"):
            val.project_version(text + "project(Vellum VERSION 9.9.9)\n")
        with self.assertRaisesRegex(val.VersionError, "no project VERSION"):
            val.project_version("# project(Vellum VERSION 0.1.10)\n")
        for bad in ("0.1", "0.1.10.1", "0.1.010", "0.1.10-rc1"):
            with self.subTest(bad=bad), self.assertRaises(val.VersionError):
                val.project_version(CMAKE.format(v=bad))


class VerifyBump(unittest.TestCase):
    """Only the bot's own bump, exactly as plan and apply would make it, is a release."""

    def setUp(self) -> None:
        saved = (val.VERSION_SURFACES, val.INSTALLER_SUMS)
        val.VERSION_SURFACES, val.INSTALLER_SUMS = ("CMakeLists.txt",), None
        self.addCleanup(lambda: setattr(val, "VERSION_SURFACES", saved[0]) or setattr(val, "INSTALLER_SUMS", saved[1]))
        self.repo = Repo(self)
        self.repo.tag("v0.1.10")
        self.repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"})

    def verify(self) -> str | None:
        return val.verify_bump(self.repo.root, self.repo.main, self.repo.tags, NO_RETIRED)

    def test_the_bots_exact_bump_is_a_release(self) -> None:
        self.repo.bot_bump("0.1.11")
        self.assertIsNone(self.verify())

    def test_a_human_pull_request_setting_9_0_0_is_refused(self) -> None:
        self.repo.land("release: 9.0.0", {"CMakeLists.txt": CMAKE.format(v="9.0.0")})
        self.assertIn("does not merge the automation/version-bump branch", self.verify())

    def test_a_human_commit_on_the_bot_branch_is_refused(self) -> None:
        self.repo.bot_bump("0.1.11", identity=("someone", "someone@example.com"))
        self.assertIn("not the version-at-land bot's commit", self.verify())

    def test_a_bot_pull_request_with_an_extra_hunk_is_refused(self) -> None:
        self.repo.bot_bump("0.1.11", extra={"foundation/src/core.cpp": "int smuggled;\n"})
        self.assertIn("beyond the rewrite", self.verify())

    def test_an_extra_hunk_in_a_version_surface_is_refused(self) -> None:
        self.repo.bot_bump("0.1.11", extra={"CMakeLists.txt": CMAKE.format(v="0.1.11") + "add_subdirectory(x)\n"})
        self.assertIn("differs from the rewrite", self.verify())

    def test_a_level_plan_would_not_choose_is_refused(self) -> None:
        self.repo.bot_bump("0.2.0")
        self.assertIn("derives 0.1.11", self.verify())

    def test_a_skipped_version_is_refused(self) -> None:
        self.repo.bot_bump("0.1.12")
        self.assertIn("derives 0.1.11", self.verify())

    def test_a_bump_with_nothing_shipped_is_refused(self) -> None:
        repo = Repo(self)
        repo.tag("v0.1.10")
        repo.land("docs: only", {"docs/x.md": "x\n"})
        repo.bot_bump("0.1.11")
        self.assertIn("derives none", val.verify_bump(repo.root, repo.main, repo.tags, NO_RETIRED))

    def test_a_bump_pull_request_of_more_than_one_commit_is_refused(self) -> None:
        self.repo.bot_bump("0.1.11", extra_commits=1)
        self.assertIn("is not one commit", self.verify())

    def test_a_bump_without_the_bot_trailer_is_refused(self) -> None:
        self.repo.bot_bump("0.1.11", body="")
        self.assertIn("lacks the version-at-land trailer", self.verify())

    def test_a_direct_commit_is_refused(self) -> None:
        self.repo.git("checkout", "-q", "--detach", self.repo.main)
        self.repo.write("CMakeLists.txt", CMAKE.format(v="0.1.11"))
        self.repo.git("commit", "-qam", "direct")
        self.repo.main = self.repo.git("rev-parse", "HEAD")
        self.assertIn("not a merge queue merge", self.verify())


class Cli(unittest.TestCase):
    def test_plan_prints_its_decision(self) -> None:
        repo = Repo(self)
        remote = repo.root.parent / (repo.root.name + "-remote.git")
        self.addCleanup(shutil.rmtree, remote, True)
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        repo.git("tag", "-a", "-m", "v0.1.10", "v0.1.10")
        repo.land("fix: correct the core", {"foundation/src/core.cpp": "int y;\n"})
        repo.write("provenance/retired-release-tags.json", json.dumps(NO_RETIRED))
        repo.git("add", "-A")
        repo.git("commit", "-q", "-m", "retired tags")
        repo.git("remote", "add", "origin", str(remote))
        repo.git("push", "-q", "origin", "HEAD:refs/heads/main", "refs/tags/v0.1.10")
        script = repo.root / "scripts/version_at_land.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts/version_at_land.py", script)
        head = repo.git("rev-parse", "HEAD")
        done = subprocess.run([sys.executable, "-I", str(script), "plan", "--commit", head], capture_output=True,
                              text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ["action=bump", "version=0.1.11", "level=patch",
                                                    "since=v0.1.10", "current=0.1.10"])
        self.assertIn("bump: patch from 1 landed change(s) since v0.1.10", done.stderr)
        failed = subprocess.run([sys.executable, "-I", str(script), "plan", "--commit", "abc"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("full SHA", failed.stderr)


if __name__ == "__main__":
    unittest.main()
