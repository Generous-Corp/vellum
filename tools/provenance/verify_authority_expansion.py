#!/usr/bin/env python3
"""Validate a non-authoritative later-expansion proposal."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


PROPOSAL_PATH = Path(
    "provenance/authority/expansions/full-design-import-render-v1/proposal.json"
)
EXPANSIONS_ROOT = Path("provenance/authority/expansions")
EXPECTED_FILES = {
    "README.md",
    "full-design-import-render-v1/proposal.json",
}
EXPECTED_PROPOSED_AT = "2026-08-10T21:55:54Z"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
OWNER = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
EXPECTED_COORDINATES = {
    "pulp_repository": "Generous-Corp/pulp",
    "pulp_baseline_commit": "190c463a0b320f28420c9af177244af04ef84233",
    "vellum_authority_repository": "Generous-Corp/vellum",
    "vellum_work_repository": "danielraffel/vellum",
    "vellum_work_repository_is_temporary": True,
    "vellum_baseline_commit": "e8c07675332b047c9b3a4f357aebb077cc52a8b6",
    "planning_repository": "danielraffel/pulp-planning",
    "planning_commit": "987b3e6c3aeac826480419b14746e4367846b03e",
}
EXPECTED_FAMILY_SCOPES = {
    "design-source-ingest": {
        "pulp_selectors": [
            "tools/import-design/**", "tools/cli/**/import_design*",
            "tools/cli/**/*design_import*", "tools/mcp/**/*design*",
            ".agents/skills/import-design/**",
        ],
        "vellum_target_roots": ["authoring/", "cli/", "packages/vellum-design-ir/"],
        "approved_retained_overlaps": ["pulp-public-cli-and-control-broker"],
    },
    "chromium-authoring-frontend": {
        "pulp_selectors": [
            "tools/import-design/**/*chrom*", "tools/import-design/**/*browser*",
            "tools/cli/**/*chrome*", "tools/cli/**/*browser*",
            "tools/scripts/**/*chrome*", "tools/scripts/**/*browser*",
        ],
        "vellum_target_roots": ["authoring/", "cli/", "scripts/"],
        "approved_retained_overlaps": ["pulp-public-cli-and-control-broker"],
    },
    "design-ir-contract": {
        "pulp_selectors": [
            "core/view/include/pulp/view/design_*.hpp", "core/view/src/design_*.cpp",
            "core/view/src/design_*.hpp", "packages/pulp-import-ir/**",
        ],
        "vellum_target_roots": [
            "foundation/", "components/", "fixtures/", "packages/vellum-design-ir/",
            "packages/vellum-ui/",
        ],
        "approved_retained_overlaps": [],
    },
    "render-assets-and-backends": {
        "pulp_selectors": [
            "core/canvas/**", "core/view/**/*render*", "core/view/**/*skia*",
            "core/view/**/*dawn*",
        ],
        "vellum_target_roots": [
            "components/", "foundation/", "graphics/", "runtime/", "web/",
        ],
        "approved_retained_overlaps": [],
    },
    "visual-proof-harness": {
        "pulp_selectors": [
            "core/view/include/pulp/view/screenshot*.hpp",
            "core/view/src/screenshot*.cpp", "core/view/platform/**/*capture*",
            "core/view/platform/**/*screenshot*", "tools/**/*screenshot*",
            "tools/**/*visual*", "tools/**/*golden*",
        ],
        "vellum_target_roots": [
            "apps/", "fixtures/", "foundation/", "graphics/", "runtime/", "tools/",
            "web/",
        ],
        "approved_retained_overlaps": ["pulp-public-cli-and-control-broker"],
    },
    "design-output-and-packaging": {
        "pulp_selectors": [
            "tools/import-design/**", "tools/cli/**/import_design*",
            "tools/mcp/**/*design*", "templates/**/*design*",
            "docs/**/*design-import*",
        ],
        "vellum_target_roots": [
            "cli/", "cmake/", "packages/", "runtime/", "scripts/", "templates/",
            "tools/", "web/",
        ],
        "approved_retained_overlaps": ["pulp-public-cli-and-control-broker"],
    },
}
EXPECTED_FAMILY_TITLES = {
    "design-source-ingest": (
        "Source detection, import, reimport, provider adapters, and staged inputs"
    ),
    "chromium-authoring-frontend": (
        "Managed Chromium, CDP capture, containment, and browser evidence"
    ),
    "design-ir-contract": (
        "Canonical DesignIR, assets, interactions, diagnostics, and normalization"
    ),
    "render-assets-and-backends": (
        "Image and vector assets, paint primitives, Skia Graphite/Dawn, and fallbacks"
    ),
    "visual-proof-harness": (
        "Generic capture, input scenarios, pixel comparison, audits, and goldens"
    ),
    "design-output-and-packaging": (
        "Live and baked outputs, validation, schemas, runtime assets, CLI, and packages"
    ),
}
EXPECTED_RETAINED_SCOPES = {
    "pulp-audio-and-dsp-harness": [
        "core/audio/**", "core/format/**", "core/gpu_audio/**", "core/graph/**",
        "core/host/**", "core/midi/**", "core/playback/**", "core/signal/**",
    ],
    "pulp-public-cli-and-control-broker": [
        "tools/cli/**", "tools/mcp/**", ".agents/skills/**", ".claude/**",
        ".claude-plugin/**",
    ],
    "pulp-product-integration": ["core/format/**", "core/host/**", "forge/**"],
    "pulp-runtime-engine-selection": ["core/js/**", "core/runtime/**"],
}
EXPECTED_RETAINED_RATIONALES = {
    "pulp-audio-and-dsp-harness": (
        "Audio measurement, offline audio rendering, telemetry, and plug-in or host "
        "acceptance remain Pulp product responsibilities."
    ),
    "pulp-public-cli-and-control-broker": (
        "Pulp remains the user-facing facade and owns grants, consent, live-instance "
        "routing, artifact ACLs, and product operations; later adapters may delegate "
        "generic visual work to Vellum."
    ),
    "pulp-product-integration": (
        "Plug-in, host, Forge, parameter-binding, and consumer adaptation stay outside "
        "the framework authority boundary."
    ),
    "pulp-runtime-engine-selection": (
        "Vellum APIs remain engine-neutral while Pulp retains product and platform "
        "selection of QuickJS, JavaScriptCore, or V8 adapters."
    ),
}
EXPECTED_MAINTENANCE_PATHS = [
    "core/view/include/pulp/view/screenshot.hpp",
    "core/view/include/pulp/view/screenshot_compare.hpp",
    "core/view/platform/mac/screenshot_mac.mm",
    "core/view/src/screenshot_compare.cpp",
    "core/view/src/screenshot_gpu.cpp",
    "core/view/src/screenshot_skia.cpp",
    "core/view/src/screenshot_stub.cpp",
]
EXPECTED_MAINTENANCE_RATIONALE = (
    "These paths are marked transferred in Pulp but the audited Vellum baseline "
    "lacks the complete capture and pixel-comparison implementation. The exception "
    "is proposed only; it has no effect until counterpart acceptance and "
    "acknowledgement."
)
EXPECTED_TRANSITIONS = [
    "vellum-proposal-merged", "pulp-watch-acceptance-merged",
    "vellum-watch-acknowledgement-merged", "compatibility-matrix-frozen",
    "exact-boundary-amendment-proposed", "exact-boundary-amendment-accepted",
    "exact-boundary-amendment-acknowledged",
]


class DuplicateKeyError(ValueError):
    pass


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
        raise ValueError(f"{path}: cannot load strict JSON: {exc}") from exc


def exact_keys(value: Any, expected: set[str], where: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{where}: expected object")
        return False
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{where}: keys differ; missing={sorted(expected - actual)} "
            f"unexpected={sorted(actual - expected)}"
        )
        return False
    return True


def strings(value: Any, where: str, errors: list[str], *, empty: bool = False) -> bool:
    if not isinstance(value, list) or (not value and not empty):
        errors.append(f"{where}: expected {'array' if empty else 'non-empty array'}")
        return False
    if not all(isinstance(item, str) and item for item in value):
        errors.append(f"{where}: expected non-empty strings")
        return False
    if len(value) != len(set(value)):
        errors.append(f"{where}: duplicate values")
        return False
    return True


def safe_path(value: Any, where: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{where}: expected string path")
        return
    parts = value.split("/")
    if value.startswith("/") or "\\" in value or any(p in {"", ".", ".."} for p in parts):
        errors.append(f"{where}: path is not safely repository-relative")


def exact_scalar(value: Any, expected: Any) -> bool:
    return type(value) is type(expected) and value == expected


def validate(data: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "proposal_id", "state", "proposed_at",
        "proposed_by", "coordinates", "scope_mode", "authority_effect",
        "implementation_authority", "capability_families", "retained_boundaries",
        "interim_maintenance", "required_transitions", "gates",
    }
    if not exact_keys(data, top, "proposal", errors):
        return errors
    scalars = {
        "schema_version": 1, "kind": "authority-expansion-proposal",
        "proposal_id": "full-design-import-render-v1", "state": "proposed",
        "proposed_by": "@danielraffel", "scope_mode": "watch-only-capability-families",
        "authority_effect": "none",
        "implementation_authority": "forbidden-until-exact-boundary-acknowledged",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"proposal.{key}: expected {expected!r}")
    if not OWNER.fullmatch(str(data["proposed_by"])):
        errors.append("proposal.proposed_by: expected individual GitHub handle")
    try:
        stamp = data["proposed_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("proposal.proposed_at: expected UTC timestamp ending in Z")
    if data["proposed_at"] != EXPECTED_PROPOSED_AT:
        errors.append("proposal.proposed_at: differs from pinned proposal timestamp")
    coordinates = data["coordinates"]
    if not isinstance(coordinates, dict):
        errors.append("proposal.coordinates: expected object")
    else:
        for key in ("pulp_baseline_commit", "vellum_baseline_commit", "planning_commit"):
            value = coordinates.get(key)
            if not isinstance(value, str) or not SHA40.fullmatch(value):
                errors.append(f"proposal.coordinates.{key}: expected full commit SHA")
    if (
        not isinstance(coordinates, dict)
        or set(coordinates) != set(EXPECTED_COORDINATES)
        or any(
            not exact_scalar(coordinates[key], expected)
            for key, expected in EXPECTED_COORDINATES.items()
        )
    ):
        errors.append("proposal.coordinates: differs from pinned exact coordinates")

    family_keys = {
        "id", "title", "pulp_selectors", "vellum_target_roots",
        "approved_retained_overlaps",
    }
    families = data["capability_families"]
    seen: set[str] = set()
    if not isinstance(families, list):
        errors.append("proposal.capability_families: expected array")
    else:
        for index, family in enumerate(families):
            where = f"proposal.capability_families[{index}]"
            if not exact_keys(family, family_keys, where, errors):
                continue
            family_id = family["id"]
            if not isinstance(family_id, str) or family_id in seen:
                errors.append(f"{where}.id: missing or duplicate")
                continue
            seen.add(family_id)
            expected = EXPECTED_FAMILY_SCOPES.get(family_id)
            if expected is None:
                errors.append(f"{where}.id: unknown family")
                continue
            if family["title"] != EXPECTED_FAMILY_TITLES[family_id]:
                errors.append(f"{where}.title: differs from pinned title")
            for key, pinned in expected.items():
                if family[key] != pinned:
                    errors.append(f"{where}.{key}: differs from pinned scope")
            for selector in family["pulp_selectors"] if isinstance(family["pulp_selectors"], list) else []:
                safe_path(selector, f"{where}.pulp_selectors", errors)
            strings(family["vellum_target_roots"], f"{where}.vellum_target_roots", errors)
            strings(family["approved_retained_overlaps"], f"{where}.approved_retained_overlaps", errors, empty=True)
        if seen != set(EXPECTED_FAMILY_SCOPES):
            errors.append("proposal.capability_families: IDs differ from pinned families")

    retained = data["retained_boundaries"]
    retained_seen: set[str] = set()
    if not isinstance(retained, list):
        errors.append("proposal.retained_boundaries: expected array")
    else:
        for index, row in enumerate(retained):
            where = f"proposal.retained_boundaries[{index}]"
            if not exact_keys(row, {"id", "pulp_selectors", "rationale"}, where, errors):
                continue
            row_id = row["id"]
            if not isinstance(row_id, str) or row_id in retained_seen:
                errors.append(f"{where}.id: missing or duplicate")
                continue
            retained_seen.add(row_id)
            if row.get("pulp_selectors") != EXPECTED_RETAINED_SCOPES.get(row_id):
                errors.append(f"{where}.pulp_selectors: differs from pinned retained scope")
            if row.get("rationale") != EXPECTED_RETAINED_RATIONALES.get(row_id):
                errors.append(f"{where}.rationale: differs from pinned retained rationale")
        if retained_seen != set(EXPECTED_RETAINED_SCOPES):
            errors.append("proposal.retained_boundaries: IDs differ from pinned seams")

    maintenance = data["interim_maintenance"]
    if not isinstance(maintenance, list) or len(maintenance) != 1 or not isinstance(maintenance[0], dict):
        errors.append("proposal.interim_maintenance: expected one audited exception")
    else:
        row = maintenance[0]
        where = "proposal.interim_maintenance[0]"
        keys = {"id", "pulp_paths", "expires_at_gate", "rationale"}
        if exact_keys(row, keys, where, errors):
            if row["id"] != "capture-primitives-unimplemented-in-vellum":
                errors.append(f"{where}.id: unexpected exception")
            if row["expires_at_gate"] != "5A-P.3-independent-pixel-and-semantic-proof":
                errors.append(f"{where}.expires_at_gate: unexpected expiry")
            if row["rationale"] != EXPECTED_MAINTENANCE_RATIONALE:
                errors.append(f"{where}.rationale: differs from pinned rationale")
            paths = row["pulp_paths"]
            if paths != EXPECTED_MAINTENANCE_PATHS:
                errors.append(f"{where}.pulp_paths: differs from audited paths")
            for path in paths if isinstance(paths, list) else []:
                safe_path(path, f"{where}.pulp_paths", errors)
    if data["required_transitions"] != EXPECTED_TRANSITIONS:
        errors.append("proposal.required_transitions: exact ordered state machine required")
    expected_gates = {
        "proposal_may_transfer_authority": False,
        "watch_acceptance_may_transfer_authority": False,
        "watch_acknowledgement_may_transfer_authority": False,
        "source_work_before_exact_boundary_acknowledgement": False,
        "exact_paths_required_for_authority": True,
        "counterpart_commit_and_digest_required": True,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(not exact_scalar(gates[key], expected) for key, expected in expected_gates.items())
    ):
        errors.append("proposal.gates: differs from fail-closed pinned gates")
    return errors


def verify(root: Path) -> dict[str, Any]:
    actual_files = {
        path.relative_to(root / EXPANSIONS_ROOT).as_posix()
        for path in (root / EXPANSIONS_ROOT).rglob("*")
        if path.is_file()
    }
    closure_errors = []
    if actual_files != EXPECTED_FILES:
        closure_errors.append(
            "expansion artifact set differs; "
            f"missing={sorted(EXPECTED_FILES - actual_files)} "
            f"unexpected={sorted(actual_files - EXPECTED_FILES)}"
        )
    try:
        data = load_json(root / PROPOSAL_PATH)
        errors = closure_errors + validate(data)
    except ValueError as exc:
        data, errors = None, closure_errors + [str(exc)]
    return {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "proposal": PROPOSAL_PATH.as_posix(),
        "proposal_id": data.get("proposal_id") if isinstance(data, dict) else None,
        "authority_effect": data.get("authority_effect") if isinstance(data, dict) else None,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.root.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
