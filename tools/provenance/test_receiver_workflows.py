#!/usr/bin/env python3
"""Static controls for receiver, replay, cutover, and watchdog workflows."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


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
        self.assertIn("vars.VELLUM_RECEIVER_LIVE == 'true'", text)
        self.assertIn("pulp_commit:", text)
        self.assertIn("options: [replay, enable]", text)

    def test_evidence_is_coalesced_to_one_reviewed_branch(self) -> None:
        text = self.text("pulp-observatory-receiver.yml")
        self.assertIn("automation/pulp-observatory-coalesced", text)
        self.assertIn("--force-with-lease", text)
        self.assertIn("--atomic", text)
        self.assertIn("persist-credentials: true", text)
        self.assertIn("VELLUM_RECEIVER_ADMIN_TOKEN", text)
        self.assertIn("VELLUM_PULP_READER_TOKEN", text)
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

    def test_watchdog_is_independent_and_default_off_until_cutover(self) -> None:
        text = self.text("pulp-observatory-watchdog.yml")
        self.assertIn("schedule:", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("pull-requests: read", text)
        self.assertIn("VELLUM_RECEIVER_WATCHDOG_ENABLED == 'true'", text)
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
