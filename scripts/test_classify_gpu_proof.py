#!/usr/bin/env python3
"""Tests for the GPU proof classifier and the workflow steps that run it."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gpu-macos.yml"
SCRIPT = Path(__file__).with_name("classify_gpu_proof.py")
SPEC = importlib.util.spec_from_file_location("classify_gpu_proof", SCRIPT)
assert SPEC and SPEC.loader
classifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classifier)

CURSOR = "provenance/pulp-observatory/cursor.json"
REPORT = "provenance/pulp-observatory/reports/current.json"
EVENT = "provenance/pulp-observatory/events/vellum-abc.yaml"
SDK = "graphics/src/canvas.cpp"


class ClassifierTests(unittest.TestCase):
    def test_observatory_evidence_alone_needs_no_proof(self) -> None:
        required, _ = classifier.classify([CURSOR, REPORT, EVENT])
        self.assertFalse(required)

    def test_anything_else_needs_the_proof(self) -> None:
        for paths in (
            [],
            [CURSOR, SDK],
            ["provenance/pulp-observatory/provenance.lock"],
            ["provenance/pulp-observatory/events/nested/x.yaml"],
            ["provenance/pulp-observatory/events/x.txt"],
            ["provenance/pulp-observatory/events/x.json"],
            ["provenance/pulp-observatory/README.md"],
            ["README.md"],
            ["scripts/classify_gpu_proof.py"],
            ["scripts/test_classify_gpu_proof.py"],
            [".github/workflows/gpu-macos.yml", CURSOR],
            ["provenance/pulp-observatory/reports/../../../graphics/x.cpp"],
            ["/provenance/pulp-observatory/cursor.json"],
            [""],
        ):
            with self.subTest(paths=paths):
                required, reason = classifier.classify(paths)
                self.assertTrue(required, reason)

    def test_cli_reads_nul_separated_paths_and_prints_one_word(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listing = Path(directory) / "paths"
            for paths, expected in (([CURSOR, EVENT], "not-required"), ([CURSOR, SDK], "required"),
                                    ([], "required")):
                listing.write_bytes(b"".join(p.encode() + b"\0" for p in paths))
                result = subprocess.run(
                    ["python3", "-I", str(SCRIPT), "--files0-from", str(listing)],
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(result.stdout, expected + "\n")


def step_script(text: str, name: str) -> str:
    block = text.split(f"      - name: {name}\n", 1)[1]
    body = block.split("        run: |\n", 1)[1]
    lines = []
    for line in body.splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines).strip() + "\n"


CLASSIFY_STEP = "Classify the change against the base's rules"
GUARD_STEP = "Prove a skipped proof was allowed"


class WorkflowStepTests(unittest.TestCase):
    """Drive the workflow's own step text against real merge commits."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t"),
                           ("commit.gpgsign", "false")):
            self.git("config", key, value)
        self.write("scripts/classify_gpu_proof.py", SCRIPT.read_text())
        self.write(SDK, "int x;\n")
        self.write(CURSOR, "{}\n")
        self.commit("base")
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def git(self, *arguments: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *arguments],
                              check=True, text=True, capture_output=True).stdout.strip()

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def merge(self, changes: dict[str, str]) -> str:
        """A pull request with these changes, merged as GitHub's merge ref is."""
        self.git("checkout", "-q", "-b", "topic")
        for path, content in changes.items():
            self.write(path, content)
        self.commit("topic")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "topic")
        return self.git("rev-parse", "HEAD")

    def run_step(self, name: str, sha: str, event: str = "pull_request", queue_base: str = ""):
        temp = Path(self.directory.name) / f"runner-{name[:8]}"
        temp.mkdir(exist_ok=True)
        output = temp / "github_output"
        output.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-c", step_script(self.text, name)],
            cwd=self.root, capture_output=True, text=True,
            env=dict(os.environ, GITHUB_SHA=sha, GITHUB_OUTPUT=str(output),
                     GITHUB_EVENT_NAME=event, RUNNER_TEMP=str(temp), QUEUE_BASE=queue_base),
        )
        return result, output.read_text()

    def test_evidence_only_change_is_unaffected_and_the_guard_agrees(self) -> None:
        sha = self.merge({CURSOR: '{"v": 2}\n', EVENT: "{}\n"})
        result, output = self.run_step(CLASSIFY_STEP, sha)
        self.assertEqual(output, "unaffected=true\n", result.stderr)
        guard, _ = self.run_step(GUARD_STEP, sha)
        self.assertEqual(guard.returncode, 0, guard.stderr)

    def test_a_pr_that_widens_the_allowlist_and_changes_sdk_code_still_needs_the_proof(self) -> None:
        widened = SCRIPT.read_text().replace(
            "def classify(paths: list[str]) -> tuple[bool, str]:\n",
            "def classify(paths: list[str]) -> tuple[bool, str]:\n"
            "    return False, 'widened'\n",
        )
        self.assertNotEqual(widened, SCRIPT.read_text())
        sha = self.merge({"scripts/classify_gpu_proof.py": widened, SDK: "int y;\n"})
        result, output = self.run_step(CLASSIFY_STEP, sha)
        self.assertEqual(output, "", result.stdout + result.stderr)

    def test_a_pr_that_only_edits_the_classifier_needs_the_proof(self) -> None:
        sha = self.merge({"scripts/classify_gpu_proof.py": SCRIPT.read_text() + "\n# edit\n"})
        _, output = self.run_step(CLASSIFY_STEP, sha)
        self.assertEqual(output, "")

    def test_a_base_without_the_classifier_needs_the_proof(self) -> None:
        self.git("rm", "-q", "scripts/classify_gpu_proof.py")
        self.commit("no classifier")
        sha = self.merge({CURSOR: '{"v": 3}\n'})
        _, output = self.run_step(CLASSIFY_STEP, sha)
        self.assertEqual(output, "")

    def test_the_guard_fails_a_skip_the_base_does_not_allow(self) -> None:
        # The negative control: a run that skipped the proof for an SDK change
        # (for example through a wiring mistake) must go red.
        sha = self.merge({SDK: "int z;\n"})
        guard, _ = self.run_step(GUARD_STEP, sha)
        self.assertNotEqual(guard.returncode, 0)
        self.assertIn("the base's classifier says: required", guard.stdout + guard.stderr)
        evidence = self.merge_again({CURSOR: '{"v": 4}\n'})
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                guard, _ = self.run_step(GUARD_STEP, evidence, event=event)
                self.assertNotEqual(guard.returncode, 0)

    def test_a_queue_batch_is_judged_against_main_not_the_entry_ahead(self) -> None:
        # Entry A changes SDK code; entry B, queued behind it, changes only
        # evidence. B's queue commit's first parent is A's, so judging B
        # against its first parent would let A land unproven.
        main = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "queue")
        self.write(SDK, "int a;\n")
        self.commit("entry A")
        self.write(CURSOR, '{"v": 5}\n')
        self.commit("entry B")
        tip = self.git("rev-parse", "HEAD")
        _, output = self.run_step(CLASSIFY_STEP, tip, event="merge_group", queue_base=main)
        self.assertEqual(output, "")
        # The control: the same tip judged against its first parent only.
        _, control = self.run_step(CLASSIFY_STEP, tip, event="pull_request")
        self.assertEqual(control, "unaffected=true\n")
        guard, _ = self.run_step(GUARD_STEP, tip, event="merge_group", queue_base=main)
        self.assertNotEqual(guard.returncode, 0)
        # A queue run without its base never skips.
        _, missing = self.run_step(CLASSIFY_STEP, tip, event="merge_group")
        self.assertEqual(missing, "")
        guard, _ = self.run_step(GUARD_STEP, tip, event="merge_group")
        self.assertNotEqual(guard.returncode, 0)

    def test_moving_sdk_code_into_the_allowlist_needs_the_proof(self) -> None:
        self.git("checkout", "-q", "-b", "topic")
        (self.root / "provenance/pulp-observatory/reports").mkdir(parents=True, exist_ok=True)
        self.git("mv", SDK, "provenance/pulp-observatory/reports/canvas.cpp")
        self.commit("rename")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "topic")
        _, output = self.run_step(CLASSIFY_STEP, self.git("rev-parse", "HEAD"))
        self.assertEqual(output, "")

    def merge_again(self, changes: dict[str, str]) -> str:
        self.git("branch", "-q", "-D", "topic")
        return self.merge(changes)

    def test_every_proof_step_waits_for_the_classifier(self) -> None:
        import re
        def steps(job: str):
            body = re.search(rf"^  {job}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", self.text, re.M | re.S)
            for block in re.split(r"^      - ", body.group(1), flags=re.M)[1:]:
                name = re.match(r"name: (.*)", block).group(1)
                condition = re.search(r"^        if: (.*)$", block, re.M)
                yield name, condition.group(1) if condition else ""
        gpu = list(steps("gpu-macos-arm64"))
        names = [name for name, _ in gpu]
        start = names.index(CLASSIFY_STEP) + 1
        # The canonical view-audio fetch is pinned byte for byte by
        # test_ci_runner_policy.py and reads only a digest-pinned archive; the
        # re-check runs only with a reused proof, which an unaffected run never has.
        exempt = {"Fetch and verify the pinned Pulp view-audio archive",
                  "Re-check the external state a reused proof read"}
        for name, condition in gpu[start:]:
            if name in exempt:
                continue
            self.assertIn("steps.classify.outputs.unaffected", condition, name)
        for name, condition in steps("sterile-consumer"):
            if name == "Report the reused sterile-consumer proof":
                continue
            self.assertIn("needs.gpu-macos-arm64.outputs.unaffected", condition, name)

    def test_the_steps_run_only_where_the_workflow_says(self) -> None:
        self.assertIn(
            f"      - name: {CLASSIFY_STEP}\n        id: classify\n"
            "        if: github.event_name == 'pull_request' || github.event_name == 'merge_group'\n",
            self.text,
        )
        self.assertIn(
            f"      - name: {GUARD_STEP}\n"
            "        if: always() && steps.classify.outputs.unaffected == 'true'\n",
            self.text,
        )
        self.assertIn("unaffected: ${{ steps.classify.outputs.unaffected }}", self.text)


if __name__ == "__main__":
    unittest.main()
