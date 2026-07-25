#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

EXPECTED_RUNNERS = {
    "authority-activation.yml": (
        "VELLUM_AUTHORITY_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-authority-linux"]',
    ),
    "authority-release.yml": (
        "VELLUM_AUTHORITY_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-authority-linux"]',
    ),
    "gpu-macos.yml": (
        "VELLUM_MACOS_RUNS_ON_JSON",
        '["self-hosted","macOS","ARM64","vellum-build-macos"]',
    ),
    "merge-on-green.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-build-linux"]',
    ),
    "product-quality.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-build-linux"]',
    ),
    "provenance.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-build-linux"]',
    ),
    "readme-quick-start.yml": (
        "VELLUM_MACOS_RUNS_ON_JSON",
        '["self-hosted","macOS","ARM64","vellum-build-macos"]',
    ),
}

HOSTED_LABELS = (
    "ubuntu-latest",
    "windows-latest",
    "macos-latest",
    "macos-13",
    "macos-14",
    "macos-15",
    "macos-26",
)

NODE24_ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    "actions/create-github-app-token": "1b10c78c7865c340bc4f6099eb2f838309f1e8c3",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",  # v8.0.1
    "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",  # v7.0.0
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # v7.0.1
    "browser-actions/setup-chrome": "2e1d749697dd1612b833dba4a722266286fbefcd",  # v2.1.2
}
ACTION_USE = re.compile(r"^\s*-?\s*uses:\s+([^./\s][^@\s]+)@([0-9a-f]{40})\s*$")

CHROME_ACTION = "browser-actions/setup-chrome"

# Jobs that drive a browser fixture. Job environments do not inherit, so each
# one must provision the pinned browser itself rather than rely on a browser
# happening to exist on the runner.
BROWSER_JOBS = {
    "gpu-macos.yml": ("gpu-macos-arm64", "sterile-consumer"),
}

JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def workflow_jobs(text: str) -> dict[str, str]:
    """Split the `jobs:` mapping into per-job bodies.

    Raises on any two-space entry it cannot recognize as a job header. Silently
    folding an unrecognized header into the previous job would attribute that
    job's steps to its neighbour, which would let a provisioning assertion pass
    for a job that does not provision anything.
    """
    lines = text.splitlines()
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    inside = False
    for number, line in enumerate(lines, start=1):
        if line.rstrip() == "jobs:":
            inside = True
            continue
        if not inside:
            continue
        if line.strip() and not line.startswith(" "):
            break
        header = JOB_HEADER.match(line)
        if header:
            current = header.group(1)
            jobs[current] = []
            continue
        # A two-space entry that is not a recognized header would corrupt the
        # split. Job bodies are indented four or more spaces.
        if re.match(r"^  [^\s]", line):
            raise AssertionError(
                f"line {number}: unrecognized two-space entry in jobs mapping: "
                f"{line.strip()!r}"
            )
        if current is not None:
            jobs[current].append(line)
    if not jobs:
        raise AssertionError("no jobs parsed; the jobs mapping shape changed")
    return {name: "\n".join(body) for name, body in jobs.items()}


class RunnerPolicyTests(unittest.TestCase):
    def test_browser_driving_jobs_provision_the_pinned_browser(self) -> None:
        for filename, job_names in BROWSER_JOBS.items():
            jobs = workflow_jobs((WORKFLOWS / filename).read_text(encoding="utf-8"))
            for job_name in job_names:
                with self.subTest(workflow=filename, job=job_name):
                    body = jobs.get(job_name)
                    self.assertIsNotNone(body, f"{filename}: {job_name} is missing")
                    self.assertIn(
                        f"{CHROME_ACTION}@{NODE24_ACTION_PINS[CHROME_ACTION]}",
                        body,
                        f"{filename}: {job_name} must install the pinned browser",
                    )
                    self.assertIn(
                        "VELLUM_CHROME_PATH=",
                        body,
                        f"{filename}: {job_name} must export the pinned browser path",
                    )

    def test_every_workflow_uses_an_explicit_self_hosted_fallback(self) -> None:
        workflow_paths = sorted(
            [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
        )
        self.assertEqual(
            [path.name for path in workflow_paths],
            sorted(EXPECTED_RUNNERS),
        )

        for path in workflow_paths:
            variable, fallback = EXPECTED_RUNNERS[path.name]
            runs_on_lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("runs-on:")
            ]
            self.assertTrue(runs_on_lines, path.name)
            for line in runs_on_lines:
                with self.subTest(workflow=path.name, selector=line):
                    self.assertIn(f"vars.{variable}", line)
                    self.assertIn(fallback, line)
                    self.assertIn("self-hosted", line)
                    for hosted_label in HOSTED_LABELS:
                        self.assertNotIn(hosted_label, line)

    def test_external_actions_are_pinned_to_reviewed_node24_releases(self) -> None:
        for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "uses:" not in line or "uses: ./" in line:
                    continue
                match = ACTION_USE.match(line)
                self.assertIsNotNone(
                    match,
                    f"{path.name}:{line_number}: external action must use a full commit SHA",
                )
                action, pin = match.groups()
                self.assertIn(
                    action,
                    NODE24_ACTION_PINS,
                    f"{path.name}:{line_number}: review the action runtime before allowing it",
                )
                self.assertEqual(
                    pin,
                    NODE24_ACTION_PINS[action],
                    f"{path.name}:{line_number}: action pin is not the reviewed Node.js 24 release",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
