#!/usr/bin/env python3
"""release_request.py tags only a valid, new, fully checked release request."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
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
    def fixture(self, *, parity: bool = False) -> tuple[Path, str]:
        """A committed checkout holding the promotion records the attestation reads."""
        sys.path.insert(0, str(rr.ROOT / "tools/provenance"))
        import verify_authority_expansion as vae
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        expansion = root / rr.EXPANSION
        expansion.mkdir(parents=True)
        shutil.copy2(rr.ROOT / rr.EXPANSION / "exact-boundary-amendment-1.json", expansion)
        (expansion / "exact-boundary-acknowledgement-1.json").write_text(json.dumps({
            "schema_version": 1,
            "kind": "full-design-import-render-exact-boundary-acknowledgement",
            "acknowledgement_id": "full-design-import-render-v1-exact-boundary-acknowledgement-1",
            "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
            "state": "acknowledged", "acknowledged_at": "2026-10-31T00:00:00Z",
            "acknowledged_by": "@danielraffel",
            "authority_effect": "exact-path-implementation-authority-activated",
            "implementation_authority": "authorized-for-matrix-exact-routes",
            "coordinates": {
                "pulp_repository": "Generous-Corp/pulp", "pulp_acceptance_merge_commit": "4" * 40,
                "pulp_acceptance_path": ".github/vellum-expansion-watch/full-design-import-render-v1/"
                                        "exact-boundary-acceptance-1.json",
                "pulp_acceptance_sha256": "5" * 64, "vellum_delivery_repository": "danielraffel/vellum",
                "vellum_amendment_merge_commit": "6" * 40,
                "amendment_path": vae.BOUNDARY_AMENDMENT_PATH.as_posix(),
                "amendment_sha256": vae.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
                "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
                "matrix_path": vae.MATRIX_PATH.as_posix(), "matrix_sha256": vae.EXPECTED_MATRIX_SHA256,
            },
            "repository_roles": {
                "authority_repository": "Generous-Corp/vellum",
                "temporary_private_delivery_repository": "danielraffel/vellum",
                "delivery_repository_is_authority": False,
            },
            "gates": {
                "only_matrix_exact_routes_authorized": True,
                "unlisted_pulp_paths_remain_pulp_owned": True,
                "retained_boundary_cells_remain_pulp_owned": True,
                "promotion_attestation_required_before_parity_release": True,
                "pulp_consumption_authorized": False,
            },
        }) + "\n")
        receipts = root / "provenance/mirror/receipts"
        receipts.mkdir(parents=True)
        names = ["provenance/mirror/receipts/20261014-a.json", "provenance/mirror/receipts/20261025-b.json"]
        for name in names:
            (root / name).write_text(json.dumps({"export_id": name}) + "\n")
        (root / vae.MIRROR_RECEIPT_INDEX_PATH).write_text(
            json.dumps({"schema": vae.MIRROR_RECEIPT_INDEX_SCHEMA, "receipts": names}) + "\n")
        if parity:
            (root / vae.PARITY_COMPLETION_PATH).write_text(
                '{"completion_id": "full-design-import-render-v1-parity-completion-1"}\n')
        for command in (["init", "-q"], ["config", "user.name", "Test"],
                        ["config", "user.email", "test@example.com"], ["add", "."],
                        ["commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True)
        return root, rr.git(root, "rev-parse", "HEAD")

    def test_a_missing_acknowledgement_refuses_before_signing(self) -> None:
        root, head = self.fixture()
        (root / rr.EXPANSION / "exact-boundary-acknowledgement-1.json").unlink()
        with self.assertRaisesRegex(rr.ReleaseRequestError, "promotion record is missing"):
            rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.9")

    def test_without_parity_the_promotion_tag_carries_the_mirror_attestation(self) -> None:
        root, head = self.fixture()
        data = rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.9")
        self.assertEqual(data["kind"], "vellum-mirror-promotion-attestation")
        self.assertEqual(data["parity"], "not-proven-deferred-to-matrix-refreeze")
        self.assertEqual(data["mirror_receipt"], "provenance/mirror/receipts/20261025-b.json")
        self.assertEqual(data["mirror_receipt_sha256"], hashlib.sha256(
            (root / data["mirror_receipt"]).read_bytes()).hexdigest())
        self.assertEqual(data["release_source_commit"], head)
        self.assertNotIn("parity_completion_sha256", data)

    def test_without_parity_a_later_tag_refuses_before_signing(self) -> None:
        root, head = self.fixture()
        for tag in ("v0.1.10", "v0.2.0"):
            with self.assertRaisesRegex(rr.ReleaseRequestError, "needs a parity completion record"):
                rr.attestation(root, head, "2026-10-31T12:00:00Z", tag)

    def test_without_a_mirror_receipt_the_attestation_refuses(self) -> None:
        root, _ = self.fixture()
        subprocess.run(["git", "-C", str(root), "rm", "-q", "-r", "provenance/mirror"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "drop receipts"], check=True)
        head = rr.git(root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(rr.ReleaseRequestError, "no mirror export receipt"):
            rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.9")

    def test_with_parity_the_attestation_binds_the_completion(self) -> None:
        root, head = self.fixture(parity=True)
        data = rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.10")
        self.assertEqual(data["kind"], "vellum-authority-promotion-attestation")
        self.assertEqual(data["parity_release_source_commit"], head)
        self.assertNotIn("parity", data)
        self.assertNotIn("mirror_receipt", data)

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
