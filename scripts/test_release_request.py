#!/usr/bin/env python3
"""release_request.py tags only a valid, new, fully checked release request."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
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


NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def preflight(tag: str = "v0.1.9", age: timedelta = timedelta(minutes=10), enabled: bool = True) -> dict:
    stamp = (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"release_tag": tag, "maximum_age_seconds": 21600,
            "administrator_check": {"checked_at": stamp, "enabled": enabled}}


def run(name: str, conclusion: str = "success", sha: str = COMMIT, completed: str = "2026-10-27T01:00:00Z",
        ident: int = 1) -> dict:
    return {"name": name, "head_sha": sha, "status": "completed", "conclusion": conclusion,
            "completed_at": completed, "id": ident}


def green() -> list:
    return [{"check_runs": [run("macos")]}, {"check_runs": [run("provenance-verify", ident=2)]}]


class Check(unittest.TestCase):
    def check(self, **overrides):
        args = dict(request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.9"}, cmakelists=CMAKE,
                    retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=RULES, pages=green(),
                    preflight=preflight(), now=NOW)
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


class Preflight(unittest.TestCase):
    def check(self, record: dict) -> str:
        return rr.check(request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.9"}, cmakelists=CMAKE,
                        retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=RULES,
                        pages=green(), preflight=record, now=NOW)

    def test_a_fresh_preflight_tags(self) -> None:
        self.assertEqual(self.check(preflight(age=timedelta(hours=3, minutes=59))), "v0.1.9")

    def test_too_little_window_left_refuses_before_tagging(self) -> None:
        with self.assertRaisesRegex(rr.ReleaseRequestError, "window left"):
            self.check(preflight(age=timedelta(hours=4, minutes=1)))

    def test_a_preflight_for_another_tag_or_disabled_refuses(self) -> None:
        with self.assertRaisesRegex(rr.ReleaseRequestError, "not for v0.1.9"):
            self.check(preflight(tag="v0.1.8"))
        with self.assertRaisesRegex(rr.ReleaseRequestError, "enabled"):
            self.check(preflight(enabled=False))
        with self.assertRaisesRegex(rr.ReleaseRequestError, "not for"):
            self.check(None)


class Attestation(unittest.TestCase):
    def test_missing_promotion_records_refuse_before_signing(self) -> None:
        head = rr.git(rr.ROOT, "rev-parse", "HEAD")
        record = rr.ROOT / rr.EXPANSION / "exact-boundary-acknowledgement-1.json"
        if record.is_file():
            self.skipTest("the promotion records exist in this tree")
        with self.assertRaisesRegex(rr.ReleaseRequestError, "promotion record is missing"):
            rr.attestation(rr.ROOT, head, "2026-10-31T12:00:00Z")

    def test_the_pipeline_attester_is_accepted_and_a_bot_name_is_not(self) -> None:
        sys.path.insert(0, str(rr.ROOT / "tools/provenance"))
        import verify_authority_expansion as vae
        sha, tree = "a" * 40, "b" * 40
        data = {
            "schema_version": 1, "kind": "vellum-authority-promotion-attestation",
            "attestation_id": "full-design-import-render-v1-authority-promotion-1", "state": "attested",
            "attested_at": "2026-10-31T12:00:00Z", "attested_by": vae.RELEASE_PIPELINE_ATTESTER,
            "authority_repository": "Generous-Corp/vellum", "delivery_repository": "danielraffel/vellum",
            "promotion_mode": "repository-transfer", "authority_commit": sha, "delivery_commit": sha,
            "authority_tree": tree, "delivery_tree": tree,
            "exact_boundary_amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
            "exact_boundary_amendment_sha256": vae.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
            "exact_boundary_acknowledgement_id": "full-design-import-render-v1-exact-boundary-acknowledgement-1",
            "exact_boundary_acknowledgement_sha256": "c" * 64,
            "parity_completion_id": "full-design-import-render-v1-parity-completion-1",
            "parity_completion_sha256": "d" * 64, "parity_release_source_commit": sha,
        }
        amendment = {"repository_roles": {"required_exact_boundary_acknowledgement_id":
                                          "full-design-import-render-v1-exact-boundary-acknowledgement-1"}}
        self.assertEqual(vae.validate_authority_promotion_attestation(data, amendment, delivery_retired=True), [])
        for other in ("release-pipeline:someone/else", "vellum-release[bot]"):
            errors = vae.validate_authority_promotion_attestation(dict(data, attested_by=other), amendment,
                                                                   delivery_retired=True)
            self.assertEqual([e for e in errors if "attested_by" in e] != [], True, (other, errors))
        self.assertEqual(vae.validate_authority_promotion_attestation(dict(data, attested_by="@danielraffel"),
                                                                       amendment, delivery_retired=True), [])


class Freeze(unittest.TestCase):
    def test_the_freeze_satisfies_the_release_finalizer(self) -> None:
        body = dict(rr.freeze_ruleset("v0.1.9"), id=7, source_type="Repository", source="Generous-Corp/vellum")
        ruleset.verify([body], repository="Generous-Corp/vellum", tag="v0.1.9")

    def test_a_malformed_tag_refuses(self) -> None:
        with self.assertRaises(rr.ReleaseRequestError):
            rr.freeze_ruleset("v0.1")


if __name__ == "__main__":
    unittest.main()
