#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import merge_on_green as steward


PR = 42
HEAD = "a" * 40


def successful_run(
    workflow: str,
    *,
    run_id: int = 1,
    head_sha: str = HEAD,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict:
    return {
        "id": run_id,
        "name": workflow,
        "event": "pull_request",
        "status": status,
        "conclusion": conclusion,
        "created_at": f"2026-07-24T00:00:{run_id:02d}Z",
        "pull_requests": [
            {
                "number": PR,
                "head": {"sha": head_sha},
                "base": {"ref": "main"},
            }
        ],
    }


def green_matrix() -> dict[str, list[dict]]:
    return {
        workflow: [successful_run(workflow, run_id=index)]
        for index, workflow in enumerate(steward.REQUIRED_WORKFLOWS, start=1)
    }


def event_payload() -> dict:
    return {
        "workflow_run": {
            "event": "pull_request",
            "head_repository": {"full_name": "Generous-Corp/vellum"},
            "pull_requests": [{"number": PR, "head": {"sha": HEAD}}],
        }
    }


class MergeOnGreenTests(unittest.TestCase):
    def test_all_exact_head_gates_green(self) -> None:
        ready, details = steward.gate_state(
            green_matrix(), pull_number=PR, head_sha=HEAD
        )
        self.assertTrue(ready)
        self.assertEqual(len(details), len(steward.REQUIRED_WORKFLOWS))

    def test_missing_gate_fails_closed(self) -> None:
        runs = green_matrix()
        del runs["provenance.yml"]
        ready, details = steward.gate_state(runs, pull_number=PR, head_sha=HEAD)
        self.assertFalse(ready)
        self.assertIn("provenance.yml: missing", details)

    def test_pending_or_failed_gate_fails_closed(self) -> None:
        for status, conclusion in (("in_progress", None), ("completed", "failure")):
            with self.subTest(status=status, conclusion=conclusion):
                runs = green_matrix()
                runs["gpu-macos.yml"] = [
                    successful_run(
                        "gpu-macos.yml",
                        status=status,
                        conclusion=conclusion,
                    )
                ]
                ready, _ = steward.gate_state(
                    runs, pull_number=PR, head_sha=HEAD
                )
                self.assertFalse(ready)

    def test_success_for_another_head_or_pull_does_not_count(self) -> None:
        runs = green_matrix()
        runs["gpu-macos.yml"] = [
            successful_run("gpu-macos.yml", head_sha="b" * 40)
        ]
        ready, _ = steward.gate_state(runs, pull_number=PR, head_sha=HEAD)
        self.assertFalse(ready)
        runs["gpu-macos.yml"][0]["pull_requests"][0]["number"] = PR + 1
        ready, _ = steward.gate_state(runs, pull_number=PR, head_sha="b" * 40)
        self.assertFalse(ready)

    def test_latest_associated_run_controls(self) -> None:
        runs = green_matrix()
        runs["gpu-macos.yml"] = [
            successful_run("gpu-macos.yml", run_id=1),
            successful_run(
                "gpu-macos.yml",
                run_id=9,
                status="completed",
                conclusion="failure",
            ),
        ]
        ready, _ = steward.gate_state(runs, pull_number=PR, head_sha=HEAD)
        self.assertFalse(ready)

    def test_candidate_rejects_non_pr_fork_and_ambiguous_events(self) -> None:
        base = event_payload()
        self.assertEqual(
            steward._candidate(base, "Generous-Corp/vellum"), (PR, HEAD)
        )
        base["workflow_run"]["event"] = "workflow_dispatch"
        self.assertIsNone(steward._candidate(base, "Generous-Corp/vellum"))
        base["workflow_run"]["event"] = "pull_request"
        base["workflow_run"]["head_repository"]["full_name"] = "fork/vellum"
        self.assertIsNone(steward._candidate(base, "Generous-Corp/vellum"))
        base["workflow_run"]["head_repository"]["full_name"] = "Generous-Corp/vellum"
        base["workflow_run"]["pull_requests"].append(
            {"number": PR + 1, "head": {"sha": HEAD}}
        )
        self.assertIsNone(steward._candidate(base, "Generous-Corp/vellum"))

    def test_run_merges_only_the_exact_green_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(event_payload()), encoding="utf-8")
            calls = []

            def api(path, *, method="GET", body=None):
                calls.append((path, method, body))
                if path.endswith(f"/pulls/{PR}"):
                    return {
                        "state": "open",
                        "draft": False,
                        "base": {"ref": "main"},
                        "head": {
                            "sha": HEAD,
                            "repo": {"full_name": "Generous-Corp/vellum"},
                        },
                    }
                if "/actions/workflows/" in path:
                    workflow = path.split("/actions/workflows/", 1)[1].split("/", 1)[0]
                    return {"workflow_runs": green_matrix()[workflow]}
                if path.endswith(f"/pulls/{PR}/merge"):
                    self.assertEqual(body, {"sha": HEAD, "merge_method": "merge"})
                    return {"merged": True}
                raise AssertionError(path)

            with mock.patch.object(steward, "_api", side_effect=api):
                self.assertEqual(
                    steward.run(event, "Generous-Corp/vellum"),
                    0,
                )
            self.assertEqual(calls[-1][1], "PUT")

    def test_run_does_not_merge_a_draft_or_stale_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(event_payload()), encoding="utf-8")
            for draft, head in ((True, HEAD), (False, "b" * 40)):
                with self.subTest(draft=draft, head=head):
                    pull = {
                        "state": "open",
                        "draft": draft,
                        "base": {"ref": "main"},
                        "head": {
                            "sha": head,
                            "repo": {"full_name": "Generous-Corp/vellum"},
                        },
                    }
                    with mock.patch.object(steward, "_api", return_value=pull) as api:
                        self.assertEqual(
                            steward.run(event, "Generous-Corp/vellum"),
                            0,
                        )
                    api.assert_called_once_with(
                        f"repos/Generous-Corp/vellum/pulls/{PR}"
                    )


class MergeMethodPolicyTests(unittest.TestCase):
    def test_steward_never_squashes(self) -> None:
        # A squash rewrites the feature commits into one, so every observation
        # event keyed to a source commit is orphaned and the observatory's
        # cursor-coverage invariant fails on main. Decision 0001 rejects squash
        # copies for the same path/commit-correspondence reason.
        source = (
            Path(steward.__file__).resolve().parent / "merge_on_green.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"merge_method": "merge"', source)
        self.assertNotIn('"merge_method": "squash"', source)
        self.assertNotIn('"merge_method": "rebase"', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
