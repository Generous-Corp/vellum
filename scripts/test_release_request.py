#!/usr/bin/env python3
"""release_request.py tags only a new, fully checked, observatory-reconciled project version."""

from __future__ import annotations

import hashlib
import json
import os
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
import version_at_land as val  # noqa: E402

COMMIT = "a" * 40
OTHER = "b" * 40
CMAKE = "project(Vellum\n    VERSION 0.1.10\n    LANGUAGES CXX)\n"
RETIRED = {"schema": "vellum.retired-release-tags.v1", "tags": [{"tag": "v0.1.7"}]}
RULES = [{"type": "required_status_checks",
          "parameters": {"required_status_checks": [{"context": "macos"}, {"context": "provenance-verify"}]}},
         {"type": "pull_request", "parameters": {}}]


NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


ENABLED = {"enabled": True, "enforced_by_owner": False}


def run(name: str, conclusion: str = "success", sha: str = COMMIT, completed: str = "2026-10-27T01:00:00Z",
        ident: int = 1) -> dict:
    return {"name": name, "head_sha": sha, "status": "completed", "conclusion": conclusion,
            "completed_at": completed, "id": ident}


def green() -> list:
    return [{"check_runs": [run("macos")]}, {"check_runs": [run("provenance-verify", ident=2)]}]


class Check(unittest.TestCase):
    def check(self, **overrides):
        args = dict(cmakelists=CMAKE,
                    retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=RULES, pages=green(),
                    immutable=ENABLED, now=NOW)
        args.update(overrides)
        return rr.check(**args)

    def refused(self, pattern: str, **overrides) -> None:
        with self.assertRaisesRegex(rr.ReleaseRequestError, pattern):
            self.check(**overrides)

    def test_positive_control(self) -> None:
        self.assertEqual(self.check(), "v0.1.10")
        # Numeric order: v0.1.9 is older than v0.1.10.
        self.assertEqual(self.check(existing_tags=["v0.1.6", "v0.1.8", "v0.1.9", "not-a-version"]), "v0.1.10")

    def test_a_version_has_one_spelling(self) -> None:
        for spelling in ("0.1.010", "0.1", "0.1.10.1", "01.1.10"):
            with self.subTest(spelling=spelling):
                self.refused("MAJOR.MINOR.PATCH", cmakelists=CMAKE.replace("0.1.10", spelling))

    def test_a_release_only_moves_forward(self) -> None:
        self.refused("not newer than v0.2.0", existing_tags=["v0.1.8", "v0.2.0"])
        self.refused("not newer than v0.1.10", existing_tags=["v0.1.10"], tag_exists=False)
        self.refused("not newer than v0.1.11", existing_tags=["v0.1.11"])
        self.refused("not newer than v0.3.0", retired_tags={"schema": "vellum.retired-release-tags.v1", "tags": [{"tag": "v0.3.0"}]})

    def test_an_existing_tag_is_never_moved(self) -> None:
        self.refused("already exists", tag_exists=True)

    def test_a_retired_tag_is_never_reused(self) -> None:
        self.refused("retired", cmakelists=CMAKE.replace("0.1.10", "0.1.7"))

    def test_the_committed_record_retires_v0_1_9(self) -> None:
        record = json.loads((rr.ROOT / "provenance/retired-release-tags.json").read_text())
        row = {entry["tag"]: entry for entry in record["tags"]}["v0.1.9"]
        self.assertEqual(row["target_commit"], "5f847399829f2d55cd0f540019ee591bd78d8fb5")
        self.assertEqual(row["tag_object"], "a3ce268d0770e1fb3b34130cd0eed4471ea1ce5d")
        self.refused("retired", cmakelists=CMAKE.replace("0.1.10", "0.1.9"), retired_tags=record)

    def test_the_tag_is_the_project_version(self) -> None:
        # The project VERSION is the only statement of the release; there is no
        # second file for it to disagree with.
        self.assertEqual(self.check(cmakelists=CMAKE.replace("0.1.10", "0.1.11")), "v0.1.11")
        self.assertEqual(self.check(cmakelists=CMAKE.replace("0.1.10", "0.2.0"), existing_tags=["v0.1.10"]),
                         "v0.2.0")
        self.refused("declares no project VERSION", cmakelists="project(Vellum LANGUAGES CXX)\n")
        self.assertFalse((rr.ROOT / "provenance/release-request.json").exists())

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
        self.assertEqual(self.check(pages=later_success), "v0.1.10")

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
        return rr.check(cmakelists=CMAKE,
                        retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=LIVE_RULES,
                        pages=[{"check_runs": runs}], immutable=ENABLED, now=NOW,
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
        self.assertEqual(self.check([app_run("gpu-macos-arm64", 1), app_run("provenance-verify", 2)]), "v0.1.10")

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


# The live incident: release-tag run 43565218883 on e50573af8. Main's ruleset
# requires release-path-review and framework-trusted-verify bound to Actions;
# both are commit statuses posted by github-actions[bot], not check runs.
INCIDENT_COMMIT = "e50573af89b7dd193b2837e75af9c5d1bb565363"
INCIDENT_RULES = [{"type": "required_status_checks", "parameters": {"required_status_checks": [
    {"context": "release-policy", "integration_id": 15368},
    {"context": "release-path-review", "integration_id": 15368},
    {"context": "framework-trusted-verify", "integration_id": 15368}]}}]
BOT = {"login": "github-actions[bot]", "type": "Bot", "id": 41898282}


def status(context: str, state: str, ident: int, creator: dict | None = None, created: str | None = None) -> dict:
    return {"id": ident, "context": context, "state": state, "creator": BOT if creator is None else creator,
            "created_at": created}


class StatusSatisfiedChecks(unittest.TestCase):
    REQUIRED = [("framework-trusted-verify", 15368)]

    def unmet(self, statuses: list, runs: list | None = None, required: list | None = None) -> list:
        return rr.unmet(required or self.REQUIRED, runs or [], COMMIT, rr.commit_statuses([statuses]))

    def test_a_bot_status_that_succeeded_meets_the_check(self) -> None:
        self.assertEqual(self.unmet([status("framework-trusted-verify", "success", 5)]), [])

    def test_a_status_from_another_creator_does_not(self) -> None:
        for creator in ({"login": "mallory", "type": "User", "id": 7},
                        {"login": "github-actions[bot]", "type": "User", "id": 41898282},
                        {"login": "github-actions[bot]", "type": "Bot", "id": 99},
                        {"login": "other[bot]", "type": "Bot", "id": 41898282}, {}):
            with self.subTest(creator=creator):
                self.assertEqual(self.unmet([status("framework-trusted-verify", "success", 5, creator)]),
                                 [f"framework-trusted-verify: no run from app 15368 on {COMMIT}"])

    def test_a_forged_newer_failure_from_another_creator_cannot_block_the_bots_success(self) -> None:
        forged = status("framework-trusted-verify", "failure", 9, {"login": "mallory", "type": "User", "id": 7})
        self.assertEqual(self.unmet([forged, status("framework-trusted-verify", "success", 5)]), [])

    def test_pending_is_waited_for_and_not_met(self) -> None:
        rows = rr.commit_statuses([[status("framework-trusted-verify", "pending", 5)]])
        self.assertEqual(rr.pending(self.REQUIRED, [], COMMIT, rows), ["framework-trusted-verify"])
        self.assertEqual(rr.unmet(self.REQUIRED, [], COMMIT, rows), ["framework-trusted-verify: status pending"])
        later = rr.commit_statuses([[status("framework-trusted-verify", "pending", 5),
                                     status("framework-trusted-verify", "success", 6)]])
        self.assertEqual(rr.pending(self.REQUIRED, [], COMMIT, later), [])

    def test_a_newer_failure_status_outranks_an_older_success(self) -> None:
        for state in ("failure", "error"):
            with self.subTest(state=state):
                self.assertEqual(self.unmet([status("framework-trusted-verify", "success", 5),
                                             status("framework-trusted-verify", state, 6)]),
                                 [f"framework-trusted-verify: status {state}"])

    def test_a_check_bound_to_another_app_is_not_met_by_a_status(self) -> None:
        self.assertEqual(self.unmet([status("framework-trusted-verify", "success", 5)],
                                    required=[("framework-trusted-verify", 999)]),
                         [f"framework-trusted-verify: no run from app 999 on {COMMIT}"])
        self.assertEqual(self.unmet([status("framework-trusted-verify", "success", 5)],
                                    required=[("framework-trusted-verify", None)]),
                         [f"framework-trusted-verify: no run on {COMMIT}"])

    def test_a_malformed_statuses_file_refuses(self) -> None:
        good = status("framework-trusted-verify", "success", 5)
        nocreator = {key: value for key, value in good.items() if key != "creator"}
        for pages in ({}, [{}], [[1]], [[nocreator]], [[{**good, "id": "5"}]], [[{**good, "state": None}]]):
            with self.subTest(pages=pages), self.assertRaises(rr.ReleaseRequestError):
                rr.commit_statuses(pages)

    def test_a_run_and_a_status_order_by_time_and_the_worse_wins_a_tie(self) -> None:
        old_ok = status("framework-trusted-verify", "success", 5, created="2026-11-13T01:00:00Z")
        new_bad = status("framework-trusted-verify", "failure", 6, created="2026-11-13T03:00:00Z")
        ok_run = {**app_run("framework-trusted-verify", 1), "head_sha": COMMIT, "started_at": "2026-11-13T02:00:00Z"}
        self.assertEqual(self.unmet([old_ok], [ok_run]), [])
        self.assertEqual(self.unmet([new_bad], [ok_run]), ["framework-trusted-verify: status failure"])
        self.assertEqual(self.unmet([old_ok], [{**ok_run, "conclusion": "failure"}]),
                         ["framework-trusted-verify: completed/failure"])
        self.assertEqual(self.unmet([{**new_bad, "created_at": "2026-11-13T02:00:00Z"}], [ok_run]),
                         ["framework-trusted-verify: status failure"])
        self.assertEqual(self.unmet([{**new_bad, "created_at": None}], [ok_run]),
                         ["framework-trusted-verify: status failure"])

    def test_the_incident_commit(self) -> None:
        runs = [{**app_run("release-policy", 1), "head_sha": INCIDENT_COMMIT}]
        statuses = [[status("release-path-review", "success", 58906707846),
                     status("framework-trusted-verify", "success", 58906704591),
                     status("framework-trusted-verify", "pending", 58906704018)]]
        required = rr.required_checks(INCIDENT_RULES, None)
        self.assertEqual(rr.unmet(required, runs, INCIDENT_COMMIT),
                         [f"framework-trusted-verify: no run from app 15368 on {INCIDENT_COMMIT}",
                          f"release-path-review: no run from app 15368 on {INCIDENT_COMMIT}"])
        self.assertEqual(rr.unmet(required, runs, INCIDENT_COMMIT, rr.commit_statuses(statuses)), [])
        self.assertEqual(rr.pending(required, runs, INCIDENT_COMMIT, rr.commit_statuses(statuses)), [])

    def test_check_takes_the_statuses_and_refuses_without_them_being_well_formed(self) -> None:
        args = dict(cmakelists=CMAKE, retired_tags=RETIRED, commit=COMMIT, tag_exists=False,
                    branch_rules=[{"type": "required_status_checks", "parameters": {"required_status_checks": [
                        {"context": "framework-trusted-verify", "integration_id": 15368}]}}],
                    pages=[{"check_runs": []}], immutable=ENABLED, now=NOW)
        self.assertEqual(rr.check(**args, statuses=[[status("framework-trusted-verify", "success", 5)]]), "v0.1.10")
        with self.assertRaisesRegex(rr.ReleaseRequestError, "no run"):
            rr.check(**args)
        with self.assertRaisesRegex(rr.ReleaseRequestError, "commit status"):
            rr.check(**args, statuses=[["junk"]])


class LiveImmutableReleases(unittest.TestCase):
    """The tag is made only when the live immutable-release answer says releases are immutable."""

    def check(self, answer) -> str:
        return rr.check(cmakelists=CMAKE,
                        retired_tags=RETIRED, commit=COMMIT, tag_exists=False, branch_rules=RULES,
                        pages=green(), immutable=answer, now=NOW)

    def test_an_enabled_answer_tags(self) -> None:
        self.assertEqual(self.check(ENABLED), "v0.1.10")
        self.assertEqual(self.check({"enabled": True, "enforced_by_owner": True}), "v0.1.10")

    def test_a_disabled_missing_or_malformed_answer_refuses(self) -> None:
        for answer in ({"enabled": False, "enforced_by_owner": False}, {"enforced_by_owner": False},
                       {"enabled": True}, {"enabled": 1, "enforced_by_owner": False}, None, [],
                       {"message": "Resource not accessible by integration"}):
            with self.subTest(answer=answer), self.assertRaisesRegex(rr.ReleaseRequestError, "immutable-release"):
                self.check(answer)

    def test_the_read_time_is_a_past_utc_time(self) -> None:
        self.assertEqual(rr.read_time("2026-11-02T01:02:03Z"), datetime(2026, 11, 2, 1, 2, 3, tzinfo=timezone.utc))
        for text in ("2026-11-02T01:02:03+00:00", "yesterday", "", "2999-01-01T00:00:00Z"):
            with self.subTest(text=text), self.assertRaises(rr.ReleaseRequestError):
                rr.read_time(text)

    def test_the_cli_requires_the_live_answer(self) -> None:
        completed = subprocess.run([sys.executable, "-I", str(rr.ROOT / "scripts/release_request.py"), "check",
                                    "--commit", COMMIT, "--branch-rules", "r", "--check-runs", "c", "--statuses", "s",
                                    "--protection", "p"], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--immutable-releases", completed.stderr)


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
            rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.10")

    def test_without_parity_the_promotion_tag_carries_the_mirror_attestation(self) -> None:
        root, head = self.fixture()
        data = rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.10")
        self.assertEqual(data["kind"], "vellum-mirror-promotion-attestation")
        for tag in ("v0.1.11", "v0.2.0"):
            self.assertEqual(rr.attestation(root, head, "2026-10-31T12:00:00Z", tag)["kind"],
                             "vellum-mirror-promotion-attestation", tag)
        self.assertEqual(data["parity"], "not-proven-deferred-to-matrix-refreeze")
        self.assertEqual(data["mirror_receipt"], "provenance/mirror/receipts/20261025-b.json")
        self.assertEqual(data["mirror_receipt_sha256"], hashlib.sha256(
            (root / data["mirror_receipt"]).read_bytes()).hexdigest())
        self.assertEqual(data["release_source_commit"], head)
        self.assertNotIn("parity_completion_sha256", data)

    def test_without_parity_a_later_tag_refuses_before_signing(self) -> None:
        root, head = self.fixture()
        # v0.2.0 is the last waived name; its neighbours are not.
        for tag in ("v0.1.9", "v0.1.12", "v0.2.1", "v0.2.0.1", "v0.3.0", "v1.0.0"):
            with self.subTest(tag=tag), self.assertRaisesRegex(rr.ReleaseRequestError,
                                                                "needs a parity completion record"):
                rr.attestation(root, head, "2026-10-31T12:00:00Z", tag)

    def test_without_a_mirror_receipt_the_attestation_refuses(self) -> None:
        root, _ = self.fixture()
        subprocess.run(["git", "-C", str(root), "rm", "-q", "-r", "provenance/mirror"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "drop receipts"], check=True)
        head = rr.git(root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(rr.ReleaseRequestError, "no mirror export receipt"):
            rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.10")

    def test_with_parity_the_attestation_binds_the_completion(self) -> None:
        root, head = self.fixture(parity=True)
        data = rr.attestation(root, head, "2026-10-31T12:00:00Z", "v0.1.12")
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


class History:
    """A scratch repository shaped like Vellum's main: queue merges and receiver reconciles."""

    def __init__(self, case: unittest.TestCase) -> None:
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@t")
        # Every version surface, so the bot's bump is the real rewrite.
        for surface in val.VERSION_SURFACES:
            self.write(surface, "version 0.1.9\n")
        self.write(rr.CMAKELISTS, CMAKE.replace("0.1.10", "0.1.9"))
        self.write("scripts/install_core.py", "core\n")
        self.write(val.INSTALLER_SUMS, f"{'0' * 64}  install_core.py\n{'0' * 64}  install.sh\n")
        self.write(rr.RETIRED_TAGS, json.dumps(RETIRED) + "\n")
        self.write("src/app.txt", "one\n")
        for report in sorted(rr.EVIDENCE_MODIFIED - {rr.CURSOR}):
            self.write(report, "{}\n")
        self.write(rr.CURSOR, self.cursor("0" * 40))
        self.base = self.commit("root")
        self.git("tag", "v0.1.9", self.base)
        self.main = self.reconcile(self.base)

    def git(self, *args: str) -> str:
        # Strictly increasing commit dates, so history order never depends on
        # two commits sharing a second.
        self.tick = getattr(self, "tick", 0) + 1
        stamp = f"@{1790000000 + self.tick} +0000"
        environment = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                              text=True, env=environment).stdout.strip()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    @staticmethod
    def cursor(vellum: str, repository: str = rr.REPOSITORY) -> str:
        return json.dumps({"schema_version": 2, "state": "active", "reconciled_at": "2026-11-02T00:00:00Z",
                           "pulp": {"last_scanned_commit": "1" * 40},
                           "vellum": {"repository": repository, "last_scanned_commit": vellum}}, indent=2) + "\n"

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def branch(self, start: str) -> None:
        self.git("checkout", "-q", "--detach", start)

    def land(self, head: str) -> str:
        """The merge queue's merge of `head` onto main."""
        self.git("checkout", "-q", "--detach", self.main)
        self.git("merge", "-q", "--no-ff", "--no-edit", head)
        self.main = self.git("rev-parse", "HEAD")
        return self.main

    def reconcile(self, through: str, *, extra: str | None = None, extra_text: str = "changed\n",
                  event: str = "e.yaml", start: str | None = None) -> str:
        """The receiver's coalesce commit: cursor and reports modified, an event added."""
        self.branch(start or through)
        self.write(rr.CURSOR, self.cursor(through))
        self.write(f"{rr.OBSERVATORY}/reports/current.json", json.dumps({"through": through}) + "\n")
        self.write(f"{rr.EVENTS_PREFIX}{through[:8]}-{event}", "kind: observation\n")
        if extra:
            self.write(extra, extra_text)
        return self.commit("chore(provenance): coalesce Pulp observatory evidence")

    def bump(self, version: str = "0.1.10") -> str:
        """A shipped fix, then the queue's merge of the version-at-land bot's bump to `version`."""
        self.branch(self.main)
        self.ships = getattr(self, "ships", 0) + 1
        self.write("foundation/src/core.cpp", f"int x = {self.ships};\n")
        self.land(self.commit("fix: correct the core"))
        self.branch(self.main)
        val.apply(self.root, version)
        self.git("add", "-A")
        self.git("-c", f"user.name={val.BOT_NAME}", "-c", f"user.email={val.BOT_EMAIL}", "commit", "-q", "-m",
                 f"chore(release): bump version to {version}\n\n{val.BOT_TRAILER}")
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.main)
        self.git("merge", "-q", "--no-ff", "-m", "Merge pull request #7 from Generous-Corp/automation/version-bump",
                 head)
        self.main = self.git("rev-parse", "HEAD")
        return self.main

    def change(self, text: str = "two\n") -> str:
        self.branch(self.main)
        self.write("src/app.txt", text)
        return self.land(self.commit("feat: change"))


class TagTargetSelection(unittest.TestCase):
    """Only the receiver's reconcile covering an untagged version bump is tagged."""

    def setUp(self) -> None:
        self.history = History(self)

    def select(self, commit: str, tag_exists: bool = False) -> rr.Selection:
        return rr.select(self.history.root, commit, tag_exists, ["v0.1.9"])

    def test_the_bump_merge_is_refused(self) -> None:
        landed = self.history.bump()
        chosen = self.select(landed)
        self.assertIsNone(chosen.tag)
        self.assertEqual(chosen.version_commit, landed)
        self.assertIn("does not yet contain", chosen.reason)

    def test_the_reconcile_commit_and_its_queue_merge_are_accepted(self) -> None:
        landed = self.history.bump()
        evidence = self.history.reconcile(landed)
        merged = self.history.land(evidence)
        self.assertEqual(self.history.git("rev-parse", f"{merged}^{{tree}}"),
                         self.history.git("rev-parse", f"{evidence}^{{tree}}"))
        for commit in (evidence, merged):
            with self.subTest(commit=commit):
                chosen = self.select(commit)
                self.assertEqual(chosen.tag, "v0.1.10", chosen.reason)
                self.assertEqual((chosen.version_commit, chosen.cursor), (landed, landed))

    def test_a_reconcile_after_later_changes_still_covers_the_bump(self) -> None:
        landed = self.history.bump()
        later = self.history.change()
        merged = self.history.land(self.history.reconcile(later))
        chosen = self.select(merged)
        self.assertEqual(chosen.tag, "v0.1.10", chosen.reason)
        self.assertEqual((chosen.version_commit, chosen.cursor), (landed, later))

    def test_a_reconcile_for_a_non_bump_change_is_ignored(self) -> None:
        # The version on main is already tagged: an ordinary change's reconcile releases nothing.
        changed = self.history.change()
        merged = self.history.land(self.history.reconcile(changed))
        chosen = self.select(merged, tag_exists=True)
        self.assertIsNone(chosen.tag)
        self.assertIn("already exists", chosen.reason)
        # ... and a reconcile of a change made before the bump landed does not release it.
        stale = self.history.reconcile(changed)
        self.history.bump()
        merged = self.history.land(stale)
        chosen = self.select(merged)
        self.assertIsNone(chosen.tag)

    def test_a_reconcile_landed_behind_another_change_is_not_tagged(self) -> None:
        landed = self.history.bump()
        evidence = self.history.reconcile(landed)
        self.history.change()
        merged = self.history.land(evidence)
        chosen = self.select(merged)
        self.assertIsNone(chosen.tag)
        self.assertIn("not an observatory-reconciled commit", chosen.reason)

    def test_anything_but_evidence_after_the_cursor_is_refused(self) -> None:
        landed = self.history.bump()
        evidence_only = "not observatory evidence"
        cases = {
            "code": (dict(extra="src/app.txt"), evidence_only),
            "workflow": (dict(extra=".github/workflows/release-tag.yml"), evidence_only),
            "added file": (dict(extra="docs/new.md"), evidence_only),
            # CMakeLists.txt changed without its version is not evidence either.
            "build file": (dict(extra=rr.CMAKELISTS, extra_text=CMAKE + "# changed\n"), evidence_only),
            "nested event": (dict(event="nested/e.yaml"), evidence_only),
            "non-yaml event": (dict(event="e.json"), evidence_only),
            # Changing the version makes this commit the version's landing,
            # which no cursor in its own tree can contain.
            "version": (dict(extra=rr.CMAKELISTS, extra_text=CMAKE.replace("0.1.10", "0.1.11")),
                        "not landed by version-at-land"),
        }
        for name, (change, reason) in cases.items():
            with self.subTest(name):
                evidence = self.history.reconcile(landed, start=landed, **change)
                chosen = self.select(evidence)
                self.assertIsNone(chosen.tag, name)
                self.assertIn(reason, chosen.reason)

    def test_a_non_linear_evidence_history_is_refused(self) -> None:
        # Two evidence commits on the cursor, joined by a merge whose tree is the
        # second one's: every diff against the commit before it is evidence, but
        # the tag's provenance run rejects the second commit as discontinuous.
        landed = self.history.bump()
        first = self.history.reconcile(landed, start=landed, event="a.yaml")
        self.history.branch(landed)
        for event in ("a.yaml", "b.yaml"):
            self.history.write(f"{rr.EVENTS_PREFIX}{landed[:8]}-{event}", "kind: observation\n")
        self.history.write(rr.CURSOR, History.cursor(landed))
        self.history.write(f"{rr.OBSERVATORY}/reports/current.json", json.dumps({"through": landed}) + "\n")
        second = self.history.commit("evidence with both events")
        self.history.git("checkout", "-q", "--detach", first)
        self.history.git("merge", "-q", "--no-ff", "--no-edit", second)
        joined = self.history.git("rev-parse", "HEAD")
        self.assertEqual(self.history.git("rev-parse", f"{joined}^{{tree}}"),
                         self.history.git("rev-parse", f"{second}^{{tree}}"))
        chosen = self.select(joined)
        self.assertIsNone(chosen.tag)
        self.assertIn("not one linear chain", chosen.reason)
        sys.path.insert(0, str(rr.ROOT / "tools/provenance"))
        import observatory  # noqa: PLC0415
        with self.assertRaisesRegex(observatory.ObservatoryError, "discontinuous"):
            observatory.verify_vellum_observatory_tail(self.history.root, landed, joined)

    def test_a_merge_that_changes_its_own_tree_is_refused(self) -> None:
        # The queue's merge of the receiver's commit is accepted only because
        # its tree equals that commit's; a merge that adds its own change is not.
        landed = self.history.bump()
        evidence = self.history.reconcile(landed)
        self.history.git("checkout", "-q", "--detach", landed)
        self.history.git("merge", "-q", "--no-ff", "--no-commit", evidence)
        self.history.write("src/app.txt", "smuggled\n")
        evil = self.history.commit("Merge pull request")
        self.assertEqual(self.history.git("rev-list", "--parents", "-n", "1", evil).split()[1:], [landed, evidence])
        chosen = self.select(evil)
        self.assertIsNone(chosen.tag)
        self.assertIn("not one linear chain", chosen.reason)

    def test_an_empty_commit_after_the_cursor_is_refused(self) -> None:
        landed = self.history.bump()
        self.history.branch(self.history.reconcile(landed))
        empty = self.history.commit("empty")
        self.assertIn("changes nothing", self.select(empty).reason)

    def test_a_cursor_off_the_pushed_history_is_refused(self) -> None:
        landed = self.history.bump()
        self.history.branch(landed)
        side = self.history.commit("side commit never landed")
        evidence = self.history.reconcile(side, start=landed)
        chosen = self.select(evidence)
        self.assertIsNone(chosen.tag)
        self.assertIn("not a strict ancestor", chosen.reason)

    def test_a_deleted_report_or_a_retyped_cursor_is_refused(self) -> None:
        landed = self.history.bump()
        evidence = self.history.reconcile(landed)
        self.history.branch(evidence)
        self.history.git("rm", "-q", f"{rr.OBSERVATORY}/reports/current.md")
        deleted = self.history.commit("delete a report")
        self.assertIn("not observatory evidence: D", self.select(deleted).reason)
        # A report replaced by a symlink is a type change (T), not evidence.
        self.history.branch(evidence)
        report = self.history.root / rr.OBSERVATORY / "reports/current.json"
        report.unlink()
        report.symlink_to("current.md")
        retyped = self.history.commit("report becomes a symlink")
        self.assertIn("not observatory evidence: T", self.select(retyped).reason)
        # A cursor replaced by a symlink is not readable as a cursor at all, which refuses loudly.
        self.history.branch(evidence)
        cursor = self.history.root / rr.CURSOR
        cursor.unlink()
        cursor.symlink_to("reports/current.json")
        with self.assertRaisesRegex(rr.ReleaseRequestError, "not JSON"):
            self.select(self.history.commit("cursor becomes a symlink"))

    def test_a_deleted_or_renamed_evidence_file_is_refused(self) -> None:
        landed = self.history.bump()
        evidence = self.history.reconcile(landed)
        self.history.branch(evidence)
        event = next((self.history.root / rr.EVENTS_PREFIX).glob("*.yaml"))
        self.history.git("mv", str(event), str(event.with_name("renamed.yaml")))
        renamed = self.history.commit("rename an event")
        self.assertIn("not observatory evidence: D", self.select(renamed).reason)

    def test_a_path_cannot_inject_a_workflow_command_through_the_reason(self) -> None:
        landed = self.history.bump()
        self.history.branch(self.history.reconcile(landed))
        self.history.write("src/x\n::error::injected", "x\n")
        hostile = self.history.commit("hostile path")
        reason = self.select(hostile).reason
        self.assertIn("\n", reason)
        self.assertNotIn("\n", rr.one_line(reason))
        self.assertIn("src/x\\n::error::injected", rr.one_line(reason))

    def test_an_existing_tag_is_never_reselected(self) -> None:
        landed = self.history.bump()
        merged = self.history.land(self.history.reconcile(landed))
        self.assertIsNone(self.select(merged, tag_exists=True).tag)

    def test_a_malformed_cursor_or_version_refuses_loudly(self) -> None:
        landed = self.history.bump()
        for name, text in (("foreign repository", History.cursor(landed, "someone/vellum")),
                           ("short commit", History.cursor("abc")), ("not JSON", "{"),
                           # An abbreviated commit git would resolve is still not a full SHA.
                           ("abbreviated commit", History.cursor(landed[:12])),
                           ("not active", History.cursor(landed).replace('"active"', '"prepared"'))):
            with self.subTest(name):
                self.history.branch(landed)
                self.history.write(rr.CURSOR, text)
                bad = self.history.commit("bad cursor")
                with self.assertRaises(rr.ReleaseRequestError):
                    self.select(bad)
        with self.assertRaisesRegex(rr.ReleaseRequestError, "full SHA"):
            self.select("abc")

    def test_a_side_branch_that_repeats_the_bump_does_not_move_its_landing(self) -> None:
        # A branch cut before the bump lands, setting the same version, then
        # merged: the newest commit that changed the file is on that branch, but
        # main's first-parent chain still says the version landed at `landed`.
        before = self.history.main
        landed = self.history.bump()
        self.history.branch(before)
        self.history.write(rr.CMAKELISTS, CMAKE)
        repeat = self.history.commit("repeat the bump on a stale branch")
        self.history.land(repeat)
        self.assertEqual(rr.version_landing(self.history.root, self.history.main), landed)

    def test_the_version_landing_is_the_first_parent_merge(self) -> None:
        landed = self.history.bump()
        self.history.change()
        self.history.land(self.history.reconcile(self.history.main))
        self.assertEqual(rr.version_landing(self.history.root, self.history.main), landed)
        # The bump branch's own commit is not on main's first-parent chain.
        self.assertNotEqual(rr.version_landing(self.history.root, self.history.main),
                            self.history.git("rev-parse", f"{landed}^2"))

    def test_a_build_change_to_cmakelists_does_not_move_the_landing(self) -> None:
        # A later change to CMakeLists.txt that leaves the version alone is not
        # a version landing; only a change to the version is.
        landed = self.history.bump()
        self.history.branch(self.history.main)
        self.history.write(rr.CMAKELISTS, CMAKE + "add_subdirectory(more)\n")
        self.history.land(self.history.commit("build: add a subdirectory"))
        self.assertEqual(rr.version_landing(self.history.root, self.history.main), landed)
        merged = self.history.land(self.history.reconcile(self.history.main))
        chosen = self.select(merged)
        self.assertEqual((chosen.tag, chosen.version_commit), ("v0.1.10", landed), chosen.reason)

    def test_only_a_version_newer_than_every_tag_is_selected(self) -> None:
        # Must-pass: a bump past the newest tag is selected. Must-fail: the
        # same reconcile with an equal, older or retired version is not.
        self.history.git("tag", "v0.1.10", self.history.main)
        landed = self.history.bump("0.1.11")
        merged = self.history.land(self.history.reconcile(landed))
        self.assertEqual(rr.select(self.history.root, merged, False, ["v0.1.9", "v0.1.10"]).tag, "v0.1.11")
        for tags, reason in ((["v0.1.11"], "not newer than v0.1.11"), (["v0.1.12"], "not newer than v0.1.12"),
                             (["v0.2.0", "v0.1.6"], "not newer than v0.2.0")):
            with self.subTest(tags=tags):
                chosen = rr.select(self.history.root, merged, False, tags)
                self.assertIsNone(chosen.tag)
                self.assertIn(reason, chosen.reason)
        # A retired version is never selected, even when no tag of that name exists.
        self.history.branch(self.history.main)
        self.history.write(rr.CMAKELISTS, CMAKE.replace("0.1.10", "0.1.7"))
        retired = self.history.land(self.history.commit("set a retired version"))
        merged = self.history.land(self.history.reconcile(retired))
        chosen = rr.select(self.history.root, merged, False, ["v0.1.6"])
        self.assertIsNone(chosen.tag)
        self.assertIn("retired", chosen.reason)

    def ship(self, title: str) -> str:
        self.history.branch(self.history.main)
        self.history.ships = getattr(self.history, "ships", 0) + 1
        self.history.write("runtime/s.cpp", f"int s = {self.history.ships};\n")
        return self.history.land(self.history.commit(title))

    def test_a_breaking_change_after_the_bump_refuses_the_patch_at_select(self) -> None:
        # Must-fail: the reconcile would ship a breaking change under 0.1.10.
        landed = self.history.bump()
        self.ship("feat!: replace the scene API")
        merged = self.history.land(self.history.reconcile(self.history.main))
        chosen = self.select(merged)
        self.assertIsNone(chosen.tag)
        self.assertIn("too low for what it would ship", chosen.reason)
        self.assertIn("needs at least 0.2.0", chosen.reason)
        self.assertEqual(chosen.version_commit, landed)

    def test_a_harmless_fix_after_the_bump_keeps_the_patch(self) -> None:
        # Must-pass control for the rule above.
        landed = self.history.bump()
        self.ship("fix: another core fix")
        merged = self.history.land(self.history.reconcile(self.history.main))
        chosen = self.select(merged)
        self.assertEqual(chosen.tag, "v0.1.10", chosen.reason)
        self.assertEqual(chosen.version_commit, landed)

    def test_only_the_bots_exact_bump_is_selected(self) -> None:
        # Must-pass is every test above. Must-fail: the same reconcile shape
        # around a version the bot did not land exactly as it derives it.
        def human(version: str, extra: dict[str, str] | None = None) -> str:
            self.history.branch(self.history.main)
            self.history.write("foundation/src/core.cpp", "int human;\n")
            self.history.land(self.history.commit("fix: ship"))
            self.history.branch(self.history.main)
            val.apply(self.history.root, version)
            for path, text in (extra or {}).items():
                self.history.write(path, text)
            return self.history.land(self.history.commit(f"release: {version}"))

        for name, version, extra in (("a human pull request setting 9.0.0", "9.0.0", None),
                                     ("a human pull request with the bot's exact rewrite", "0.1.10", None)):
            with self.subTest(name):
                history = History(self)
                self.history = history
                landed = human(version, extra)
                merged = history.land(history.reconcile(landed))
                chosen = rr.select(history.root, merged, False, ["v0.1.9"])
                self.assertIsNone(chosen.tag)
                self.assertIn("not landed by version-at-land", chosen.reason)
        # The bot's merge with a smuggled hunk is refused too.
        history = History(self)
        self.history = history
        history.branch(history.main)
        history.write("foundation/src/core.cpp", "int x;\n")
        history.land(history.commit("fix: ship"))
        history.branch(history.main)
        val.apply(history.root, "0.1.10")
        history.write("src/app.txt", "smuggled\n")
        history.git("add", "-A")
        history.git("-c", f"user.name={val.BOT_NAME}", "-c", f"user.email={val.BOT_EMAIL}", "commit", "-q", "-m",
                    f"chore(release): bump version to 0.1.10\n\n{val.BOT_TRAILER}")
        head = history.git("rev-parse", "HEAD")
        history.git("checkout", "-q", "--detach", history.main)
        history.git("merge", "-q", "--no-ff", "-m", "Merge pull request #8 from Generous-Corp/automation/version-bump",
                    head)
        history.main = history.git("rev-parse", "HEAD")
        merged = history.land(history.reconcile(history.main))
        chosen = rr.select(history.root, merged, False, ["v0.1.9"])
        self.assertIsNone(chosen.tag)
        self.assertIn("beyond the rewrite", chosen.reason)

    def test_the_selection_agrees_with_the_tag_provenance_tail_check(self) -> None:
        # Whatever select() accepts, the tag's provenance run accepts too.
        sys.path.insert(0, str(rr.ROOT / "tools/provenance"))
        import observatory  # noqa: PLC0415
        landed = self.history.bump()
        evidence = self.history.reconcile(landed)
        merged = self.history.land(evidence)
        for commit in (evidence, merged):
            chosen = self.select(commit)
            self.assertIsNotNone(chosen.tag)
            observatory.verify_vellum_observatory_tail(self.history.root, chosen.cursor, commit)
        with self.assertRaises(observatory.ObservatoryError):
            observatory.verify_vellum_observatory_tail(self.history.root, rr.observatory_cursor(
                self.history.root, landed), landed)

    def test_the_cli_prints_the_selection(self) -> None:
        landed = self.history.bump()
        merged = self.history.land(self.history.reconcile(landed))
        remote = self.history.root.parent / (self.history.root.name + "-remote.git")
        self.addCleanup(shutil.rmtree, remote, True)
        git("init", "-q", "--bare", str(remote))
        self.history.git("remote", "add", "origin", str(remote))
        script = self.history.root / "scripts/release_request.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        for name in ("release_request.py", "retired_release_tags.py", "verify_immutable_release_preflight.py",
                     "version_at_land.py"):
            shutil.copy2(rr.ROOT / "scripts" / name, script.parent / name)
        self.history.git("push", "-q", "origin", "refs/tags/v0.1.9")

        def run(commit: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, "-I", str(script), "select", "--commit", commit],
                                  capture_output=True, text=True, check=False)

        chosen = run(merged)
        self.assertEqual(chosen.returncode, 0, chosen.stderr)
        self.assertEqual(chosen.stdout.splitlines(), ["tag=v0.1.10", f"version-commit={landed}",
                                                      f"cursor={landed}", f"pulp-cursor={'1' * 40}"])
        refused = run(landed)
        self.assertEqual(refused.returncode, 0, refused.stderr)
        self.assertEqual(refused.stdout.splitlines()[0], "tag=")
        git("tag", "v0.1.10", merged, cwd=self.history.root)
        git("push", "-q", "origin", "refs/tags/v0.1.10", cwd=self.history.root)
        self.assertEqual(run(merged).stdout.splitlines()[0], "tag=")
        self.history.git("remote", "set-url", "origin", str(remote) + "-missing")
        failed = run(merged)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("could not list release tags", failed.stderr)


class CheckCli(unittest.TestCase):
    """`release_request.py check` refuses a landing version-at-land did not make, before any other input."""

    def run_check(self, history: History, commit: str) -> subprocess.CompletedProcess[str]:
        remote = history.root.parent / (history.root.name + "-remote.git")
        self.addCleanup(shutil.rmtree, remote, True)
        git("init", "-q", "--bare", str(remote))
        history.git("remote", "add", "origin", str(remote))
        history.git("push", "-q", "origin", "refs/tags/v0.1.9")
        scripts = history.root / "scripts"
        for name in ("release_request.py", "retired_release_tags.py", "verify_immutable_release_preflight.py",
                     "version_at_land.py"):
            shutil.copy2(rr.ROOT / "scripts" / name, scripts / name)
        history.git("checkout", "-q", "--detach", commit)
        missing = str(history.root / "not-read-yet.json")
        return subprocess.run([sys.executable, "-I", str(scripts / "release_request.py"), "check", "--commit", commit,
                               "--branch-rules", missing, "--check-runs", missing, "--statuses", missing,
                               "--protection", missing,
                               "--immutable-releases", missing, "--immutable-read-at", "2026-11-01T00:00:00Z"],
                              capture_output=True, text=True, check=False)

    def test_a_hand_set_version_is_refused_by_check(self) -> None:
        history = History(self)
        history.branch(history.main)
        val.apply(history.root, "0.1.10")
        landed = history.land(history.commit("release: 0.1.10 by hand"))
        merged = history.land(history.reconcile(landed))
        done = self.run_check(history, merged)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not landed by version-at-land", done.stderr)

    def test_a_patch_short_of_a_later_breaking_change_is_refused_by_check(self) -> None:
        history = History(self)
        history.bump()
        history.branch(history.main)
        history.write("runtime/s.cpp", "int s;\n")
        history.land(history.commit("feat!: replace the scene API"))
        merged = history.land(history.reconcile(history.main))
        done = self.run_check(history, merged)
        self.assertEqual(done.returncode, 1)
        self.assertIn("too low for what it would ship", done.stderr)

    def test_the_bots_bump_passes_check_to_the_next_input(self) -> None:
        history = History(self)
        merged = history.land(history.reconcile(history.bump()))
        done = self.run_check(history, merged)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("version-at-land", done.stderr)
        self.assertIn("not-read-yet.json", done.stderr)


class RealHistoryReplay(unittest.TestCase):
    """Replay the tag's reconciled observatory check on Vellum's own release history.

    v0.1.8 and v0.1.9 tagged the commit that landed their request and could
    never pass it; v0.1.6 tagged a dedicated reconcile commit and did. The
    commit select() picks after v0.1.9's request, #139's reconcile and its
    queue merge, passes it. release-policy.yml checks out full history so
    these commits are present; a missing one fails rather than skips.
    """

    V0_1_6 = "9595737b1903819bc497ee41f870b0f36e667c42"
    V0_1_8 = "b9953df49ac6568dca069d7c18d7755556d1104f"
    V0_1_9 = "5f847399829f2d55cd0f540019ee591bd78d8fb5"
    RECONCILE = "6302e0061dd18a325683ac9dd0b4433227c24a54"
    RECONCILE_MERGE = "63dcb77e1e1b5b2fdabdd43932effe8de9a17e07"

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(rr.ROOT / "tools/provenance"))
        import observatory  # noqa: PLC0415
        cls.observatory = observatory
        for commit in (cls.V0_1_6, cls.V0_1_8, cls.V0_1_9, cls.RECONCILE, cls.RECONCILE_MERGE):
            if subprocess.run(["git", "-C", str(rr.ROOT), "cat-file", "-e", f"{commit}^{{commit}}"],
                              capture_output=True).returncode != 0:
                raise AssertionError(f"{commit} is missing: replay needs the full history (fetch-depth: 0)")

    def tail(self, target: str) -> None:
        # The cursor as provenance reads it; v0.1.6's was still `prepared`.
        cursor = json.loads(rr.blob_at(rr.ROOT, target, rr.CURSOR))["vellum"]["last_scanned_commit"]
        self.observatory.verify_vellum_observatory_tail(rr.ROOT, cursor, target)

    def test_the_request_commits_v0_1_8_and_v0_1_9_fail(self) -> None:
        for target in (self.V0_1_8, self.V0_1_9):
            with self.subTest(target=target), self.assertRaisesRegex(
                    self.observatory.ObservatoryError, "discontinuous|non-evidence|linear"):
                self.tail(target)

    def test_the_reconcile_commit_v0_1_6_passes(self) -> None:
        self.tail(self.V0_1_6)

    def test_the_reconcile_after_v0_1_9_passes_the_tail_but_its_hand_bump_is_not_a_release(self) -> None:
        # #139's reconcile passes the tag's tail check. Its version was set by a
        # person (#137), not by version-at-land, so today it is not selected.
        self.assertIsNone(rr.select(rr.ROOT, self.V0_1_9, tag_exists=False).tag)
        self.assertEqual(rr.git(rr.ROOT, "rev-parse", f"{self.RECONCILE_MERGE}^2"), self.RECONCILE)
        for target in (self.RECONCILE, self.RECONCILE_MERGE):
            with self.subTest(target=target):
                self.assertEqual(rr.version_landing(rr.ROOT, target), self.V0_1_9)
                self.tail(target)
                chosen = rr.select(rr.ROOT, target, tag_exists=False, existing_tags=["v0.1.6", "v0.1.8"])
                self.assertIsNone(chosen.tag)
                self.assertIn("does not merge the automation/version-bump branch", chosen.reason)
        # With the tag present, as it is, the same reconcile releases nothing.
        self.assertIsNone(rr.select(rr.ROOT, self.RECONCILE_MERGE, tag_exists=True).tag)

    V0_1_10 = "6b0bee8e61e49c21076af5d6cd6bfa29a06ed75b"
    V0_1_10_BUMP = "23e59fb4b04f382a283b049c5df4086cf4c49612"

    def test_v0_1_10s_landing_is_found_and_its_hand_bump_is_refused(self) -> None:
        # The commit v0.1.10 tags, replayed as if its tag did not exist yet,
        # with the release tags that existed before it. Its version landed in
        # #141's merge (a person's bump), which the landing rule finds and the
        # bot-bump rule refuses.
        before = ["v0.1.0", "v0.1.1", "v0.1.6", "v0.1.8", "v0.1.9"]
        self.assertEqual(rr.version_landing(rr.ROOT, self.V0_1_10), self.V0_1_10_BUMP)
        self.tail(self.V0_1_10)
        chosen = rr.select(rr.ROOT, self.V0_1_10, tag_exists=False, existing_tags=before)
        self.assertIsNone(chosen.tag)
        self.assertEqual(chosen.version_commit, self.V0_1_10_BUMP)
        self.assertIn("not landed by version-at-land", chosen.reason)
        # The same commit selects nothing once v0.1.10 exists, or with a newer tag.
        self.assertIsNone(rr.select(rr.ROOT, self.V0_1_10, tag_exists=True).tag)
        self.assertIsNone(rr.select(rr.ROOT, self.V0_1_10, tag_exists=False,
                                    existing_tags=[*before, "v0.1.10"]).tag)


class ReleaseTagWorkflow(unittest.TestCase):
    """release-tag.yml wires the selection, the live immutable read and the waits as described."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (rr.ROOT / ".github/workflows/release-tag.yml").read_text()
        _, _, jobs = cls.text.partition("\njobs:\n")
        cls.select_job, _, cls.sign_job = jobs.partition("\n  sign-release-tag:\n")

    def step(self, title: str) -> str:
        start = self.sign_job.index(f"- name: {title}")
        following = self.sign_job.find("\n      - name:", start + 1)
        return self.sign_job[start:following if following != -1 else None]

    def test_only_an_observatory_reconcile_starts_it(self) -> None:
        trigger = self.text[self.text.index("\non:\n"):self.text.index("\npermissions:")]
        self.assertIn("paths: [provenance/pulp-observatory/cursor.json]", trigger)
        self.assertNotIn("release-request.json", trigger)
        self.assertNotIn("immutable-release-preflight", self.text)
        # The version comes from the tagged commit's CMakeLists.txt; no request file is read.
        self.assertNotIn("release-request", self.text)

    def test_the_selection_job_holds_no_secret_and_no_environment(self) -> None:
        self.assertNotIn("secrets.", self.select_job)
        code = "\n".join(line for line in self.select_job.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("environment", code)
        self.assertNotIn("outputs.token", self.select_job)
        self.assertIn("python3 -I scripts/release_request.py select --commit \"$GITHUB_SHA\"", self.select_job)

    def test_the_signing_job_runs_only_for_a_selected_commit_and_decides_again(self) -> None:
        self.assertIn("needs: select-release-commit", self.sign_job)
        self.assertIn("if: needs.select-release-commit.outputs.tag != ''", self.sign_job)
        self.assertIn("environment: release", self.sign_job)
        decide = self.step("Decide whether the version may be tagged")
        self.assertIn("release_request.py select --commit \"$GITHUB_SHA\"", decide)
        self.assertIn('= "$SELECTED_TAG"', decide.split("release_request.py select", 1)[1].split("\n")[2])
        self.assertIn('test "$tag" = "$SELECTED_TAG"', decide)
        self.assertIn('--immutable-releases "$RUNNER_TEMP/immutable-releases.json"', decide)
        self.assertLess(decide.index("release_request.py pending"), decide.index("release_request.py check"))

    def test_immutable_releases_are_read_live_with_the_app_token(self) -> None:
        read = self.step("Read main's required checks and the immutable-release setting")
        self.assertIn("GH_TOKEN: ${{ steps.app-token.outputs.token }}", read)
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"',
                      read)
        self.assertNotIn("python", read)

    def test_the_push_uses_a_token_minted_after_the_wait(self) -> None:
        self.assertLess(self.sign_job.index("- name: Decide whether the version may be tagged"),
                        self.sign_job.index("id: push-token"))
        push = self.step("Push the tag and freeze it")
        self.assertIn("GH_TOKEN: ${{ steps.push-token.outputs.token }}", push)
        self.assertNotIn("python", push)

    def test_the_tag_content_checks_run_before_the_selection_is_released(self) -> None:
        # Whatever can refuse the tag must refuse it before it exists: the job's
        # tag output comes only from the last step, after the content checks,
        # and the signing job needs this job.
        self.assertIn("tag: ${{ steps.release.outputs.tag }}", self.select_job)
        self.assertNotIn("tag: ${{ steps.select.outputs.tag }}", self.select_job)
        order = [self.select_job.index(f"- name: {name}") for name in (
            "Decide whether this commit is the release's reconcile", "Check out Pulp main for release readiness",
            "Check out the Pulp commit the observatory cursor names",
            "Run the tag's content checks before the tag exists", "Release the selection")]
        self.assertEqual(order, sorted(order))
        checks = self.select_job[order[3]:order[4]]
        self.assertIn("if: steps.select.outputs.tag != ''", checks)
        self.assertIn('python3 -I scripts/pretag_checks.py --tag "$TAG" --commit "$GITHUB_SHA"', checks)
        self.assertNotIn("continue-on-error", self.select_job)
        self.assertIn("path: .exact-boundary-pulp", self.select_job)
        self.assertIn("ref: ${{ steps.select.outputs.pulp-cursor }}", self.select_job)
        self.assertIn("needs: select-release-commit", self.sign_job)
        self.assertIn("if: needs.select-release-commit.outputs.tag != ''", self.sign_job)

    def test_the_tag_and_attestation_bind_the_pushed_commit(self) -> None:
        self.assertIn('attestation --commit "$GITHUB_SHA"', self.sign_job)
        self.assertIn('tag -s -F "$RUNNER_TEMP/promotion-attestation.json" "$TAG" "$GITHUB_SHA"', self.sign_job)
        self.assertIn('--source-commit "$GITHUB_SHA"', self.sign_job)
        self.assertIn("ref: ${{ github.sha }}", self.sign_job)


class Freeze(unittest.TestCase):
    def test_the_freeze_satisfies_the_release_finalizer(self) -> None:
        body = dict(rr.freeze_ruleset("v0.1.10"), id=7, source_type="Repository", source="Generous-Corp/vellum")
        ruleset.verify([body], repository="Generous-Corp/vellum", tag="v0.1.10")

    def test_github_stored_freeze_satisfies_the_release_finalizer(self) -> None:
        # The finalizer reads the ruleset back from GitHub, which stores the
        # update rule's default `update_allows_fetch_and_merge: false` as a
        # bare `{"type": "update"}`. The posted freeze for v0.1.9 is exactly
        # that live read-back, apart from GitHub's normalization and its own
        # fields, and the finalizer accepts what GitHub returned.
        live = json.loads((Path(__file__).resolve().parent / "fixtures"
                           / "freeze-v0.1.9-ruleset-readback.json").read_text(encoding="utf-8"))
        posted = rr.freeze_ruleset("v0.1.9")
        stored = {key: live[key] for key in posted}
        normalized_rules = [
            {"type": rule["type"]} if rule.get("parameters") == {"update_allows_fetch_and_merge": False}
            else rule
            for rule in posted["rules"]
        ]
        self.assertEqual(stored, dict(posted, rules=normalized_rules))
        ruleset.verify([live], repository="Generous-Corp/vellum", tag="v0.1.9")

    def test_a_malformed_tag_refuses(self) -> None:
        with self.assertRaises(rr.ReleaseRequestError):
            rr.freeze_ruleset("v0.1")


if __name__ == "__main__":
    unittest.main()
