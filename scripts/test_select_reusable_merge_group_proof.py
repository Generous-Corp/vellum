#!/usr/bin/env python3
"""Tests for the push-to-main reuse of the merge-queue GPU proof."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gpu-macos.yml"
SCRIPT = Path(__file__).with_name("select_reusable_merge_group_proof.py")
SPEC = importlib.util.spec_from_file_location("select_reusable_merge_group_proof", SCRIPT)
assert SPEC and SPEC.loader
selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selector)

REPO = "Generous-Corp/vellum"
SHA = "a" * 40
RUN_ID = 41000
ATTEMPT = 1
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)


def steps(*names_and_conclusions):
    return [{"name": name, "conclusion": conclusion} for name, conclusion in names_and_conclusions]


BUILT = steps(
    ("Set up job", "success"), (selector.BUILT_STEP, "success"),
    (selector.REUSED_STEP, "skipped"), (selector.CLASSIFIER_SKIP_STEP, "skipped"),
)
REUSED = steps(
    (selector.BUILT_STEP, "skipped"), (selector.REUSED_STEP, "success"),
    (selector.CLASSIFIER_SKIP_STEP, "skipped"),
)
CLASSIFIER_SKIP = steps(
    (selector.BUILT_STEP, "skipped"), (selector.REUSED_STEP, "skipped"),
    (selector.CLASSIFIER_SKIP_STEP, "success"),
)


class FakeGitHub:
    def __init__(self) -> None:
        self.runs = [{
            "id": RUN_ID, "run_attempt": ATTEMPT, "event": "merge_group",
            "path": selector.WORKFLOW_PATH, "head_sha": SHA,
            "head_branch": "gh-readonly-queue/main/pr-9-" + "b" * 40,
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "status": "completed", "conclusion": "success",
            "run_started_at": "2026-11-14T11:00:00Z",
        }]
        self.jobs = {RUN_ID: [
            {"name": "gpu-macos-arm64", "conclusion": "success", "run_attempt": ATTEMPT,
             "head_sha": SHA, "steps": copy.deepcopy(BUILT)},
            {"name": "sterile-consumer", "conclusion": "success", "run_attempt": ATTEMPT,
             "head_sha": SHA, "steps": []},
        ]}
        self.queries: list[str] = []

    def api(self, path: str):
        self.queries.append(path)
        if "/actions/workflows/gpu-macos.yml/runs?" in path:
            assert "event=merge_group" in path and "status=success" in path
            assert f"head_sha={SHA}" in path
            return {"workflow_runs": self.runs}
        match = re.search(r"/actions/runs/(\d+)/jobs", path)
        if match:
            return {"jobs": self.jobs[int(match.group(1))]}
        raise AssertionError(path)

    def select(self, **options):
        return selector.select(repository=REPO, sha=SHA, api=self.api, now=lambda: NOW, **options)


class SelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github = FakeGitHub()

    def test_reuses_a_successful_queue_proof_of_this_exact_commit(self) -> None:
        self.assertEqual(self.github.select()[:2], (RUN_ID, "built"))

    def test_a_queue_run_that_itself_reused_a_pull_request_proof_counts(self) -> None:
        self.github.jobs[RUN_ID][0]["steps"] = copy.deepcopy(REUSED)
        self.assertEqual(self.github.select()[:2], (RUN_ID, "reused"))

    def test_a_classifier_skip_needs_an_equivalence_check_that_passes(self) -> None:
        self.github.jobs[RUN_ID][0]["steps"] = copy.deepcopy(CLASSIFIER_SKIP)
        with self.assertRaises(selector.Decline):
            self.github.select()
        calls = []
        result = self.github.select(equivalence=lambda run, attempt: calls.append((run, attempt)) or ["proved"])
        self.assertEqual(result[:2], (RUN_ID, "classifier-skip"))
        self.assertEqual(calls, [(RUN_ID, ATTEMPT)])
        self.assertIn("proved", result[2])

        def refuse(run, attempt):
            raise selector.Decline("different tree")
        with self.assertRaises(selector.Decline):
            self.github.select(equivalence=refuse)

    def test_a_built_or_reused_proof_never_consults_the_equivalence_check(self) -> None:
        def explode(run, attempt):
            raise AssertionError("consulted")
        self.assertEqual(self.github.select(equivalence=explode)[:2], (RUN_ID, "built"))

    def test_a_proof_job_that_shows_no_proof_is_refused(self) -> None:
        for shape in ([], steps(("Set up job", "success")),
                      steps((selector.BUILT_STEP, "failure")),
                      steps((selector.BUILT_STEP, "success"), (selector.REUSED_STEP, "success")),
                      steps((selector.BUILT_STEP, "success"), (selector.CLASSIFIER_SKIP_STEP, "success"))):
            with self.subTest(shape=shape):
                self.github.jobs[RUN_ID][0]["steps"] = shape
                with self.assertRaises(selector.Decline):
                    self.github.select(equivalence=lambda run, attempt: [])

    def test_every_disqualifying_run_field_is_refused(self) -> None:
        changes = {
            "event": "pull_request", "path": ".github/workflows/other.yml",
            "head_sha": "c" * 40, "head_branch": "main",
            "status": "in_progress", "conclusion": "failure",
            "run_started_at": "2026-11-13T11:00:00Z",
            "repository": {"full_name": "someone/vellum"},
            "head_repository": {"full_name": "someone/vellum"},
            "run_attempt": True, "id": "41000",
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                github = FakeGitHub()
                github.runs[0][key] = value
                with self.assertRaises(selector.Decline):
                    github.select()

    def test_a_future_or_missing_start_time_is_refused(self) -> None:
        for value in ("2026-11-14T13:00:00Z", None, "yesterday"):
            with self.subTest(value=value):
                github = FakeGitHub()
                github.runs[0]["run_started_at"] = value
                with self.assertRaises(selector.Decline):
                    github.select()

    def test_exactly_the_age_bound_is_still_reused(self) -> None:
        start = NOW - selector.MAX_AGE
        self.github.runs[0]["run_started_at"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self.github.select()[:2], (RUN_ID, "built"))

    def test_both_proof_jobs_must_succeed_in_the_latest_attempt_on_this_commit(self) -> None:
        mutations = [
            lambda jobs: jobs[0].update(conclusion="skipped"),
            lambda jobs: jobs[1].update(conclusion="failure"),
            lambda jobs: jobs[1].update(run_attempt=ATTEMPT + 1),
            lambda jobs: jobs[0].update(head_sha="c" * 40),
            lambda jobs: jobs.pop(1),
            lambda jobs: jobs.append(copy.deepcopy(jobs[1])),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                github = FakeGitHub()
                mutate(github.jobs[RUN_ID])
                with self.assertRaises(selector.Decline):
                    github.select()

    def test_the_newest_qualifying_run_wins_and_older_failures_do_not_block(self) -> None:
        newer = copy.deepcopy(self.github.runs[0])
        newer["id"] = RUN_ID + 1
        newer["event"] = "pull_request"
        self.github.runs.append(newer)
        self.github.jobs[RUN_ID + 1] = self.github.jobs[RUN_ID]
        self.assertEqual(self.github.select()[:2], (RUN_ID, "built"))

    def test_no_runs_declines(self) -> None:
        self.github.runs = []
        with self.assertRaises(selector.Decline):
            self.github.select()

    def test_a_api_error_propagates_so_main_runs_the_full_proof(self) -> None:
        def broken(path: str):
            raise RuntimeError("API down")
        with self.assertRaises(RuntimeError):
            selector.select(repository=REPO, sha=SHA, api=broken, now=lambda: NOW)


CLASSIFIER_SOURCE = (ROOT / "scripts" / "classify_gpu_proof.py").read_bytes()
EVIDENCE_PATH = "provenance/pulp-observatory/cursor.json"
PARITY_PATH = selector.PUSH_ONLY_RECEIPT_TRIGGERS[0]


def run_git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *arguments],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def commit_files(root: Path, files: dict[str, bytes | None], message: str) -> str:
    for name, data in files.items():
        path = root / name
        if data is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", message)
    return run_git(root, "rev-parse", "HEAD")


class EquivalenceTests(unittest.TestCase):
    """verify_equivalence on real git history with a recorded receipt."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        run_git(self.root, "init", "-q")
        self.before = commit_files(self.root, {
            selector.CLASSIFIER_PATH: CLASSIFIER_SOURCE, EVIDENCE_PATH: b"{}\n",
        }, "base")
        self.sha = commit_files(self.root, {EVIDENCE_PATH: b'{"n": 1}\n'}, "evidence")
        self.receipt = self.good_receipt()

    def good_receipt(self) -> dict:
        changed = subprocess.run(
            ["git", "-C", str(self.root), "diff", "--name-only", "-z", "--no-renames",
             self.before, self.sha], check=True, capture_output=True).stdout
        return {
            "schema": selector.SKIP_RECEIPT_SCHEMA, "event": "merge_group", "repository": REPO,
            "run_id": RUN_ID, "run_attempt": ATTEMPT, "commit": self.sha,
            "tree": run_git(self.root, "rev-parse", self.sha + "^{tree}"),
            "base_sha": self.before,
            "classifier_blob": run_git(self.root, "rev-parse", f"{self.before}:{selector.CLASSIFIER_PATH}"),
            "classifier_config": "embedded-in-classifier",
            "changed_paths_sha256": hashlib.sha256(changed).hexdigest(),
            "verdict": "not-required", "required_contexts": list(selector.PROOF_JOBS),
        }

    def archive(self, receipt: dict, name: str = selector.SKIP_RECEIPT_FILE) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(name, json.dumps(receipt))
        return buffer.getvalue()

    def verify(self, *, receipt=None, archive=None, artifacts=None, sha=None, before=None):
        sha = self.sha if sha is None else sha
        body = archive if archive is not None else self.archive(self.receipt if receipt is None else receipt)
        listing = artifacts if artifacts is not None else [{
            "id": 7, "name": selector.SKIP_RECEIPT_ARTIFACT, "expired": False,
            "workflow_run": {"id": RUN_ID},
        }]
        return selector.verify_equivalence(
            root=self.root, repository=REPO, sha=sha, before=self.before if before is None else before,
            api=lambda path: {"artifacts": listing}, download=lambda path: body,
            run_id=RUN_ID, attempt=ATTEMPT,
        )

    def test_a_matching_receipt_and_classification_is_proved_equivalent(self) -> None:
        evidence = "\n".join(self.verify())
        for fact in (self.receipt["tree"], self.before, self.receipt["classifier_blob"],
                     self.receipt["changed_paths_sha256"], "not-required"):
            self.assertIn(fact, evidence)

    def test_every_receipt_field_must_match(self) -> None:
        wrong = {
            "schema": "vellum.other.v1", "event": "pull_request", "repository": "x/y",
            "run_id": RUN_ID + 1, "run_attempt": ATTEMPT + 1, "commit": "c" * 40,
            "tree": "d" * 40, "base_sha": "e" * 40, "classifier_blob": "f" * 40,
            "classifier_config": "file", "changed_paths_sha256": "0" * 64,
            "verdict": "required", "required_contexts": ["gpu-macos-arm64"],
        }
        for key, value in wrong.items():
            with self.subTest(field=key):
                with self.assertRaises(selector.Decline):
                    self.verify(receipt={**self.receipt, key: value})
        for key in self.receipt:
            with self.subTest(missing=key):
                partial = {k: v for k, v in self.receipt.items() if k != key}
                with self.assertRaises(selector.Decline):
                    self.verify(receipt=partial)

    def test_a_boolean_does_not_stand_in_for_an_integer(self) -> None:
        with self.assertRaises(selector.Decline):
            self.verify(receipt={**self.receipt, "run_attempt": True})

    def test_a_different_tree_is_refused(self) -> None:
        other = commit_files(self.root, {EVIDENCE_PATH: b'{"n": 2}\n'}, "later")
        with self.assertRaises(selector.Decline):
            self.verify(sha=other)

    def test_a_base_that_is_not_the_receipts_is_refused(self) -> None:
        middle = commit_files(self.root, {"unrelated.txt": b"x"}, "main moved")
        with self.assertRaises(selector.Decline):
            self.verify(before=middle)

    def test_a_different_classifier_is_refused(self) -> None:
        widened = CLASSIFIER_SOURCE + b"\n# edited\n"
        before = commit_files(self.root, {selector.CLASSIFIER_PATH: widened}, "edit classifier")
        sha = commit_files(self.root, {EVIDENCE_PATH: b'{"n": 3}\n'}, "evidence again")
        receipt = {**self.receipt, "commit": sha, "base_sha": before,
                   "tree": run_git(self.root, "rev-parse", sha + "^{tree}")}
        with self.assertRaises(selector.Decline):
            self.verify(sha=sha, before=before, receipt=receipt)

    def test_a_classifier_that_now_requires_the_proof_is_refused(self) -> None:
        # The receipt agrees with git, but the change touches a proof input.
        sha = commit_files(self.root, {"src/render.cpp": b"x"}, "source")
        changed = subprocess.run(
            ["git", "-C", str(self.root), "diff", "--name-only", "-z", "--no-renames",
             self.before, sha], check=True, capture_output=True).stdout
        receipt = {**self.receipt, "commit": sha,
                   "tree": run_git(self.root, "rev-parse", sha + "^{tree}"),
                   "changed_paths_sha256": hashlib.sha256(changed).hexdigest()}
        with self.assertRaises(selector.Decline) as raised:
            self.verify(sha=sha, receipt=receipt)
        self.assertIn("classifier now says", str(raised.exception))

    def test_an_unusable_receipt_artifact_is_refused(self) -> None:
        good = {"id": 7, "name": selector.SKIP_RECEIPT_ARTIFACT, "expired": False,
                "workflow_run": {"id": RUN_ID}}
        cases = {
            "none": [],
            "two": [good, {**good, "id": 8}],
            "expired": [{**good, "expired": True}],
            "another run": [{**good, "workflow_run": {"id": RUN_ID + 1}}],
            "another name": [{**good, "name": "other"}],
        }
        for name, artifacts in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(selector.Decline):
                    self.verify(artifacts=artifacts)
        with self.assertRaises(selector.Decline):
            self.verify(archive=self.archive(self.receipt, name="other.json"))
        with self.assertRaises(selector.Decline):
            self.verify(archive=self.archive([1]))

    def test_an_unreadable_or_zero_commit_is_refused(self) -> None:
        for before in ("0" * 40, "", "main", "g" * 40):
            with self.subTest(before=before):
                with self.assertRaises(selector.Decline):
                    self.verify(before=before)

    def test_a_push_that_changes_nothing_is_refused(self) -> None:
        with self.assertRaises(selector.Decline):
            self.verify(sha=self.before, before=self.before)

    def test_a_tree_the_push_runs_the_parity_receipt_for_is_refused_for_any_kind(self) -> None:
        selector.require_no_push_only_receipt(self.root, self.sha)
        sha = commit_files(self.root, {PARITY_PATH: b"{}\n"}, "parity completion")
        with self.assertRaises(selector.Decline):
            selector.require_no_push_only_receipt(self.root, sha)

    def test_main_writes_the_evidence_and_prints_only_the_run_id(self) -> None:
        github = FakeGitHub()
        github.runs[0]["head_sha"] = self.sha
        for job in github.jobs[RUN_ID]:
            job["head_sha"] = self.sha
        github.jobs[RUN_ID][0]["steps"] = copy.deepcopy(CLASSIFIER_SKIP)
        summary = self.root / "summary.md"
        listing = {"artifacts": [{"id": 7, "name": selector.SKIP_RECEIPT_ARTIFACT,
                                  "expired": False, "workflow_run": {"id": RUN_ID}}]}
        archive = self.archive(self.receipt)
        original = (selector.gh_api, selector.gh_download, selector.datetime)

        def api(path: str):
            if "/artifacts" in path:
                return listing
            if "/actions/workflows/gpu-macos.yml/runs?" in path:
                return {"workflow_runs": github.runs}
            return {"jobs": github.jobs[RUN_ID]}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        selector.gh_api, selector.gh_download, selector.datetime = api, lambda path: archive, Clock
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = selector.main([
                    "--repository", REPO, "--sha", self.sha, "--before", self.before,
                    "--root", str(self.root), "--summary", str(summary)])
        finally:
            selector.gh_api, selector.gh_download, selector.datetime = original
        self.assertEqual(status, 0, err.getvalue())
        self.assertEqual(out.getvalue(), f"{RUN_ID}\n")
        text = summary.read_text()
        self.assertIn("classifier-skip", text)
        self.assertIn(self.receipt["classifier_blob"], text)

    def test_main_declines_a_classifier_skip_without_a_before_sha(self) -> None:
        github = FakeGitHub()
        github.runs[0]["head_sha"] = self.sha
        for job in github.jobs[RUN_ID]:
            job["head_sha"] = self.sha
        github.jobs[RUN_ID][0]["steps"] = copy.deepcopy(CLASSIFIER_SKIP)
        original = (selector.gh_api, selector.gh_download, selector.datetime)

        def api(path: str):
            if "/actions/workflows/gpu-macos.yml/runs?" in path:
                return {"workflow_runs": github.runs}
            return {"jobs": github.jobs[RUN_ID]}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        selector.gh_api, selector.datetime = api, Clock
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = selector.main(["--repository", REPO, "--sha", self.sha,
                                        "--root", str(self.root)])
        finally:
            selector.gh_api, selector.gh_download, selector.datetime = original
        self.assertEqual((status, out.getvalue()), (selector.NO_REUSE, ""))


class MainTests(unittest.TestCase):
    def repo(self) -> tuple[tempfile.TemporaryDirectory, str]:
        directory = tempfile.TemporaryDirectory()
        subprocess.run(["git", "init", "-q", directory.name], check=True)
        subprocess.run(
            ["git", "-C", directory.name, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        head = subprocess.run(["git", "-C", directory.name, "rev-parse", "HEAD"],
                              check=True, text=True, capture_output=True).stdout.strip()
        return directory, head

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = selector.main(argv)
        return status, out.getvalue()

    def test_a_head_that_is_not_the_pushed_commit_declines_with_exit_3(self) -> None:
        directory, _ = self.repo()
        with directory:
            status, stdout = self.run_main(
                ["--repository", REPO, "--sha", "d" * 40, "--root", directory.name])
        self.assertEqual((status, stdout), (selector.NO_REUSE, ""))

    def test_a_malformed_sha_declines(self) -> None:
        directory, _ = self.repo()
        with directory:
            status, stdout = self.run_main(
                ["--repository", REPO, "--sha", "HEAD", "--root", directory.name])
        self.assertEqual((status, stdout), (selector.NO_REUSE, ""))

    def test_an_unreachable_api_declines_with_exit_3(self) -> None:
        directory, head = self.repo()
        original = selector.gh_api
        selector.gh_api = lambda path: (_ for _ in ()).throw(OSError("offline"))
        try:
            with directory:
                status, stdout = self.run_main(
                    ["--repository", REPO, "--sha", head, "--root", directory.name])
        finally:
            selector.gh_api = original
        self.assertEqual((status, stdout), (selector.NO_REUSE, ""))


class WorkflowTests(unittest.TestCase):
    def test_push_reuse_is_limited_to_main_and_release_refs_are_never_offered_it(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        block = text.split("      - name: Reuse an identical-tree proof\n", 1)[1].split("\n\n", 1)[0]
        condition = re.search(r"^        if: (.*)$", block, re.M).group(1)
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", condition)
        self.assertNotIn("refs/tags", condition)
        self.assertIn("select_reusable_merge_group_proof.py", block)
        self.assertIn('--before "${EVENT_BEFORE:-}"', block)
        self.assertIn("EVENT_BEFORE: ${{ github.event.before }}", block)
        self.assertNotIn("--allow-classifier-skip", text)

    def test_the_queue_run_records_the_receipt_only_for_a_classifier_skip(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for name in ("Record the classifier-skip equivalence receipt",
                     "Upload the classifier-skip equivalence receipt"):
            block = text.split(f"      - name: {name}\n", 1)[1].split("\n\n", 1)[0]
            self.assertIn(
                "if: steps.classify.outputs.unaffected == 'true' && github.event_name == 'merge_group'",
                block, name)
        self.assertIn(f"name: {selector.SKIP_RECEIPT_ARTIFACT}", text)
        self.assertIn(selector.SKIP_RECEIPT_FILE, text)
        self.assertIn(selector.SKIP_RECEIPT_SCHEMA, text)

    def test_the_receipt_the_workflow_writes_passes_the_selector_check(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        block = text.split(
            "      - name: Record the classifier-skip equivalence receipt\n", 1)[1]
        body = block.split("        run: |\n", 1)[1]
        lines = []
        for line in body.splitlines():
            if line and not line.startswith("          "):
                break
            lines.append(line[10:])
        script = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            temp = Path(directory) / "temp"
            root.mkdir()
            temp.mkdir()
            run_git(root, "init", "-q")
            before = commit_files(root, {
                selector.CLASSIFIER_PATH: CLASSIFIER_SOURCE, EVIDENCE_PATH: b"{}\n"}, "base")
            sha = commit_files(root, {EVIDENCE_PATH: b'{"n": 1}\n'}, "evidence")
            changed = subprocess.run(
                ["git", "-C", str(root), "diff", "--name-only", "-z", "--no-renames", before, sha],
                check=True, capture_output=True).stdout
            (temp / "guard-paths").write_bytes(changed)
            environment = dict(
                os.environ, QUEUE_BASE=before, GITHUB_SHA=sha, GITHUB_EVENT_NAME="merge_group",
                GITHUB_REPOSITORY=REPO, GITHUB_RUN_ID=str(RUN_ID),
                GITHUB_RUN_ATTEMPT=str(ATTEMPT), RUNNER_TEMP=str(temp),
                GITHUB_STEP_SUMMARY=str(temp / "summary.md"),
            )
            completed = subprocess.run(
                ["bash", "-e", "-c", script], cwd=root, env=environment,
                capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt_path = temp / "gpu-classifier-skip" / selector.SKIP_RECEIPT_FILE
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                bundle.writestr(selector.SKIP_RECEIPT_FILE, receipt_path.read_text())
            evidence = selector.verify_equivalence(
                root=root, repository=REPO, sha=sha, before=before,
                api=lambda path: {"artifacts": [{
                    "id": 7, "name": selector.SKIP_RECEIPT_ARTIFACT, "expired": False,
                    "workflow_run": {"id": RUN_ID}}]},
                download=lambda path: buffer.getvalue(), run_id=RUN_ID, attempt=ATTEMPT,
            )
            self.assertTrue(evidence)

    def test_the_workflow_token_can_read_actions(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^permissions:\n  actions: read\n")

    def test_the_proof_jobs_still_report_on_a_push(self) -> None:
        # release-tag.yml reads the newest check run of each required context
        # on the landed commit; a job skipped by its own condition would read
        # as unmet there. Neither proof job may gain a push-skipping if:.
        text = WORKFLOW.read_text(encoding="utf-8")
        for job in ("gpu-macos-arm64", "sterile-consumer"):
            header = re.search(rf"^  {job}:\n(.*?)\n    runs-on:", text, re.M | re.S).group(1)
            self.assertNotIn("'push'", header, job)


if __name__ == "__main__":
    unittest.main()
