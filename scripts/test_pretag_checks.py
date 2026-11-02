#!/usr/bin/env python3
"""pretag_checks.py runs the tag pipeline's own content checks, and any failure refuses the tag."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pretag_checks as pc  # noqa: E402

COMMIT = "a" * 40
STEPS = {check.step for check in pc.CHECKS}


def fixture_workflows(failing: str | None = None) -> dict[str, str]:
    """Workflows with every checked step, each appending its name to a log; `failing` exits 1."""
    texts: dict[str, list[str]] = {}
    for check in pc.CHECKS:
        jobs = texts.setdefault(check.workflow, [])
        jobs.append((check.job, check))
    result = {}
    for workflow, rows in texts.items():
        lines = ["name: fixture", "on: push", "jobs:"]
        for job in dict.fromkeys(job for job, _ in rows):
            lines += [f"  {job}:", "    runs-on: x", "    steps:"]
            for _, check in [row for row in rows if row[0] == job]:
                step_id = {"Resolve the exact authority lifecycle phase": "authority-phase",
                           "Resolve the committed Pulp observatory cursor": "observatory-cursor"}.get(check.step)
                lines += [f"      - name: {check.step}"]
                if step_id:
                    lines += [f"        id: {step_id}"]
                lines += ["        env:", "          SEEN_REF: ${{ github.ref }}", "        run: |",
                          f"          printf '%s|%s|%s|%s|%s\\n' {check.step!r} \"$SEEN_REF\" \"$GITHUB_REF\" \"$GITHUB_REF_NAME\" \"$GITHUB_SHA\" >> \"$LOG\""]
                if step_id == "authority-phase":
                    lines += ["          echo phase=active >> \"$GITHUB_OUTPUT\""]
                if step_id == "observatory-cursor":
                    lines += ["          echo pulp-sha=${CURSOR} >> \"$GITHUB_OUTPUT\""]
                if check.step == failing:
                    lines += ["          exit 1"]
        result[workflow] = "\n".join(lines) + "\n"
    return result


class Runner(pc.Runner):
    """The real runner, with the tag signing and checkout checks replaced for a scratch directory."""

    def sign_local_tag(self) -> None:
        self.signed = True


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "log"
        self.temp = self.root / "temp"
        self.temp.mkdir()
        for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
                     ["commit", "-q", "--allow-empty", "-m", "c"]):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.commit = pc.git("rev-parse", "HEAD", cwd=self.root)
        pulp = self.root / ".observatory-pulp"
        subprocess.run(["git", "clone", "-q", str(self.root), str(pulp)], check=True, capture_output=True)
        self.cursor = pc.git("rev-parse", "HEAD", cwd=pulp)

    def runner(self, failing: str | None = None) -> Runner:
        (self.root / ".github/workflows").mkdir(parents=True, exist_ok=True)
        for name, text in fixture_workflows(failing).items():
            (self.root / ".github/workflows" / name).write_text(text)
        runner = Runner(self.root, "v0.1.10", self.commit, self.temp)
        runner.environment.update(LOG=str(self.log), CURSOR=self.cursor)
        # main() tags and untags: give it a tag to delete.
        pc.git("tag", "v0.1.10", cwd=self.root)
        return runner

    def ran(self) -> list[str]:
        return [line.split("|")[0] for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_every_check_runs_with_the_tag_environment_and_passes(self) -> None:
        runner = self.runner()
        self.assertEqual(runner.main(), 0)
        self.assertEqual(self.ran(), [check.step for check in pc.CHECKS])
        for line in self.log.read_text().splitlines():
            _, expression, ref, name, sha = line.split("|")
            self.assertEqual((expression, ref, name, sha),
                             ("refs/tags/v0.1.10", "refs/tags/v0.1.10", "v0.1.10", self.commit))
        self.assertTrue(runner.signed)
        self.assertEqual(pc.git("tag", "--list", "v0.1.10", cwd=self.root), "", "the local tag is deleted")

    def test_a_planted_readiness_failure_refuses_and_stops_every_later_check(self) -> None:
        runner = self.runner(failing="Test authority protocol and negative controls")
        self.assertEqual(runner.main(), 1)
        ran = self.ran()
        self.assertEqual(ran[-1], "Test authority protocol and negative controls")
        self.assertNotIn("Verify two-way observatory health and negative controls", ran)
        self.assertEqual(pc.git("tag", "--list", "v0.1.10", cwd=self.root), "", "the local tag is deleted")

    def test_any_failing_check_refuses(self) -> None:
        for check in pc.CHECKS:
            with self.subTest(check.step):
                self.log.unlink(missing_ok=True)
                self.assertEqual(self.runner(failing=check.step).main(), 1)
                self.assertEqual(self.ran()[-1], check.step)

    def test_the_observatory_checkout_must_be_the_cursor(self) -> None:
        runner = self.runner()
        runner.environment["CURSOR"] = "b" * 40
        with self.assertRaisesRegex(pc.PretagError, "not the cursor"):
            runner.main()

    def test_a_skipped_condition_is_the_step_own_if(self) -> None:
        runner = self.runner()
        runner.environment["LOG"] = str(self.log)
        text = (self.root / ".github/workflows/provenance.yml").read_text().replace(
            "echo phase=active", "echo phase=tagged-pending")
        (self.root / ".github/workflows/provenance.yml").write_text(text)
        self.assertEqual(runner.main(), 0)
        self.assertNotIn("Verify active repository documentation", self.ran())


class Coverage(unittest.TestCase):
    def test_every_tag_only_content_check_is_listed(self) -> None:
        # Each of these can refuse a v* tag on the tagged commit's content and
        # runs only on the tag; dropping one reopens the v0.1.9 failure.
        required = {
            ("gpu-macos.yml", "Derive the release identity from CMakeLists.txt"),
            ("provenance.yml", "Resolve exact-boundary and signed-tag promotion evidence"),
            ("provenance.yml", "Test authority protocol and negative controls"),
            ("provenance.yml", "Verify two-way observatory health and negative controls"),
            ("sdk-release.yml", "Fetch and verify the pinned Pulp view-audio archive"),
            ("sdk-release.yml", "Require renderer release eligibility at the release source"),
        }
        self.assertLessEqual(required, {(check.workflow, check.step) for check in pc.CHECKS})


class Reader(unittest.TestCase):
    def test_every_check_reads_from_the_real_workflows(self) -> None:
        workflows = Path(pc.ROOT / ".github/workflows")
        for check in pc.CHECKS:
            with self.subTest(check.step):
                step = pc.read_step((workflows / check.workflow).read_text(), check.job, check.step)
                self.assertTrue(step.run.strip())
                self.assertFalse(step.run.startswith(" "), "the block is dedented")

    def test_the_content_checks_are_the_tag_specific_ones(self) -> None:
        provenance = (pc.ROOT / ".github/workflows/provenance.yml").read_text()
        readiness = pc.read_step(provenance, "provenance-verify", "Test authority protocol and negative controls")
        self.assertIn('if [[ "$GITHUB_REF" == refs/tags/v[0-9]* ]]; then', readiness.run)
        self.assertIn("release_readiness+=(--release-readiness)", readiness.run)
        self.assertIn("open-pr-snapshot", readiness.run)
        self.assertEqual(readiness.env["PROMOTION_ATTESTATION_PATH"],
                         "${{ steps.expansion-evidence.outputs.promotion-path }}")
        observatory = pc.read_step(provenance, "provenance-verify",
                                   "Verify two-way observatory health and negative controls")
        self.assertIn("observatory_tail_policy=reconciled", observatory.run)
        gpu = (pc.ROOT / ".github/workflows/gpu-macos.yml").read_text()
        identity = pc.read_step(gpu, "gpu-macos-arm64", "Derive the release identity from CMakeLists.txt")
        self.assertIn('"$GITHUB_REF_NAME" != "v$version"', identity.run)
        sdk = (pc.ROOT / ".github/workflows/sdk-release.yml").read_text()
        renderer = pc.read_step(sdk, "finalize-sdk-release",
                                "Require renderer release eligibility at the release source")
        self.assertIn("--require-release-eligible", renderer.run)

    def test_the_reader_agrees_with_a_yaml_parser(self) -> None:
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            self.skipTest("PyYAML is not installed; the content assertions above still run")
        for check in pc.CHECKS:
            with self.subTest(check.step):
                document = yaml.safe_load((pc.ROOT / ".github/workflows" / check.workflow).read_text())
                expected = next(step for step in document["jobs"][check.job]["steps"]
                                if step.get("name") == check.step)
                step = pc.read_step((pc.ROOT / ".github/workflows" / check.workflow).read_text(),
                                    check.job, check.step)
                self.assertEqual(step.run, expected["run"])
                self.assertEqual(step.env, {key: str(value) for key, value in (expected.get("env") or {}).items()})
                self.assertEqual(step.id, expected.get("id"))

    def test_an_unknown_step_job_or_expression_refuses(self) -> None:
        text = (pc.ROOT / ".github/workflows/provenance.yml").read_text()
        with self.assertRaisesRegex(pc.PretagError, "has no step"):
            pc.read_step(text, "provenance-verify", "No such step")
        with self.assertRaisesRegex(pc.PretagError, "not found"):
            pc.read_step(text, "no-such-job", "x")
        with self.assertRaisesRegex(pc.PretagError, "cannot resolve"):
            pc.resolve("${{ secrets.X }}", {}, "step")
        self.assertEqual(pc.read_step("jobs:\n  j:\n    steps:\n      - name: s\n        run: echo hi\n",
                                      "j", "s").run, "echo hi")
        for value in ('"echo hi"', "> folded", "|- kept", "echo ${{ x }}"):
            flow = f"jobs:\n  j:\n    steps:\n      - name: s\n        run: {value}\n"
            with self.subTest(value=value), self.assertRaisesRegex(pc.PretagError, "neither"):
                pc.read_step(flow, "j", "s")

    def test_a_malformed_tag_or_commit_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for tag, commit in (("0.1.10", COMMIT), ("v0.1.10", "abc")):
                with self.subTest(tag=tag, commit=commit), self.assertRaises(pc.PretagError):
                    pc.Runner(Path(temporary), tag, commit, Path(temporary))


if __name__ == "__main__":
    unittest.main()
