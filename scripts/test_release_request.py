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
    return {"schema": "vellum.immutable-release-preflight.v1", "repository": "Generous-Corp/vellum",
            "documentation": "https://docs.github.com/",
            "release_tag": tag, "integrity_model": "covered-by-exact-signed-annotated-release-tag",
            "maximum_age_seconds": 21600,
            "administrator_check": {"checked_at": stamp, "enabled": enabled, "enforced_by_owner": False,
                                    "endpoint": "GET /repos/Generous-Corp/vellum/immutable-releases",
                                    "required_permission": "Administration: read"}}


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
        self.assertEqual(self.check(existing_tags=["v0.1.6", "v0.1.8", "not-a-version"]), "v0.1.9")

    def test_a_release_only_moves_forward(self) -> None:
        self.refused("not newer than v0.2.0", existing_tags=["v0.1.8", "v0.2.0"])
        self.refused("not newer than v0.1.9", existing_tags=["v0.1.9"], tag_exists=False)
        self.refused("not newer than v0.3.0", retired_tags={"tags": [{"tag": "v0.3.0"}]})

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
        self.refused("requires no status check", branch_rules=[{"type": "pull_request"}])

    def test_a_short_commit_refuses(self) -> None:
        self.refused("full SHA", commit="abc")


# The live shapes (2026-11-01): Vellum's required checks come from classic branch
# protection, each bound to GitHub Actions (app 15368); its rulesets add only the
# merge queue.
LIVE_RULES = [{"type": "merge_queue", "parameters": {"merge_method": "MERGE"}}]
LIVE_PROTECTION = {"strict": False, "contexts": ["gpu-macos-arm64", "provenance-verify"],
                   "checks": [{"context": "gpu-macos-arm64", "app_id": 15368},
                              {"context": "provenance-verify", "app_id": 15368}]}


def app_run(name: str, run_id: int, app: int = 15368, status: str = "completed",
            conclusion: str | None = "success") -> dict:
    return {"name": name, "head_sha": COMMIT, "status": status, "conclusion": conclusion,
            "completed_at": None if status != "completed" else "2026-11-01T01:00:00Z",
            "id": run_id, "app": {"id": app}}


class RequiredChecksFromProtection(unittest.TestCase):
    def check(self, runs: list) -> str:
        return rr.check(request={"schema": rr.REQUEST_SCHEMA, "version": "0.1.9"}, cmakelists=CMAKE,
                        retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=LIVE_RULES,
                        pages=[{"check_runs": runs}], preflight=preflight(), now=NOW,
                        protection=LIVE_PROTECTION)

    def test_pending_lists_only_checks_whose_newest_run_is_unfinished(self) -> None:
        runs = [app_run("gpu-macos-arm64", 1), app_run("gpu-macos-arm64", 3, status="in_progress", conclusion=None),
                app_run("provenance-verify", 2)]
        required = rr.required_checks(LIVE_RULES, LIVE_PROTECTION)
        self.assertEqual(rr.pending(required, runs, COMMIT), ["gpu-macos-arm64"])
        self.assertEqual(rr.pending(required, runs[:1] + runs[2:], COMMIT), [])
        # A failed or missing run is not pending: the check that follows refuses it.
        self.assertEqual(rr.pending(required, [app_run("gpu-macos-arm64", 1, conclusion="failure")], COMMIT), [])

    def test_the_live_api_shapes_are_read(self) -> None:
        self.assertEqual(rr.required_checks(LIVE_RULES, LIVE_PROTECTION),
                         [("gpu-macos-arm64", 15368), ("provenance-verify", 15368)])
        self.assertEqual(self.check([app_run("gpu-macos-arm64", 1), app_run("provenance-verify", 2)]), "v0.1.9")

    def test_rules_alone_cannot_hide_protection_checks(self) -> None:
        with self.assertRaisesRegex(rr.ReleaseRequestError, "provenance-verify: no run"):
            self.check([app_run("gpu-macos-arm64", 1)])

    def test_a_newer_run_in_progress_outranks_an_older_success(self) -> None:
        runs = [app_run("gpu-macos-arm64", 1), app_run("provenance-verify", 2),
                app_run("gpu-macos-arm64", 9, status="in_progress", conclusion=None)]
        with self.assertRaisesRegex(rr.ReleaseRequestError, "gpu-macos-arm64: in_progress/None"):
            self.check(runs)

    def test_a_same_named_check_from_another_app_does_not_count(self) -> None:
        runs = [app_run("gpu-macos-arm64", 1, app=999), app_run("provenance-verify", 2)]
        with self.assertRaisesRegex(rr.ReleaseRequestError, "gpu-macos-arm64: no run from app 15368"):
            self.check(runs)
        # ... and a newer success from the wrong app cannot override a failure from the right one
        runs = [app_run("gpu-macos-arm64", 1, conclusion="failure"), app_run("gpu-macos-arm64", 5, app=999),
                app_run("provenance-verify", 2)]
        with self.assertRaisesRegex(rr.ReleaseRequestError, "gpu-macos-arm64: completed/failure"):
            self.check(runs)


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

    def forged(self, change) -> dict:
        record = preflight()
        change(record)
        return record

    def test_the_strict_verifier_runs_before_tagging(self) -> None:
        cases = {
            "schema": lambda r: r.update(schema="vellum.immutable-release-preflight.v0"),
            "repository": lambda r: r.update(repository="someone/vellum"),
            "integrity": lambda r: r.update(integrity_model="trust-me"),
            "maximum_age_seconds": lambda r: r.update(maximum_age_seconds=10 ** 9),
            "endpoint": lambda r: r["administrator_check"].update(endpoint="GET /repos/someone/vellum/immutable-releases"),
            "Administration: read": lambda r: r["administrator_check"].update(required_permission="none"),
            "enforced_by_owner": lambda r: r["administrator_check"].update(enforced_by_owner="yes"),
            "future": lambda r: r["administrator_check"].update(checked_at="2026-11-30T12:00:00Z"),
            "UTC": lambda r: r["administrator_check"].update(checked_at="2026-10-31T11:50:00+00:00"),
        }
        for message, change in cases.items():
            with self.subTest(message), self.assertRaisesRegex(rr.ReleaseRequestError, message):
                self.check(self.forged(change))

    def test_a_far_future_preflight_cannot_buy_window(self) -> None:
        with self.assertRaisesRegex(rr.ReleaseRequestError, "future"):
            self.check(preflight(age=-timedelta(days=30)))


def git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class RemoteTag(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.remote = self.dir / "remote.git"
        self.work = self.dir / "work"
        git("init", "-q", "--bare", str(self.remote))
        git("init", "-q", str(self.work))
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c", cwd=self.work)
        git("tag", "v0.1.8", cwd=self.work)
        git("remote", "add", "origin", str(self.remote), cwd=self.work)
        git("push", "-q", "origin", "HEAD:refs/heads/main", "refs/tags/v0.1.8", cwd=self.work)

    def test_a_present_tag_reads_as_present(self) -> None:
        self.assertTrue(rr.remote_tag_exists(self.work, "v0.1.8"))

    def test_only_a_no_match_answer_reads_as_absent(self) -> None:
        self.assertFalse(rr.remote_tag_exists(self.work, "v0.1.9"))

    def test_a_failed_lookup_refuses_instead_of_reading_as_absent(self) -> None:
        git("remote", "set-url", "origin", str(self.dir / "missing.git"), cwd=self.work)
        with self.assertRaisesRegex(rr.ReleaseRequestError, "could not look up v0.1.9"):
            rr.remote_tag_exists(self.work, "v0.1.9")
        with self.assertRaisesRegex(rr.ReleaseRequestError, "could not look up"):
            rr.remote_tag_exists(self.work, "v0.1.9", "no-such-remote")


class RemoteReleaseTags(RemoteTag):
    def test_release_tags_are_listed(self) -> None:
        git("tag", "authority/x", cwd=self.work)
        git("push", "-q", "origin", "refs/tags/authority/x", cwd=self.work)
        self.assertEqual(rr.remote_release_tags(self.work), ["v0.1.8"])

    def test_a_failed_listing_refuses(self) -> None:
        git("remote", "set-url", "origin", str(self.dir / "missing.git"), cwd=self.work)
        with self.assertRaisesRegex(rr.ReleaseRequestError, "could not list release tags"):
            rr.remote_release_tags(self.work)


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
