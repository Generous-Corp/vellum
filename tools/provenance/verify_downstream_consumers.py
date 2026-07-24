#!/usr/bin/env python3
"""Offline validation for the pinned downstream-consumer proof registry."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "provenance" / "downstream-consumers.v1.json"
SCHEMA = "vellum.downstream-consumer-validation.v1"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"v\d+\.\d+\.\d+\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
EVIDENCE_IDS = {
    "immutable-framework-install",
    "deterministic-import-reimport",
    "developer-authored-behavior",
    "native-gpu-runtime",
    "custom-native-component",
    "interaction-and-visual-testing",
    "browser-wasm",
    "packaging",
}
FIX_SEQUENCE = [
    "reproduce-in-consumer",
    "classify-framework-vs-application",
    "fix-and-test-framework",
    "release-immutable-framework-version",
    "update-consumer-pin",
    "rerun-evidence-ladder",
]
EXCEPTION_FIELDS = {
    "owner", "reason", "upstreamIssue", "expiry", "removalCondition"
}


class RegistryError(ValueError):
    pass


def exact_keys(value: Any, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{context} must be an object")
    actual = set(value)
    if actual != expected:
        raise RegistryError(
            f"{context} fields differ: missing={sorted(expected - actual)} "
            f"unknown={sorted(actual - expected)}"
        )
    return value


def immutable(value: Any, pattern: re.Pattern[str], context: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RegistryError(f"{context} must be an immutable full value")
    return value


def validate_registry(value: Any) -> None:
    root = exact_keys(value, {"schema", "framework", "consumers"}, "registry")
    if root["schema"] != SCHEMA:
        raise RegistryError(f"registry schema must be {SCHEMA}")

    framework = exact_keys(
        root["framework"],
        {"repository", "version", "sourceCommit", "artifact"},
        "framework",
    )
    framework_repo = immutable(
        framework["repository"], REPOSITORY, "framework.repository"
    )
    immutable(framework["version"], VERSION, "framework.version")
    immutable(framework["sourceCommit"], FULL_SHA, "framework.sourceCommit")
    artifact = exact_keys(
        framework["artifact"], {"target", "sha256"}, "framework.artifact"
    )
    if not isinstance(artifact["target"], str) or not artifact["target"]:
        raise RegistryError("framework.artifact.target must be non-empty")
    immutable(artifact["sha256"], SHA256, "framework.artifact.sha256")

    consumers = root["consumers"]
    if not isinstance(consumers, list) or not consumers:
        raise RegistryError("consumers must be a non-empty array")
    seen_ids: set[str] = set()
    seen_repositories: set[str] = set()
    for index, raw_consumer in enumerate(consumers):
        context = f"consumers[{index}]"
        consumer = exact_keys(
            raw_consumer,
            {
                "id",
                "repository",
                "commit",
                "validationRecord",
                "evidenceDigest",
                "evidenceLadder",
                "frameworkFirstFixProtocol",
            },
            context,
        )
        consumer_id = consumer["id"]
        if not isinstance(consumer_id, str) or not consumer_id:
            raise RegistryError(f"{context}.id must be non-empty")
        if consumer_id in seen_ids:
            raise RegistryError(f"duplicate consumer id: {consumer_id}")
        seen_ids.add(consumer_id)

        consumer_repo = immutable(
            consumer["repository"], REPOSITORY, f"{context}.repository"
        )
        if consumer_repo == framework_repo:
            raise RegistryError(f"{context} must be in a separate repository")
        if consumer_repo in seen_repositories:
            raise RegistryError(f"duplicate consumer repository: {consumer_repo}")
        seen_repositories.add(consumer_repo)
        immutable(consumer["commit"], FULL_SHA, f"{context}.commit")
        record = consumer["validationRecord"]
        if (
            not isinstance(record, str)
            or record.startswith("/")
            or ".." in Path(record).parts
            or not record.endswith(".json")
        ):
            raise RegistryError(
                f"{context}.validationRecord must be a relative JSON path"
            )
        digest = exact_keys(
            consumer["evidenceDigest"],
            {"kind", "sha256"},
            f"{context}.evidenceDigest",
        )
        if digest["kind"] != "montage-sha256":
            raise RegistryError(
                f"{context}.evidenceDigest.kind must be montage-sha256"
            )
        immutable(digest["sha256"], SHA256, f"{context}.evidenceDigest.sha256")
        validate_evidence(consumer["evidenceLadder"], context)
        validate_fix_protocol(
            consumer["frameworkFirstFixProtocol"],
            context,
            framework_repo,
            consumer_repo,
        )


def validate_evidence(value: Any, context: str) -> None:
    if not isinstance(value, list):
        raise RegistryError(f"{context}.evidenceLadder must be an array")
    actual: set[str] = set()
    for index, raw_item in enumerate(value):
        item_context = f"{context}.evidenceLadder[{index}]"
        item = exact_keys(raw_item, {"id", "expectation", "status"}, item_context)
        evidence_id = item["id"]
        if not isinstance(evidence_id, str) or not evidence_id:
            raise RegistryError(f"{item_context}.id must be non-empty")
        if evidence_id in actual:
            raise RegistryError(f"duplicate evidence id: {evidence_id}")
        actual.add(evidence_id)
        if not isinstance(item["expectation"], str) or not item["expectation"]:
            raise RegistryError(f"{item_context}.expectation must be non-empty")
        if item["status"] != "passed":
            raise RegistryError(f"{item_context}.status must be passed")
    if actual != EVIDENCE_IDS:
        raise RegistryError(
            f"{context}.evidenceLadder ids differ: "
            f"missing={sorted(EVIDENCE_IDS - actual)} "
            f"unknown={sorted(actual - EVIDENCE_IDS)}"
        )


def validate_fix_protocol(
    value: Any, context: str, framework_repo: str, consumer_repo: str
) -> None:
    protocol = exact_keys(
        value,
        {
            "mode",
            "frameworkRepository",
            "consumerRepository",
            "requiredSequence",
            "consumerWorkaroundPolicy",
            "exceptionRequiredFields",
            "exceptions",
        },
        f"{context}.frameworkFirstFixProtocol",
    )
    if protocol["mode"] != "framework-first":
        raise RegistryError(f"{context} fix mode must be framework-first")
    if protocol["frameworkRepository"] != framework_repo:
        raise RegistryError(f"{context} fix protocol framework repository differs")
    if protocol["consumerRepository"] != consumer_repo:
        raise RegistryError(f"{context} fix protocol consumer repository differs")
    if protocol["requiredSequence"] != FIX_SEQUENCE:
        raise RegistryError(f"{context} framework-first sequence differs")
    if (
        protocol["consumerWorkaroundPolicy"]
        != "prohibited-without-explicit-exception"
    ):
        raise RegistryError(f"{context} workaround policy differs")
    fields = protocol["exceptionRequiredFields"]
    if (
        not isinstance(fields, list)
        or len(fields) != len(EXCEPTION_FIELDS)
        or set(fields) != EXCEPTION_FIELDS
    ):
        raise RegistryError(f"{context} exception required fields differ")
    exceptions = protocol["exceptions"]
    if not isinstance(exceptions, list):
        raise RegistryError(f"{context}.exceptions must be an array")
    for index, exception in enumerate(exceptions):
        exact_keys(
            exception,
            EXCEPTION_FIELDS,
            f"{context}.frameworkFirstFixProtocol.exceptions[{index}]",
        )
        if not all(isinstance(item, str) and item for item in exception.values()):
            raise RegistryError(f"{context} exception fields must be non-empty")


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"cannot load {path}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args(argv)
    try:
        validate_registry(load(args.registry))
    except RegistryError as error:
        print(f"downstream-consumer-registry: FAIL: {error}", file=sys.stderr)
        return 1
    print(f"downstream-consumer-registry: OK ({args.registry})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
