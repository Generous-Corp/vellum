#!/usr/bin/env python3
"""Tests for the GPU proof classifier and the workflow steps that run it."""

from __future__ import annotations

import importlib.util
import os
import re
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


# What each real defect changed, from the week's pull-request proofs whose only
# catch was the GPU: the early full proof must keep running for each of them.
GPU_ONLY_DEFECTS = {
    "html-import GPU capture": [
        "apps/app-host/CMakeLists.txt", "apps/app-host/test_html_import_geometry.py",
        "cli/vellum_web_backend.py", "packages/vellum-ui/src/design.js", "scripts/ci_lanes.json"],
    "retained-surface partial present": [
        "apps/app-host/main.mm", "docs/architecture/gpu-boundary.md",
        "graphics/src/skia_dawn_surface.mm", "graphics/tests/partial_present_test.cpp"],
    "graphics tests registered in CI": [
        "CMakeLists.txt", "graphics/CMakeLists.txt", "tools/mirror/VellumMirror.cmake"],
    "graphics tests registered in CI, damage": [
        "CMakeLists.txt", "graphics/CMakeLists.txt", "graphics/src/damage.cpp"],
    "render cache": [
        "graphics/include/vellum/graphics/damage.hpp", "graphics/src/render_cache.hpp",
        "graphics/tests/render_cache_gpu_test.cpp", "packages/vellum-ui/src/runtime.js"],
}


class EarlyProofTests(unittest.TestCase):
    def test_each_gpu_only_defect_runs_the_early_proof(self) -> None:
        for name, paths in GPU_ONLY_DEFECTS.items():
            with self.subTest(defect=name):
                early, reason = classifier.early_proof(paths)
                self.assertTrue(early, reason)

    def test_changes_the_cheap_lane_covers_stay_cheap(self) -> None:
        for paths in (
            ["docs/ci/local-first-actions.md"],
            ["scripts/ci_lanes.json", "scripts/test_gates.py"],
            ["framework/core/platform/src/alert.cpp", "provenance/framework/ports.json"],
            ["tools/provenance/observatory.py", ".github/workflows/provenance.yml"],
            ["packages/vellum-ui/src/design.js", "cli/vellum_web_backend.py"],
            ["CMakeLists.txt", "cmake/VellumTestClasses.cmake"],
        ):
            with self.subTest(paths=paths):
                early, reason = classifier.early_proof(paths)
                self.assertFalse(early, reason)

    def test_every_doubt_runs_the_early_proof(self) -> None:
        for paths in (
            [], [""], ["/graphics/x.cpp"], ["docs/../graphics/x.cpp"],
            ["scripts/classify_gpu_proof.py"], ["scripts/test_classify_gpu_proof.py"],
            [".github/workflows/gpu-macos.yml"],
            ["docs/a.md", "gpu-runtime/renderer-lock.json"],
            ["rmlui/src/soft_raster.hpp"], ["provenance/third-party-lock.json"],
            ["graphics/shaders/blur.wgsl"],
        ):
            with self.subTest(paths=paths):
                early, reason = classifier.early_proof(paths)
                self.assertTrue(early, reason)

    def test_cli_prints_one_word_and_an_old_base_exits_two(self) -> None:
        for paths, expected in ((["docs/a.md"], "cheap"), ([SDK], "early"), ([], "early")):
            result = subprocess.run(["python3", "-I", str(SCRIPT), "--early-proof", *paths],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, expected + "\n")
        # The verdict that gates the full proof is unchanged by the new flag.
        result = subprocess.run(["python3", "-I", str(SCRIPT), SDK],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "required\n")


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
SKIP_GUARD_STEP = "Prove a skipped job was allowed"


class StepHarness(unittest.TestCase):
    """Drive the workflow's own step text against real merge commits."""

    workflow = WORKFLOW
    guard_step = GUARD_STEP

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
        self.text = self.workflow.read_text(encoding="utf-8")

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

    def merge_again(self, changes: dict[str, str]) -> str:
        self.git("branch", "-q", "-D", "topic")
        return self.merge(changes)


class WorkflowStepTests(StepHarness):
    def test_evidence_only_change_is_unaffected_and_the_guard_agrees(self) -> None:
        sha = self.merge({CURSOR: '{"v": 2}\n', EVENT: "{}\n"})
        result, output = self.run_step(CLASSIFY_STEP, sha)
        self.assertEqual(output, "unaffected=true\n", result.stderr)
        guard, _ = self.run_step(self.guard_step, sha)
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
        guard, _ = self.run_step(self.guard_step, sha)
        self.assertNotEqual(guard.returncode, 0)
        self.assertIn("the base's classifier says: required", guard.stdout + guard.stderr)
        evidence = self.merge_again({CURSOR: '{"v": 4}\n'})
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                guard, _ = self.run_step(self.guard_step, evidence, event=event)
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
        guard, _ = self.run_step(self.guard_step, tip, event="merge_group", queue_base=main)
        self.assertNotEqual(guard.returncode, 0)
        # A queue run without its base never skips.
        _, missing = self.run_step(CLASSIFY_STEP, tip, event="merge_group")
        self.assertEqual(missing, "")
        guard, _ = self.run_step(self.guard_step, tip, event="merge_group")
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

DEPTH_STEP = "Choose the pull-request proof depth"
CHEAP_GUARD_STEP = "Prove the cheap lane was allowed"


class CheapLaneStepTests(StepHarness):
    """The depth step and its guard, run against real merge commits with a stubbed `gh`."""

    def run_depth(self, sha: str, labels: str | None = "", mode: str = "", payload: str = "[]",
                  event: str = "pull_request", classifier_flag: bool = True):
        temp = Path(self.directory.name) / "depth"
        temp.mkdir(exist_ok=True)
        output = temp / "github_output"
        output.write_text("")
        bin_dir = temp / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh = bin_dir / "gh"
        # labels None: the API is unreachable and the payload's labels are read.
        gh.write_text("#!/bin/sh\n" + ("exit 1\n" if labels is None else f"printf '%s' '{labels}'\n"))
        gh.chmod(0o755)
        result = subprocess.run(
            ["bash", "-e", "-c", step_script(self.text, DEPTH_STEP)],
            cwd=self.root, capture_output=True, text=True,
            env=dict(os.environ, GITHUB_SHA=sha, GITHUB_OUTPUT=str(output), GITHUB_EVENT_NAME=event,
                     GITHUB_REPOSITORY="o/r", RUNNER_TEMP=str(temp), PR_NUMBER="7", GH_TOKEN="x",
                     PAYLOAD_LABELS=payload, PROOF_MODE=mode, PATH=f"{bin_dir}:{os.environ['PATH']}"),
        )
        return result, output.read_text()

    def test_a_pull_request_the_classifier_clears_takes_the_cheap_lane(self) -> None:
        sha = self.merge({"docs/note.md": "x\n"})
        result, output = self.run_depth(sha)
        self.assertEqual(output, "cheap=true\n", result.stdout + result.stderr)
        guard, _ = self.run_step(CHEAP_GUARD_STEP, sha)
        self.assertEqual(guard.returncode, 0, guard.stderr)

    def test_each_reason_for_the_early_full_proof_is_honoured(self) -> None:
        docs = self.merge({"docs/note.md": "x\n"})
        for name, kwargs in (
            ("the rollback variable", {"mode": "full"}),
            ("the gpu-proof label", {"labels": "bug\ngpu-proof\n"}),
            ("the gpu-proof label from the event payload", {"labels": None, "payload": '["gpu-proof"]'}),
            ("an unreadable label list", {"labels": None, "payload": "not json"}),
        ):
            with self.subTest(reason=name):
                result, output = self.run_depth(docs, **kwargs)
                self.assertEqual(output, "", result.stdout + result.stderr)
                self.assertIn("Running the full GPU proof now", result.stdout)
        other = self.run_depth(docs, labels="bug\nsuperseded\n")[1]
        self.assertEqual(other, "cheap=true\n")
        fallback = self.run_depth(docs, labels=None, payload='["bug"]')[1]
        self.assertEqual(fallback, "cheap=true\n")

    def test_a_gpu_relevant_change_runs_the_early_full_proof(self) -> None:
        sha = self.merge({SDK: "int changed;\n"})
        result, output = self.run_depth(sha)
        self.assertEqual(output, "", result.stdout + result.stderr)
        self.assertIn("the base's classifier says: early", result.stdout)

    def test_a_base_without_the_flag_runs_the_early_full_proof(self) -> None:
        # A base from before the flag rejects it with exit 2.
        self.write("scripts/classify_gpu_proof.py", "import sys\nprint('usage', file=sys.stderr)\nsys.exit(2)\n")
        self.commit("old classifier")
        sha = self.merge({"docs/note.md": "x\n"})
        result, output = self.run_depth(sha)
        self.assertEqual(output, "", result.stdout + result.stderr)
        self.assertIn("unavailable", result.stdout)

    def test_a_change_to_the_classifier_never_takes_the_cheap_lane(self) -> None:
        sha = self.merge({"scripts/classify_gpu_proof.py": SCRIPT.read_text() + "\n# edit\n"})
        self.assertEqual(self.run_depth(sha)[1], "")

    def test_the_guard_refuses_the_cheap_lane_outside_a_pull_request_or_for_a_gpu_change(self) -> None:
        docs = self.merge({"docs/note.md": "x\n"})
        for event in ("merge_group", "push", "workflow_dispatch"):
            with self.subTest(event=event):
                guard, _ = self.run_step(CHEAP_GUARD_STEP, docs, event=event)
                self.assertNotEqual(guard.returncode, 0)
                self.assertIn("may not take the cheap lane", guard.stdout)
        gpu = self.merge_again({SDK: "int w;\n"})
        guard, _ = self.run_step(CHEAP_GUARD_STEP, gpu)
        self.assertNotEqual(guard.returncode, 0)
        self.assertIn("the base's classifier says: early", guard.stdout)


try:
    import yaml
except ImportError:  # the isolated lane has no PyYAML; the text-level wiring tests still run
    yaml = None


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class CheapLaneWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.steps = {step["name"]: step for step in self.workflow["jobs"]["gpu-macos-arm64"]["steps"]}

    def test_the_required_contexts_keep_their_names(self) -> None:
        self.assertEqual(sorted(self.workflow["jobs"]),
                         ["cancel-destroyed-groups", "gpu-macos-arm64", "sterile-consumer"])
        for job in ("gpu-macos-arm64", "sterile-consumer"):
            self.assertEqual(self.workflow["jobs"][job]["name"], job)

    def test_the_depth_step_runs_only_on_pull_requests(self) -> None:
        condition = self.steps[DEPTH_STEP]["if"]
        self.assertIn("github.event_name == 'pull_request'", condition)
        self.assertNotIn("merge_group", condition)
        self.assertEqual(self.steps[DEPTH_STEP]["id"], "depth")

    # The steps the cheap lane runs; every other proof step must skip when it is chosen.
    CHEAP_KEEPS = {
        CLASSIFY_STEP, DEPTH_STEP, "Derive the release identity from CMakeLists.txt",
        "Check out full preserved Vellum history without credentials",
        "Reuse an identical-tree proof", "Re-check the external state a reused proof read",
        "Size builds to this runner", "Download and verify the locked Skia and Dawn archive",
        "Set up the authoring toolchain", "Verify the exact release source contract",
        "Build the installed TypeScript and JSX Phase 3 fixture",
        "Fetch and verify the pinned Pulp view-audio archive",
        "Build the cheap pull-request slice", "Run the pr-fast tests", CHEAP_GUARD_STEP,
        "Prove a skipped proof was allowed", "Record the classifier-skip equivalence receipt",
        "Upload the classifier-skip equivalence receipt",
    }

    def test_every_other_proof_step_skips_in_the_cheap_lane(self) -> None:
        for name, step in self.steps.items():
            if name in self.CHEAP_KEEPS:
                continue
            with self.subTest(step=name):
                self.assertIn("steps.depth.outputs.cheap != 'true'", step.get("if", ""))

    def test_the_cheap_lane_runs_the_labelled_tests_and_leaves_no_reusable_receipt(self) -> None:
        self.assertIn("ctest --test-dir build-cheap -L pr-fast", self.steps["Run the pr-fast tests"]["run"])
        self.assertIn("-DVELLUM_REQUIRE_GPU=ON", self.steps["Build the cheap pull-request slice"]["run"])
        # select_reusable_gpu_proof.py needs this receipt; a cheap run must never write it.
        for name in ("Record the tree this pull-request proof covered", "Upload the proven-tree receipt"):
            self.assertIn("steps.depth.outputs.cheap != 'true'", self.steps[name]["if"])

    def test_the_verified_release_source_step_stays_in_the_cheap_lane(self) -> None:
        step = self.steps["Verify the exact release source contract"]
        self.assertNotIn("depth", step["if"])
        self.assertIn("--lane release-source", step["run"])

    def test_sterile_consumer_only_reports_a_cheap_run(self) -> None:
        job = self.workflow["jobs"]["sterile-consumer"]
        self.assertIn("outputs.cheap == 'true'", job["runs-on"])
        for step in job["steps"]:
            condition = step.get("if", "")
            if step["name"].startswith(("Report", "Install the pinned", "Export", "Download", "Record",
                                        "Verify", "Demonstrate", "Run the complete", "Retain")):
                if step["name"] in ("Report the cheap pull-request lane",):
                    self.assertIn("outputs.cheap == 'true'", condition)
                elif "reused" not in step["name"] and "does not cover" not in step["name"]:
                    self.assertIn("outputs.cheap != 'true'", condition, step["name"])


class ForbiddenDepsStepTests(WorkflowStepTests):
    """An evidence-only change skips forbidden-deps by the same classifier, and only then."""

    workflow = ROOT / ".github" / "workflows" / "provenance.yml"
    guard_step = SKIP_GUARD_STEP
    # The GPU workflow's own step layout; the static tests below cover these jobs.
    test_every_proof_step_waits_for_the_classifier = None
    test_the_steps_run_only_where_the_workflow_says = None


class CleanReleaseStepTests(WorkflowStepTests):
    """The README clean-release job skips on evidence only, by the same classifier."""

    workflow = ROOT / ".github" / "workflows" / "readme-quick-start.yml"
    guard_step = SKIP_GUARD_STEP
    # The GPU workflow's own step layout; the static tests below cover these jobs.
    test_every_proof_step_waits_for_the_classifier = None
    test_the_steps_run_only_where_the_workflow_says = None


class SkippingJobsStillReportTests(unittest.TestCase):
    """A skipped job must finish green, so no job is skipped as a whole and every step is gated."""

    GATE = "steps.classify.outputs.unaffected != 'true'"

    def job_steps(self, workflow: str, job: str) -> tuple[str, list[str]]:
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        block = text.split(f"\n  {job}:\n", 1)[1]
        header, steps = block.split("    steps:\n", 1)
        steps = steps.split("\n  provenance-verify:\n", 1)[0] if job == "forbidden-deps" else steps
        return header, re.split(r"(?m)^(?=      - )", steps)[1:]

    def test_the_job_has_no_job_level_condition(self) -> None:
        for workflow, job in (("provenance.yml", "forbidden-deps"), ("readme-quick-start.yml", "clean-release")):
            header, _ = self.job_steps(workflow, job)
            with self.subTest(job=job):
                self.assertNotIn("\n    if:", header)

    def test_every_step_but_the_classifier_and_its_guard_is_gated_on_the_verdict(self) -> None:
        for workflow, job in (("provenance.yml", "forbidden-deps"), ("readme-quick-start.yml", "clean-release")):
            _, steps = self.job_steps(workflow, job)
            for step in steps:
                name = re.search(r"- name: (.*)", step)
                label = name.group(1) if name else step.splitlines()[0]
                if label in (CLASSIFY_STEP, SKIP_GUARD_STEP) or "uses: actions/checkout" in step:
                    continue
                with self.subTest(job=job, step=label):
                    condition = re.search(r"(?m)^        if: (.*)$", step)
                    self.assertIsNotNone(condition)
                    gated = self.GATE in condition.group(1)
                    # clean-release's other steps only run outside pull requests and queue builds.
                    outside = "github.event_name != 'pull_request'" in condition.group(1)
                    self.assertTrue(gated or outside, condition.group(1))

    def test_the_skip_never_applies_to_pushes_tags_or_dispatches(self) -> None:
        for workflow in ("provenance.yml", "readme-quick-start.yml"):
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            block = text.split(f"- name: {CLASSIFY_STEP}\n", 1)[1].split("shell:", 1)[0]
            with self.subTest(workflow=workflow):
                self.assertIn("if: github.event_name == 'pull_request' || github.event_name == 'merge_group'", block)


if __name__ == "__main__":
    unittest.main()
