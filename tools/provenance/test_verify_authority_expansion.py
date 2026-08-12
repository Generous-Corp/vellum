#!/usr/bin/env python3
"""Negative controls for the non-authoritative expansion proposal."""

from __future__ import annotations

import builtins
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("verify_authority_expansion.py")
SPEC = importlib.util.spec_from_file_location("verify_authority_expansion", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
ROOT = Path(__file__).resolve().parents[2]


class Tests(unittest.TestCase):
    def exact_boundary_acknowledgement(
        self,
        *,
        pulp_commit: str = "4" * 40,
        pulp_digest: str = "5" * 64,
        amendment_commit: str = "6" * 40,
    ) -> dict:
        return {
            "schema_version": 1,
            "kind": "full-design-import-render-exact-boundary-acknowledgement",
            "acknowledgement_id": "full-design-import-render-v1-exact-boundary-acknowledgement-1",
            "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
            "state": "acknowledged",
            "acknowledged_at": "2026-11-30T00:00:00Z",
            "acknowledged_by": "@danielraffel",
            "authority_effect": "exact-path-implementation-authority-activated",
            "implementation_authority": "authorized-for-matrix-exact-routes",
            "coordinates": {
                "pulp_repository": "Generous-Corp/pulp",
                "pulp_acceptance_merge_commit": pulp_commit,
                "pulp_acceptance_path": ".github/vellum-expansion-watch/full-design-import-render-v1/exact-boundary-acceptance-1.json",
                "pulp_acceptance_sha256": pulp_digest,
                "vellum_delivery_repository": "danielraffel/vellum",
                "vellum_amendment_merge_commit": amendment_commit,
                "amendment_path": verifier.BOUNDARY_AMENDMENT_PATH.as_posix(),
                "amendment_sha256": verifier.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
                "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
                "matrix_path": verifier.MATRIX_PATH.as_posix(),
                "matrix_sha256": verifier.EXPECTED_MATRIX_SHA256,
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
        }

    def promotion_attestation(
        self, acknowledgement_sha256: str, *, commit: str = "1" * 40,
        tree: str = "2" * 40, completion_sha256: str = "4" * 64,
    ) -> dict:
        return {
            "schema_version": 1,
            "kind": "vellum-authority-promotion-attestation",
            "attestation_id": "full-design-import-render-v1-authority-promotion-1",
            "state": "attested",
            "attested_at": "2026-12-01T00:00:00Z",
            "attested_by": "@danielraffel",
            "authority_repository": "Generous-Corp/vellum",
            "delivery_repository": "danielraffel/vellum",
            "promotion_mode": "exact-mirror",
            "authority_commit": commit,
            "delivery_commit": commit,
            "authority_tree": tree,
            "delivery_tree": tree,
            "exact_boundary_amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
            "exact_boundary_amendment_sha256": verifier.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
            "exact_boundary_acknowledgement_id": "full-design-import-render-v1-exact-boundary-acknowledgement-1",
            "exact_boundary_acknowledgement_sha256": acknowledgement_sha256,
            "parity_completion_id": "full-design-import-render-v1-parity-completion-1",
            "parity_completion_sha256": completion_sha256,
            "parity_release_source_commit": commit,
        }

    def pulp_exact_boundary_acceptance(
        self, acknowledgement: dict, *, pulp_main_commit: str = "7" * 40,
        projection_sha256: str = "9" * 64,
        router_sha256: str = "a" * 64,
        router_test_sha256: str = "b" * 64,
        router_dependency_sha256: str = "c" * 64,
    ) -> dict:
        coordinates = acknowledgement["coordinates"]
        return {
            "schema_version": 1,
            "kind": "full-design-import-render-exact-boundary-acceptance",
            "acceptance_id": "full-design-import-render-v1-exact-boundary-acceptance-1",
            "state": "accepted",
            "accepted_at": "2026-11-29T00:00:00Z",
            "accepted_by": "@danielraffel",
            "pulp_repository": "Generous-Corp/pulp",
            "counterpart": {
                "repository": "danielraffel/vellum",
                "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
                "amendment_merge_commit": coordinates["vellum_amendment_merge_commit"],
                "amendment_path": verifier.BOUNDARY_AMENDMENT_PATH.as_posix(),
                "amendment_sha256": verifier.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
                "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
                "matrix_path": verifier.MATRIX_PATH.as_posix(),
                "matrix_sha256": verifier.EXPECTED_MATRIX_SHA256,
            },
            "refresh": {
                "audited_at": "2026-11-29T00:00:00Z",
                "pulp_main_commit": pulp_main_commit,
                "open_pr_audit_complete": True,
                "open_pr_rows": [],
                "open_vellum_overlap_count": 0,
            },
            "routing_projection": {
                "path": verifier.PULP_OWNERSHIP_PATH,
                "sha256": projection_sha256,
                "schema_version": 3,
                "expansion_id": "full-design-import-render-v1",
                "route_set_sha256": verifier.canonical_sha256(
                    verifier.exact_route_rows(
                        json.loads((ROOT / verifier.MATRIX_PATH).read_text()),
                        json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text()),
                    )
                ),
                "router_path": ".agents/skills/pulp-vellum-change-routing/scripts/route_change.py",
                "router_sha256": router_sha256,
                "router_contract_test_path": ".agents/skills/pulp-vellum-change-routing/scripts/test_route_change.py",
                "router_contract_test_sha256": router_test_sha256,
                "router_dependency_path": ".agents/skills/pulp-vellum-change-routing/scripts/routing_evidence.py",
                "router_dependency_sha256": router_dependency_sha256,
                "router_contract_check": {
                    "name": "vellum-routing-contract",
                    "app_id": 15368,
                    "workflow_path": ".github/workflows/vellum-routing-contract.yml",
                    "event": "push",
                    "branch": "main",
                    "contract_scope": "full-bound-router-contract-suite",
                    "required_case_ids": verifier.REQUIRED_PULP_ROUTER_CASES,
                },
            },
            "authority_effect": "none",
            "implementation_authority": (
                "forbidden-until-vellum-exact-boundary-acknowledged"
            ),
            "gates": {
                "exact_matrix_routes_accepted": True,
                "refreshed_open_pr_audit_complete": True,
                "vellum_acknowledgement_required": True,
                "source_work_authorized": False,
                "pulp_consumption_authorized": False,
            },
        }

    @staticmethod
    def open_pr_snapshot(*pulls: dict) -> dict:
        grouped = {
            "Generous-Corp/pulp": [],
            "Generous-Corp/vellum": [],
            "danielraffel/vellum": [],
        }
        for pull in pulls:
            grouped[pull["repository"]].append({
                "number": pull["number"],
                "base_commit": pull.get("base_commit", "f" * 40),
                "merge_base_commit": pull.get("merge_base_commit", "e" * 40),
                "head_commit": pull["head_commit"],
                "paths": sorted(pull["paths"]),
                "diff_path_count": pull.get("diff_path_count", len(pull["paths"])),
            })
        return {
            "schema_version": 2,
            "kind": "github-open-pull-request-snapshot",
            "repositories": [
                {"repository": repository, "pulls": sorted(rows, key=lambda row: row["number"])}
                for repository, rows in sorted(grouped.items())
            ],
        }

    def pulp_ownership_projection(self) -> dict:
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        routes = verifier.exact_route_rows(matrix, amendment)
        return {
            "schema_version": 3,
            "framework_repository": "Generous-Corp/vellum",
            "slices": [],
            "expansions": [{
                "id": "full-design-import-render-v1",
                "state": "accepted-pending-vellum-acknowledgement",
                "accepted_at": "2026-11-29T00:00:00Z",
                "accepted_by": "@danielraffel",
                "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
                "matrix_id": "full-design-import-render-v1-compatibility-matrix",
                "matrix_sha256": verifier.EXPECTED_MATRIX_SHA256,
                "route_set_sha256": verifier.canonical_sha256(routes),
                "routes": routes,
            }],
        }

    def parity_completion(self, root: Path) -> dict:
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        routes = verifier.exact_route_rows(matrix, amendment)
        rows = []
        for cell in matrix["cells"]:
            if cell["status"] == cell["target_status"]:
                continue
            cell_id = cell["id"]
            paths = {}
            for role, key in (
                ("vellum_future_implementation", "implementation_paths"),
                ("vellum_future_proof", "proof_paths"),
            ):
                paths[key] = sorted(
                    row["path"] for row in routes
                    if row["repository"] == "Generous-Corp/vellum"
                    and {"cell_id": cell_id, "role": role} in row["cell_roles"]
                )
                for path in paths[key]:
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("completion fixture\n")
            rows.append({
                "cell_id": cell_id,
                "achieved_status": cell["target_status"],
                **paths,
                "required_checks": [
                    item["name"] for item in verifier.REQUIRED_PARITY_CHECKS
                ],
                "proof_executions": [
                    {
                        "path": path,
                        "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                        "check": "gpu-macos-arm64",
                        "test_id": (
                            verifier.CPP_PROOF_TEST_IDS.get(path)
                            or verifier.ARGUMENT_DRIVEN_PROOF_TEST_IDS.get(path)
                            or verifier.FIXTURE_PROOF_CONSUMERS.get(path)
                            or path
                        ),
                        "runner": (
                            "ctest-case"
                            if path in verifier.ARGUMENT_DRIVEN_PROOF_TEST_IDS
                            else {
                            ".py": "python-file", ".js": "node-test-file",
                            ".mjs": "node-test-file", ".cpp": "ctest-case",
                            ".zip": "fixture-consumer",
                            }[Path(path).suffix]
                        ),
                    }
                    for path in paths["proof_paths"]
                ],
            })
        return {
            "schema_version": 1,
            "kind": "full-design-import-render-parity-completion",
            "completion_id": "full-design-import-render-v1-parity-completion-1",
            "state": "complete",
            "completed_at": "2026-12-01T00:00:00Z",
            "completed_by": "@danielraffel",
            "matrix_id": "full-design-import-render-v1-compatibility-matrix",
            "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
            "matrix_sha256": verifier.EXPECTED_MATRIX_SHA256,
            "required_check_runs": verifier.REQUIRED_PARITY_CHECKS,
            "cells": rows,
        }

    def init_git_repo(self, root: Path, *, remote: str) -> tuple[str, str]:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", remote], check=True)
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        tree = subprocess.check_output(
            ["git", "-C", str(root), "show", "-s", "--format=%T", "HEAD"], text=True
        ).strip()
        return commit, tree

    def test_workflow_runs_and_retains_expansion_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/provenance.yml").read_text()
        self.assertIn(
            "python3 tools/provenance/test_verify_authority_expansion.py",
            workflow,
        )
        self.assertIn(
            "python3 tools/provenance/verify_authority_expansion.py", workflow
        )
        self.assertIn("refs/tags/v[0-9]*", workflow)
        self.assertIn("--release-readiness", workflow)
        self.assertIn("repository: Generous-Corp/pulp", workflow)
        self.assertIn("Check out authoritative Pulp main with full history", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("repository: danielraffel/vellum", workflow)
        self.assertIn("--pulp-root .exact-boundary-pulp", workflow)
        self.assertIn("--delivery-root .promotion-delivery", workflow)
        self.assertIn("--promotion-attestation", workflow)
        self.assertIn("--check-runs-json", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("checks: read", workflow)
        self.assertIn("gh api --method GET --paginate --slurp", workflow)
        self.assertIn("-f filter=all -F per_page=100", workflow)
        clean_workflow = (ROOT / ".github/workflows/readme-quick-start.yml").read_text()
        self.assertIn("push:\n    branches: [main]", clean_workflow)
        self.assertIn("secrets.VELLUM_DELIVERY_READER_TOKEN", workflow)
        self.assertIn("Require private delivery repository read access", workflow)
        self.assertIn("release tag lacks an SSH-signed JSON message", workflow)
        self.assertIn("object_pairs_hook=strict_object", workflow)
        self.assertIn("promotion-release-tag-trust.json", workflow)
        self.assertGreaterEqual(workflow.count("authority-expansion-report.json"), 2)

    def test_sdk_release_mutations_require_exact_authority_provenance(self) -> None:
        gpu = (ROOT / ".github/workflows/gpu-macos.yml").read_text()
        workflow = (ROOT / ".github/workflows/sdk-release.yml").read_text()
        gate = workflow.index("- name: Require exact provenance readiness")
        publish = workflow.index(
            "- name: Publish only through live no-bypass controls"
        )
        self.assertLess(gate, publish)
        self.assertIn("contents: read", gpu)
        self.assertNotIn("gh release create", gpu)
        self.assertIn("permission-actions: read", workflow)
        self.assertIn(
            'test "$GITHUB_REPOSITORY" = Generous-Corp/vellum', workflow
        )
        preflight = json.loads(
            (ROOT / "provenance/immutable-release-preflight.json").read_text()
        )
        self.assertEqual(preflight["repository"], "Generous-Corp/vellum")
        self.assertEqual(
            preflight["administrator_check"]["endpoint"],
            "GET /repos/Generous-Corp/vellum/immutable-releases",
        )
        self.assertIn("scripts/select_exact_provenance_run.py", workflow)
        self.assertIn("scripts/verify_exact_provenance_artifact.py", workflow)
        self.assertIn("promotion-release-tag-trust.json", workflow)
        self.assertIn('--tag-object-sha "$TAG_OBJECT_SHA"', workflow)
        provenance = (ROOT / ".github/workflows/provenance.yml").read_text()
        self.assertIn("python3 scripts/test_select_exact_provenance_run.py", provenance)
        self.assertIn("python3 scripts/test_verify_exact_provenance_artifact.py", provenance)
        self.assertEqual(
            gpu.count("python3 scripts/test_select_exact_provenance_run.py"), 1
        )
        self.assertIn('-f branch="$RELEASE_TAG"', workflow)
        self.assertIn('--head-sha "$SOURCE_COMMIT"', workflow)
        self.assertIn('test "$conclusion" = success', workflow)

    def copy(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        target = root / verifier.PROPOSAL_PATH
        target.parent.mkdir(parents=True)
        shutil.copy2(ROOT / verifier.PROPOSAL_PATH, target)
        shutil.copy2(
            ROOT / verifier.EXPANSIONS_ROOT / "README.md",
            root / verifier.EXPANSIONS_ROOT / "README.md",
        )
        addendum = root / verifier.ADDENDUM_PATH
        addendum.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.ADDENDUM_PATH, addendum)
        acknowledgement = root / verifier.ACKNOWLEDGEMENT_PATH
        acknowledgement.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.ACKNOWLEDGEMENT_PATH, acknowledgement)
        matrix = root / verifier.MATRIX_PATH
        matrix.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.MATRIX_PATH, matrix)
        boundary_amendment = root / verifier.BOUNDARY_AMENDMENT_PATH
        boundary_amendment.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.BOUNDARY_AMENDMENT_PATH, boundary_amendment)
        return temporary, root, target

    def mutate(self, callback) -> dict:
        data = json.loads((ROOT / verifier.PROPOSAL_PATH).read_text())
        callback(data)
        errors = verifier.validate(data)
        passed = not errors
        return {
            "status": "pass" if passed else "fail",
            "proposal_id": data.get("proposal_id") if passed else None,
            "authority_effect": data.get("authority_effect") if passed else None,
            "errors": errors,
        }

    def mutate_addendum(self, callback) -> dict:
        data = json.loads((ROOT / verifier.ADDENDUM_PATH).read_text())
        callback(data)
        errors = verifier.validate_addendum(data)
        return {
            "status": "pass" if not errors else "fail",
            "authority_effect": data.get("authority_effect") if not errors else None,
            "errors": errors,
        }

    def mutate_acknowledgement(self, callback) -> dict:
        data = json.loads((ROOT / verifier.ACKNOWLEDGEMENT_PATH).read_text())
        callback(data)
        errors = verifier.validate_acknowledgement(data)
        return {
            "status": "pass" if not errors else "fail",
            "authority_effect": data.get("authority_effect") if not errors else None,
            "errors": errors,
        }

    def mutate_matrix(self, callback) -> dict:
        data = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        callback(data)
        errors = verifier.validate_matrix(data)
        return {
            "status": "pass" if not errors else "fail",
            "authority_effect": data.get("authority_effect") if not errors else None,
            "errors": errors,
        }

    def mutate_boundary_amendment(self, callback) -> dict:
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        data = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        callback(data)
        errors = verifier.validate_boundary_amendment(data, matrix)
        return {
            "status": "pass" if not errors else "fail",
            "authority_effect": data.get("authority_effect") if not errors else None,
            "errors": errors,
        }

    def test_committed_proposal_passes_without_authority(self) -> None:
        report = verifier.verify(ROOT)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["authority_effect"], "none")
        self.assertEqual(
            report["scope_addendum_id"],
            "full-design-import-render-v1-scope-addendum-1",
        )
        self.assertEqual(
            report["watch_acknowledgement_id"],
            "full-design-import-render-v1-watch-acknowledgement",
        )
        self.assertEqual(
            report["compatibility_matrix_id"],
            "full-design-import-render-v1-compatibility-matrix",
        )
        self.assertEqual(
            report["exact_boundary_amendment_id"],
            "full-design-import-render-v1-exact-boundary-amendment-1",
        )

    def test_boundary_amendment_is_inert_and_preserves_repository_roles(self) -> None:
        data = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        self.assertEqual(data["authority_effect"], "none")
        self.assertEqual(
            data["implementation_authority"],
            "forbidden-until-exact-boundary-acknowledged",
        )
        self.assertEqual(
            data["repository_roles"]["authority_repository"],
            "Generous-Corp/vellum",
        )
        self.assertFalse(
            data["repository_roles"]["delivery_repository_is_authority"]
        )
        self.assertFalse(
            data["repository_roles"]["delivery_work_authorized_by_this_amendment"]
        )
        self.assertEqual(
            data["repository_roles"]["required_exact_boundary_acknowledgement_id"],
            "full-design-import-render-v1-exact-boundary-acknowledgement-1",
        )
        self.assertTrue(
            data["repository_roles"][
                "transfer_or_exact_mirror_required_before_parity_release"
            ]
        )

    def test_boundary_amendment_authority_and_route_drift_fail(self) -> None:
        report = self.mutate_boundary_amendment(
            lambda data: data.update(authority_effect="transferred")
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_boundary_amendment(
            lambda data: data["repository_roles"].update(
                delivery_repository_is_authority=True
            )
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_boundary_amendment(
            lambda data: data["exact_path_routes"].update(
                unlisted_pulp_path_owner="Generous-Corp/vellum"
            )
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_boundary_amendment(
            lambda data: data["exact_path_routes"].update(
                routed_cell_filter="all-cells"
            )
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_boundary_amendment(
            lambda data: data["open_overlap_audit"].update(
                pulp_acceptance_must_refresh_open_prs=False
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_boundary_amendment_cannot_drop_retained_cell_or_release_gate(self) -> None:
        report = self.mutate_boundary_amendment(
            lambda data: data["exact_path_routes"]["retained_boundary_cells"].pop()
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_boundary_amendment(
            lambda data: data["gates"].update(pulp_consumption_authorized=True)
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_boundary_amendment(
            lambda data: data["gates"].update(
                authority_promotion_attestation_required_for_release=False
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_boundary_routes_reject_prefix_or_glob_paths(self) -> None:
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        matrix["cells"][0]["vellum_future_implementation"][0] = "authoring/**"
        errors = verifier.validate_boundary_amendment(amendment, matrix)
        self.assertTrue(any("exact path" in error for error in errors))
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        next(
            cell
            for cell in matrix["cells"]
            if cell["id"] == "render.text-runs-fonts-fallback"
        )["vellum_future_implementation"][0] = "runtime/assets/unlisted-fonts"
        errors = verifier.validate_boundary_amendment(amendment, matrix)
        self.assertTrue(any("directory-shaped path" in error for error in errors))
        for malformed in ({}, []):
            matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
            matrix["cells"][0]["vellum_future_implementation"][0] = malformed
            errors = verifier.validate_boundary_amendment(amendment, matrix)
            self.assertTrue(errors)

    def test_boundary_maintenance_allowance_is_prospective_only(self) -> None:
        data = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        row = data["interim_maintenance"][0]
        self.assertFalse(row["authorized_by_this_amendment"])
        self.assertIn("after-exact-boundary-acknowledgement", row["disposition"])
        report = self.mutate_boundary_amendment(
            lambda value: value["interim_maintenance"][0].update(
                authorized_by_this_amendment=True
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_boundary_amendment_byte_drift_fails(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        amendment = root / verifier.BOUNDARY_AMENDMENT_PATH
        amendment.write_bytes(amendment.read_bytes() + b"\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("exact-boundary amendment differs" in error for error in report["errors"])
        )

    def test_matrix_freezes_required_routes_without_authority(self) -> None:
        data = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        cells = {cell["id"]: cell for cell in data["cells"]}
        self.assertEqual(data["authority_effect"], "none")
        self.assertEqual(cells["source.agent-html-chromium"]["status"], "partial")
        self.assertEqual(cells["source.agent-html-chromium"]["target_status"], "supported")
        self.assertEqual(cells["source.figma-local-fig"]["target_status"], "supported")
        self.assertEqual(cells["source.figma-rest"]["target_status"], "supported")
        self.assertEqual(cells["source.figma-mcp-context"]["target_status"], "supported")
        self.assertEqual(cells["harness.capture-native-and-web"]["status"], "partial")
        self.assertEqual(
            cells["render.skia-graphite-dawn-macos15-arm64"]["status"],
            "supported",
        )
        self.assertEqual(
            cells["boundary.non-macos-arm64-platform-adoption"]["status"],
            "not-applicable",
        )
        self.assertEqual(cells["boundary.audio-dsp-harness"]["status"], "rejected-by-contract")

    def test_supported_vellum_paths_resolve_at_pinned_commit(self) -> None:
        data = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        self.assertEqual(verifier.validate_matrix_repository_paths(ROOT, data), [])
        cell = next(
            row for row in data["cells"] if row["id"] == "source.canonical-design-ir"
        )
        cell["vellum_future_implementation"] = ["missing/supported-owner.cpp"]
        errors = verifier.validate_matrix_repository_paths(ROOT, data)
        self.assertTrue(any("supported path does not exist" in error for error in errors))

    def test_production_verify_requires_git_head_for_path_resolution(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("Git HEAD is required" in error for error in report["errors"]))

    def test_matrix_authority_and_consumption_claims_fail(self) -> None:
        report = self.mutate_matrix(lambda d: d.update(authority_effect="transferred"))
        self.assertEqual(report["status"], "fail")
        report = self.mutate_matrix(
            lambda d: d["gates"].update(pulp_consumption_authorized=True)
        )
        self.assertEqual(report["status"], "fail")

    def test_matrix_closed_vocabulary_fields_reject_non_string_values(self) -> None:
        for field in ("family", "status", "target_status"):
            for value in ({}, []):
                with self.subTest(field=field, value_type=type(value).__name__):
                    report = self.mutate_matrix(
                        lambda data, field=field, value=value: data["cells"][0].update(
                            {field: value}
                        )
                    )
                    self.assertEqual(report["status"], "fail")
                    self.assertTrue(report["errors"])

    def test_matrix_cannot_drop_chromium_or_weaken_required_target(self) -> None:
        report = self.mutate_matrix(
            lambda d: d["cells"].__setitem__(
                slice(None),
                [cell for cell in d["cells"] if cell["id"] != "source.agent-html-chromium"],
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_release_readiness_rejects_partial_cells(self) -> None:
        data = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        errors = verifier.validate_release_readiness(ROOT, data, amendment)
        self.assertTrue(any("versioned parity completion" in error for error in errors))
        self.assertTrue(any("workflow-run evidence" in error for error in errors))
        for cell in data["cells"]:
            cell["status"] = cell["target_status"]
        errors = verifier.validate_release_readiness(ROOT, data, amendment)
        self.assertTrue(any("versioned parity completion" in error for error in errors))
        self.assertTrue(any("promotion attestation" in error for error in errors))

    def test_release_readiness_accepts_valid_promotion_evidence(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        acknowledgement = root / verifier.EXPANSIONS_ROOT / (
            "full-design-import-render-v1/exact-boundary-acknowledgement-1.json"
        )
        acknowledgement.write_text(
            json.dumps(self.exact_boundary_acknowledgement()) + "\n"
        )
        digest = hashlib.sha256(acknowledgement.read_bytes()).hexdigest()
        completion = root / verifier.PARITY_COMPLETION_PATH
        completion_data = self.parity_completion(root)
        completion.write_text(json.dumps(completion_data) + "\n")
        completion_digest = hashlib.sha256(completion.read_bytes()).hexdigest()
        authority_commit, authority_tree = self.init_git_repo(
            root, remote="https://github.com/Generous-Corp/vellum.git"
        )
        delivery_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(delivery_temporary.cleanup)
        delivery_root = Path(delivery_temporary.name) / "vellum"
        subprocess.run(
            ["git", "clone", "-q", str(root), str(delivery_root)], check=True
        )
        promotion = Path(temporary.name) / "signed-tag-promotion.json"
        promotion.write_text(
            json.dumps(
                self.promotion_attestation(
                    digest, commit=authority_commit, tree=authority_tree,
                    completion_sha256=completion_digest,
                )
            )
            + "\n"
        )
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        check_runs = {
            "check_runs": [
                {
                    "name": item["name"],
                    "head_sha": authority_commit,
                    "conclusion": "success",
                    "app": {"id": item["app_id"]},
                    "details_url": (
                        "https://github.com/Generous-Corp/vellum/actions/runs/"
                        f"{1000 + index}/job/{2000 + index}"
                    ),
                }
                for index, item in enumerate(verifier.REQUIRED_PARITY_CHECKS)
            ],
            "workflow_runs": [
                {
                    "id": 1000 + index,
                    "path": item["workflow_path"],
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": authority_commit,
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, item in enumerate(verifier.REQUIRED_PARITY_CHECKS)
            ],
            "parity_proof_execution_receipts": [{
                "schema_version": 1,
                "kind": "vellum-parity-proof-execution",
                "repository": "Generous-Corp/vellum",
                "head_sha": authority_commit,
                "run_id": 1002,
                "check_name": "gpu-macos-arm64",
                "workflow_path": ".github/workflows/gpu-macos.yml",
                "status": "pass",
                "proofs": [
                    {
                        "path": execution["path"],
                        "sha256": execution["sha256"],
                        "test_id": execution["test_id"],
                        "runner": execution["runner"],
                        "status": "pass",
                    }
                    for row in completion_data["cells"]
                    for execution in row["proof_executions"]
                ],
            }],
        }
        self.assertEqual(
            verifier.validate_release_readiness(
                root,
                matrix,
                amendment,
                promotion_attestation=promotion,
                delivery_root=delivery_root,
                repository="Generous-Corp/vellum",
                release_source_commit=authority_commit,
                check_runs=check_runs,
            ),
            [],
        )
        completion.write_text('{"forged":"worktree-only"}\n')
        self.assertEqual(
            verifier.validate_release_readiness(
                root,
                matrix,
                amendment,
                promotion_attestation=promotion,
                delivery_root=delivery_root,
                repository="Generous-Corp/vellum",
                release_source_commit=authority_commit,
                check_runs=check_runs,
            ),
            [],
        )

    def test_parity_completion_is_a_versioned_successor_to_frozen_matrix(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        completion = self.parity_completion(root)
        self.assertEqual(
            verifier.validate_parity_completion(root, completion, matrix, amendment), []
        )
        completion["cells"].pop()
        errors = verifier.validate_parity_completion(root, completion, matrix, amendment)
        self.assertTrue(any("required cell IDs differ" in error for error in errors))
        self.assertEqual(
            hashlib.sha256((ROOT / verifier.MATRIX_PATH).read_bytes()).hexdigest(),
            verifier.EXPECTED_MATRIX_SHA256,
        )

    def test_parity_completion_rejects_untracked_paths_and_missing_exact_checks(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        completion = self.parity_completion(root)
        victim = Path(completion["cells"][0]["implementation_paths"][0])
        (root / victim).unlink()
        commit, _ = self.init_git_repo(
            root, remote="https://github.com/Generous-Corp/vellum.git"
        )
        (root / victim).write_text("untracked placeholder\n")
        check_runs = {
            "check_runs": [
                {
                    "name": item["name"], "head_sha": commit,
                    "conclusion": "success", "app": {"id": item["app_id"]},
                    "details_url": (
                        "https://github.com/Generous-Corp/vellum/actions/runs/"
                        f"{1000 + index}/job/{2000 + index}"
                    ),
                }
                for index, item in enumerate(verifier.REQUIRED_PARITY_CHECKS[:-1])
            ],
            "workflow_runs": [
                {
                    "id": 1000 + index, "path": item["workflow_path"],
                    "event": "push", "head_branch": "main", "head_sha": commit,
                    "status": "completed", "conclusion": "success",
                }
                for index, item in enumerate(verifier.REQUIRED_PARITY_CHECKS[:-1])
            ],
        }
        errors = verifier.validate_parity_completion(
            root, completion, matrix, amendment,
            release_source_commit=commit, check_runs=check_runs,
        )
        self.assertTrue(any("tagged release commit lacks regular blob" in e for e in errors))
        self.assertTrue(any("sterile-consumer" in e for e in errors))

    def test_pulp_projection_must_equal_frozen_exact_routes(self) -> None:
        acknowledgement = self.exact_boundary_acknowledgement()
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        projection = self.pulp_ownership_projection()
        self.assertEqual(
            verifier.validate_pulp_ownership_projection(
                projection, acceptance, matrix, amendment
            ),
            [],
        )
        projection["expansions"][0]["routes"].pop()
        errors = verifier.validate_pulp_ownership_projection(
            projection, acceptance, matrix, amendment
        )
        self.assertTrue(any("differs from exact routes" in error for error in errors))

    def test_exact_acknowledgement_resolves_pulp_acceptance_and_vellum_artifacts(self) -> None:
        amendment_commit = subprocess.check_output(
            [
                "git", "-C", str(ROOT), "log", "--diff-filter=A", "--format=%H",
                "--", verifier.BOUNDARY_AMENDMENT_PATH.as_posix(),
            ],
            text=True,
        ).splitlines()[-1]
        acknowledgement = self.exact_boundary_acknowledgement(
            amendment_commit=amendment_commit
        )
        pulp_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(pulp_temporary.cleanup)
        pulp_root = Path(pulp_temporary.name)
        (pulp_root / "README.md").write_text("Pulp fixture\n")
        pulp_main_commit, _ = self.init_git_repo(
            pulp_root, remote="https://github.com/Generous-Corp/pulp.git"
        )
        projection_path = pulp_root / verifier.PULP_OWNERSHIP_PATH
        projection_path.parent.mkdir(parents=True, exist_ok=True)
        projection_path.write_text(json.dumps(self.pulp_ownership_projection()) + "\n")
        projection_digest = hashlib.sha256(projection_path.read_bytes()).hexdigest()
        router_root = (
            pulp_root / ".agents/skills/pulp-vellum-change-routing/scripts"
        )
        router_root.mkdir(parents=True, exist_ok=True)
        router_path = router_root / "route_change.py"
        router_path.write_text(
            """import builtins
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
if os.environ.get('GH_TOKEN'):
    raise RuntimeError('parent credential crossed isolation boundary')
builtins.VELLUM_EXTERNAL_ROUTER_MUTATION = True
class AuthorityError(RuntimeError): pass
@dataclass(frozen=True)
class Authority:
    vellum: Path
    pulp: Path
    vellum_head: str
    pulp_head: str
    pulp_projection: dict[str, Any]
    counterpart_map: dict[str, Any]
    coordinates: dict[str, str]
def exact_slices(authority, path):
    matches = [row for row in authority.pulp_projection.get('slices', []) if path in row.get('paths', [])]
    for expansion in authority.pulp_projection.get('expansions', []):
        routes = [row for row in expansion.get('routes', []) if row.get('repository') == 'Generous-Corp/pulp' and row.get('path') == path]
        if routes:
            matches.append({'id': expansion['id'], 'state': 'pulp-authoritative-untransferred', 'paths': [path], 'authority': None})
    if len(matches) > 1: raise AuthorityError('multiple exact owners')
    return matches
def route(authority, *, source_repo, paths, intent, **kwargs):
    if source_repo == 'vellum':
        return {'status': 'routed', 'primary_repository': 'vellum', 'matched_slices': []}
    matches = exact_slices(authority, paths[0])
    return {'status': 'routed', 'primary_repository': 'pulp', 'matched_slices': [row['id'] for row in matches]}
"""
        )
        router_test_path = router_root / "test_route_change.py"
        router_test_path.write_text("# expansion routing negative controls\n")
        router_dependency_path = router_root / "routing_evidence.py"
        router_dependency_path.write_text("# no dependencies in fixture router\n")
        acceptance_path = pulp_root / acknowledgement["coordinates"]["pulp_acceptance_path"]
        acceptance_path.parent.mkdir(parents=True)
        acceptance_path.write_text(
            json.dumps(
                self.pulp_exact_boundary_acceptance(
                    acknowledgement, pulp_main_commit=pulp_main_commit,
                    projection_sha256=projection_digest,
                    router_sha256=hashlib.sha256(router_path.read_bytes()).hexdigest(),
                    router_test_sha256=hashlib.sha256(
                        router_test_path.read_bytes()
                    ).hexdigest(),
                    router_dependency_sha256=hashlib.sha256(
                        router_dependency_path.read_bytes()
                    ).hexdigest(),
                )
            )
            + "\n"
        )
        subprocess.run(["git", "-C", str(pulp_root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(pulp_root), "commit", "-qm", "accept boundary"],
            check=True,
        )
        pulp_commit = subprocess.check_output(
            ["git", "-C", str(pulp_root), "rev-parse", "HEAD"], text=True
        ).strip()
        digest = hashlib.sha256(acceptance_path.read_bytes()).hexdigest()
        acknowledgement["coordinates"]["pulp_acceptance_merge_commit"] = pulp_commit
        acknowledgement["coordinates"]["pulp_acceptance_sha256"] = digest
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        with mock.patch.dict(
            os.environ, {"GH_TOKEN": "must-not-cross-isolation-boundary"}
        ):
            errors = verifier.validate_exact_boundary_repository_evidence(
                ROOT, pulp_root, acknowledgement, amendment,
                open_pr_snapshot=self.open_pr_snapshot(),
            )
        self.assertEqual(errors, [])
        self.assertFalse(hasattr(builtins, "VELLUM_EXTERNAL_ROUTER_MUTATION"))
        (pulp_root / verifier.PULP_OWNERSHIP_PATH).write_text('{"schema_version":3}\n')
        subprocess.run(["git", "-C", str(pulp_root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(pulp_root), "commit", "-qm", "drift current routing"],
            check=True,
        )
        errors = verifier.validate_exact_boundary_repository_evidence(
            ROOT, pulp_root, acknowledgement, amendment
        )
        self.assertTrue(
            any("authoritative Pulp main" in error for error in errors)
        )
        subprocess.run(
            ["git", "-C", str(pulp_root), "checkout", "-q", pulp_main_commit], check=True
        )
        errors = verifier.validate_exact_boundary_repository_evidence(
            ROOT, pulp_root, acknowledgement, amendment
        )
        self.assertTrue(any("not on authoritative Pulp main" in error for error in errors))
        subprocess.run(
            ["git", "-C", str(pulp_root), "checkout", "-q", pulp_commit], check=True
        )
        acknowledgement["coordinates"]["pulp_acceptance_sha256"] = "0" * 64
        errors = verifier.validate_exact_boundary_repository_evidence(
            ROOT, pulp_root, acknowledgement, amendment
        )
        self.assertTrue(any("acceptance artifact digest differs" in error for error in errors))

        acknowledgement["coordinates"]["pulp_acceptance_sha256"] = digest
        acceptance = json.loads(acceptance_path.read_text())
        acceptance["refresh"]["pulp_main_commit"] = "7" * 40
        acceptance_path.write_text(json.dumps(acceptance) + "\n")
        subprocess.run(["git", "-C", str(pulp_root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(pulp_root), "commit", "--amend", "-qm", "accept boundary"],
            check=True,
        )
        fabricated_commit = subprocess.check_output(
            ["git", "-C", str(pulp_root), "rev-parse", "HEAD"], text=True
        ).strip()
        acknowledgement["coordinates"]["pulp_acceptance_merge_commit"] = fabricated_commit
        acknowledgement["coordinates"]["pulp_acceptance_sha256"] = hashlib.sha256(
            acceptance_path.read_bytes()
        ).hexdigest()
        errors = verifier.validate_exact_boundary_repository_evidence(
            ROOT, pulp_root, acknowledgement, amendment
        )
        self.assertTrue(any("refreshed overlap audit commit" in error for error in errors))

    def test_exact_acknowledgement_rejects_unmerged_vellum_amendment(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        vellum_root = Path(temporary.name) / "vellum"
        subprocess.run(["git", "clone", "-q", str(ROOT), str(vellum_root)], check=True)
        subprocess.run(
            ["git", "-C", str(vellum_root), "config", "user.name", "Test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(vellum_root), "config", "user.email", "test@example.com"],
            check=True,
        )
        authoritative_head = subprocess.check_output(
            ["git", "-C", str(vellum_root), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git", "-C", str(vellum_root), "checkout", "-q", "-b", "unmerged",
                "bbe187d581f3f021a25b3ebd01332f89bbde142e",
            ],
            check=True,
        )
        amendment_path = vellum_root / verifier.BOUNDARY_AMENDMENT_PATH
        amendment_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.BOUNDARY_AMENDMENT_PATH, amendment_path)
        subprocess.run(["git", "-C", str(vellum_root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(vellum_root), "commit", "-qm", "unmerged amendment"],
            check=True,
        )
        unmerged = subprocess.check_output(
            ["git", "-C", str(vellum_root), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(vellum_root), "checkout", "-q", authoritative_head],
            check=True,
        )
        acknowledgement = self.exact_boundary_acknowledgement(
            amendment_commit=unmerged
        )
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        errors = verifier.validate_exact_boundary_repository_evidence(
            vellum_root, None, acknowledgement, amendment
        )
        self.assertTrue(any("not on the authoritative history" in error for error in errors))

    def test_promotion_attestation_rejects_mismatch_and_bad_digest(self) -> None:
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        data = self.promotion_attestation("3" * 64)
        data["authority_commit"] = "4" * 40
        errors = verifier.validate_authority_promotion_attestation(data, amendment)
        self.assertTrue(any("commits must match" in error for error in errors))
        data = self.promotion_attestation("not-a-digest")
        errors = verifier.validate_authority_promotion_attestation(data, amendment)
        self.assertTrue(any("expected SHA-256" in error for error in errors))
        data = self.promotion_attestation("3" * 64)
        data["promotion_mode"] = {}
        errors = verifier.validate_authority_promotion_attestation(data, amendment)
        self.assertTrue(any("closed promotion mode" in error for error in errors))

    def test_promotion_repository_evidence_rejects_fabricated_git_and_delivery_release(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        commit, tree = self.init_git_repo(
            root, remote="https://github.com/Generous-Corp/vellum.git"
        )
        data = self.promotion_attestation("3" * 64, commit=commit, tree=tree)
        errors = verifier.validate_promotion_repository_evidence(
            root,
            None,
            data,
            repository="danielraffel/vellum",
            release_source_commit=commit,
        )
        self.assertTrue(any("must run in Generous-Corp/vellum" in error for error in errors))
        self.assertTrue(any("--delivery-root" in error for error in errors))
        data["authority_tree"] = "f" * 40
        errors = verifier.validate_promotion_repository_evidence(
            root,
            root,
            data,
            repository="Generous-Corp/vellum",
            release_source_commit=commit,
        )
        self.assertTrue(any("authority tree differs from Git" in error for error in errors))
        data = self.promotion_attestation("3" * 64, commit="e" * 40, tree="f" * 40)
        errors = verifier.validate_promotion_repository_evidence(
            root,
            root,
            data,
            repository="Generous-Corp/vellum",
            release_source_commit=commit,
        )
        self.assertTrue(any("commit is unavailable" in error for error in errors))

    def test_exact_boundary_acknowledgement_requires_full_binding(self) -> None:
        data = self.exact_boundary_acknowledgement()
        self.assertEqual(verifier.validate_exact_boundary_acknowledgement(data), [])
        data["coordinates"]["pulp_acceptance_sha256"] = "bad"
        errors = verifier.validate_exact_boundary_acknowledgement(data)
        self.assertTrue(any("pulp_acceptance_sha256" in error for error in errors))

    def test_pulp_acceptance_must_bind_the_acknowledged_amendment(self) -> None:
        acknowledgement = self.exact_boundary_acknowledgement()
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        self.assertEqual(
            verifier.validate_pulp_exact_boundary_acceptance(
                acceptance, acknowledgement, amendment
            ),
            [],
        )
        acceptance["counterpart"]["amendment_sha256"] = "0" * 64
        errors = verifier.validate_pulp_exact_boundary_acceptance(
            acceptance, acknowledgement, amendment
        )
        self.assertTrue(any("amendment_sha256" in error for error in errors))
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        acceptance["refresh"]["open_vellum_overlap_count"] = 1
        errors = verifier.validate_pulp_exact_boundary_acceptance(
            acceptance, acknowledgement, amendment
        )
        self.assertTrue(any("zero unresolved overlaps" in error for error in errors))
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        acceptance["refresh"]["open_pr_rows"] = ["not-a-row"]
        errors = verifier.validate_pulp_exact_boundary_acceptance(
            acceptance, acknowledgement, amendment
        )
        self.assertTrue(any("expected object" in error for error in errors))
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        acceptance["refresh"]["open_pr_rows"] = [
            {
                "repository": "Generous-Corp/vellum",
                "pull_request": 99,
                "merge_base_commit": "7" * 40,
                "head_commit": "8" * 40,
                "paths": ["authoring/import_html.py"],
                "disposition": "conflict-awaiting-owner",
                "resolution": "unresolved",
            }
        ]
        errors = verifier.validate_pulp_exact_boundary_acceptance(
            acceptance, acknowledgement, amendment
        )
        self.assertTrue(any("contradicts open PR rows" in error for error in errors))
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        acceptance["refresh"]["audited_at"] = "2026-11-28T23:59:59Z"
        errors = verifier.validate_pulp_exact_boundary_acceptance(
            acceptance, acknowledgement, amendment
        )
        self.assertTrue(any("timestamps must match" in error for error in errors))

    def test_open_pr_snapshot_independently_binds_every_live_overlap(self) -> None:
        acknowledgement = self.exact_boundary_acknowledgement()
        acceptance = self.pulp_exact_boundary_acceptance(acknowledgement)
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        route = next(
            row for row in verifier.exact_route_rows(matrix, amendment)
            if row["repository"] == "Generous-Corp/pulp"
        )
        snapshot = self.open_pr_snapshot({
            "repository": "Generous-Corp/pulp",
            "number": 42,
            "head_commit": "8" * 40,
            "paths": [route["path"], "docs/unrelated.md"],
        })
        errors = verifier.validate_open_pr_snapshot(
            snapshot, acceptance, matrix, amendment
        )
        self.assertTrue(any("live overlap Generous-Corp/pulp#42 is absent" in error for error in errors))
        acceptance["refresh"]["open_pr_rows"] = [{
            "repository": "Generous-Corp/pulp",
            "pull_request": 42,
            "merge_base_commit": "e" * 40,
            "head_commit": "8" * 40,
            "paths": [route["path"]],
            "disposition": "audited exact-route overlap",
            "resolution": "pulp-retained",
        }]
        self.assertEqual(
            verifier.validate_open_pr_snapshot(snapshot, acceptance, matrix, amendment),
            [],
        )
        snapshot["repositories"][0]["pulls"][0]["head_commit"] = "9" * 40
        errors = verifier.validate_open_pr_snapshot(
            snapshot, acceptance, matrix, amendment
        )
        self.assertTrue(any("head differs" in error for error in errors))
        snapshot = self.open_pr_snapshot({
            "repository": "Generous-Corp/pulp", "number": 42,
            "merge_base_commit": "6" * 40, "head_commit": "8" * 40,
            "paths": [route["path"]],
        })
        errors = verifier.validate_open_pr_snapshot(snapshot, acceptance, matrix, amendment)
        self.assertTrue(any("merge base differs" in error for error in errors))
        snapshot = self.open_pr_snapshot()
        errors = verifier.validate_open_pr_snapshot(
            snapshot, acceptance, matrix, amendment
        )
        self.assertTrue(any("is not currently open" in error for error in errors))
        mismatched_count = self.open_pr_snapshot({
            "repository": "Generous-Corp/pulp",
            "number": 43,
            "head_commit": "a" * 40,
            "paths": [route["path"]],
            "diff_path_count": 2,
        })
        errors = verifier.validate_open_pr_snapshot(
            mismatched_count, self.pulp_exact_boundary_acceptance(acknowledgement),
            matrix, amendment,
        )
        self.assertTrue(any("tree diff path count differs" in error for error in errors))
        vellum_route = next(
            row for row in verifier.exact_route_rows(matrix, amendment)
            if row["repository"] == "Generous-Corp/vellum"
        )
        delivery_snapshot = self.open_pr_snapshot({
            "repository": "danielraffel/vellum",
            "number": 44,
            "head_commit": "b" * 40,
            "paths": [vellum_route["path"]],
        })
        errors = verifier.validate_open_pr_snapshot(
            delivery_snapshot,
            self.pulp_exact_boundary_acceptance(acknowledgement), matrix, amendment,
        )
        self.assertTrue(any("live overlap danielraffel/vellum#44 is absent" in error for error in errors))

    def test_provenance_workflow_collects_live_open_prs_and_changed_files(self) -> None:
        workflow = (ROOT / ".github/workflows/provenance.yml").read_text()
        self.assertIn(
            'for repository in Generous-Corp/pulp Generous-Corp/vellum danielraffel/vellum', workflow
        )
        self.assertIn('pulls?state=open&per_page=100', workflow)
        self.assertIn("github.event_name != 'pull_request' && secrets.VELLUM_DELIVERY_READER_TOKEN", workflow)
        self.assertIn('export GH_TOKEN="$DELIVERY_READER_TOKEN"', workflow)
        self.assertIn('repository_evidence+=(--defer-live-open-pr-audit)', workflow)
        self.assertIn('repos/$repository/pulls/$pull_number"', workflow)
        self.assertIn('compare/$base_commit...$head_commit', workflow)
        self.assertIn('git/commits/$merge_base_commit', workflow)
        self.assertIn('git/commits/$head_commit', workflow)
        self.assertIn('git/trees/$merge_base_tree?recursive=1', workflow)
        self.assertIn('git/trees/$head_tree?recursive=1', workflow)
        self.assertIn('get("truncated") is not False', workflow)
        self.assertIn('--open-pr-snapshot-json "$RUNNER_TEMP/open-pr-snapshot.json"', workflow)
        self.assertIn('${{ runner.temp }}/open-pr-snapshot.json', workflow)
        self.assertIn('write_live_open_pr_cursor "$RUNNER_TEMP/open-pr-cursor-before.json"', workflow)
        self.assertIn('write_live_open_pr_cursor "$RUNNER_TEMP/open-pr-cursor-after.json"', workflow)
        self.assertEqual(
            workflow.count('cmp "$RUNNER_TEMP/open-pr-cursor.json"'), 2
        )

    def test_pulp_router_contract_requires_exact_authority_ci(self) -> None:
        commit = "a" * 40
        acceptance = self.pulp_exact_boundary_acceptance(
            self.exact_boundary_acknowledgement()
        )
        projection = acceptance["routing_projection"]
        evidence = {
            "check_runs": [{
                "name": "vellum-routing-contract",
                "head_sha": commit,
                "conclusion": "success",
                "app": {"id": 15368},
                "details_url": (
                    "https://github.com/Generous-Corp/pulp/actions/runs/42/job/43"
                ),
            }],
            "workflow_runs": [{
                "id": 42,
                "path": ".github/workflows/vellum-routing-contract.yml",
                "event": "push",
                "head_branch": "main",
                "head_sha": commit,
                "status": "completed",
                "conclusion": "success",
                "repository": {"full_name": "Generous-Corp/pulp"},
            }],
            "pulp_router_contract_receipts": [{
                "schema_version": 1,
                "kind": "pulp-vellum-routing-contract-execution",
                "repository": "Generous-Corp/pulp",
                "head_sha": commit,
                "run_id": 42,
                "workflow_path": ".github/workflows/vellum-routing-contract.yml",
                "status": "pass",
                "route_set_sha256": projection["route_set_sha256"],
                "router_sha256": projection["router_sha256"],
                "router_contract_test_sha256": projection[
                    "router_contract_test_sha256"
                ],
                "router_dependency_sha256": projection[
                    "router_dependency_sha256"
                ],
                "case_results": [
                    {"case_id": case_id, "status": "pass"}
                    for case_id in verifier.REQUIRED_PULP_ROUTER_CASES
                ],
            }],
        }
        self.assertEqual(
            verifier.validate_pulp_router_check_evidence(
                evidence, commit, acceptance
            ),
            [],
        )
        evidence["workflow_runs"][0]["repository"]["full_name"] = (
            "danielraffel/pulp"
        )
        errors = verifier.validate_pulp_router_check_evidence(
            evidence, commit, acceptance
        )
        self.assertTrue(any("missing exact workflow-bound" in error for error in errors))

    def test_external_evidence_strict_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: state"):
            verifier.load_json_bytes(
                b'{"state":"accepted","state":"rejected"}', "external evidence"
            )

    def test_cli_release_readiness_fails_before_parity_completion(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(ROOT), "--release-readiness"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = json.loads(completed.stdout)
        self.assertTrue(report["release_readiness_requested"])
        self.assertTrue(any("versioned parity completion" in error for error in report["errors"]))
        report = self.mutate_matrix(
            lambda d: next(
                cell for cell in d["cells"] if cell["id"] == "source.figma-rest"
            ).update(target_status="partial")
        )
        self.assertEqual(report["status"], "fail")

    def test_matrix_design_ir_relationship_drift_fails(self) -> None:
        report = self.mutate_matrix(
            lambda d: d["design_ir_relationship"].update(
                relationship="schemas-are-identical"
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_matrix_byte_drift_fails(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        matrix = root / verifier.MATRIX_PATH
        matrix.write_bytes(matrix.read_bytes() + b"\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("matrix differs" in error for error in report["errors"]))

    def test_acknowledgement_binds_exact_acceptance_without_authority(self) -> None:
        data = json.loads((ROOT / verifier.ACKNOWLEDGEMENT_PATH).read_text())
        self.assertEqual(data["authority_effect"], "none")
        self.assertEqual(
            data["coordinates"]["pulp_acceptance_merge_commit"],
            "a494429f4cf29a2d45c45fce12debfa0417ced21",
        )
        self.assertEqual(
            data["coordinates"]["pulp_acceptance_sha256"],
            "1535d76e34dfc80eda55247c1b0d47b9f47b3e58a08b6f1ab4b749692e6056fd",
        )

    def test_acknowledgement_authority_claim_fails(self) -> None:
        report = self.mutate_acknowledgement(
            lambda d: d.update(authority_effect="transferred")
        )
        self.assertEqual(report["status"], "fail")
        self.assertIsNone(report["authority_effect"])

    def test_acknowledgement_acceptance_coordinate_drift_fails(self) -> None:
        report = self.mutate_acknowledgement(
            lambda d: d["coordinates"].update(pulp_acceptance_merge_commit="0" * 40)
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_acknowledgement(
            lambda d: d["coordinates"].update(pulp_acceptance_sha256="0" * 64)
        )
        self.assertEqual(report["status"], "fail")

    def test_acknowledgement_gate_relaxation_fails(self) -> None:
        report = self.mutate_acknowledgement(
            lambda d: d["gates"].update(
                source_work_before_exact_boundary_acknowledgement=True
            )
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_acknowledgement(
            lambda d: d["gates"].update(pulp_consumption_authorized=True)
        )
        self.assertEqual(report["status"], "fail")

    def test_acknowledgement_timestamp_and_state_drift_fail(self) -> None:
        report = self.mutate_acknowledgement(
            lambda d: d.update(acknowledged_at="2026-08-11T20:54:34Z")
        )
        self.assertEqual(report["status"], "fail")
        report = self.mutate_acknowledgement(lambda d: d.update(state="accepted"))
        self.assertEqual(report["status"], "fail")

    def test_acknowledgement_byte_drift_fails(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        acknowledgement = root / verifier.ACKNOWLEDGEMENT_PATH
        acknowledgement.write_bytes(acknowledgement.read_bytes() + b"\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("acknowledgement differs" in error for error in report["errors"])
        )

    def test_addendum_closes_audited_blind_spots_without_authority(self) -> None:
        rows = json.loads((ROOT / verifier.ADDENDUM_PATH).read_text())[
            "added_capability_family_selectors"
        ]
        scopes = {row["id"]: row["pulp_selectors"] for row in rows}
        self.assertIn("compat/imports.json", scopes["design-source-ingest"])
        self.assertIn("compat/rn.json", scopes["design-source-ingest"])
        self.assertIn(
            "docs/reference/compat/rn.md",
            scopes["design-source-ingest"],
        )
        self.assertIn(
            "experimental/pulp-rs/src/cmd/design.rs",
            scopes["design-source-ingest"],
        )
        self.assertIn(
            "experimental/pulp-rs/src/main.rs",
            scopes["design-source-ingest"],
        )
        self.assertIn("test/test_import*", scopes["design-source-ingest"])
        self.assertIn("design/**", scopes["design-source-ingest"])
        self.assertIn("examples/design*/**", scopes["design-source-ingest"])
        self.assertIn("test/test_design_*", scopes["design-source-ingest"])
        self.assertIn("tools/figma-plugin/**", scopes["design-source-ingest"])
        self.assertIn(
            "test/fixtures/browser-capture-*/**",
            scopes["chromium-authoring-frontend"],
        )
        self.assertIn("test/test_jsx_lock.cpp", scopes["design-ir-contract"])
        self.assertIn(
            "test/test_widget_bridge_runtime_import.cpp",
            scopes["design-ir-contract"],
        )
        self.assertIn("core/view/**", scopes["render-assets-and-backends"])
        self.assertIn("assets/design-system/**", scopes["render-assets-and-backends"])
        self.assertIn("tools/scripts/*skia*", scopes["render-assets-and-backends"])
        self.assertIn("tools/import-validation/**", scopes["visual-proof-harness"])
        self.assertIn("tools/harness/**", scopes["visual-proof-harness"])
        self.assertIn("test/test_screenshot*", scopes["visual-proof-harness"])
        self.assertIn("test/test_*screenshot*", scopes["visual-proof-harness"])
        self.assertIn("external/skia-build/**", scopes["visual-proof-harness"])
        self.assertIn(
            "tools/scripts/package_cli.py",
            scopes["design-output-and-packaging"],
        )
        self.assertIn(
            "templates/swiftui-design-host/**",
            scopes["design-output-and-packaging"],
        )

    def test_addendum_authority_claim_fails(self) -> None:
        report = self.mutate_addendum(
            lambda d: d.update(authority_effect="transferred")
        )
        self.assertEqual(report["status"], "fail")
        self.assertIsNone(report["authority_effect"])

    def test_addendum_cannot_relax_retained_boundaries(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["audit"].update(retained_boundaries_unchanged=False)
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_cannot_omit_import_validation(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["added_capability_family_selectors"][4][
                "pulp_selectors"
            ].remove("tools/import-validation/**")
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_cannot_omit_import_compatibility_catalog(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["added_capability_family_selectors"][0][
                "pulp_selectors"
            ].remove("compat/imports.json")
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_cannot_omit_rust_design_command(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["added_capability_family_selectors"][0][
                "pulp_selectors"
            ].remove("experimental/pulp-rs/src/cmd/design.rs")
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_cannot_omit_import_test_family(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["added_capability_family_selectors"][0][
                "pulp_selectors"
            ].remove("test/test_import*")
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_cannot_omit_non_design_named_ir_evidence(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["added_capability_family_selectors"][2][
                "pulp_selectors"
            ].remove("test/test_jsx_lock.cpp")
        )
        self.assertEqual(report["status"], "fail")

    def test_duplicate_addendum_family_fails(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["added_capability_family_selectors"].append(
                dict(d["added_capability_family_selectors"][0])
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_gate_relaxation_fails(self) -> None:
        report = self.mutate_addendum(
            lambda d: d["gates"].update(pulp_consumption_authorized=True)
        )
        self.assertEqual(report["status"], "fail")

    def test_addendum_byte_drift_fails(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        addendum = root / verifier.ADDENDUM_PATH
        addendum.write_bytes(addendum.read_bytes() + b"\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("addendum differs" in error for error in report["errors"]))

    def test_duplicate_key_fails(self) -> None:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        target.write_text('{"schema_version":1,"schema_version":1}\n')
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("duplicate JSON key" in e for e in report["errors"]))

    def test_authority_claim_fails(self) -> None:
        report = self.mutate(lambda d: d.update(authority_effect="transferred"))
        self.assertEqual(report["status"], "fail")
        self.assertIsNone(report["authority_effect"])
        self.assertIsNone(report["proposal_id"])

    def test_coordinate_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["coordinates"].update(pulp_baseline_commit="0" * 40)
        )
        self.assertEqual(report["status"], "fail")

    def test_proposal_timestamp_drift_fails(self) -> None:
        report = self.mutate(lambda d: d.update(proposed_at="2026-08-10T22:00:00Z"))
        self.assertEqual(report["status"], "fail")

    def test_family_selector_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0]["pulp_selectors"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_family_target_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0]["vellum_target_roots"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_family_title_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0].update(title=None)
        )
        self.assertEqual(report["status"], "fail")

    def test_retained_boundary_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["retained_boundaries"][0]["pulp_selectors"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_retained_rationale_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["retained_boundaries"][0].update(rationale="inverted")
        )
        self.assertEqual(report["status"], "fail")

    def test_maintenance_path_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0]["pulp_paths"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_path_traversal_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0]["pulp_paths"].__setitem__(
                0, "core/../../outside"
            )
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("safely repository-relative" in e for e in report["errors"]))

    def test_truncated_state_machine_fails(self) -> None:
        report = self.mutate(lambda d: d["required_transitions"].pop())
        self.assertEqual(report["status"], "fail")

    def test_gate_relaxation_fails(self) -> None:
        report = self.mutate(
            lambda d: d["gates"].update(
                source_work_before_exact_boundary_acknowledgement=True
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_boolean_schema_version_does_not_equal_integer(self) -> None:
        report = self.mutate(lambda d: d.update(schema_version=True))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("schema_version" in e for e in report["errors"]))

    def test_integer_gate_does_not_equal_boolean(self) -> None:
        report = self.mutate(
            lambda d: d["gates"].update(proposal_may_transfer_authority=0)
        )
        self.assertEqual(report["status"], "fail")

    def test_integer_coordinate_does_not_equal_boolean(self) -> None:
        report = self.mutate(
            lambda d: d["coordinates"].update(vellum_work_repository_is_temporary=1)
        )
        self.assertEqual(report["status"], "fail")

    def test_maintenance_expiry_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0].update(expires_at_gate="never")
        )
        self.assertEqual(report["status"], "fail")

    def test_maintenance_rationale_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0].update(rationale="rewritten")
        )
        self.assertEqual(report["status"], "fail")

    def test_non_string_nested_path_returns_report(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0].update(pulp_selectors=[{}])
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["errors"])

    def test_unhashable_nested_target_returns_report(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0].update(vellum_target_roots=[{}])
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["errors"])

    def test_unknown_expansion_artifact_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        unknown = root / verifier.EXPANSIONS_ROOT / "unreviewed-acceptance.json"
        unknown.write_text('{"authority_effect":"transferred"}\n')
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("artifact set differs" in e for e in report["errors"]))

    def test_filesystem_closure_rejects_untracked_artifact_in_git_checkout(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "add", verifier.EXPANSIONS_ROOT.as_posix()],
            check=True,
        )
        unknown = root / verifier.EXPANSIONS_ROOT / "untracked-acceptance.json"
        unknown.write_text('{"authority_effect":"transferred"}\n')
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("untracked-acceptance.json" in e for e in report["errors"]))

    def test_git_closure_ignores_incidental_untracked_file(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "add", verifier.EXPANSIONS_ROOT.as_posix()],
            check=True,
        )
        (root / verifier.EXPANSIONS_ROOT / ".DS_Store").write_bytes(b"incidental")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "pass", report["errors"])

    def test_git_closure_rejects_non_json_untracked_artifact(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "add", verifier.EXPANSIONS_ROOT.as_posix()],
            check=True,
        )
        unknown = root / verifier.EXPANSIONS_ROOT / "acceptance.txt"
        unknown.write_text("authority_effect=transferred\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("acceptance.txt" in e for e in report["errors"]))

    def test_git_closure_rejects_extensionless_untracked_artifact(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "add", verifier.EXPANSIONS_ROOT.as_posix()],
            check=True,
        )
        unknown = root / verifier.EXPANSIONS_ROOT / "ACCEPTANCE"
        unknown.write_text("authority transferred\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("ACCEPTANCE" in e for e in report["errors"]))

    def test_dangling_symlink_artifact_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        unknown = root / verifier.EXPANSIONS_ROOT / "pulp-watch-acceptance.json"
        unknown.symlink_to(root / "missing-acceptance.json")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("must not be symlinks" in e for e in report["errors"]))

    def test_symlinked_directory_artifact_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        external = root / "external-acceptance"
        external.mkdir()
        unknown = root / verifier.EXPANSIONS_ROOT / "acceptance"
        unknown.symlink_to(external, target_is_directory=True)
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("must not be symlinks" in e for e in report["errors"]))

    def test_formatting_only_proposal_drift_fails_closed(self) -> None:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        target.write_text(target.read_text() + "\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("proposal differs" in e for e in report["errors"]))

    def test_expansion_readme_drift_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        readme = root / verifier.EXPANSIONS_ROOT / "README.md"
        readme.write_text(readme.read_text() + "\nWatch acceptance authorizes source.\n")
        report = verifier.verify(root, repository_checks=False)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("README differs" in e for e in report["errors"]))

    def test_cli_failure_writes_report_and_exits_nonzero(self) -> None:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        data = json.loads(target.read_text())
        data["authority_effect"] = "transferred"
        target.write_text(json.dumps(data, indent=2) + "\n")
        output = root / "reports" / "authority-expansion.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(output),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = json.loads(output.read_text())
        self.assertEqual(report["status"], "fail")

    def test_malformed_nested_types_return_report(self) -> None:
        report = self.mutate(lambda d: d.update(capability_families=None))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main()
