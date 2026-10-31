#!/usr/bin/env python3
"""release_request.py tags only a valid, new, fully checked release request."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_request as rr  # noqa: E402
import verify_release_tag_ruleset as ruleset  # noqa: E402

COMMIT = "a" * 40
OTHER = "b" * 40
CMAKE = "project(Vellum\n    VERSION 0.1.9\n    LANGUAGES CXX)\n"
RETIRED = {"tags": [{"tag": "v0.1.7"}]}
RULES = [{"type": "required_status_checks",
          "parameters": {"required_status_checks": [{"context": "macos"}, {"context": "provenance-verify"}]}},
         {"type": "pull_request", "parameters": {}}]


def run(name: str, conclusion: str = "success", sha: str = COMMIT, completed: str = "2026-10-27T01:00:00Z",
        ident: int = 1) -> dict:
    return {"name": name, "head_sha": sha, "status": "completed", "conclusion": conclusion,
            "completed_at": completed, "id": ident}


def green() -> list:
    return [{"check_runs": [run("macos")]}, {"check_runs": [run("provenance-verify", ident=2)]}]


class Check(unittest.TestCase):
    def check(self, **overrides):
        args = dict(request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.9"}, cmakelists=CMAKE,
                    retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=RULES, pages=green())
        args.update(overrides)
        return rr.check(**args)

    def refused(self, pattern: str, **overrides) -> None:
        with self.assertRaisesRegex(rr.ReleaseRequestError, pattern):
            self.check(**overrides)

    def test_positive_control(self) -> None:
        self.assertEqual(self.check(), "v0.1.9")

    def test_an_existing_tag_is_never_moved(self) -> None:
        self.refused("already exists", tag_exists=True)

    def test_a_retired_tag_is_never_reused(self) -> None:
        self.refused("retired", request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.7"},
                     cmakelists=CMAKE.replace("0.1.9", "0.1.7"))

    def test_the_project_version_must_match(self) -> None:
        self.refused("declares 0.1.8", cmakelists=CMAKE.replace("0.1.9", "0.1.8"))

    def test_the_request_shape_is_closed(self) -> None:
        self.refused("exactly", request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.9", "commit": COMMIT})
        self.refused("schema", request={"schema": "other", "version": "0.1.9"})
        self.refused("MAJOR", request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.9-rc1"})

    def test_a_failed_required_check_refuses(self) -> None:
        self.refused("macos: completed/failure", pages=[{"check_runs": [run("macos", "failure"),
                                                                          run("provenance-verify", ident=2)]}])

    def test_a_missing_required_check_refuses(self) -> None:
        self.refused("provenance-verify: no run", pages=[{"check_runs": [run("macos")]}])

    def test_a_run_on_another_commit_does_not_count(self) -> None:
        self.refused("macos: no run", pages=[{"check_runs": [run("macos", sha=OTHER),
                                                              run("provenance-verify", ident=2)]}])

    def test_the_latest_run_decides(self) -> None:
        later_failure = [{"check_runs": [run("macos"), run("macos", "failure", completed="2026-10-27T02:00:00Z",
                                                            ident=3), run("provenance-verify", ident=2)]}]
        self.refused("macos: completed/failure", pages=later_failure)
        later_success = [{"check_runs": [run("macos", "failure"), run("macos", completed="2026-10-27T02:00:00Z",
                                                                       ident=3), run("provenance-verify", ident=2)]}]
        self.assertEqual(self.check(pages=later_success), "v0.1.9")

    def test_rules_that_require_nothing_refuse(self) -> None:
        self.refused("require no status check", branch_rules=[{"type": "pull_request"}])

    def test_a_short_commit_refuses(self) -> None:
        self.refused("full SHA", commit="abc")


class Freeze(unittest.TestCase):
    def test_the_freeze_satisfies_the_release_finalizer(self) -> None:
        body = dict(rr.freeze_ruleset("v0.1.9"), id=7, source_type="Repository", source="Generous-Corp/vellum")
        ruleset.verify([body], repository="Generous-Corp/vellum", tag="v0.1.9")

    def test_a_malformed_tag_refuses(self) -> None:
        with self.assertRaises(rr.ReleaseRequestError):
            rr.freeze_ruleset("v0.1")


if __name__ == "__main__":
    unittest.main()
