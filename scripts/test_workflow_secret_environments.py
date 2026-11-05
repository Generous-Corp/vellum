#!/usr/bin/env python3
"""Every job that reads a credential reads it from a main-only environment.

A repository-level secret is readable by any workflow run, including one
started by pushing a branch that edits the workflow. An environment secret is
released only to a job whose ref passes the environment's deployment policy
(branch main). This test pins the code half of that boundary: each credential
named below is read only by jobs that declare the expected environment, and no
job a pull request can trigger declares one. The policy half lives in the
repository settings; check_release_policy.py separately keeps `release` to
release-tag.yml alone.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# Credential -> the environment that holds it.
ENVIRONMENT_SECRETS = {
    "RELEASE_APP_PRIVATE_KEY": "release",
    "RELEASE_TAG_SIGNING_KEY": "release",
    "SHIPYARD_APP_ID": "automation",
    "SHIPYARD_APP_PRIVATE_KEY": "automation",
}

# Repository-level secrets that stay readable from any ref, with the reason.
REPOSITORY_SECRETS = {
    # Optional read-only token for the README clean-release check; not set.
    "VELLUM_CANONICAL_READ_TOKEN": "read-only, unset",
}

SECRET = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
JOB_HEADER = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):[ \t]*$", re.MULTILINE)
ENVIRONMENT = re.compile(r"^    environment:[ \t]*(?P<value>\S+)[ \t]*$", re.MULTILINE)
PULL_REQUEST_TRIGGER = re.compile(r"^  (pull_request|pull_request_target|merge_group):", re.MULTILINE)


def jobs(text: str) -> dict[str, str]:
    found = re.search(r"(?:^|\n)jobs:[ \t]*\n", text)
    body = text[found.end():] if found else ""
    marks = list(JOB_HEADER.finditer(body))
    return {mark["name"]: body[mark.start():(marks[i + 1].start() if i + 1 < len(marks) else None)]
            for i, mark in enumerate(marks)}


def scan():
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        triggers = text.split("\njobs:", 1)[0]
        for name, job in jobs(text).items():
            env = ENVIRONMENT.search(job)
            yield (path.name, bool(PULL_REQUEST_TRIGGER.search(triggers)), name,
                   env["value"] if env else None, set(SECRET.findall(job)) - {"GITHUB_TOKEN"})


class WorkflowSecretEnvironments(unittest.TestCase):
    def test_the_scan_sees_every_known_credential(self) -> None:
        # Control: a renamed secret or a broken job splitter would otherwise
        # make every assertion below vacuous.
        seen = set().union(*(secrets for *_, secrets in scan()))
        self.assertEqual(set(ENVIRONMENT_SECRETS) - seen, set())

    def test_every_credential_is_read_inside_its_environment(self) -> None:
        problems = []
        for wf, _, job, env, secrets in scan():
            for secret in sorted(secrets - set(REPOSITORY_SECRETS)):
                want = ENVIRONMENT_SECRETS.get(secret)
                if want is None:
                    problems.append(f"{wf}:{job}: classify {secret} in this test")
                elif env != want:
                    problems.append(f"{wf}:{job}: {secret} read in env {env!r}, not {want!r}")
        self.assertEqual(problems, [])

    def test_no_pull_request_triggered_job_names_an_environment(self) -> None:
        offenders = [f"{wf}:{job}" for wf, pr, job, env, _ in scan() if pr and env]
        self.assertEqual(offenders, [])

    def test_environment_detection_is_live(self) -> None:
        # Control: release-tag.yml's signing job is known to declare `release`.
        envs = {(wf, job): env for wf, _, job, env, _ in scan()}
        self.assertEqual(envs[("release-tag.yml", "sign-release-tag")], "release")


if __name__ == "__main__":
    unittest.main()
