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


NODE_ID = "PR_kwDOexample"


def open_pull(head: str = HEAD, draft: bool = False) -> dict:
    return {
        "state": "open",
        "draft": draft,
        "base": {"ref": "main"},
        "head": {"sha": head, "repo": {"full_name": "Generous-Corp/vellum"}},
    }


def pull_state(*, armed: bool = False, queued: bool = False) -> dict:
    return {
        "repository": {
            "pullRequest": {
                "id": NODE_ID,
                "autoMergeRequest": (
                    {"enabledAt": "2026-10-11T00:00:00Z", "mergeMethod": "MERGE"}
                    if armed
                    else None
                ),
                "mergeQueueEntry": {"id": "MQE_1"} if queued else None,
            }
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

    def _rest(self, calls, *, merged=None):
        def api(path, *, method="GET", body=None):
            calls.append((path, method, body))
            if path.endswith(f"/pulls/{PR}"):
                return open_pull()
            if "/actions/workflows/" in path:
                workflow = path.split("/actions/workflows/", 1)[1].split("/", 1)[0]
                return {"workflow_runs": green_matrix()[workflow]}
            if path.endswith(f"/pulls/{PR}/merge") and merged is not None:
                self.assertEqual(method, "PUT")
                self.assertEqual(body, {"sha": HEAD, "merge_method": "merge"})
                return {"merged": merged}
            raise AssertionError(path)

        return api

    def test_run_arms_auto_merge_on_the_exact_green_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(event_payload()), encoding="utf-8")
            calls = []
            graphql_calls = []

            def graphql(query, variables):
                graphql_calls.append((query, variables))
                if query is steward.PULL_STATE_QUERY:
                    self.assertEqual(
                        variables,
                        {"owner": "Generous-Corp", "name": "vellum", "number": PR},
                    )
                    return pull_state()
                if query is steward.ENABLE_AUTO_MERGE_MUTATION:
                    self.assertEqual(
                        variables, {"pullRequestId": NODE_ID, "expectedHeadOid": HEAD}
                    )
                    return {"enablePullRequestAutoMerge": {"pullRequest": {}}}
                raise AssertionError(query)

            with mock.patch.object(steward, "_api", side_effect=self._rest(calls)), \
                    mock.patch.object(steward, "_graphql", side_effect=graphql):
                self.assertEqual(steward.run(event, "Generous-Corp/vellum"), 0)
            # Arming is the only write; no REST merge was attempted.
            self.assertEqual([method for _, method, _ in calls if method != "GET"], [])
            self.assertEqual(
                [query for query, _ in graphql_calls],
                [steward.PULL_STATE_QUERY, steward.ENABLE_AUTO_MERGE_MUTATION],
            )

    def test_run_merges_directly_only_when_github_refuses_to_arm_a_mergeable_head(
        self,
    ) -> None:
        # Without a merge queue, a PR whose requirements are all met is in
        # "clean" status and GitHub refuses to arm auto-merge on it. That is
        # the pre-queue world; the steward then lands the merge commit itself.
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(event_payload()), encoding="utf-8")
            calls = []

            def graphql(query, variables):
                if query is steward.PULL_STATE_QUERY:
                    return pull_state()
                raise steward.MergeError(
                    "GitHub GraphQL failed: Pull request is in clean status"
                )

            with mock.patch.object(
                steward, "_api", side_effect=self._rest(calls, merged=True)
            ), mock.patch.object(steward, "_graphql", side_effect=graphql):
                self.assertEqual(steward.run(event, "Generous-Corp/vellum"), 0)
            self.assertEqual(calls[-1][1], "PUT")

    def test_run_raises_other_arming_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(event_payload()), encoding="utf-8")

            def graphql(query, variables):
                if query is steward.PULL_STATE_QUERY:
                    return pull_state()
                raise steward.MergeError("GitHub GraphQL failed: Resource not accessible")

            with mock.patch.object(steward, "_api", side_effect=self._rest([])), \
                    mock.patch.object(steward, "_graphql", side_effect=graphql):
                with self.assertRaises(steward.MergeError):
                    steward.run(event, "Generous-Corp/vellum")

    def test_run_is_a_no_op_when_already_armed_or_queued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(event_payload()), encoding="utf-8")
            for state in (pull_state(armed=True), pull_state(queued=True)):
                with self.subTest(state=state):
                    calls = []
                    with mock.patch.object(
                        steward, "_api", side_effect=self._rest(calls)
                    ), mock.patch.object(
                        steward, "_graphql", return_value=state
                    ) as graphql:
                        self.assertEqual(steward.run(event, "Generous-Corp/vellum"), 0)
                    graphql.assert_called_once()
                    self.assertEqual(
                        [method for _, method, _ in calls if method != "GET"], []
                    )

    def test_graphql_surfaces_errors_and_posts_through_the_guarded_api(self) -> None:
        with mock.patch.object(
            steward, "_api", return_value={"errors": [{"message": "boom"}]}
        ) as api:
            with self.assertRaises(steward.MergeError) as raised:
                steward._graphql("query {}", {"a": 1})
        self.assertIn("boom", str(raised.exception))
        api.assert_called_once_with(
            "graphql", method="POST", body={"query": "query {}", "variables": {"a": 1}}
        )
        with mock.patch.object(steward, "_api", return_value={"data": {"ok": True}}):
            self.assertEqual(steward._graphql("query {}", {}), {"ok": True})

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
        # The arming path spells the same policy in GraphQL's enum.
        self.assertIn("mergeMethod: MERGE", source)
        self.assertNotIn("mergeMethod: SQUASH", source)
        self.assertNotIn("mergeMethod: REBASE", source)

    def test_steward_arms_before_it_merges(self) -> None:
        # The REST merge is a fallback for the pre-queue "clean" case only; the
        # first write must be the auto-merge mutation, so a merge queue on main
        # (which refuses direct merges) is honoured without a code change.
        source = (
            Path(steward.__file__).resolve().parent / "merge_on_green.py"
        ).read_text(encoding="utf-8")
        self.assertLess(
            source.index("ENABLE_AUTO_MERGE_MUTATION,"),
            source.index('"merge_method": "merge"'),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
