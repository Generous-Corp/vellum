#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import local_ci_route


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PROFILE = ROOT / ".shipyard" / "ci-profiles" / "normal-local-fast.toml"

EXPECTED_RUNNERS = {
    "authority-activation.yml": (
        "ubuntu-latest",
        ("authority-active",),
        "privileged",
    ),
    "authority-release.yml": (
        "ubuntu-latest",
        ("finalize-authority-release",),
        "privileged",
    ),
    "gpu-macos.yml": (
        "macos-15",
        ("gpu-macos-arm64", "sterile-consumer"),
        "mixed-release",
    ),
    "merge-on-green.yml": (
        "ubuntu-latest",
        ("merge",),
        "privileged",
    ),
    "product-quality.yml": (
        "${{ fromJSON(vars.VELLUM_PR_SAFE_LINUX_RUNS_ON_JSON || '[\"ubuntu-latest\"]') }}",
        ("product-quality",),
        "local-eligible",
    ),
    "provenance.yml": (
        "${{ fromJSON(vars.VELLUM_PR_SAFE_LINUX_RUNS_ON_JSON || '[\"ubuntu-latest\"]') }}",
        ("forbidden-deps", "provenance-verify"),
        "local-eligible",
    ),
    "readme-quick-start.yml": (
        "${{ fromJSON(vars.VELLUM_PR_MACOS_RUNS_ON_JSON || '[\"macos-15\"]') }}",
        ("clean-release",),
        "local-eligible",
    ),
}

EXPECTED_CHECKS = {
    "authority-activation.yml": {"authority-active": "authority-active"},
    "authority-release.yml": {
        "finalize-authority-release": "finalize-authority-release",
    },
    "gpu-macos.yml": {
        "gpu-macos-arm64": "gpu-macos-arm64",
        "sterile-consumer": "sterile-consumer",
    },
    "merge-on-green.yml": {"merge": "merge-exact-green-head"},
    "product-quality.yml": {"product-quality": "product-quality"},
    "provenance.yml": {
        "forbidden-deps": "forbidden-deps",
        "provenance-verify": "provenance-verify",
    },
    # A job without an explicit display name uses its job id as the check name.
    "readme-quick-start.yml": {"clean-release": "clean-release"},
}

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

    def test_every_workflow_keeps_its_stable_job_names_and_hosted_baseline(
        self,
    ) -> None:
        workflow_paths = sorted(
            [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
        )
        self.assertEqual(
            [path.name for path in workflow_paths],
            sorted(EXPECTED_RUNNERS),
        )

        for path in workflow_paths:
            runner, job_names, classification = EXPECTED_RUNNERS[path.name]
            text = path.read_text(encoding="utf-8")
            jobs = workflow_jobs(text)
            self.assertEqual(set(jobs), set(job_names), path.name)
            for job_name, body in jobs.items():
                runs_on_lines = [
                    line.strip()
                    for line in body.splitlines()
                    if line.strip().startswith("runs-on:")
                ]
                with self.subTest(workflow=path.name, job=job_name):
                    self.assertEqual(runs_on_lines, [f"runs-on: {runner}"])
                    self.assertNotIn("self-hosted", runs_on_lines[0])
                    if classification == "local-eligible":
                        self.assertIn("fromJSON", runs_on_lines[0])
                        self.assertIn("vars.VELLUM_", runs_on_lines[0])
                        fallback = (
                            "ubuntu-latest\"]"
                            if path.name != "readme-quick-start.yml"
                            else "macos-15\"]"
                        )
                        self.assertIn(fallback, runs_on_lines[0])
                    else:
                        self.assertNotIn("fromJSON", runs_on_lines[0])
                        self.assertNotIn("vars.VELLUM_", runs_on_lines[0])
                    names = re.findall(r"^    name:\s*(.+?)\s*$", body, re.MULTILINE)
                    self.assertLessEqual(len(names), 1)
                    effective_name = names[0] if names else job_name
                    self.assertEqual(
                        effective_name,
                        EXPECTED_CHECKS[path.name][job_name],
                    )
            if classification == "local-eligible":
                self.assertNotIn("${{ secrets.", text, path.name)
                self.assertNotIn("contents: write", text, path.name)

    def test_profile_and_read_only_resolver_share_the_exact_target_contract(
        self,
    ) -> None:
        profile = PROFILE.read_text(encoding="utf-8")
        sections = {
            match.group(1): match.group(2)
            for match in re.finditer(
                r'^\[targets\."([^"]+)"\]\n(.*?)(?=^\[|\Z)',
                profile,
                flags=re.MULTILINE | re.DOTALL,
            )
        }
        self.assertEqual(set(sections), set(local_ci_route.TARGETS))
        for target_id, target in local_ci_route.TARGETS.items():
            body = sections[target_id]
            with self.subTest(target=target_id):
                self.assertIn(f'provider = "{target["provider"]}"', body)
                if target["provider"] == "github":
                    self.assertIn(f'runs_on_json = "{target["runs_on"]}"', body)
                    continue
                self.assertIn("proven = false", body)
                self.assertIn(f'runner_group = "{target["group"]}"', body)
                for label in target["labels"]:
                    self.assertIn(f'"{label}"', body)

        for lane, route in local_ci_route.ROUTES.items():
            if lane.startswith("privileged."):
                continue
            section_name = f'[repo."Generous-Corp/vellum".{lane}]'
            self.assertIn(section_name, profile, lane)
            start = profile.index(section_name)
            end = profile.find("\n[", start + len(section_name))
            body = profile[start:] if end < 0 else profile[start:end]
            local_targets = [
                target_id
                for target_id in route["targets"]
                if local_ci_route.TARGETS[target_id]["provider"] != "github"
            ]
            if local_targets:
                hosted_target = route["targets"][-1]
                self.assertIn('strategy = "github-only"', body, lane)
                self.assertIn(f'targets = ["{hosted_target}"]', body, lane)
                self.assertIn(
                    'activation_strategy = "ordered-fallback"', body, lane
                )
                activation_start = body.index("activation_targets")
                activation_body = body[activation_start:]
                for target_id in route["targets"]:
                    self.assertIn(f'"{target_id}"', activation_body, lane)
                self.assertIn(
                    f'health_lease_variable = "{route["lease_variable"]}"',
                    body,
                )
                self.assertIn(
                    f'health_lease_ttl_seconds = {route["lease_ttl_seconds"]}',
                    body,
                )
            else:
                self.assertIn('strategy = "github-only"', body, lane)
                self.assertIn(
                    f'targets = ["{route["targets"][0]}"]', body, lane
                )

    @staticmethod
    def _eligible_linux_inventory(now: datetime) -> dict:
        target = local_ci_route.TARGETS[
            "macpro.vellum-linux-x64-pr-safe"
        ]
        return {
            "repository": local_ci_route.REPOSITORY,
            "workflow_ref": target["workflow_access"][0],
            "leases": {
                "VELLUM_PR_SAFE_LINUX_LEASE_UNTIL": (
                    now + timedelta(seconds=120)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "groups": {
                target["group"]: {
                    "repositories": [local_ci_route.REPOSITORY],
                    "restricted_to_workflows": True,
                    "allows_public_repositories": False,
                    "workflow_access": list(target["workflow_access"]),
                },
            },
            "runners": [
                {
                    "name": target["name_prefix"] + "proof",
                    "repository": local_ci_route.REPOSITORY,
                    "group": target["group"],
                    "status": "online",
                    "busy": False,
                    "healthy": True,
                    "ephemeral": True,
                    "teardown_proven": True,
                    "credentials_reusable": False,
                    "egress_policy_proven": True,
                    "writable_host_mounts": [],
                    "labels": list(target["labels"]),
                },
            ],
        }

    def test_unproven_target_cannot_activate_from_inventory_alone(self) -> None:
        now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
        result = local_ci_route.resolve_route(
            "pr.linux",
            "workflow_run",
            self._eligible_linux_inventory(now),
            now=now,
        )
        self.assertEqual(result["selected"]["target"], "github.linux-x64")
        self.assertEqual(result["selected"]["runs_on"], "ubuntu-latest")
        self.assertEqual(result["skipped"][0]["reason"], "target-unproven")

    def test_complete_simulated_proof_selects_the_local_target(self) -> None:
        now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
        target_id = "macpro.vellum-linux-x64-pr-safe"
        with patch.dict(local_ci_route.TARGETS[target_id], {"proven": True}):
            result = local_ci_route.resolve_route(
                "pr.linux",
                "workflow_run",
                self._eligible_linux_inventory(now),
                now=now,
            )
        self.assertEqual(result["selected"]["target"], target_id)
        self.assertEqual(
            result["selected"]["runs_on"],
            local_ci_route.TARGETS[target_id]["labels"],
        )
        self.assertFalse(result["hosted_fallback"])

    def test_contributor_pull_request_event_cannot_select_protected_target(
        self,
    ) -> None:
        now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
        target_id = "macpro.vellum-linux-x64-pr-safe"
        with patch.dict(local_ci_route.TARGETS[target_id], {"proven": True}):
            result = local_ci_route.resolve_route(
                "pr.linux",
                "pull_request",
                self._eligible_linux_inventory(now),
                now=now,
            )
        self.assertEqual(result["selected"]["target"], "github.linux-x64")
        self.assertEqual(result["skipped"][0]["reason"], "event-not-eligible")

    def test_each_missing_admission_fact_forces_hosted_fallback(self) -> None:
        now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
        target_id = "macpro.vellum-linux-x64-pr-safe"
        cases = {}

        expired = self._eligible_linux_inventory(now)
        expired["leases"]["VELLUM_PR_SAFE_LINUX_LEASE_UNTIL"] = (
            now - timedelta(seconds=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        cases["expired lease"] = expired

        too_long = self._eligible_linux_inventory(now)
        too_long["leases"]["VELLUM_PR_SAFE_LINUX_LEASE_UNTIL"] = (
            now + timedelta(seconds=301)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        cases["overlong lease"] = too_long

        unrestricted = self._eligible_linux_inventory(now)
        unrestricted["groups"]["vellum-pr-safe-build"][
            "restricted_to_workflows"
        ] = False
        cases["unrestricted group"] = unrestricted

        wrong_label = self._eligible_linux_inventory(now)
        wrong_label["runners"][0]["labels"].remove("vellum-host-macpro")
        cases["missing label"] = wrong_label

        extra_label = self._eligible_linux_inventory(now)
        extra_label["runners"][0]["labels"].append("pulp-build-linux-x64")
        cases["cross-repository label alias"] = extra_label

        wrong_workflow = self._eligible_linux_inventory(now)
        wrong_workflow["workflow_ref"] = (
            "Generous-Corp/vellum/.github/workflows/product-quality.yml@"
            "refs/pull/15/merge"
        )
        cases["contributor-controlled workflow ref"] = wrong_workflow

        shared_group = self._eligible_linux_inventory(now)
        shared_group["groups"]["vellum-pr-safe-build"]["repositories"].append(
            "Generous-Corp/pulp"
        )
        cases["cross-repository runner group"] = shared_group

        widened_workflows = self._eligible_linux_inventory(now)
        widened_workflows["groups"]["vellum-pr-safe-build"][
            "workflow_access"
        ].append("Generous-Corp/vellum/.github/workflows/gpu-macos.yml@refs/heads/main")
        cases["widened workflow access"] = widened_workflows

        for field, unsafe in (
            ("busy", True),
            ("healthy", False),
            ("ephemeral", False),
            ("teardown_proven", False),
            ("credentials_reusable", True),
            ("egress_policy_proven", False),
            ("writable_host_mounts", ["/host/cache"]),
        ):
            inventory = self._eligible_linux_inventory(now)
            inventory["runners"][0][field] = unsafe
            cases[field] = inventory

        with patch.dict(local_ci_route.TARGETS[target_id], {"proven": True}):
            for name, inventory in cases.items():
                with self.subTest(case=name):
                    result = local_ci_route.resolve_route(
                        "pr.linux",
                        "workflow_run",
                        inventory,
                        now=now,
                    )
                    self.assertEqual(
                        result["selected"]["target"], "github.linux-x64"
                    )

    def test_privileged_lanes_have_no_local_candidate(self) -> None:
        result = local_ci_route.resolve_route(
            "privileged.authority",
            "workflow_dispatch",
            {},
            now=datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["selected"]["target"], "github.linux-x64")
        self.assertEqual(result["skipped"], [])

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
