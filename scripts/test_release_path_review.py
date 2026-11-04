#!/usr/bin/env python3
"""Tests for release_path_review.py, the two-review gate on release-path files."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_path_review", ROOT / "scripts" / "release_path_review.py")
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules["release_path_review"] = gate
SPEC.loader.exec_module(gate)

REPO = "Generous-Corp/vellum"
HEAD = "a" * 40
OLD_HEAD = "b" * 40
BASE = "c" * 40
PROTECTED = "scripts/verify_release_tag.py"
UNPROTECTED = "docs/ci/releasing.md"
WORKFLOW = ".github/workflows/release-path-review.yml"
TRIGGER = ".github/workflows/release-path-review-trigger.yml"
CODEOWNERS = f"""# release policy
/.github/CODEOWNERS @danielraffel
/{WORKFLOW} @danielraffel
/.shipyard/config.toml @danielraffel
/scripts/release_path_review.py @danielraffel
/{PROTECTED} @danielraffel
"""
CONFIG = """[auto_merge]
reviewer_signers = ["pulp-review", "infra-review2"]
reviewer_stamp_sessions = ["fleet-pass-reviewer-*"]
"""
APP = {"login": "shipyard-local[bot]", "type": "Bot"}


def verdict(signer: str, session: str, head: str = HEAD, kind: str = "APPROVE", number: int = 1,
            user: dict[str, str] | None = None, stamp: str | None = None,
            association: str = "NONE") -> dict[str, Any]:
    stamp = stamp if stamp is not None else (
        f"<!-- session: Daniels-Mac-Studio-m3/{session} agent: {signer} -->")
    body = (f"Reviewed.\n\nreviewer: {signer}\nsession: {signer} (team-lead team, host m3)\n"
            f"{stamp}\nverdict: {kind} head:{head}\n")
    return {"id": number, "user": user or APP, "author_association": association, "body": body}


def files(*names: str) -> list[dict[str, Any]]:
    return [{"filename": name} for name in names]


def make_root(codeowners: str = CODEOWNERS, config: str = CONFIG) -> Path:
    root = Path(tempfile.mkdtemp(prefix="release-path-review-"))
    (root / ".github").mkdir()
    (root / ".shipyard").mkdir()
    (root / ".github" / "CODEOWNERS").write_text(codeowners, encoding="utf-8")
    (root / ".shipyard" / "config.toml").write_text(config, encoding="utf-8")
    return root


class FakeApi(gate.Api):
    def __init__(self, routes: dict[str, Any]) -> None:
        super().__init__("token")
        self.routes = routes
        self.reads: list[str] = []

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if method != "GET":
            raise AssertionError(f"the gate must only read, not {method} {path}")
        self.reads.append(path)
        if path in self.routes:
            return self.routes[path]
        stripped = re.sub(r"\?per_page=\d+(?:&page=1)?$", "", path)
        if stripped in self.routes:
            return self.routes[stripped]
        if re.search(r"[?&]page=\d+$", path) and re.sub(r"[?&]per_page=\d+&page=\d+$", "", path) in self.routes:
            return []
        raise gate.GateError(f"no route {path}")


def pull_routes(number: int, head: str, changed: list[dict[str, Any]], comments: list[dict[str, Any]],
                reviews: list[dict[str, Any]] | None = None, pulls_on_head: bool = True) -> dict[str, Any]:
    routes = {
        f"repos/{REPO}/pulls/{number}": {"number": number, "state": "open", "head": {"sha": head},
                                         "changed_files": len(changed)},
        f"repos/{REPO}/pulls/{number}/files": changed,
        f"repos/{REPO}/issues/{number}/comments": comments,
        f"repos/{REPO}/pulls/{number}/reviews": reviews or [],
    }
    if pulls_on_head:
        routes[f"repos/{REPO}/commits/{head}/pulls"] = [{"number": number, "state": "open", "head": {"sha": head}}]
    return routes


def pull_event(event: str = "pull_request_target") -> dict[str, Any]:
    if event == "pull_request_target":
        return {"pull_request": {"number": 7}}
    if event == "issue_comment":
        return {"issue": {"number": 7, "pull_request": {"url": "pulls/7"}}}
    return {"workflow_run": {"event": "pull_request_review", "head_sha": HEAD, "head_branch": "feature/x",
                             "pull_requests": [{"number": 7}]}}


class GateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def judge(self, changed: list[dict[str, Any]], comments: list[dict[str, Any]],
              reviews: list[dict[str, Any]] | None = None, root: Path | None = None,
              event: str = "pull_request_target", routes: dict[str, Any] | None = None) -> tuple[str, str]:
        api = FakeApi(routes or pull_routes(7, HEAD, changed, comments, reviews))
        decision = gate.decide(event, pull_event(event), REPO, api, root or self.root, log=lambda _: None)
        self.assertIsNotNone(decision)
        sha, state, description = decision
        self.assertEqual(sha, HEAD)
        self.assertLessEqual(len(description), 140)
        return state, description


class ProtectedPathTests(GateCase):
    two = [verdict("pulp-review", "fleet-pass-reviewer-m3-1", number=1),
           verdict("infra-review2", "fleet-pass-reviewer-m3-2", number=2)]

    def test_one_approval_fails(self) -> None:
        self.assertEqual(self.judge(files(PROTECTED), self.two[:1])[0], "failure")

    def test_two_distinct_counted_approvals_on_the_head_pass(self) -> None:
        state, description = self.judge(files(PROTECTED), self.two)
        self.assertEqual(state, "success")
        self.assertIn("pulp-review", description)

    def test_an_approval_of_an_older_head_does_not_count(self) -> None:
        stale = [self.two[0], verdict("infra-review2", "fleet-pass-reviewer-m3-2", head=OLD_HEAD, number=2)]
        self.assertEqual(self.judge(files(PROTECTED), stale)[0], "failure")

    def test_an_uppercase_head_in_the_marker_does_not_count(self) -> None:
        upper = [self.two[0], verdict("infra-review2", "fleet-pass-reviewer-m3-2", head="A" * 40, number=2)]
        self.assertEqual(self.judge(files(PROTECTED), upper)[0], "failure")

    def test_the_same_signer_twice_does_not_count(self) -> None:
        same = [self.two[0], verdict("pulp-review", "fleet-pass-reviewer-m3-2", number=2)]
        self.assertEqual(self.judge(files(PROTECTED), same)[0], "failure")

    def test_one_session_signing_two_names_does_not_count(self) -> None:
        same = [self.two[0], verdict("infra-review2", "fleet-pass-reviewer-m3-1", number=2)]
        self.assertEqual(self.judge(files(PROTECTED), same)[0], "failure")

    def test_one_comment_naming_two_signers_does_not_count(self) -> None:
        both = verdict("infra-review2", "fleet-pass-reviewer-m3-2", number=2)
        both["body"] = "reviewer: pulp-review\n" + both["body"]
        self.assertEqual(self.judge(files(PROTECTED), [self.two[0], both])[0], "failure")

    def test_an_author_lane_session_does_not_count(self) -> None:
        author = [self.two[0], verdict("infra-review2", "pulp-author-session", number=2)]
        self.assertEqual(self.judge(files(PROTECTED), author)[0], "failure")

    def test_an_unlisted_signer_does_not_count(self) -> None:
        other = [self.two[0], verdict("pulp-review2", "fleet-pass-reviewer-m3-2", number=2)]
        self.assertEqual(self.judge(files(PROTECTED), other)[0], "failure")

    def test_a_human_or_user_account_approve_does_not_count(self) -> None:
        user = {"login": "shipyard-local[bot]", "type": "User"}
        owner = {"login": "danielraffel", "type": "User"}
        for account in (user, owner):
            with self.subTest(account=account):
                forged = [self.two[0], verdict("infra-review2", "fleet-pass-reviewer-m3-2", number=2,
                                               user=account, association="OWNER")]
                self.assertEqual(self.judge(files(PROTECTED), forged)[0], "failure")

    def test_a_missing_or_doubled_or_variant_stamp_does_not_count(self) -> None:
        good = "<!-- session: Daniels-Mac-Studio-m3/fleet-pass-reviewer-m3-2 agent: infra-review2 -->"
        for stamp in ("", good + "\n" + good, "<!-- session: x/fleet-pass-reviewer-m3-2 -->",
                      good + "\n<!--  session: x/y -->",
                      "<!-- session: Daniels-Mac-Studio-m3/unattributed agent: infra-review2 -->"):
            with self.subTest(stamp=stamp):
                bad = [self.two[0], verdict("infra-review2", "fleet-pass-reviewer-m3-2", number=2, stamp=stamp)]
                self.assertEqual(self.judge(files(PROTECTED), bad)[0], "failure")

    def test_a_hold_on_the_head_refuses_two_approvals(self) -> None:
        hold = verdict("pulp-review", "fleet-pass-reviewer-m3-3", kind="HOLD", number=3)
        state, description = self.judge(files(PROTECTED), [*self.two, hold])
        self.assertEqual(state, "failure")
        self.assertIn("HOLD", description)

    def test_an_untrusted_hold_is_ignored(self) -> None:
        stranger = verdict("pulp-review", "x", kind="HOLD", number=3, user={"login": "someone", "type": "User"},
                           association="CONTRIBUTOR")
        self.assertEqual(self.judge(files(PROTECTED), [*self.two, stranger])[0], "success")

    def test_a_changes_requested_review_on_the_head_refuses(self) -> None:
        review = {"id": 9, "state": "CHANGES_REQUESTED", "commit_id": HEAD, "user": {"login": "danielraffel"},
                  "author_association": "OWNER"}
        for event in ("pull_request_target", "workflow_run"):
            with self.subTest(event=event):
                self.assertEqual(self.judge(files(PROTECTED), self.two, [review], event=event)[0], "failure")

    def test_an_edited_or_deleted_approval_changes_the_outcome(self) -> None:
        self.assertEqual(self.judge(files(PROTECTED), self.two, event="issue_comment")[0], "success")
        edited = dict(self.two[1], body=self.two[1]["body"].replace(HEAD, OLD_HEAD))
        self.assertEqual(self.judge(files(PROTECTED), [self.two[0], edited], event="issue_comment")[0], "failure")
        self.assertEqual(self.judge(files(PROTECTED), self.two[:1], event="issue_comment")[0], "failure")

    def test_a_rename_away_from_a_protected_path_is_protected(self) -> None:
        moved = [{"filename": "scripts/elsewhere.py", "previous_filename": PROTECTED}]
        self.assertEqual(self.judge(moved, [])[0], "failure")

    def test_any_workflow_or_local_action_is_protected(self) -> None:
        for name in (".github/workflows/x.yml", ".github/workflows/x.yaml", ".github/actions/a/action.yml"):
            with self.subTest(name=name):
                self.assertEqual(self.judge(files(name), [])[0], "failure")
                self.assertEqual(self.judge(files(name), self.two)[0], "success")

    def test_the_shipyard_review_policy_is_protected(self) -> None:
        self.assertEqual(self.judge(files(".shipyard/config.toml"), self.two[:1])[0], "failure")

    def test_every_open_pull_request_on_the_head_must_pass(self) -> None:
        routes = pull_routes(7, HEAD, files(UNPROTECTED), [])
        routes.update({key: value for key, value in pull_routes(8, HEAD, files(PROTECTED), []).items()
                       if "/commits/" not in key})
        routes[f"repos/{REPO}/commits/{HEAD}/pulls"] = [
            {"number": 7, "state": "open", "head": {"sha": HEAD}}, {"number": 8, "state": "open", "head": {"sha": HEAD}}]
        state, description = self.judge([], [], routes=routes)
        self.assertEqual(state, "failure")
        self.assertIn("PR #8", description)


class UnprotectedTests(GateCase):
    def test_a_pull_request_touching_no_protected_path_passes_without_reviews(self) -> None:
        state, description = self.judge(files(UNPROTECTED, "src/vellum/thing.py"), [])
        self.assertEqual(state, "success")
        self.assertIn("no release-path file", description)

    def test_a_closed_pull_request_or_plain_issue_posts_nothing(self) -> None:
        routes = pull_routes(7, HEAD, files(PROTECTED), [])
        routes[f"repos/{REPO}/pulls/7"]["state"] = "closed"
        api = FakeApi(routes)
        self.assertIsNone(gate.decide("pull_request_target", pull_event(), REPO, api, self.root, log=lambda _: None))
        self.assertIsNone(gate.decide("issue_comment", {"issue": {"number": 7}}, REPO, api, self.root,
                                      log=lambda _: None))


class BaseCopyTests(GateCase):
    def test_a_pull_request_editing_the_gate_is_judged_by_the_base_codeowners(self) -> None:
        # The pull request's own tree would drop every entry; the gate reads only `root`.
        changed = files("scripts/release_path_review.py", ".github/CODEOWNERS")
        state, description = self.judge(changed, [verdict("pulp-review", "fleet-pass-reviewer-m3-1")])
        self.assertEqual(state, "failure")
        self.assertIn(".github/CODEOWNERS", description)

    def test_the_workflows_run_only_the_base_copy(self) -> None:
        text = (ROOT / WORKFLOW).read_text(encoding="utf-8")
        triggers = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+?)^\S", text, re.MULTILINE)
        self.assertIsNotNone(triggers)
        events = set(re.findall(r"^  ([a-z_]+):", triggers.group(1), re.MULTILINE))
        # pull_request, pull_request_review and merge_group run the pull request's own copy.
        self.assertEqual(events, {"pull_request_target", "issue_comment", "workflow_run"})
        self.assertIn("workflows: [Release path review trigger]", text)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", text)
        self.assertNotIn(".head.", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python3 -I scripts/release_path_review.py", text)
        trigger = (ROOT / TRIGGER).read_text(encoding="utf-8")
        self.assertIn("name: Release path review trigger", trigger)
        self.assertIn("permissions: {}", trigger)
        self.assertNotIn("uses:", trigger)

    def test_only_the_post_step_holds_the_gate_app_token(self) -> None:
        text = (ROOT / WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("environment: release-path-review", text)
        blocks = re.split(r"\n(?=      - name:)", text)
        holders = [block.splitlines()[0].strip() for block in blocks
                   if "secrets." in block or "gate-token.outputs" in block]
        self.assertEqual(holders, ["- name: Mint the gate App token", "- name: Post release-path-review"])
        for block in blocks:
            if "gate-token.outputs" in block:
                self.assertNotIn("python", block)
        self.assertNotIn("statuses: write", text.split("jobs:", 1)[0])

    def test_codeowners_is_the_protected_set(self) -> None:
        live = gate.protected_paths((ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8"))
        for path in (WORKFLOW, TRIGGER, "scripts/release_path_review.py", ".github/CODEOWNERS",
                     ".shipyard/config.toml"):
            self.assertIn(path, live)

    def test_a_loose_codeowners_pattern_fails_closed(self) -> None:
        for pattern in ("scripts/*.py", "/scripts/", "docs/x.md", "/a/**"):
            with self.subTest(pattern=pattern):
                root = make_root(codeowners=f"{pattern} @danielraffel\n")
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                self.assertEqual(self.judge(files(UNPROTECTED), [], root=root)[0], "error")

    def test_a_base_without_signers_or_sessions_fails_closed(self) -> None:
        for config in ("[auto_merge]\nreviewer_signers = [\"pulp-review\"]\n",
                       "[auto_merge]\nreviewer_stamp_sessions = [\"fleet-pass-reviewer-*\"]\n", "x = ["):
            with self.subTest(config=config):
                root = make_root(config=config)
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                self.assertEqual(self.judge(files(UNPROTECTED), [], root=root)[0], "error")

    def test_a_truncated_file_list_fails_closed(self) -> None:
        routes = pull_routes(7, HEAD, files(UNPROTECTED), [])
        routes[f"repos/{REPO}/pulls/7"]["changed_files"] = 2
        self.assertEqual(self.judge([], [], routes=routes)[0], "error")

    def test_a_head_that_moved_mid_run_fails_closed(self) -> None:
        routes = pull_routes(7, HEAD, files(UNPROTECTED), [])
        routes[f"repos/{REPO}/pulls/7"] = dict(routes[f"repos/{REPO}/pulls/7"], head={"sha": OLD_HEAD})
        api = FakeApi(routes)
        decision = gate.decide("workflow_run", pull_event("workflow_run"), REPO, api, self.root, log=lambda _: None)
        self.assertEqual(decision[1], "error")


class OutputTests(GateCase):
    def test_a_description_cannot_add_output_lines(self) -> None:
        import os
        from unittest import mock
        out = self.root / "output"
        event = self.root / "event.json"
        event.write_text("{}", encoding="utf-8")
        env = {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_PATH": str(event),
               "GH_TOKEN": "t", "GITHUB_OUTPUT": str(out)}
        with mock.patch.dict(os.environ, env), mock.patch.object(
                gate, "decide", return_value=(HEAD, "failure", "x\nstate=success\r\nsha=" + OLD_HEAD)):
            self.assertEqual(gate.main(), 0)
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual([line.split("=", 1)[0] for line in lines], ["sha", "state", "description"])
        self.assertEqual(lines[1], "state=failure")


class LogTests(GateCase):
    def test_a_crafted_file_name_cannot_emit_a_workflow_command(self) -> None:
        name = ".github/workflows/x\n::set-output name=state::success\r\n::error::y"
        api = FakeApi(pull_routes(7, HEAD, files(name), []))
        logged: list[str] = []
        sha, state, description = gate.decide("pull_request_target", pull_event(), REPO, api, self.root,
                                              log=logged.append)
        self.assertEqual(state, "failure")
        for text in [*logged, description]:
            self.assertNotIn("\n", text)
            self.assertNotIn("\r", text)
            self.assertFalse(text.startswith("::"))

    def test_main_wraps_its_log_in_stop_commands(self) -> None:
        import contextlib
        import io
        import os
        from unittest import mock
        event = self.root / "event.json"
        event.write_text("{}", encoding="utf-8")
        env = {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_PATH": str(event),
               "GH_TOKEN": "t", "GITHUB_OUTPUT": str(self.root / "output")}
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(stdout), mock.patch.object(
                gate, "decide", side_effect=lambda *a, **k: print("::set-output name=state::success") or None):
            self.assertEqual(gate.main(), 0)
        lines = stdout.getvalue().splitlines()
        token = re.fullmatch(r"::stop-commands::([0-9a-f]{32})", lines[0]).group(1)
        self.assertEqual(lines[-1], f"::{token}::")


class QueueTests(GateCase):
    first, second, merge1, merge2 = "1" * 40, "2" * 40, "d" * 40, "e" * 40

    def queue(self, entries: dict[int, tuple[str, list[dict[str, Any]], list[dict[str, Any]]]],
              commits: list[dict[str, Any]], head: str, branch: str = "gh-readonly-queue/main/pr-8-x",
              total: int | None = None, pages: list[list[dict[str, Any]]] | None = None) -> tuple[str, str]:
        compare = f"repos/{REPO}/compare/main...{head}"
        routes: dict[str, Any] = {}
        for index, page in enumerate(pages or [commits], start=1):
            routes[f"{compare}?per_page=100&page={index}"] = {
                "total_commits": len(commits) if total is None else total,
                "merge_base_commit": {"sha": BASE}, "commits": page}
        for number, (pr_head, changed, comments) in entries.items():
            routes.update(pull_routes(number, pr_head, changed, comments))
        api = FakeApi(routes)
        event = {"workflow_run": {"event": "merge_group", "head_sha": head, "head_branch": branch,
                                  "pull_requests": []},
                 "repository": {"default_branch": "main"}}
        decision = gate.decide("workflow_run", event, REPO, api, self.root, log=lambda _: None)
        self.assertIsNotNone(decision)
        self.assertEqual(decision[0], head)
        return decision[1], decision[2]

    def batch(self) -> list[dict[str, Any]]:
        return [{"sha": self.merge1, "parents": [{"sha": BASE}, {"sha": self.first}]},
                {"sha": self.merge2, "parents": [{"sha": self.merge1}, {"sha": self.second}]}]

    def approvals(self, head: str) -> list[dict[str, Any]]:
        return [verdict("pulp-review", "fleet-pass-reviewer-m3-1", head=head, number=1),
                verdict("infra-review2", "fleet-pass-reviewer-m3-2", head=head, number=2)]

    def test_a_batch_judges_every_entry_at_its_head(self) -> None:
        state, description = self.queue(
            {7: (self.first, files(PROTECTED), []), 8: (self.second, files(UNPROTECTED), self.approvals(self.second))},
            self.batch(), self.merge2)
        self.assertEqual(state, "failure")
        self.assertIn("PR #7", description)
        state, _ = self.queue(
            {7: (self.first, files(PROTECTED), self.approvals(self.first)), 8: (self.second, files(UNPROTECTED), [])},
            self.batch(), self.merge2)
        self.assertEqual(state, "success")

    def test_a_paginated_range_is_read_whole(self) -> None:
        commits = self.batch()
        state, _ = self.queue(
            {7: (self.first, files(UNPROTECTED), []), 8: (self.second, files(UNPROTECTED), [])},
            commits, self.merge2, pages=[commits[:1], commits[1:]])
        self.assertEqual(state, "success")

    def test_a_range_missing_commits_or_too_long_fails_closed(self) -> None:
        for total in (3, gate.QUEUE_COMMIT_LIMIT + 1):
            with self.subTest(total=total):
                self.assertEqual(self.queue({}, self.batch(), self.merge2, total=total)[0], "error")

    def test_a_queue_commit_that_is_not_a_merge_fails_closed(self) -> None:
        single = "f" * 40
        self.assertEqual(self.queue({}, [{"sha": single, "parents": [{"sha": BASE}]}], single)[0], "error")

    def test_an_entry_that_heads_zero_or_two_open_pull_requests_fails_closed(self) -> None:
        commits = self.batch()[:1]
        for owners in ([], [7, 9]):
            with self.subTest(owners=owners):
                routes_entries = {7: (self.first, files(UNPROTECTED), [])}
                compare = f"repos/{REPO}/compare/main...{self.merge1}?per_page=100&page=1"
                routes = {compare: {"total_commits": 1, "merge_base_commit": {"sha": BASE}, "commits": commits}}
                for number, (pr_head, changed, comments) in routes_entries.items():
                    routes.update(pull_routes(number, pr_head, changed, comments))
                routes[f"repos/{REPO}/commits/{self.first}/pulls"] = [
                    {"number": n, "state": "open", "head": {"sha": self.first}} for n in owners]
                event = {"workflow_run": {"event": "merge_group", "head_sha": self.merge1,
                                          "head_branch": "gh-readonly-queue/main/pr-7-x"},
                         "repository": {"default_branch": "main"}}
                decision = gate.decide("workflow_run", event, REPO, FakeApi(routes), self.root, log=lambda _: None)
                self.assertEqual(decision[1], "error")

    def test_a_merge_group_run_off_a_queue_branch_fails_closed(self) -> None:
        entries = {7: (self.first, files(UNPROTECTED), []), 8: (self.second, files(UNPROTECTED), [])}
        self.assertEqual(self.queue(entries, self.batch(), self.merge2)[0], "success")
        for branch in ("feature/x", "gh-readonly-queue/other/pr-8-x"):
            with self.subTest(branch=branch):
                self.assertEqual(self.queue(entries, self.batch(), self.merge2, branch=branch)[0], "error")


if __name__ == "__main__":
    unittest.main()
