#!/usr/bin/env python3
"""Deterministic tests for the Pulp/Vellum change router."""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SCENARIOS = HERE.parent / "references/routing-scenarios.v1.json"
SPEC = importlib.util.spec_from_file_location("route_change", HERE / "route_change.py")
assert SPEC and SPEC.loader
ROUTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ROUTER
SPEC.loader.exec_module(ROUTER)

RECORD_COMMIT = "a" * 40
PULP_COMMIT = "b" * 40
RECORD_PATH = "provenance/authority/records/native-design-kernel-v1.json"
EVENT_ID = "20260724-authority-activation"
EMERGENCY_EVENT = ".github/vellum-change-events/20260724-routing-emergency.json"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_fixture(root: Path, mutation: dict[str, Any] | None = None) -> tuple[Path, Path]:
    mutation = mutation or {}
    vellum = root / "vellum"
    pulp = root / "pulp"
    (vellum / "provenance").mkdir(parents=True)
    (pulp / ".github").mkdir(parents=True)
    (pulp / "docs/contracts").mkdir(parents=True)
    (pulp / ".gitignore").write_text("build/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(vellum)], check=True)
    subprocess.run(["git", "-C", str(vellum), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(vellum), "config", "user.name", "Routing Test"], check=True)
    write_json(
        vellum / RECORD_PATH,
        {"schema_version": 2, "state": "active", "event_id": EVENT_ID},
    )
    subprocess.run(["git", "-C", str(vellum), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(vellum), "commit", "-qm", "authority record"],
        check=True,
    )
    record_commit = subprocess.run(
        ["git", "-C", str(vellum), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    vellum_state = mutation.get("vellum_activation_state", "active")
    pulp_state = mutation.get("pulp_activation_state", "active")
    pulp_record_commit = mutation.get("pulp_record_commit", record_commit)
    ownership = f"""schema_version: 2
framework_repository: Generous-Corp/vellum
activation:
  state: {vellum_state}
  vellum_authority_record_commit: {record_commit}
  authority_record_path: {RECORD_PATH}
  pulp_activation_commit: {PULP_COMMIT}
  pulp_authority_event_id: {EVENT_ID}
"""
    (vellum / "provenance/ownership-map.yaml").write_text(ownership, encoding="utf-8")
    write_json(
        vellum / "provenance/pulp-extraction.json",
        {
            "authority": {
                "state": vellum_state,
                "authority_record_commit": record_commit,
                "authority_record_path": RECORD_PATH,
                "pulp_activation_commit": PULP_COMMIT,
                "pulp_authority_event_id": EVENT_ID,
            }
        },
    )
    projection = {
            "framework_repository": "Generous-Corp/vellum",
            "activation": {
                "state": pulp_state,
                "vellum_authority_commit": pulp_record_commit,
                "authority_record_path": RECORD_PATH,
                "initial_transition_event": EVENT_ID,
            },
            "slices": [
                {
                    "id": "canvas-kernel",
                    "state": "framework-authoritative-transferred",
                    "paths": ["core/canvas/src/text_layout.cpp"],
                },
                {
                    "id": "macos-shell",
                    "state": "framework-authoritative-transferred",
                    "paths": ["core/view/platform/mac/accessibility_mac.mm"],
                },
                {
                    "id": "design-schema-compiler",
                    "state": "framework-authoritative-transferred",
                    "paths": ["packages/pulp-import-ir/src/anchors.ts"],
                },
                {
                    "id": "retained-ui-kernel-deferred",
                    "state": "excluded",
                    "paths": ["core/view/src/view.cpp"],
                },
                {
                    "id": "legacy-figma-schema",
                    "state": "pulp-only",
                    "paths": ["tools/figma-plugin/schema/figma-plugin-export-v1.json"],
                },
                {
                    "id": "pulp-audio-plugin-product",
                    "state": "pulp-only",
                    "paths": ["core/audio/"],
                },
                {
                    "id": "pulp-import-tooling",
                    "state": "pulp-only",
                    "paths": ["tools/import-design/"],
                },
                {
                    "id": "runtime-assets",
                    "state": "pulp-authoritative-untransferred",
                    "paths": ["external/fonts/Inter-Regular.ttf"],
                },
            ],
        }
    slice_authority = {
        "event_id": EVENT_ID,
        "vellum_commit": record_commit,
        "counterpart": RECORD_PATH,
        "accepted_by": "@routing-test",
        "accepted_at": "2026-07-24T12:03:00Z",
    }
    for item in projection["slices"]:
        item["authority"] = (
            dict(slice_authority)
            if item["state"] == "framework-authoritative-transferred"
            else None
        )
    slice_mutation = mutation.get("slice_authority")
    if slice_mutation is not None:
        projection["slices"][0]["authority"] = slice_mutation
    write_json(pulp / ".github/vellum-ownership.json", projection)
    subprocess.run(["git", "init", "-q", str(pulp)], check=True)
    subprocess.run(["git", "-C", str(pulp), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(pulp), "config", "user.name", "Routing Test"], check=True)
    subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(pulp), "commit", "-qm", "activate authority"],
        check=True,
    )
    pulp_activation_commit = subprocess.run(
        ["git", "-C", str(pulp), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    write_json(
        vellum / "provenance/pulp-observatory/legacy-path-map.yaml",
        {
            "schema_version": 2,
            "state": "active",
            "mappings": [
                {
                    "id": "canvas-kernel",
                    "pulp_paths": ["core/canvas/"],
                    "vellum_paths": ["graphics/"],
                    "contract_tests": ["vellum.gpu-native", "vellum.install-consumer"],
                },
                {
                    "id": "macos-shell",
                    "pulp_paths": ["core/view/platform/mac/"],
                    "vellum_paths": ["apps/app-host/"],
                    "contract_tests": ["vellum.authoring", "vellum.gpu-native"],
                },
                {
                    "id": "retained-ui-kernel",
                    "pulp_paths": ["core/view/"],
                    "vellum_paths": ["authoring/", "packages/vellum-ui/"],
                    "contract_tests": ["vellum-ui", "vellum.authoring"],
                },
                {
                    "id": "design-schema-compiler",
                    "pulp_paths": ["packages/pulp-import-ir/"],
                    "vellum_paths": ["packages/vellum-design-ir/"],
                    "contract_tests": ["vellum-design-ir", "vellum-design-ir-sterile-consumer"],
                },
                {
                    "id": "pulp-figma-exporter",
                    "pulp_paths": ["tools/figma-plugin/"],
                    "vellum_paths": [],
                    "contract_tests": ["vellum-design-ir"],
                },
                {
                    "id": "pulp-design-import-tooling",
                    "pulp_paths": ["tools/import-design/"],
                    "vellum_paths": [],
                    "contract_tests": ["vellum-design-ir", "vellum-design-ir-sterile-consumer"],
                },
                {
                    "id": "render-skia-dawn",
                    "pulp_paths": ["core/render/"],
                    "vellum_paths": ["graphics/src/skia_dawn_surface.mm"],
                    "contract_tests": ["vellum.gpu-native", "vellum.install-consumer"],
                },
            ],
            "transitive_path_rules": ["CMakeLists.txt", "*.cmake", "*.schema.json"],
        },
    )

    ownership = ownership.replace(PULP_COMMIT, pulp_activation_commit)
    (vellum / "provenance/ownership-map.yaml").write_text(
        ownership,
        encoding="utf-8",
    )
    extraction_path = vellum / "provenance/pulp-extraction.json"
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    extraction["authority"]["pulp_activation_commit"] = pulp_activation_commit
    write_json(extraction_path, extraction)
    subprocess.run(["git", "-C", str(vellum), "add", "."], check=True)
    subprocess.run(["git", "-C", str(vellum), "commit", "-qm", "finalize authority"], check=True)
    sdk_source_commit = subprocess.run(
        ["git", "-C", str(vellum), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    write_json(
        pulp / "docs/contracts/vellum-sdk-adoption.json",
        {
            "schema": "pulp.vellum.sdk-adoption.v1",
            "state": "active",
            "framework_repository": "Generous-Corp/vellum",
            "vellum_authority_commit": record_commit,
            "authority_record_path": RECORD_PATH,
            "pulp_activation_commit": pulp_activation_commit,
            "recorded_by": "@routing-test",
            "recorded_at": "2026-07-24T12:03:00Z",
            "sdk": {
                "version": "0.1.6",
                "source_commit": sdk_source_commit,
                "artifact_sha256": "c" * 64,
            },
        },
    )
    write_json(
        pulp / EMERGENCY_EVENT,
        {
            "schema_version": 1,
            "event_id": "20260724-routing-emergency",
            "kind": "change",
            "created_at": "2026-07-24T12:03:00Z",
            "slices": ["canvas-kernel"],
            "rationale": "Bounded routing test emergency.",
            "tests": ["vellum.gpu-native"],
            "disposition": "emergency-exception",
            "owner": "@owner",
            "expiry": "2026-08-01",
            "follow_up": "https://github.com/Generous-Corp/vellum/issues/1",
        },
    )
    subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
    subprocess.run(["git", "-C", str(pulp), "commit", "-qm", "adopt SDK"], check=True)
    return vellum, pulp


def assert_subset(test: unittest.TestCase, expected: Any, actual: Any, path: str = "result") -> None:
    if isinstance(expected, dict):
        test.assertIsInstance(actual, dict, path)
        for key, value in expected.items():
            test.assertIn(key, actual, path)
            assert_subset(test, value, actual[key], f"{path}.{key}")
    else:
        test.assertEqual(expected, actual, path)


class RoutingScenariosTest(unittest.TestCase):
    def test_all_twenty_adversarial_scenarios(self) -> None:
        document = json.loads(SCENARIOS.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 1)
        scenarios = document["scenarios"]
        self.assertEqual(len(scenarios), 20)
        self.assertEqual(len({row["id"] for row in scenarios}), 20)

        for scenario in scenarios:
            with self.subTest(scenario=scenario["id"]):
                with tempfile.TemporaryDirectory() as temporary:
                    vellum, pulp = make_fixture(Path(temporary), scenario.get("fixture"))
                    try:
                        authority = ROUTER.load_authority(vellum, pulp)
                        arguments = dict(scenario["input"])
                        if "now" in arguments:
                            arguments["now"] = dt.date.fromisoformat(arguments["now"])
                        result = ROUTER.route(authority, **arguments)
                    except ROUTER.AuthorityError as exc:
                        result = ROUTER._result("invalid_authority", reasons=[str(exc)])
                    assert_subset(self, scenario["expected"], result)

    def test_valid_framework_backport_commit_must_resolve_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            commit = subprocess.run(
                ["git", "-C", str(vellum), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            result = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="pulp",
                paths=["core/canvas/src/text_layout.cpp"],
                intent="generic",
                operation="framework-backport",
                framework_commit=commit,
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["pulp_event_disposition"], "framework-backport")
            self.assertEqual(result["observatory_disposition"], "port-required")

            contradictory = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="pulp",
                paths=["core/canvas/src/text_layout.cpp"],
                intent="generic",
                counterpart_result="not-affected",
                operation="framework-backport",
                framework_commit=commit,
            )
            self.assertEqual(contradictory["status"], "decision_required")
            self.assertIn("contradicts", contradictory["reasons"][0])

    def test_paths_and_framework_backport_mode_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            authority = ROUTER.load_authority(vellum, pulp)
            for malformed in (
                "/core/canvas/src/text_layout.cpp",
                "../core/canvas/src/text_layout.cpp",
                "./core/canvas/src/text_layout.cpp",
                "core//canvas/src/text_layout.cpp",
                r"core\canvas\src\text_layout.cpp",
            ):
                with self.subTest(path=malformed):
                    result = ROUTER.route(
                        authority,
                        source_repo="pulp",
                        paths=[malformed],
                        intent="generic",
                    )
                    self.assertEqual(result["status"], "decision_required")
                    self.assertIn(
                        "normalized repository-relative POSIX paths",
                        result["reasons"][0],
                    )

            commit = subprocess.run(
                ["git", "-C", str(vellum), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            for source_repo, intent in (
                ("vellum", "generic"),
                ("pulp", "pulp-specific"),
                ("pulp", "emergency"),
            ):
                with self.subTest(source_repo=source_repo, intent=intent):
                    result = ROUTER.route(
                        authority,
                        source_repo=source_repo,
                        paths=["core/canvas/src/text_layout.cpp"],
                        intent=intent,
                        operation="framework-backport",
                        framework_commit=commit,
                    )
                    self.assertEqual(result["status"], "decision_required")
                    self.assertIn(
                        "requires generic intent",
                        result["reasons"][0],
                    )

    def test_emergency_and_adoption_metadata_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            authority = ROUTER.load_authority(vellum, pulp)
            missing_emergency = ROUTER.route(
                authority,
                source_repo="pulp",
                paths=["core/canvas/src/text_layout.cpp"],
                intent="emergency",
                now=dt.date(2026, 7, 24),
            )
            self.assertEqual(missing_emergency["status"], "decision_required")
            self.assertIn(
                "committed Pulp event",
                missing_emergency["reasons"][0],
            )

            expired_emergency = ROUTER.route(
                authority,
                source_repo="pulp",
                paths=["core/canvas/src/text_layout.cpp"],
                intent="emergency",
                emergency_event=EMERGENCY_EVENT,
                emergency_owner="@owner",
                emergency_created="2026-07-20",
                emergency_expiry="2026-07-23",
                emergency_follow_up="https://github.com/Generous-Corp/vellum/issues/1",
                now=dt.date(2026, 7, 24),
            )
            self.assertIn("emergency is already expired", expired_emergency["reasons"])

            renewable_emergency = ROUTER.route(
                authority,
                source_repo="pulp",
                paths=["core/canvas/src/text_layout.cpp"],
                intent="emergency",
                emergency_event=EMERGENCY_EVENT,
                emergency_owner="@owner",
                emergency_created="2026-07-01",
                emergency_expiry="2026-08-01",
                emergency_follow_up="https://github.com/Generous-Corp/vellum/issues/1",
                now=dt.date(2026, 7, 24),
            )
            self.assertEqual(renewable_emergency["status"], "decision_required")
            self.assertTrue(
                any(
                    "more than 14 days after its creation date" in reason
                    for reason in renewable_emergency["reasons"]
                )
            )

            missing_adoption = ROUTER.route(
                authority,
                source_repo="vellum",
                paths=["graphics/src/skia_dawn_surface.mm"],
                intent="generic",
                operation="sdk-adoption",
            )
            self.assertEqual(missing_adoption["status"], "decision_required")

            unmapped_adoption = ROUTER.route(
                authority,
                source_repo="vellum",
                paths=["scripts/install_core.py"],
                intent="generic",
                counterpart_result="affected",
                operation="sdk-adoption",
                adoption_contract="docs/contracts/vellum-sdk-adoption.json",
            )
            self.assertEqual(unmapped_adoption["status"], "decision_required")
            self.assertIn(
                "applicable Pulp counterpart",
                unmapped_adoption["reasons"][0],
            )

            unchecked_adoption = ROUTER.route(
                authority,
                source_repo="vellum",
                paths=["graphics/src/skia_dawn_surface.mm"],
                intent="generic",
                operation="sdk-adoption",
                adoption_contract="docs/contracts/vellum-sdk-adoption.json",
            )
            self.assertEqual(unchecked_adoption["status"], "decision_required")
            self.assertIn(
                "independently reproduced as affected",
                unchecked_adoption["reasons"][0],
            )

            adoption_path = pulp / "docs/contracts/vellum-sdk-adoption.json"
            ignored_adoption = pulp / "build/contract.json"
            ignored_adoption.parent.mkdir()
            ignored_adoption.write_bytes(adoption_path.read_bytes())
            ignored_result = ROUTER.route(
                authority,
                source_repo="vellum",
                paths=["graphics/src/skia_dawn_surface.mm"],
                intent="generic",
                counterpart_result="affected",
                operation="sdk-adoption",
                adoption_contract="build/contract.json",
            )
            self.assertEqual(ignored_result["status"], "decision_required")
            self.assertIn("not committed", ignored_result["reasons"][0])

            adoption = json.loads(adoption_path.read_text(encoding="utf-8"))
            adoption["vellum_authority_commit"] = "d" * 40
            write_json(adoption_path, adoption)
            subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(pulp), "commit", "-qm", "stale adoption"],
                check=True,
            )
            stale_adoption = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="vellum",
                paths=["graphics/src/skia_dawn_surface.mm"],
                intent="generic",
                counterpart_result="affected",
                operation="sdk-adoption",
                adoption_contract="docs/contracts/vellum-sdk-adoption.json",
            )
            self.assertEqual(stale_adoption["status"], "decision_required")
            self.assertIn("disagrees", stale_adoption["reasons"][0])

            invalid_owner = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="pulp",
                paths=["core/canvas/src/text_layout.cpp"],
                intent="emergency",
                emergency_event=EMERGENCY_EVENT,
                emergency_owner="@",
                emergency_created="2026-07-24",
                emergency_expiry="2026-07-25",
                emergency_follow_up="https://github.com/Generous-Corp/vellum/issues/1",
                now=dt.date(2026, 7, 24),
            )
            self.assertIn("valid @account", invalid_owner["reasons"][0])

    def test_transferred_slice_authority_must_match_activation(self) -> None:
        bad_authority = {
            "event_id": EVENT_ID,
            "vellum_commit": "d" * 40,
            "counterpart": RECORD_PATH,
            "accepted_by": "@routing-test",
            "accepted_at": "2026-07-24T12:03:00Z",
        }
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(
                Path(temporary),
                {"slice_authority": bad_authority},
            )
            with self.assertRaisesRegex(
                ROUTER.AuthorityError,
                "authority disagrees on vellum_commit",
            ):
                ROUTER.load_authority(vellum, pulp)

    def test_counterpart_map_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            mapping_path = (
                vellum / "provenance/pulp-observatory/legacy-path-map.yaml"
            )
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            mapping["mappings"][0]["contract_tests"] = None
            write_json(mapping_path, mapping)
            subprocess.run(["git", "-C", str(vellum), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(vellum), "commit", "-qm", "malformed map"],
                check=True,
            )
            with self.assertRaisesRegex(
                ROUTER.AuthorityError,
                "contract_tests must be a string array",
            ):
                ROUTER.load_authority(vellum, pulp)

    def test_exact_projection_rejects_duplicate_owners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            projection_path = pulp / ".github/vellum-ownership.json"
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["slices"].append(
                {
                    "id": "conflicting-owner",
                    "state": "pulp-only",
                    "paths": ["core/canvas/src/text_layout.cpp"],
                }
            )
            write_json(projection_path, projection)
            subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(pulp), "commit", "-qm", "duplicate owner"],
                check=True,
            )
            with self.assertRaisesRegex(ROUTER.AuthorityError, "multiple exact owners"):
                ROUTER.route(
                    ROUTER.load_authority(vellum, pulp),
                    source_repo="pulp",
                    paths=["core/canvas/src/text_layout.cpp"],
                    intent="generic",
                )

    def test_dirty_or_changed_authority_checkout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            authority = ROUTER.load_authority(vellum, pulp)
            projection = pulp / ".github/vellum-ownership.json"
            projection.write_text(
                projection.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ROUTER.AuthorityError,
                "Pulp checkout must be clean",
            ):
                ROUTER.route(
                    authority,
                    source_repo="pulp",
                    paths=["core/canvas/src/text_layout.cpp"],
                    intent="generic",
                )

    def test_transitive_metadata_follows_the_direct_owned_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            result = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="pulp",
                paths=[
                    "core/canvas/src/text_layout.cpp",
                    "core/generated.cmake",
                ],
                intent="generic",
            )
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["primary_repository"], "vellum")
            self.assertEqual(result["transitive_paths"], ["core/generated.cmake"])

    def test_generic_pulp_counterpart_must_be_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            result = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="pulp",
                paths=["core/view/src/view.cpp"],
                intent="generic",
            )
            self.assertEqual(result["status"], "decision_required")
            self.assertEqual(result["observatory_disposition"], "pending")
            self.assertIn("reproduced independently", result["reasons"][0])

    def test_activated_slice_cannot_be_downgraded_and_unmapped_cannot_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            audio_result = ROUTER.route(
                ROUTER.load_authority(vellum, pulp),
                source_repo="pulp",
                paths=["core/audio/src/processor.cpp"],
                intent="generic",
                counterpart_result="affected",
            )
            self.assertEqual(audio_result["status"], "decision_required")
            self.assertIn("no Vellum counterpart", audio_result["reasons"][0])

            projection_path = pulp / ".github/vellum-ownership.json"
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["slices"][0]["state"] = "pulp-only"
            projection["slices"][0]["authority"] = None
            write_json(projection_path, projection)
            subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(pulp), "commit", "-qm", "downgrade authority"],
                check=True,
            )
            with self.assertRaisesRegex(
                ROUTER.AuthorityError,
                "transferred slice set changed",
            ):
                ROUTER.load_authority(vellum, pulp)

        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            projection_path = pulp / ".github/vellum-ownership.json"
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            duplicate = dict(projection["slices"][0])
            duplicate["paths"] = ["core/canvas/src/injected.cpp"]
            projection["slices"].insert(0, duplicate)
            write_json(projection_path, projection)
            subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(pulp), "commit", "-qm", "duplicate slice id"],
                check=True,
            )
            with self.assertRaisesRegex(
                ROUTER.AuthorityError,
                "slice IDs must be present and unique",
            ):
                ROUTER.load_authority(vellum, pulp)

        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            projection_path = pulp / ".github/vellum-ownership.json"
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            projection["slices"][3]["state"] = "framework-authoritative-transferred"
            projection["slices"][3]["authority"] = dict(
                projection["slices"][0]["authority"]
            )
            write_json(projection_path, projection)
            subprocess.run(["git", "-C", str(pulp), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(pulp), "commit", "-qm", "promote authority"],
                check=True,
            )
            with self.assertRaisesRegex(
                ROUTER.AuthorityError,
                "transferred slice set changed",
            ):
                ROUTER.load_authority(vellum, pulp)

    def test_cli_exit_codes_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary))
            common = [
                "--vellum-repo", str(vellum),
                "--pulp-repo", str(pulp),
                "--source-repo", "pulp",
                "--path", "core/canvas/src/text_layout.cpp",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ROUTER.main([*common, "--intent", "generic"]), 0)
                self.assertEqual(ROUTER.main([*common, "--intent", "unknown"]), 2)

        with tempfile.TemporaryDirectory() as temporary:
            vellum, pulp = make_fixture(Path(temporary), {"pulp_activation_state": "prepared"})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    ROUTER.main(
                        [
                            "--vellum-repo", str(vellum),
                            "--pulp-repo", str(pulp),
                            "--source-repo", "pulp",
                            "--intent", "generic",
                            "--path", "core/canvas/src/text_layout.cpp",
                        ]
                    ),
                    3,
                )


if __name__ == "__main__":
    unittest.main()
