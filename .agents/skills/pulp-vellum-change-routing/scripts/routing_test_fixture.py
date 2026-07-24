#!/usr/bin/env python3
"""Deterministic tests for the Pulp/Vellum change router."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


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
    emergency_event = {
        "schema_version": 1,
        "event_id": "20260724-routing-emergency",
        "kind": "change",
        "created_at": "2026-07-24T12:03:00Z",
        "slices": ["canvas-kernel"],
        "rationale": "Bounded routing test emergency.",
        "tests": ["vellum.gpu-native", "vellum.install-consumer"],
        "disposition": "emergency-exception",
        "owner": "@owner",
        "expiry": "2026-08-01",
        "follow_up": "https://github.com/Generous-Corp/vellum/issues/1",
    }
    emergency_event.update(mutation.get("emergency_event", {}))
    write_json(pulp / EMERGENCY_EVENT, emergency_event)
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

