#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("verify_authority_activation.py")
SPEC = importlib.util.spec_from_file_location("verify_authority_activation", MODULE_PATH)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


class FakeGitHub:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.requests: list[tuple[str, str]] = []

    def get(self, path: str, token: str) -> object:
        self.requests.append((path, token))
        if path not in self.responses:
            raise AssertionError(f"unexpected GitHub request: {path}")
        return self.responses[path]


class AuthorityActivationTests(unittest.TestCase):
    def test_current_repository_is_prepared_and_cannot_activate(self) -> None:
        root = MODULE_PATH.parents[2]
        with tempfile.TemporaryDirectory() as directory:
            prepared = Path(directory)
            (prepared / activation.PLAN_PATH.parent).mkdir(parents=True)
            shutil.copy2(root / activation.PLAN_PATH, prepared / activation.PLAN_PATH)
            shutil.copy2(root / activation.TRUST_PATH, prepared / activation.TRUST_PATH)
            trust = activation.load_json(prepared / activation.TRUST_PATH)
            trust["state"] = "disabled-for-test"
            (prepared / activation.TRUST_PATH).write_text(
                json.dumps(trust, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = activation.verify_prepared(prepared)
            self.assertEqual(result["status"], "prepared-not-active")
            records = prepared / "provenance/authority/records"
            records.mkdir(parents=True)
            (records / "pending.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                activation.ActivationError, "must not contain"
            ):
                activation.verify_prepared(prepared)
        with tempfile.TemporaryDirectory() as directory:
            ready_root = Path(directory)
            (ready_root / activation.PLAN_PATH.parent).mkdir(parents=True)
            shutil.copy2(
                root / activation.PLAN_PATH,
                ready_root / activation.PLAN_PATH,
            )
            shutil.copy2(
                root / activation.TRUST_PATH,
                ready_root / activation.TRUST_PATH,
            )
            ready = activation.verify_ready(ready_root)
            self.assertEqual(ready["status"], "ready-for-pending-record")
            policy = activation.validate_trust_policy(
                activation.load_json(ready_root / activation.TRUST_PATH)
            )
            self.assertEqual(policy["state"], "enabled")

    def test_pending_record_must_be_exact_single_commit_and_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Vellum Test"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "vellum-test@example.invalid"],
                cwd=root,
                check=True,
            )
            (root / "authority-start.txt").write_text("start\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "authority start"],
                cwd=root,
                check=True,
            )
            authority_start = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            record_path = (
                root
                / "provenance/authority/records/native-design-kernel-v1.json"
            )
            record_path.parent.mkdir(parents=True)
            record = {
                "schema_version": 2,
                "state": "pending-pulp-activation",
                "source_repository": "Generous-Corp/pulp",
                "framework_repository": "Generous-Corp/vellum",
                "pulp_extraction_base": "a" * 40,
                "historical_seed_commit": "b" * 40,
                "pulp_candidate_commit": "c" * 40,
                "pulp_ownership_projection_blob": "d" * 40,
                "authority_start_commit": authority_start,
                "authority_record_ref": (
                    "refs/tags/authority/native-design-kernel-v1"
                ),
                "cut_manifest_sha256": "e" * 64,
                "authority_groups": [
                    {
                        "id": "native-design-kernel-v1",
                        "lineage_mode": (
                            "history-seed-ancestor-active-reimplementation"
                        ),
                        "pulp_legacy_slices": ["render"],
                        "pulp_historical_seed_projection": {
                            "render.cpp": {
                                "blob": "f" * 40,
                                "mode": "100644",
                                "classification": "framework-core",
                            }
                        },
                        "pulp_activation_candidate_projection": {
                            "render.cpp": {
                                "blob": "1" * 40,
                                "mode": "100644",
                            }
                        },
                        "vellum_implementation_projection": {
                            "graphics/render.cpp": {
                                "blob": "2" * 40,
                                "mode": "100644",
                            }
                        },
                    }
                ],
                "pulp_activation": None,
                "approved_by": "@danielraffel",
                "approved_at": "2026-07-23T23:23:20Z",
            }
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "authority record"],
                cwd=root,
                check=True,
            )
            record_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            relative_record = record_path.relative_to(root).as_posix()
            with mock.patch.object(
                activation, "verify_structural_record", return_value=record
            ):
                result = activation.verify_pending_record(
                    root=root,
                    pulp_repo=root,
                    pulp_ownership_commit="c" * 40,
                    record_path=relative_record,
                    authority_record_commit=record_commit,
                    expected_authority_ref=record["authority_record_ref"],
                    require_head=True,
                )
                self.assertEqual(result["status"], "pending-pulp-activation")
                with self.assertRaisesRegex(
                    activation.ActivationError, "expected checkout ref"
                ):
                    activation.verify_pending_record(
                        root=root,
                        pulp_repo=root,
                        pulp_ownership_commit="c" * 40,
                        record_path=relative_record,
                        authority_record_commit=record_commit,
                        expected_authority_ref="refs/tags/authority/wrong",
                        require_head=True,
                    )

                record_path.write_text("{}\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    activation.ActivationError, "authority record fields differ"
                ):
                    activation.verify_pending_record(
                        root=root,
                        pulp_repo=root,
                        pulp_ownership_commit="c" * 40,
                        record_path=relative_record,
                        authority_record_commit=record_commit,
                        expected_authority_ref=record["authority_record_ref"],
                        require_head=True,
                    )
                record_path.write_text(
                    json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                (root / "extra.txt").write_text("extra\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=root, check=True)
                subprocess.run(
                    ["git", "commit", "-q", "-m", "intervening"],
                    cwd=root,
                    check=True,
                )
                intervening_commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                with self.assertRaisesRegex(
                    activation.ActivationError, "exact record commit"
                ):
                    activation.verify_pending_record(
                        root=root,
                        pulp_repo=root,
                        pulp_ownership_commit="c" * 40,
                        record_path=relative_record,
                        authority_record_commit=record_commit,
                        expected_authority_ref=record["authority_record_ref"],
                        require_head=True,
                    )
                with self.assertRaisesRegex(
                    activation.ActivationError, "directly after"
                ):
                    activation.verify_pending_record(
                        root=root,
                        pulp_repo=root,
                        pulp_ownership_commit="c" * 40,
                        record_path=relative_record,
                        authority_record_commit=intervening_commit,
                        expected_authority_ref=record["authority_record_ref"],
                        require_head=True,
                    )

                subprocess.run(
                    ["git", "reset", "--soft", authority_start],
                    cwd=root,
                    check=True,
                )
                subprocess.run(
                    ["git", "commit", "-q", "-m", "record plus extra"],
                    cwd=root,
                    check=True,
                )
                combined_commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                with self.assertRaisesRegex(
                    activation.ActivationError, "add only"
                ):
                    activation.verify_pending_record(
                        root=root,
                        pulp_repo=root,
                        pulp_ownership_commit="c" * 40,
                        record_path=relative_record,
                        authority_record_commit=combined_commit,
                        expected_authority_ref=record["authority_record_ref"],
                        require_head=True,
                    )

    def test_authority_workflows_expose_exact_required_checks(self) -> None:
        root = MODULE_PATH.parents[2]
        provenance = (root / ".github/workflows/provenance.yml").read_text(
            encoding="utf-8"
        )
        gpu = (root / ".github/workflows/gpu-macos.yml").read_text(
            encoding="utf-8"
        )
        active = (
            root / ".github/workflows/authority-activation.yml"
        ).read_text(encoding="utf-8")
        release = (
            root / ".github/workflows/authority-release.yml"
        ).read_text(encoding="utf-8")
        for workflow in (provenance, gpu):
            self.assertIn(
                "tags: ['v[0-9]*', 'authority/**']", workflow
            )
        self.assertNotIn("refs/heads/authority/", provenance)
        self.assertIn("forbidden-deps:\n    name: forbidden-deps", provenance)
        self.assertIn(
            "provenance-verify:\n    name: provenance-verify", provenance
        )
        self.assertIn("verify-pending", provenance)
        self.assertIn(
            "test_finalize_authority_reconciliation.py", provenance
        )
        self.assertIn(
            "Resolve the exact authority lifecycle phase", provenance
        )
        self.assertIn(
            "phase = \"tagged-pending\"", provenance
        )
        self.assertIn("phase = \"ready\"", provenance)
        self.assertIn("phase = \"active\"", provenance)
        self.assertIn(
            "if: steps.authority-phase.outputs.phase == 'ready'",
            provenance,
        )
        self.assertIn(
            "authority-start={authority_start}", provenance
        )
        self.assertIn(
            'observatory_vellum_target="$AUTHORITY_START_SHA"',
            provenance,
        )
        self.assertIn(
            "sterile-consumer:\n    name: sterile-consumer", gpu
        )
        self.assertIn("repositories: vellum", active)
        self.assertIn("permission-checks: read", active)
        self.assertIn("permission-contents: read", active)
        self.assertIn("ref: main", active)
        self.assertIn(
            "python3 tools/provenance/verify_authority_activation.py",
            active,
        )
        self.assertNotIn(
            "python3 .authority-record/tools/provenance/"
            "verify_authority_activation.py",
            active,
        )
        self.assertIn(
            r"[A-Za-z0-9][A-Za-z0-9._-]{1,160}\.json", active
        )
        self.assertIn(
            r"[a-z0-9][a-z0-9._-]{2,120}\.json", active
        )
        self.assertNotIn(
            "'${{ steps.authority-record.outputs.path }}'", active
        )
        self.assertIn(
            "group: authority-release-${{ inputs.authority_tag }}", release
        )
        self.assertIn("cancel-in-progress: false", release)
        self.assertIn("ref: main", release)
        authority_runs_on = (
            "runs-on: ${{ fromJSON(vars.VELLUM_AUTHORITY_RUNS_ON_JSON "
            "|| '[\"self-hosted\",\"Linux\",\"ARM64\","
            "\"vellum-authority-linux\"]') }}"
        )
        for workflow in (release, active):
            self.assertIn(authority_runs_on, workflow)
            self.assertNotIn("runs-on: ubuntu-latest", workflow)

    def test_historical_unresolved_requires_explicit_candidate_selection(self) -> None:
        group = {"pulp_legacy_slices": ["render"]}
        ownership = {"slices": [{"id": "render", "state": "pulp-authoritative-untransferred", "paths": ["core/render/a.cpp"]}]}
        manifest = {"entries": [{
            "source_path": "core/render/a.cpp", "classification": "unresolved",
            "git_blob_sha": "a" * 40, "git_mode": "100644",
        }]}
        seed = {"core/render/a.cpp": {"blob": "a" * 40, "mode": "100644"}}

        paths = activation.selected_slice_paths(group=group, ownership=ownership)
        projection = activation.build_historical_seed_projection(
            paths=paths, manifest=manifest, seed_tree=seed
        )
        self.assertEqual(projection["core/render/a.cpp"]["blob"], "a" * 40)
        self.assertEqual(projection["core/render/a.cpp"]["classification"], "unresolved")

        ownership["slices"][0]["state"] = "excluded"
        with self.assertRaisesRegex(activation.ActivationError, "not authoritative"):
            activation.selected_slice_paths(group=group, ownership=ownership)

    def test_candidate_projection_can_evolve_from_historical_blob(self) -> None:
        paths = ["core/render/a.cpp"]
        manifest = {"entries": [{
            "source_path": paths[0], "classification": "framework-core",
            "git_blob_sha": "a" * 40, "git_mode": "100644",
        }]}
        historical = activation.build_historical_seed_projection(
            paths=paths,
            manifest=manifest,
            seed_tree={paths[0]: {"blob": "a" * 40, "mode": "100644"}},
        )
        candidate = activation.build_activation_candidate_projection(
            paths=paths,
            candidate_tree={paths[0]: {"blob": "b" * 40, "mode": "100644"}},
        )
        self.assertEqual(historical[paths[0]]["blob"], "a" * 40)
        self.assertEqual(candidate[paths[0]]["blob"], "b" * 40)

    def test_historical_seed_mismatch_and_missing_candidate_fail(self) -> None:
        paths = ["core/render/a.cpp"]
        manifest = {"entries": [{
            "source_path": paths[0], "classification": "framework-core",
            "git_blob_sha": "a" * 40, "git_mode": "100644",
        }]}
        with self.assertRaisesRegex(activation.ActivationError, "historical seed"):
            activation.build_historical_seed_projection(
                paths=paths,
                manifest=manifest,
                seed_tree={paths[0]: {"blob": "b" * 40, "mode": "100644"}},
            )
        with self.assertRaisesRegex(activation.ActivationError, "absent at the candidate"):
            activation.build_activation_candidate_projection(paths=paths, candidate_tree={})

    def test_candidate_slice_paths_reject_duplicates(self) -> None:
        group = {"pulp_legacy_slices": ["a", "b"]}
        ownership = {"slices": [
            {"id": "a", "state": "pulp-authoritative-untransferred", "paths": ["same.cpp"]},
            {"id": "b", "state": "pulp-authoritative-untransferred", "paths": ["same.cpp"]},
        ]}
        with self.assertRaisesRegex(activation.ActivationError, "duplicate paths"):
            activation.selected_slice_paths(group=group, ownership=ownership)

    def test_record_shape_rejects_narrowed_candidate_path_set(self) -> None:
        root = MODULE_PATH.parents[2]
        record = activation.load_json(
            root / "provenance/authority/templates/pending-authority-record.v2.json"
        )
        for field in (
            "pulp_extraction_base", "historical_seed_commit", "pulp_candidate_commit",
            "authority_start_commit",
        ):
            record[field] = "a" * 40
        record["pulp_ownership_projection_blob"] = "b" * 40
        record["cut_manifest_sha256"] = "c" * 64
        record["authority_record_ref"] = "refs/tags/authority/test-v2"
        record["approved_at"] = "2026-07-23T20:00:00Z"
        group = record["authority_groups"][0]
        group["pulp_historical_seed_projection"] = {
            "a.cpp": {"blob": "a" * 40, "mode": "100644", "classification": "unresolved"}
        }
        group["pulp_activation_candidate_projection"] = {
            "b.cpp": {"blob": "b" * 40, "mode": "100644"}
        }
        group["vellum_implementation_projection"] = {
            "graphics/a.cpp": {"blob": "c" * 40, "mode": "100644"}
        }
        with self.assertRaisesRegex(activation.ActivationError, "path sets differ"):
            activation.validate_record_shape(record)

    def test_candidate_change_guard_has_positive_and_negative_controls(self) -> None:
        with mock.patch.object(activation, "git", return_value=""):
            activation.verify_candidate_unchanged(
                pulp_repo=Path("/pulp"),
                candidate_commit="a" * 40,
                activation_commit="b" * 40,
                paths=["core/render/a.cpp"],
            )
        with mock.patch.object(
            activation, "git", return_value="core/render/a.cpp\ncore/render/b.cpp"
        ):
            with self.assertRaisesRegex(activation.ActivationError, "candidate source changed"):
                activation.verify_candidate_unchanged(
                    pulp_repo=Path("/pulp"),
                    candidate_commit="a" * 40,
                    activation_commit="b" * 40,
                    paths=["core/render/a.cpp", "core/render/b.cpp"],
                )

    def test_activated_ownership_path_set_must_match_candidate(self) -> None:
        slices = {
            "render": {
                "id": "render",
                "state": "framework-authoritative-transferred",
                "paths": ["core/render/a.cpp"],
            }
        }
        activation.verify_activated_path_set(
            slices=slices,
            expected_slice_ids={"render"},
            candidate_paths=["core/render/a.cpp"],
        )
        slices["render"]["paths"] = ["core/render/b.cpp"]
        with self.assertRaisesRegex(activation.ActivationError, "path set differs"):
            activation.verify_activated_path_set(
                slices=slices,
                expected_slice_ids={"render"},
                candidate_paths=["core/render/a.cpp"],
            )

    def test_active_projection_must_not_restore_historical_blob(self) -> None:
        group = {"vellum_implementation_paths": ["graphics/"]}
        active = {"graphics/render.cpp": {"blob": "b" * 40, "mode": "100644"}}
        projection = activation.expand_implementation_projection(
            group=group, active_tree=active, historical_blob_ids={"a" * 40}
        )
        self.assertEqual(projection, active)

        active["graphics/render.cpp"]["blob"] = "a" * 40
        with self.assertRaisesRegex(activation.ActivationError, "restored"):
            activation.expand_implementation_projection(
                group=group, active_tree=active, historical_blob_ids={"a" * 40}
            )

    def test_check_evidence_positive_control_then_rejects_wrong_producer(self) -> None:
        commit = "a" * 40
        path = f"/repos/Generous-Corp/vellum/commits/{commit}/check-runs?per_page=100"
        run = {
            "id": 42,
            "name": "provenance-verify",
            "head_sha": commit,
            "conclusion": "success",
            "details_url": "https://example.invalid/check/42",
            "app": {"id": 123},
        }
        supplied = [{
            "name": "provenance-verify",
            "head_sha": commit,
            "conclusion": "success",
            "app_id": 123,
            "check_run_id": "42",
            "details_url": "https://example.invalid/check/42",
        }]
        github = FakeGitHub({path: {"check_runs": [run]}})

        activation.verify_checks(
            github=github,
            token="installation-token",
            full_name="Generous-Corp/vellum",
            commit=commit,
            expected_apps={"provenance-verify": 123},
            supplied=supplied,
        )

        with self.assertRaisesRegex(activation.ActivationError, "wrong producer"):
            activation.verify_checks(
                github=github,
                token="installation-token",
                full_name="Generous-Corp/vellum",
                commit=commit,
                expected_apps={"provenance-verify": 999},
                supplied=supplied,
            )

    def test_repository_reader_identity_and_scope_have_controls(self) -> None:
        expected = {
            "full_name": "Generous-Corp/vellum",
            "private": True,
            "repository_id": 777,
            "reader_app_id": 123,
            "required_check_app_ids": {"provenance-verify": 123},
        }
        responses = {
            "/app": {"id": 123},
            "/repos/Generous-Corp/vellum/installation": {"app_id": 123},
            "/repos/Generous-Corp/vellum": {"id": 777, "private": True, "archived": False},
            "/installation/repositories?per_page=100": {
                "total_count": 1,
                "repositories": [{"id": 777}],
            },
        }
        github = FakeGitHub(responses)
        activation.verify_repository(
            github, "installation-token", expected, app_jwt="app-jwt"
        )
        self.assertIn(("/app", "app-jwt"), github.requests)
        self.assertIn(
            ("/repos/Generous-Corp/vellum/installation", "app-jwt"),
            github.requests,
        )
        self.assertIn(
            ("/installation/repositories?per_page=100", "installation-token"),
            github.requests,
        )

        responses["/installation/repositories?per_page=100"] = {
            "total_count": 2,
            "repositories": [{"id": 777}, {"id": 888}],
        }
        with self.assertRaisesRegex(activation.ActivationError, "one-repository"):
            activation.verify_repository(
                FakeGitHub(responses), "installation-token", expected, app_jwt="app-jwt"
            )

    def test_pulp_activation_evidence_is_live_and_exact(self) -> None:
        root = MODULE_PATH.parents[2]
        with tempfile.TemporaryDirectory() as directory:
            pulp = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=pulp, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Pulp Test"],
                cwd=pulp,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "pulp-test@example.invalid"],
                cwd=pulp,
                check=True,
            )
            ownership = pulp / ".github/vellum-ownership.json"
            event = (
                pulp
                / ".github/vellum-change-events/"
                "20260723-authority-activation.json"
            )
            ownership.parent.mkdir(parents=True)
            event.parent.mkdir(parents=True)
            ownership.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "activation": {"state": "prepared"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=pulp, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "prepare"],
                cwd=pulp,
                check=True,
            )
            ownership.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "activation": {"state": "active"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            event.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "authority-transition",
                        "transition": "activate",
                        "slices": ["render"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=pulp, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "activate"],
                cwd=pulp,
                check=True,
            )
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=pulp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            checks_path = (
                f"/repos/Generous-Corp/pulp/commits/{commit}/"
                "check-runs?per_page=100"
            )
            responses = {
                "/app": {"id": 3878000},
                "/repos/Generous-Corp/pulp/installation": {"app_id": 3878000},
                "/repos/Generous-Corp/pulp": {
                    "id": 1203111607,
                    "private": False,
                    "archived": False,
                },
                "/installation/repositories?per_page=100": {
                    "total_count": 1,
                    "repositories": [{"id": 1203111607}],
                },
                "/repos/Generous-Corp/pulp/git/ref/heads/main": {
                    "object": {"type": "commit", "sha": commit}
                },
                checks_path: {
                    "check_runs": [
                        {
                            "id": 10,
                            "name": "Vellum freeze",
                            "head_sha": commit,
                            "conclusion": "success",
                            "details_url": "https://example.invalid/freeze",
                            "app": {"id": 15368},
                        },
                        {
                            "id": 11,
                            "name": "Vellum trusted freeze",
                            "head_sha": commit,
                            "conclusion": "success",
                            "details_url": "https://example.invalid/trusted",
                            "app": {"id": 15368},
                        },
                    ]
                },
                "/repos/Generous-Corp/pulp/branches/main/protection": {
                    "required_status_checks": {
                        "strict": True,
                        "checks": [
                            {"context": "Vellum freeze", "app_id": 15368},
                            {
                                "context": "Vellum trusted freeze",
                                "app_id": 15368,
                            },
                        ],
                    }
                },
            }
            evidence = activation.build_pulp_activation_evidence(
                root=root,
                pulp_repo=pulp,
                activation_commit=commit,
                authority_event_path=event.relative_to(pulp).as_posix(),
                github=FakeGitHub(responses),
                pulp_token="pulp-token",
                pulp_app_jwt="app-jwt",
            )
            self.assertEqual(
                evidence["state"], "landed-pulp-activation-evidence"
            )
            self.assertEqual(
                [row["name"] for row in evidence["checks"]],
                ["Vellum freeze", "Vellum trusted freeze"],
            )
            activation.validate_evidence_shape(evidence)
            responses[
                "/repos/Generous-Corp/pulp/branches/main/protection"
            ]["required_status_checks"]["strict"] = False
            with self.assertRaisesRegex(
                activation.ActivationError, "not strict"
            ):
                activation.build_pulp_activation_evidence(
                    root=root,
                    pulp_repo=pulp,
                    activation_commit=commit,
                    authority_event_path=event.relative_to(pulp).as_posix(),
                    github=FakeGitHub(responses),
                    pulp_token="pulp-token",
                    pulp_app_jwt="app-jwt",
                )

            (pulp / "later.txt").write_text("later\n", encoding="utf-8")
            subprocess.run(["git", "add", "later.txt"], cwd=pulp, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "later descendant"],
                cwd=pulp,
                check=True,
            )
            later_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=pulp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            with self.assertRaisesRegex(
                activation.ActivationError, "already existed"
            ):
                activation.verify_exact_pulp_activation_transition(
                    pulp_repo=pulp,
                    activation_commit=later_commit,
                    authority_event_path=event.relative_to(pulp).as_posix(),
                )

            subprocess.run(
                ["git", "checkout", "-q", "--orphan", "unlanded"],
                cwd=pulp,
                check=True,
            )
            (pulp / "side.txt").write_text("side\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=pulp, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "side"],
                cwd=pulp,
                check=True,
            )
            side_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=pulp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            with self.assertRaisesRegex(
                activation.ActivationError, "not an ancestor"
            ):
                activation.verify_landed_on_pulp_main(
                    github=FakeGitHub(responses),
                    token="pulp-token",
                    pulp_repo=pulp,
                    activation_commit=side_commit,
                )

    def test_schema_v2_rejects_authority_branch_downgrade(self) -> None:
        record = activation.load_json(
            MODULE_PATH.parents[2]
            / "provenance/authority/templates/pending-authority-record.v2.json"
        )
        for field in (
            "pulp_extraction_base",
            "historical_seed_commit",
            "pulp_candidate_commit",
            "authority_start_commit",
        ):
            record[field] = "a" * 40
        record["pulp_ownership_projection_blob"] = "b" * 40
        record["cut_manifest_sha256"] = "c" * 64
        record["approved_at"] = "2026-07-23T20:00:00Z"
        record["authority_record_ref"] = (
            "refs/heads/authority/native-design-kernel-v1"
        )
        group = record["authority_groups"][0]
        group["pulp_historical_seed_projection"] = {
            "a.cpp": {
                "blob": "a" * 40,
                "mode": "100644",
                "classification": "framework-core",
            }
        }
        group["pulp_activation_candidate_projection"] = {
            "a.cpp": {"blob": "b" * 40, "mode": "100644"}
        }
        group["vellum_implementation_projection"] = {
            "graphics/a.cpp": {"blob": "c" * 40, "mode": "100644"}
        }
        with self.assertRaisesRegex(
            activation.ActivationError, "record ref is invalid"
        ):
            activation.validate_record_shape(record)

    def test_immutable_signed_authority_tag_is_a_protected_ref(self) -> None:
        commit = "a" * 40
        tag_object = "b" * 40
        ref = "refs/tags/authority/native-design-kernel-v1"
        ref_path = (
            "/repos/Generous-Corp/vellum/git/ref/tags/"
            "authority/native-design-kernel-v1"
        )
        tag_path = f"/repos/Generous-Corp/vellum/git/tags/{tag_object}"
        release_path = (
            "/repos/Generous-Corp/vellum/releases/tags/"
            "authority%2Fnative-design-kernel-v1"
        )
        responses = {
            ref_path: {"object": {"type": "tag", "sha": tag_object}},
            tag_path: {
                "object": {"type": "commit", "sha": commit},
                "verification": {"verified": True, "reason": "valid"},
            },
            release_path: {
                "tag_name": "authority/native-design-kernel-v1",
                "draft": False,
                "immutable": True,
                "published_at": "2026-07-23T23:35:00Z",
            },
        }
        activation.verify_protected_ref(
            github=FakeGitHub(responses),
            token="token",
            full_name="Generous-Corp/vellum",
            ref=ref,
            commit=commit,
            expected_apps={"provenance-verify": 123},
        )
        responses[release_path]["immutable"] = False
        with self.assertRaisesRegex(activation.ActivationError, "immutable"):
            activation.verify_protected_ref(
                github=FakeGitHub(responses),
                token="token",
                full_name="Generous-Corp/vellum",
                ref=ref,
                commit=commit,
                expected_apps={"provenance-verify": 123},
            )

    def test_evidence_template_cannot_masquerade_as_landed_evidence(self) -> None:
        root = MODULE_PATH.parents[2]
        template = activation.load_json(
            root / "provenance/authority/templates/pulp-activation-evidence.v1.json"
        )
        with self.assertRaisesRegex(activation.ActivationError, "must be landed"):
            activation.validate_evidence_shape(template)


if __name__ == "__main__":
    unittest.main()
