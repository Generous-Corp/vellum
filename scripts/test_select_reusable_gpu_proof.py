#!/usr/bin/env python3
"""Tests for the merge-queue GPU proof reuse selector and its workflow wiring."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gpu-macos.yml"
SCRIPT = Path(__file__).with_name("select_reusable_gpu_proof.py")
SPEC = importlib.util.spec_from_file_location("select_reusable_gpu_proof", SCRIPT)
assert SPEC and SPEC.loader
selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selector)


REPO = "Generous-Corp/vellum"
HEAD = "a" * 40
TREE = "b" * 40
RUN_ID = 41000
ATTEMPT = 2


def receipt_zip(**changes) -> bytes:
    value = {
        "schema": selector.RECEIPT_SCHEMA,
        "event": "pull_request",
        "repository": REPO,
        "run_id": RUN_ID,
        "run_attempt": ATTEMPT,
        "commit": "c" * 40,
        "tree": TREE,
    }
    value.update(changes)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(selector.RECEIPT_FILE, json.dumps(value))
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self) -> None:
        self.runs = [{
            "id": RUN_ID,
            "run_attempt": ATTEMPT,
            "event": "pull_request",
            "path": selector.WORKFLOW_PATH,
            "head_sha": HEAD,
            "head_repository": {"full_name": REPO},
            "status": "completed",
            "conclusion": "success",
        }]
        self.jobs = {RUN_ID: [
            {"name": name, "conclusion": "success", "run_attempt": ATTEMPT}
            for name in selector.PROOF_JOBS
        ]}
        self.artifacts = {RUN_ID: [{
            "id": 7, "name": selector.RECEIPT_ARTIFACT, "expired": False,
            "workflow_run": {"id": RUN_ID},
        }]}
        self.archives = {7: receipt_zip()}

    def api(self, path: str):
        if "/actions/workflows/gpu-macos.yml/runs?" in path:
            assert f"head_sha={HEAD}" in path and "event=pull_request" in path
            return {"workflow_runs": self.runs}
        match = re.search(r"/actions/runs/(\d+)/(jobs|artifacts)\?", path)
        assert match, path
        run_id = int(match.group(1))
        if match.group(2) == "jobs":
            return {"jobs": self.jobs.get(run_id, [])}
        return {"artifacts": self.artifacts.get(run_id, [])}

    def download(self, path: str) -> bytes:
        match = re.search(r"/actions/artifacts/(\d+)/zip$", path)
        assert match, path
        return self.archives[int(match.group(1))]

    def select(self) -> int:
        return selector.select(
            repository=REPO, tree=TREE, head_sha=HEAD,
            api=self.api, download=self.download,
        )


class SelectorTests(unittest.TestCase):
    def assert_declines(self, github: FakeGitHub, fragment: str) -> None:
        with self.assertRaises(selector.Decline) as caught:
            github.select()
        self.assertIn(fragment, str(caught.exception))

    def test_reuses_a_successful_same_tree_pull_request_proof(self) -> None:
        self.assertEqual(FakeGitHub().select(), RUN_ID)

    def test_a_different_tree_is_never_reused(self) -> None:
        github = FakeGitHub()
        github.archives[7] = receipt_zip(tree="d" * 40)
        self.assert_declines(github, "receipt tree")

    def test_fork_heads_failed_or_unfinished_runs_are_refused(self) -> None:
        cases = {
            "fork": ({"head_repository": {"full_name": "someone/vellum"}}, "outside"),
            "no head repository": ({"head_repository": None}, "outside"),
            "failure": ({"conclusion": "failure"}, "completed/failure"),
            "in progress": ({"status": "in_progress", "conclusion": None}, "in_progress"),
            "merge queue run": ({"event": "merge_group"}, "merge_group run"),
            "other workflow": ({"path": ".github/workflows/other.yml"}, "other.yml"),
            "other head": ({"head_sha": "e" * 40}, "ran head"),
            "boolean id": ({"id": True}, "invalid ID"),
        }
        for name, (change, fragment) in cases.items():
            with self.subTest(case=name):
                github = FakeGitHub()
                github.runs[0].update(change)
                self.assert_declines(github, fragment)

    def test_both_proof_jobs_must_succeed_in_the_latest_attempt(self) -> None:
        for name in selector.PROOF_JOBS:
            for change, fragment in (
                ({"conclusion": "skipped"}, "skipped"),
                ({"conclusion": "failure"}, "failure"),
                ({"run_attempt": ATTEMPT - 1}, "attempt 1"),
            ):
                with self.subTest(job=name, change=change):
                    github = FakeGitHub()
                    for job in github.jobs[RUN_ID]:
                        if job["name"] == name:
                            job.update(change)
                    self.assert_declines(github, fragment)
            with self.subTest(job=name, change="missing"):
                github = FakeGitHub()
                github.jobs[RUN_ID] = [
                    job for job in github.jobs[RUN_ID] if job["name"] != name
                ]
                self.assert_declines(github, f"0 {name} jobs")
            with self.subTest(job=name, change="duplicated"):
                github = FakeGitHub()
                github.jobs[RUN_ID].append(dict(github.jobs[RUN_ID][0], name=name))
                self.assert_declines(github, f"2 {name} jobs")

    def test_receipt_must_come_from_this_run_and_attempt(self) -> None:
        cases = {
            "no artifact": (lambda g: g.artifacts.update({RUN_ID: []}), "no tree receipt"),
            "expired": (lambda g: g.artifacts[RUN_ID][0].update(expired=True), "no tree receipt"),
            "other run's artifact": (
                lambda g: g.artifacts[RUN_ID][0].update(workflow_run={"id": 1}),
                "no tree receipt",
            ),
            "earlier attempt only": (
                lambda g: g.archives.update({7: receipt_zip(run_attempt=ATTEMPT - 1)}),
                "no tree receipt",
            ),
            "other run id": (lambda g: g.archives.update({7: receipt_zip(run_id=1)}), "run_id"),
            "other repository": (
                lambda g: g.archives.update({7: receipt_zip(repository="x/y")}), "repository",
            ),
            "other event": (
                lambda g: g.archives.update({7: receipt_zip(event="merge_group")}), "event",
            ),
            "other schema": (lambda g: g.archives.update({7: receipt_zip(schema="v0")}), "schema"),
        }
        for name, (mutate, fragment) in cases.items():
            with self.subTest(case=name):
                github = FakeGitHub()
                mutate(github)
                self.assert_declines(github, fragment)

    def test_a_receipt_archive_with_extra_files_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(selector.RECEIPT_FILE, "{}")
            bundle.writestr("other.json", "{}")
        github = FakeGitHub()
        github.archives[7] = buffer.getvalue()
        self.assert_declines(github, "receipt artifact holds")

    def test_the_newest_matching_run_wins_and_older_failures_do_not_block(self) -> None:
        github = FakeGitHub()
        older = copy.deepcopy(github.runs[0])
        older.update(id=RUN_ID - 5, conclusion="failure")
        github.runs.insert(0, older)
        self.assertEqual(github.select(), RUN_ID)

    def test_no_runs_declines(self) -> None:
        github = FakeGitHub()
        github.runs = []
        self.assert_declines(github, "no pull_request run")


class QueueCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")
        (self.root / "base").write_text("base\n")
        self.git("add", "base")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "topic")
        (self.root / "topic").write_text("topic\n")
        self.git("add", "topic")
        self.git("commit", "-q", "-m", "topic")
        self.topic = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        (self.root / "main").write_text("main\n")
        self.git("add", "main")
        self.git("commit", "-q", "-m", "main")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True, text=True, capture_output=True,
        ).stdout.strip()

    def test_merge_commit_yields_its_tree_and_second_parent(self) -> None:
        self.git("merge", "-q", "--no-ff", "-m", "queue", "topic")
        head = self.git("rev-parse", "HEAD")
        tree, pull_head = selector.queue_commit(self.root, head)
        self.assertEqual(tree, self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(pull_head, self.topic)

    def test_a_non_merge_or_mismatched_head_declines(self) -> None:
        head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(selector.Decline, "1 parents"):
            selector.queue_commit(self.root, head)
        with self.assertRaisesRegex(selector.Decline, "is not the merge-group commit"):
            selector.queue_commit(self.root, "f" * 40)
        with self.assertRaisesRegex(selector.Decline, "full lowercase SHA"):
            selector.queue_commit(self.root, "HEAD")

    def test_main_declines_with_exit_3_and_prints_nothing(self) -> None:
        head = self.git("rev-parse", "HEAD")
        stdout = io.StringIO()
        from contextlib import redirect_stderr, redirect_stdout
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            status = selector.main([
                "--repository", REPO, "--merge-group-sha", head, "--root", str(self.root),
            ])
        self.assertEqual(status, selector.NO_REUSE)
        self.assertEqual(stdout.getvalue(), "")


def job_steps(text: str, job: str) -> list[tuple[str, str]]:
    """Return (name, if-expression) for every step of one job."""
    body = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text, re.M | re.S)
    assert body, job
    steps = []
    for block in re.split(r"^      - ", body.group(1), flags=re.M)[1:]:
        name = re.match(r"name: (.*)", block)
        condition = re.search(r"^        if: (.*)$", block, re.M)
        steps.append((name.group(1) if name else "", condition.group(1) if condition else ""))
    return steps


GPU_GATE = "steps.reuse.outputs.run_id == ''"
# test_ci_runner_policy.py holds this step to a byte-exact canonical text
# shared with the release finalizer, so it carries no condition. It only
# fetches and verifies a digest-pinned archive, so a reused run still passes it
# or fails closed.
UNGATED_GPU_STEPS = {"Fetch and verify the pinned Pulp view-audio archive"}
STERILE_GATE = "needs.gpu-macos-arm64.outputs.reused_run == ''"


def assert_reuse_wiring(test: unittest.TestCase, text: str) -> None:
    steps = job_steps(text, "gpu-macos-arm64")
    names = [name for name, _ in steps]
    reuse = names.index("Reuse an identical-tree pull-request proof")
    test.assertEqual(dict(steps)[names[reuse]], "github.event_name == 'merge_group'")
    # Checkout and the release identity precede the decision; nothing else.
    test.assertEqual(reuse, 2)
    for name, condition in steps[reuse + 1:]:
        if name in UNGATED_GPU_STEPS:
            test.assertEqual(condition, "", name)
            continue
        # No subTest here: a mutation test must see this failure raised.
        test.assertNotIn("||", condition, f"gpu-macos-arm64 {name!r}")
        test.assertTrue(
            condition.startswith(f"{GPU_GATE} && ") or condition in (
                GPU_GATE, f"always() && {GPU_GATE}"
            ),
            f"gpu-macos-arm64 {name!r} runs in a reused queue run: if: {condition!r}",
        )
    receipt = dict(steps)["Record the tree this pull-request proof covered"]
    test.assertEqual(receipt, f"{GPU_GATE} && github.event_name == 'pull_request'")
    test.assertGreater(
        names.index("Record the tree this pull-request proof covered"),
        names.index("Prove installed HTML import through DesignIR and GPU capture"),
    )
    test.assertIn(
        "reused_run: ${{ steps.reuse.outputs.run_id }}", text,
    )
    sterile = job_steps(text, "sterile-consumer")
    test.assertEqual(
        sterile[0],
        ("Report the reused sterile-consumer proof",
         "needs.gpu-macos-arm64.outputs.reused_run != ''"),
    )
    for name, condition in sterile[1:]:
        test.assertTrue(
            condition in (STERILE_GATE, f"always() && {STERILE_GATE}"),
            f"sterile-consumer {name!r} runs in a reused queue run: if: {condition!r}",
        )


class WorkflowWiringTests(unittest.TestCase):
    def test_every_proof_step_is_skipped_only_by_a_reuse_decision(self) -> None:
        assert_reuse_wiring(self, WORKFLOW.read_text(encoding="utf-8"))

    def test_wiring_mutations_fail(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        mutations = {
            "ungated build": text.replace(
                "      - name: Build the release GPU slice\n        if: steps.reuse.outputs.run_id == ''\n",
                "      - name: Build the release GPU slice\n", 1,
            ),
            "reuse on pull requests": text.replace(
                "        if: github.event_name == 'merge_group'\n",
                "        if: github.event_name != 'push'\n", 1,
            ),
            "ungated sterile journey": text.replace(
                "      - name: Run the complete installed journey without a framework checkout\n"
                f"        if: {STERILE_GATE}\n",
                "      - name: Run the complete installed journey without a framework checkout\n", 1,
            ),
            "receipt on every event": text.replace(
                f"        if: {GPU_GATE} && github.event_name == 'pull_request'\n"
                "        shell: bash",
                f"        if: {GPU_GATE}\n        shell: bash", 1,
            ),
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                self.assertNotEqual(mutated, text)
                with self.assertRaises(AssertionError):
                    assert_reuse_wiring(self, mutated)

    def test_the_proof_jobs_are_never_skipped_by_job_condition(self) -> None:
        # A job skipped by its own `if` reports a passing required check, so
        # the reuse decision must stay at step level.
        text = WORKFLOW.read_text(encoding="utf-8")
        for job in selector.PROOF_JOBS:
            body = re.search(rf"^  {job}:\n(.*?)\n    steps:", text, re.M | re.S).group(1)
            self.assertNotIn("reuse", body.split("outputs:")[0], job)


if __name__ == "__main__":
    unittest.main()
