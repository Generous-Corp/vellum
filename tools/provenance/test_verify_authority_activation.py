#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("verify_authority_activation.py")
SPEC = importlib.util.spec_from_file_location("verify_authority_activation", MODULE_PATH)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


class FakeGitHub:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses

    def get(self, path: str, token: str) -> object:
        if path not in self.responses:
            raise AssertionError(f"unexpected GitHub request: {path}")
        return self.responses[path]


class AuthorityActivationTests(unittest.TestCase):
    def test_current_repository_is_prepared_and_cannot_activate(self) -> None:
        root = MODULE_PATH.parents[2]
        result = activation.verify_prepared(root)
        self.assertEqual(result["status"], "prepared-not-active")
        with self.assertRaisesRegex(activation.ActivationError, "not enabled"):
            activation.validate_trust_policy(activation.load_json(root / activation.TRUST_PATH))

    def test_source_projection_positive_control_then_rejects_unresolved(self) -> None:
        group = {"pulp_legacy_slices": ["render"]}
        ownership = {"slices": [{"id": "render", "state": "pulp-authoritative-untransferred", "paths": ["core/render/a.cpp"]}]}
        manifest = {"entries": [{
            "source_path": "core/render/a.cpp", "classification": "framework-core",
            "git_blob_sha": "a" * 40, "git_mode": "100644",
        }]}
        seed = {"core/render/a.cpp": {"blob": "a" * 40, "mode": "100644"}}

        projection = activation.build_source_projection(
            group=group, ownership=ownership, manifest=manifest, seed_tree=seed
        )
        self.assertEqual(projection["core/render/a.cpp"]["blob"], "a" * 40)

        manifest["entries"][0]["classification"] = "unresolved"
        with self.assertRaisesRegex(activation.ActivationError, "not transferable"):
            activation.build_source_projection(
                group=group, ownership=ownership, manifest=manifest, seed_tree=seed
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
            "/repos/Generous-Corp/vellum": {"id": 777, "private": True, "archived": False},
            "/installation/repositories?per_page=100": {
                "total_count": 1,
                "repositories": [{"id": 777}],
            },
        }
        activation.verify_repository(
            FakeGitHub(responses), "installation-token", expected, app_jwt="app-jwt"
        )

        responses["/installation/repositories?per_page=100"] = {
            "total_count": 2,
            "repositories": [{"id": 777}, {"id": 888}],
        }
        with self.assertRaisesRegex(activation.ActivationError, "one-repository"):
            activation.verify_repository(
                FakeGitHub(responses), "installation-token", expected, app_jwt="app-jwt"
            )

    def test_protected_ref_positive_control_then_rejects_non_strict(self) -> None:
        commit = "a" * 40
        ref = "refs/heads/authority/native-design-kernel-v1"
        ref_path = "/repos/Generous-Corp/vellum/git/ref/heads/authority/native-design-kernel-v1"
        protection_path = "/repos/Generous-Corp/vellum/branches/authority%2Fnative-design-kernel-v1/protection"
        responses = {
            ref_path: {"object": {"sha": commit}},
            protection_path: {
                "required_status_checks": {
                    "strict": True,
                    "checks": [{"context": "provenance-verify", "app_id": 123}],
                }
            },
        }
        activation.verify_protected_ref(
            github=FakeGitHub(responses), token="token", full_name="Generous-Corp/vellum",
            ref=ref, commit=commit, expected_apps={"provenance-verify": 123},
        )

        responses[protection_path]["required_status_checks"]["strict"] = False
        with self.assertRaisesRegex(activation.ActivationError, "strict"):
            activation.verify_protected_ref(
                github=FakeGitHub(responses), token="token", full_name="Generous-Corp/vellum",
                ref=ref, commit=commit, expected_apps={"provenance-verify": 123},
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
