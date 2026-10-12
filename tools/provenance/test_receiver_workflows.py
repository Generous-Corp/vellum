#!/usr/bin/env python3
"""Static controls for receiver, replay, cutover, and watchdog workflows."""

from __future__ import annotations

from pathlib import Path
import json
import re
import unittest
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
sys.path.insert(0, str(ROOT / "tools/provenance"))
from live_delivery import enabled as live_enabled, validate_enabled_barrier


class ReceiverWorkflowTests(unittest.TestCase):
    def text(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_activation_is_manual_only_and_independently_validated(self) -> None:
        text = self.text("authority-activation.yml")
        self.assertNotIn("repository_dispatch:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("durable event is not authority activation", text)

    def test_receiver_replay_and_cutover_share_non_cancelling_mutex(self) -> None:
        text = self.text("pulp-observatory-receiver.yml")
        self.assertIn("group: vellum-pulp-observatory-cursor", text)
        self.assertIn("cancel-in-progress: false", text)
        activation = self.text("authority-activation.yml")
        self.assertIn("group: vellum-authority-activation", activation)

    def test_live_dispatch_is_default_off_but_trusted_replay_is_available(self) -> None:
        text = self.text("pulp-observatory-receiver.yml")
        self.assertIn("types: [pulp-change-landed]", text)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("pulp_commit:", text)
        self.assertIn("options: [replay, enable]", text)
        # The live switch is a committed file, not a repository variable:
        # nothing this workflow can mint may write variables (R7 delta D3).
        switch = json.loads(
            (ROOT / "provenance/pulp-observatory/live-delivery.json").read_text()
        )
        self.assertIs(switch["enabled"], False)
        self.assertIn('Path("provenance/pulp-observatory/live-delivery.json")', text)
        self.assertIn('skip = os.environ["GITHUB_EVENT_NAME"] == "repository_dispatch" and not live\n', text)
        self.assertIn("if: steps.coordinates.outputs.skip != 'true'", text)
        self.assertIn("automation/pulp-observatory-live-delivery", text)
        self.assertNotIn("actions/variables", text)
        self.assertNotIn("VELLUM_RECEIVER_LIVE", text)

    def test_live_switch_is_read_from_trusted_main_only(self) -> None:
        text = self.text("pulp-observatory-receiver.yml")
        # A pull-request branch copy of the switch must never control a run.
        self.assertIn("token: ${{ steps.evidence-token.outputs.token }}\n          ref: main", text)
        self.assertIn('Path("provenance/pulp-observatory/live-delivery.json")', text)
        self.assertNotIn("github.event.pull_request.head", text)
        self.assertNotIn("client_payload.live", text)

    def test_live_switch_enable_uses_required_provenance_check_and_fails_closed(self) -> None:
        text = self.text("pulp-observatory-receiver.yml")
        self.assertIn("required checks", text)
        self.assertIn("provenance-verify", text)
        self.assertIn("from live_delivery import enabled as live_enabled", text)
        self.assertIn("live = live_enabled", text)

    def test_live_switch_parser_is_fail_closed_and_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "switch.json"
            for raw, expected in ((b'{"enabled": true}', True),
                                  (b'{"enabled": "true"}', False),
                                  (b'{"enabled": false, "enabled": true}', False),
                                  (b"{bad", False), (b"\xff", False)):
                path.write_bytes(raw)
                self.assertEqual(live_enabled(path), expected, raw)
            path.write_text("null", encoding="utf-8")
            self.assertFalse(live_enabled(path))

    def test_live_switch_barrier_rejects_frozen_or_uncovered_enablement(self) -> None:
        cursor = {"pulp": {"last_scanned_commit": "a" * 40}}
        with self.assertRaises(ValueError):
            validate_enabled_barrier({"enabled": True, "barrier_pulp_commit": "a" * 40},
                                     {"state": "frozen"}, cursor)
        with self.assertRaises(ValueError):
            validate_enabled_barrier({"enabled": True, "barrier_pulp_commit": "b" * 40},
                                     {"state": "active"}, cursor)
        validate_enabled_barrier({"enabled": False}, {"state": "frozen"}, cursor)

    def test_evidence_is_coalesced_to_one_reviewed_branch(self) -> None:
        text = self.text("pulp-observatory-receiver.yml")
        self.assertIn("automation/pulp-observatory-coalesced", text)
        self.assertIn("--force-with-lease", text)
        self.assertIn("--atomic", text)
        self.assertIn("persist-credentials: true", text)
        self.assertIn("steps.evidence-token.outputs.token", text)
        self.assertIn("steps.pulp-reader.outputs.token", text)
        self.assertIn("--paginate", text)
        self.assertIn("--jq 'length'", text)
        self.assertIn("awk '{ total += $1 } END { print total + 0 }'", text)
        self.assertNotIn("--slurp", text)
        self.assertIn("$GITHUB_REPOSITORY_OWNER:$EVIDENCE_BRANCH", text)
        self.assertIn("timeout-minutes: 60", text)
        self.assertIn("Vellum main advanced while evidence was prepared", text)
        self.assertIn("gh pr create", text)
        self.assertNotIn("HEAD:refs/heads/main", text)
        self.assertIn("enable barrier requires a fully covered committed cursor", text)

    def test_receiver_advances_the_vellum_cursor_after_every_landing(self) -> None:
        text = (ROOT / ".github/workflows/pulp-observatory-receiver.yml").read_text()
        self.assertIn("  push:\n    branches: [main]\n", text)
        self.assertIn("github.event_name == 'push' ||", text)
        self.assertIn('elif os.environ["GITHUB_EVENT_NAME"] == "push":', text)
        self.assertIn("--vellum-repo . \\", text)
        self.assertIn('--vellum-main "$(git rev-parse HEAD)"', text)
        self.assertIn('--vellum-target "$(git rev-parse HEAD)"', text)

    def test_watchdog_builds_its_state_with_the_tested_module(self) -> None:
        text = (ROOT / ".github/workflows/pulp-observatory-watchdog.yml").read_text()
        self.assertIn("python3 tools/provenance/test_receiver_watchdog_state.py", text)
        self.assertIn("python3 tools/provenance/receiver_watchdog_state.py", text)
        self.assertNotIn("import receiver", text)

    def test_receiver_and_watchdog_use_only_credentials_that_exist(self) -> None:
        # R7 MF3: the repository holds exactly the two App secrets and no
        # variables. A job that reads an undefined secret runs with an empty
        # token and fails late; a job gated on an undefined variable is a job
        # that silently never runs.
        defined_secrets = {"SHIPYARD_APP_ID", "SHIPYARD_APP_PRIVATE_KEY"}
        deliberate_gate_vars: set[str] = set()  # the live switch is a committed file
        for name in ("pulp-observatory-receiver.yml", "pulp-observatory-watchdog.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            with self.subTest(workflow=name):
                for secret in set(re.findall(r"secrets\.([A-Z0-9_]+)", text)):
                    self.assertIn(secret, defined_secrets, f"{name} reads undefined secret {secret}")
                for line in text.splitlines():
                    for var in re.findall(r"vars\.([A-Z0-9_]+)", line):
                        if "||" in line:
                            continue  # inline fallback: safe when unset
                        self.assertIn(var, deliberate_gate_vars, f"{name} depends on undefined var {var}")
                # Every cross-repository checkout and every API write uses a
                # per-run App token, never an undefined secret or GITHUB_TOKEN.
                for block in re.findall(r"repository: Generous-Corp/pulp\n(?:.*\n){0,3}", text):
                    self.assertIn("token: ${{ steps.pulp-reader.outputs.token }}", block)
                self.assertIn("actions/create-github-app-token@", text)
                # The pinned action has no variables permission input, and a
                # mint that asks for more than the installation grants fails
                # outright (R7 delta D3, run 38173182114). Every App mint
                # requests only permissions proved by a completed run.
                self.assertNotIn("permission-variables", text)
                proved = {
                    frozenset({"contents: write", "pull-requests: write"}),  # steward
                    frozenset({"actions: write", "pull-requests: read"}),  # watchdog
                    frozenset({"contents: read"}),  # Pulp reader
                }
                mints = [
                    step for step in text.split("\n      - name: ")
                    if "uses: actions/create-github-app-token@" in step
                ]
                self.assertGreaterEqual(len(mints), 2, f"{name}: mint steps not found")
                for mint in mints:
                    requested = frozenset(
                        f"{name}: {level}"
                        for name, level in re.findall(r"permission-([a-z-]+): (\w+)", mint)
                    )
                    self.assertIn(requested, proved, f"{name} mints an unproved scope set")
                # GITHUB_TOKEN is never used, so a push or pull request from
                # these workflows always triggers its required checks.
                self.assertNotIn("github.token", text)
        receiver = (ROOT / ".github/workflows/pulp-observatory-receiver.yml").read_text()
        self.assertIn("permissions:\n  contents: read\n", receiver)
        watchdog = (ROOT / ".github/workflows/pulp-observatory-watchdog.yml").read_text()
        gate = watchdog.split("\njobs:\n", 1)[1].split("    steps:", 1)[0]
        self.assertNotIn("vars.", gate, "watchdog job must not be gated on a variable")
        gate = receiver.split("\njobs:\n", 1)[1].split("    steps:", 1)[0]
        self.assertIn("github.event_name == 'push' ||", gate)
        self.assertNotIn("vars.VELLUM_RECEIVER", gate, "receiver job must not be gated on a variable")

    def test_watchdog_is_independent_and_always_on(self) -> None:
        text = self.text("pulp-observatory-watchdog.yml")
        self.assertIn("schedule:", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("pull-requests: read", text)
        self.assertNotIn("VELLUM_RECEIVER_WATCHDOG_ENABLED", text)
        self.assertIn("group: vellum-pulp-observatory-watchdog", text)
        self.assertIn("--paginate", text)
        self.assertIn("--jq 'length'", text)
        self.assertIn("awk '{ total += $1 } END { print total + 0 }'", text)
        self.assertNotIn("--slurp", text)
        self.assertIn("receiver_watchdog.py", text)
        self.assertIn("Retry lag or refresh an aging receiver heartbeat", text)
        self.assertIn("dispatch_required", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
