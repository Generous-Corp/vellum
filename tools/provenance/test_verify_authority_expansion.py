#!/usr/bin/env python3
"""Negative controls for the non-authoritative expansion proposal."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify_authority_expansion.py")
SPEC = importlib.util.spec_from_file_location("verify_authority_expansion", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
ROOT = Path(__file__).resolve().parents[2]


class Tests(unittest.TestCase):
    def exact_boundary_acknowledgement(self) -> dict:
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
                "pulp_acceptance_merge_commit": "4" * 40,
                "pulp_acceptance_path": ".github/vellum-expansion-watch/full-design-import-render-v1/exact-boundary-acceptance-1.json",
                "pulp_acceptance_sha256": "5" * 64,
                "vellum_delivery_repository": "danielraffel/vellum",
                "vellum_amendment_merge_commit": "6" * 40,
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

    def promotion_attestation(self, acknowledgement_sha256: str) -> dict:
        commit = "1" * 40
        tree = "2" * 40
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
            "parity_release_source_commit": commit,
        }

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
        self.assertGreaterEqual(workflow.count("authority-expansion-report.json"), 2)

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
        self.assertTrue(any("source.agent-html-chromium" in error for error in errors))
        for cell in data["cells"]:
            cell["status"] = cell["target_status"]
        errors = verifier.validate_release_readiness(ROOT, data, amendment)
        self.assertFalse(any("have not reached target" in error for error in errors))
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
        import hashlib
        digest = hashlib.sha256(acknowledgement.read_bytes()).hexdigest()
        promotion = root / verifier.EXPANSIONS_ROOT / (
            "full-design-import-render-v1/authority-promotion-attestation-1.json"
        )
        promotion.write_text(json.dumps(self.promotion_attestation(digest)) + "\n")
        matrix = json.loads((ROOT / verifier.MATRIX_PATH).read_text())
        for cell in matrix["cells"]:
            cell["status"] = cell["target_status"]
        amendment = json.loads((ROOT / verifier.BOUNDARY_AMENDMENT_PATH).read_text())
        self.assertEqual(verifier.validate_release_readiness(root, matrix, amendment), [])

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

    def test_exact_boundary_acknowledgement_requires_full_binding(self) -> None:
        data = self.exact_boundary_acknowledgement()
        self.assertEqual(verifier.validate_exact_boundary_acknowledgement(data), [])
        data["coordinates"]["pulp_acceptance_sha256"] = "bad"
        errors = verifier.validate_exact_boundary_acknowledgement(data)
        self.assertTrue(any("pulp_acceptance_sha256" in error for error in errors))

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
        self.assertTrue(any("have not reached target" in error for error in report["errors"]))
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
