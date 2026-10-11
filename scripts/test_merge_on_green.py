#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

import merge_on_green as steward


PR = 42
HEAD = "a" * 40


REQUIRED_CONTEXTS = [
    "gpu-macos-arm64",
    "product-quality",
    "provenance-verify",
    "clean-release",
]


def check_run(
    name: str,
    *,
    run_id: int = 1,
    head_sha: str = HEAD,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict:
    return {
        "id": run_id,
        "name": name,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
    }


def green_checks() -> list[dict]:
    return [
        check_run(context, run_id=index)
        for index, context in enumerate(REQUIRED_CONTEXTS, start=1)
    ]


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
            green_checks(), required_contexts=REQUIRED_CONTEXTS, head_sha=HEAD
        )
        self.assertTrue(ready)
        self.assertEqual(len(details), len(REQUIRED_CONTEXTS))

    def test_missing_gate_fails_closed(self) -> None:
        checks = green_checks()[:-1]
        ready, details = steward.gate_state(
            checks, required_contexts=REQUIRED_CONTEXTS, head_sha=HEAD
        )
        self.assertFalse(ready)
        self.assertIn("clean-release: missing", details)

    def test_pending_or_failed_gate_fails_closed(self) -> None:
        for status, conclusion in (("in_progress", None), ("completed", "failure")):
            with self.subTest(status=status, conclusion=conclusion):
                checks = green_checks()
                checks[0] = check_run(
                        "gpu-macos-arm64",
                        status=status,
                        conclusion=conclusion,
                    )
                ready, _ = steward.gate_state(
                    checks, required_contexts=REQUIRED_CONTEXTS, head_sha=HEAD
                )
                self.assertFalse(ready)

    def test_success_for_another_head_or_pull_does_not_count(self) -> None:
        checks = green_checks()
        checks[0] = check_run("gpu-macos-arm64", head_sha="b" * 40)
        ready, _ = steward.gate_state(
            checks, required_contexts=REQUIRED_CONTEXTS, head_sha=HEAD
        )
        self.assertFalse(ready)

    def test_queued_workflow_with_green_required_context_is_ready(self) -> None:
        checks = green_checks()
        checks.extend(
            [
                check_run("gpu-macos.yml", run_id=98, status="queued", conclusion=None),
                check_run("sterile-consumer", run_id=99, conclusion="failure"),
            ]
        )
        ready, _ = steward.gate_state(
            checks, required_contexts=REQUIRED_CONTEXTS, head_sha=HEAD
        )
        self.assertTrue(ready)

    def test_extra_non_required_red_job_does_not_block(self) -> None:
        checks = green_checks() + [
            check_run("forbidden-deps", run_id=99, conclusion="failure")
        ]
        ready, _ = steward.gate_state(
            checks, required_contexts=REQUIRED_CONTEXTS, head_sha=HEAD
        )
        self.assertTrue(ready)

    def test_required_context_published_late_is_retried(self) -> None:
        responses = [
            {"check_runs": green_checks()[:-1]},
            {"check_runs": green_checks()},
        ]
        policy = {"contexts": REQUIRED_CONTEXTS}
        with mock.patch.object(
            steward, "_api", side_effect=[policy, responses[0], policy, responses[1]]
        ), mock.patch.object(steward.time, "sleep") as sleep:
            ready, _ = steward.live_gate_state(
                "Generous-Corp/vellum", base_ref="main", head_sha=HEAD
            )
        self.assertTrue(ready)
        sleep.assert_called_once_with(1.0)

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
                if path.endswith("/protection/required_status_checks"):
                    return {"contexts": REQUIRED_CONTEXTS}
                if "/commits/" in path and path.endswith("/check-runs?per_page=100"):
                    return {"check_runs": green_checks()}
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


class ApiTransportTests(unittest.TestCase):
    """Cover _api itself. Every other test mocks it, which is exactly how a
    missing `gh` binary reached production and failed every merge attempt."""

    @staticmethod
    def _response(payload: bytes):
        response = mock.MagicMock()
        response.read.return_value = payload
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *exc: False
        return response

    def test_get_sends_a_bearer_token_and_parses_json(self) -> None:
        with mock.patch.dict("os.environ", {"GH_TOKEN": "t0ken"}), mock.patch.object(
            steward._OPENER, "open",
            return_value=self._response(b'{"merged": true}'),
        ) as urlopen:
            self.assertEqual(steward._api("repos/o/r/pulls/1"), {"merged": True})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/o/r/pulls/1")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer t0ken")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertIsNone(request.data)

    def test_put_sends_a_json_body(self) -> None:
        with mock.patch.dict("os.environ", {"GH_TOKEN": "t0ken"}), mock.patch.object(
            steward._OPENER, "open",
            return_value=self._response(b'{"merged": true}'),
        ) as urlopen:
            steward._api(
                "repos/o/r/pulls/1/merge",
                method="PUT",
                body={"sha": HEAD, "merge_method": "merge"},
            )
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"sha": HEAD, "merge_method": "merge"},
        )

    def test_empty_body_returns_an_empty_mapping(self) -> None:
        with mock.patch.dict("os.environ", {"GH_TOKEN": "t0ken"}), mock.patch.object(
            steward._OPENER, "open", return_value=self._response(b"")
        ):
            self.assertEqual(steward._api("repos/o/r"), {})

    def test_missing_token_fails_closed(self) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GH_TOKEN", "GITHUB_TOKEN"}
        }
        with mock.patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(steward.MergeError, "GH_TOKEN"):
                steward._api("repos/o/r")

    def test_http_and_network_errors_become_merge_errors(self) -> None:
        failures = [
            urllib.error.HTTPError(
                "https://api.github.com/repos/o/r", 422, "Unprocessable",
                {}, io.BytesIO(b'{"message":"head sha is stale"}'),
            ),
            urllib.error.URLError("connection refused"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.dict(
                    "os.environ", {"GH_TOKEN": "t0ken"}
                ), mock.patch.object(
                    steward._OPENER, "open", side_effect=failure
                ):
                    with self.assertRaises(steward.MergeError):
                        steward._api("repos/o/r")


class RedirectCredentialTests(unittest.TestCase):
    """A redirect off the API host must not carry the installation token."""

    @staticmethod
    def _request(url: str) -> urllib.request.Request:
        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization", "Bearer secret")
        return request

    def test_authorization_is_dropped_when_the_host_changes(self) -> None:
        handler = steward._SameHostRedirect()
        following = handler.redirect_request(
            self._request("https://api.github.com/repos/o/r"),
            io.BytesIO(b""), 302, "Found", {}, "https://evil.example/steal",
        )
        self.assertIsNotNone(following)
        self.assertIsNone(following.get_header("Authorization"))

    def test_authorization_survives_a_same_host_redirect(self) -> None:
        handler = steward._SameHostRedirect()
        following = handler.redirect_request(
            self._request("https://api.github.com/repos/o/r"),
            io.BytesIO(b""), 302, "Found", {},
            "https://api.github.com/repositories/1",
        )
        self.assertEqual(following.get_header("Authorization"), "Bearer secret")

    def test_api_uses_the_guarded_opener(self) -> None:
        source = (
            Path(steward.__file__).resolve().parent / "merge_on_green.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_OPENER.open(request", source)
        self.assertNotIn("urllib.request.urlopen(request", source)


class MergeMethodPolicyTests(unittest.TestCase):
    def test_steward_does_not_shell_out_to_a_cli(self) -> None:
        source = (
            Path(steward.__file__).resolve().parent / "merge_on_green.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn('"gh"', source)


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
