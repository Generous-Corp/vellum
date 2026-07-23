#!/usr/bin/env python3
"""Offline contract checks for the Phase 3 authoring fixture.

This intentionally uses only the Python standard library so a clean checkout can
validate the protocol fixtures before any SDK prerequisites are installed.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIRECTORY = ROOT / "authoring" / "schema"
FIXTURE_DIRECTORY = ROOT / "fixtures" / "authoring-phase3"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


class SchemaFailure(AssertionError):
    pass


class OfflineSchemaValidator:
    """Small Draft 2020-12 subset covering Vellum's checked-in contracts."""

    def validate(self, instance: Any, schema_path: Path) -> None:
        schema = load_json(schema_path)
        self._validate(instance, schema, schema, schema_path, "$")

    def _resolve(
        self, reference: str, root_schema: dict[str, Any], schema_path: Path
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        if reference.startswith("#/"):
            target: Any = root_schema
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return target, root_schema, schema_path
        external_path = (schema_path.parent / reference).resolve()
        external = load_json(external_path)
        return external, external, external_path

    def _validate(
        self,
        instance: Any,
        schema: dict[str, Any],
        root_schema: dict[str, Any],
        schema_path: Path,
        location: str,
    ) -> None:
        if "$ref" in schema:
            target, target_root, target_path = self._resolve(
                schema["$ref"], root_schema, schema_path
            )
            self._validate(instance, target, target_root, target_path, location)
            return

        for subschema in schema.get("allOf", []):
            self._validate(instance, subschema, root_schema, schema_path, location)

        if "oneOf" in schema:
            matches = 0
            failures = []
            for subschema in schema["oneOf"]:
                try:
                    self._validate(instance, subschema, root_schema, schema_path, location)
                    matches += 1
                except SchemaFailure as error:
                    failures.append(str(error))
            if matches != 1:
                raise SchemaFailure(
                    f"{location}: expected exactly one oneOf match, got {matches}: "
                    + "; ".join(failures)
                )

        expected_type = schema.get("type")
        if expected_type is not None and not self._is_type(instance, expected_type):
            raise SchemaFailure(
                f"{location}: expected {expected_type}, got {type(instance).__name__}"
            )
        if "const" in schema and instance != schema["const"]:
            raise SchemaFailure(f"{location}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            raise SchemaFailure(f"{location}: {instance!r} is not in enum")

        if isinstance(instance, dict):
            required = schema.get("required", [])
            missing = [key for key in required if key not in instance]
            if missing:
                raise SchemaFailure(f"{location}: missing required keys {missing}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extras = sorted(set(instance) - set(properties))
                if extras:
                    raise SchemaFailure(f"{location}: unexpected keys {extras}")
            for key, value in instance.items():
                if key in properties:
                    self._validate(
                        value,
                        properties[key],
                        root_schema,
                        schema_path,
                        f"{location}.{key}",
                    )

        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                raise SchemaFailure(f"{location}: too few items")
            if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in instance}) != len(instance):
                raise SchemaFailure(f"{location}: items are not unique")
            if "items" in schema:
                for index, value in enumerate(instance):
                    self._validate(
                        value,
                        schema["items"],
                        root_schema,
                        schema_path,
                        f"{location}[{index}]",
                    )

        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                raise SchemaFailure(f"{location}: string is too short")
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                raise SchemaFailure(f"{location}: does not match {schema['pattern']!r}")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if instance < schema.get("minimum", instance):
                raise SchemaFailure(f"{location}: below minimum")

    @staticmethod
    def _is_type(value: Any, expected: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }[expected]


class Phase3ContractsTest(unittest.TestCase):
    def test_all_schemas_are_offline_json_and_versioned(self) -> None:
        expected = {
            "authoring-host-v2.schema.json": "authoring-host-v2.schema.json",
            "dev-transcript-v1.schema.json": "dev-transcript-v1.schema.json",
            "events-v2.schema.json": "events-v2.schema.json",
            "services-v1.schema.json": "services-v1.schema.json",
            "scenario-v2.schema.json": "scenario-v2.schema.json",
        }
        self.assertTrue(
            set(expected).issubset(
                {path.name for path in SCHEMA_DIRECTORY.glob("*.schema.json")}
            )
        )
        for filename, identifier_suffix in expected.items():
            schema = load_json(SCHEMA_DIRECTORY / filename)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            self.assertTrue(schema["$id"].endswith(identifier_suffix))

    def test_golden_envelopes_validate(self) -> None:
        manifest_path = FIXTURE_DIRECTORY / "golden" / "envelopes.json"
        manifest = load_json(manifest_path)
        self.assertEqual(
            manifest["schema"], "vellum.authoring-phase3-golden-envelopes.v1"
        )
        validator = OfflineSchemaValidator()
        for case in manifest["cases"]:
            with self.subTest(case=case["file"]):
                instance_path = (manifest_path.parent / case["file"]).resolve()
                schema_path = (manifest_path.parent / case["schema"]).resolve()
                self.assertTrue(instance_path.is_relative_to(FIXTURE_DIRECTORY))
                self.assertTrue(schema_path.is_relative_to(SCHEMA_DIRECTORY))
                validator.validate(load_json(instance_path), schema_path)

    def test_validator_rejects_a_mismatched_protocol(self) -> None:
        invalid = load_json(FIXTURE_DIRECTORY / "golden" / "composition-event.json")
        invalid["protocol"] = "vellum.events.v1"
        with self.assertRaises(SchemaFailure):
            OfflineSchemaValidator()._validate(
                invalid,
                load_json(SCHEMA_DIRECTORY / "events-v2.schema.json"),
                load_json(SCHEMA_DIRECTORY / "events-v2.schema.json"),
                SCHEMA_DIRECTORY / "events-v2.schema.json",
                "$",
            )

    def test_gate_tracks_runtime_evidence_without_overclaiming_native_parity(self) -> None:
        manifest = load_json(FIXTURE_DIRECTORY / "gate-manifest.json")
        self.assertFalse(manifest["contractOnly"])
        self.assertEqual(manifest["gate"]["status"], "pending")
        self.assertEqual(manifest["application"]["entry"], "src/App.tsx")
        self.assertEqual(
            manifest["application"]["runtimes"], ["native", "browser"]
        )
        self.assertTrue(
            manifest["application"]["mustRemainByteIdenticalAcrossRuntimes"]
        )
        self.assertGreaterEqual(len(manifest["requirements"]), 15)
        statuses = {
            requirement["id"]: requirement["status"]
            for requirement in manifest["requirements"]
        }
        self.assertEqual(
            {identifier for identifier, status in statuses.items() if status == "pending"},
            {
                "keyboard-pointer-touch",
                "capability-checked-commands",
                "capability-checked-files",
            },
        )
        for requirement in manifest["requirements"]:
            evidence = FIXTURE_DIRECTORY / requirement["contractEvidence"]
            self.assertTrue(evidence.exists(), requirement["id"])
            self.assertIn(requirement["status"], {"passed", "pending"})
            runtime_evidence = requirement.get("runtimeEvidence")
            self.assertIsInstance(runtime_evidence, list, requirement["id"])
            self.assertGreater(len(runtime_evidence), 0, requirement["id"])
            for relative in runtime_evidence:
                path = (FIXTURE_DIRECTORY / relative).resolve()
                self.assertTrue(path.is_relative_to(ROOT), requirement["id"])
                self.assertTrue(path.is_file(), f"{requirement['id']}: {path}")

        entry = FIXTURE_DIRECTORY / manifest["application"]["entry"]
        digest = hashlib.sha256(entry.read_bytes()).hexdigest()
        self.assertEqual(manifest["application"]["sourceSha256"], digest)

    def test_pure_esm_fixture_is_transitive_and_browser_neutral(self) -> None:
        root_package = load_json(
            FIXTURE_DIRECTORY / "vendor" / "pure-esm-root" / "package.json"
        )
        leaf_package = load_json(
            FIXTURE_DIRECTORY / "vendor" / "pure-esm-leaf" / "package.json"
        )
        self.assertEqual(root_package["type"], "module")
        self.assertEqual(leaf_package["type"], "module")
        self.assertIn(
            "@vellum/fixture-pure-esm-leaf", root_package["dependencies"]
        )
        source = (FIXTURE_DIRECTORY / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("@vellum/fixture-pure-esm-root", source)
        self.assertNotIn("window.", source)
        self.assertNotIn("document.", source)


if __name__ == "__main__":
    unittest.main()
