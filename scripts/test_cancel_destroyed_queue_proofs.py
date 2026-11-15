#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cancel_destroyed_queue_proofs as cancel  # noqa: E402

REPO = "Generous-Corp/vellum"
SHA_A = "a" * 40
SHA_B = "b" * 40
CURRENT = 500


def queue_run(run_id: int, entry: str = "pr-460", status: str = "queued", **overrides: Any) -> dict[str, Any]:
    run = {
        "id": run_id,
        "event": "merge_group",
        "path": cancel.WORKFLOW_PATH,
        "status": status,
        "head_branch": f"gh-readonly-queue/main/{entry}-{SHA_A}",
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
    }
    run.update(overrides)
    return run


class FakeApi:
    """Serves runs by status, the way the list endpoint filters them."""

    def __init__(self, runs: list[dict[str, Any]]) -> None:
        self.runs = runs
        self.paths: list[str] = []

    def __call__(self, path: str) -> dict[str, Any]:
        self.paths.append(path)
        assert path.startswith(f"repos/{REPO}/actions/workflows/gpu-macos.yml/runs?event=merge_group&status=")
        status = path.split("status=", 1)[1].split("&", 1)[0]
        page = int(path.rsplit("page=", 1)[1])
        matching = [run for run in self.runs if run.get("status") == status]
        return {"workflow_runs": matching[(page - 1) * 100:page * 100]}


def run(runs: list[dict[str, Any]], ref: str = f"refs/heads/gh-readonly-queue/main/pr-460-{SHA_B}") -> list[int]:
    cancelled: list[int] = []
    with contextlib.redirect_stdout(io.StringIO()):
        result = cancel.run(repository=REPO, ref=ref, run_id=CURRENT, api=FakeApi(runs),
                            cancel=cancelled.append)
    assert result == cancelled
    return cancelled


class QueueEntryTests(unittest.TestCase):
    def test_reads_the_entry_from_a_queue_ref(self) -> None:
        self.assertEqual(cancel.queue_entry(f"refs/heads/gh-readonly-queue/main/pr-460-{SHA_A}"), "pr-460")

    def test_any_other_ref_names_no_entry(self) -> None:
        for ref in ("refs/heads/main", f"refs/heads/gh-readonly-queue/main/pr-460-{SHA_A[:39]}",
                    f"refs/heads/gh-readonly-queue/other/pr-460-{SHA_A}",
                    f"refs/heads/gh-readonly-queue/main/pr-0460-{SHA_A}",
                    f"refs/heads/gh-readonly-queue/main/pr-460-{SHA_A.upper()}",
                    f"refs/pull/460/merge", ""):
            with self.subTest(ref=ref):
                self.assertIsNone(cancel.queue_entry(ref))


class CancelTests(unittest.TestCase):
    def test_cancels_every_older_unfinished_run_of_the_same_entry(self) -> None:
        runs = [queue_run(401, status="queued"), queue_run(402, status="in_progress"),
                queue_run(403, status="waiting"), queue_run(404, status="pending"),
                queue_run(405, status="requested")]
        self.assertEqual(run(runs), [401, 402, 403, 404, 405])

    def test_leaves_every_run_that_is_not_an_older_group_of_this_entry(self) -> None:
        runs = [
            queue_run(CURRENT),                                   # this run
            queue_run(CURRENT + 1),                               # a newer group
            queue_run(410, entry="pr-46"),                        # prefix of the entry
            queue_run(411, entry="pr-4600"),                      # the entry is a prefix
            queue_run(412, entry="pr-461"),                       # another entry
            queue_run(413, event="pull_request"),
            queue_run(414, path=".github/workflows/provenance.yml"),
            queue_run(415, repository={"full_name": "someone/vellum"}),
            queue_run(416, head_repository={"full_name": "someone/vellum"}),
            queue_run(417, head_branch=f"gh-readonly-queue/main/pr-460-{SHA_A}-x"),
            queue_run(418, head_branch=None),
            queue_run(True),                                      # bool is not an id
            queue_run("419"),
            queue_run(0),
        ]
        self.assertEqual(run(runs), [])
        # Control: the same filter cancels a genuine older group among them.
        self.assertEqual(run([*runs, queue_run(420)]), [420])

    def test_a_finished_run_is_never_cancelled(self) -> None:
        self.assertEqual(run([queue_run(430, status="completed"), queue_run(431)]), [431])

        # A row the listing returns under a status it does not report is not
        # trusted to be unfinished.
        def mislabelled(path: str) -> dict[str, Any]:
            if "status=queued&" in path:
                return {"workflow_runs": [queue_run(432, status="completed")]}
            return {"workflow_runs": []}

        self.assertEqual(cancel.superseded_runs(mislabelled, repository=REPO, entry="pr-460", run_id=CURRENT), [])

    def test_reads_every_page(self) -> None:
        runs = [queue_run(100 + index, entry="pr-1") for index in range(150)] + [queue_run(400)]
        self.assertEqual(run(runs), [400])

    def test_a_ref_that_is_not_a_queue_entry_cancels_nothing_and_reads_nothing(self) -> None:
        api = FakeApi([queue_run(401)])
        cancelled: list[int] = []
        with contextlib.redirect_stdout(io.StringIO()):
            cancel.run(repository=REPO, ref="refs/heads/main", run_id=CURRENT, api=api, cancel=cancelled.append)
        self.assertEqual((cancelled, api.paths), ([], []))

    def test_an_error_warns_and_exits_zero(self) -> None:
        original = cancel.gh_api

        def broken(path: str) -> Any:
            raise RuntimeError("API down")

        cancel.gh_api = broken
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cancel.main(["--repository", REPO, "--run-id", str(CURRENT),
                                    "--ref", f"refs/heads/gh-readonly-queue/main/pr-460-{SHA_B}"])
        finally:
            cancel.gh_api = original
        self.assertEqual(code, 0)
        self.assertIn("::warning::could not cancel destroyed merge groups: RuntimeError: API down", out.getvalue())


if __name__ == "__main__":
    unittest.main()
