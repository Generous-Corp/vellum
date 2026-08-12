#!/usr/bin/env python3
"""Validate a non-authoritative later-expansion proposal."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


PROPOSAL_PATH = Path(
    "provenance/authority/expansions/full-design-import-render-v1/proposal.json"
)
ADDENDUM_PATH = Path(
    "provenance/authority/expansions/"
    "full-design-import-render-v1/scope-addendum-1.json"
)
ACKNOWLEDGEMENT_PATH = Path(
    "provenance/authority/expansions/"
    "full-design-import-render-v1/watch-acknowledgement.json"
)
MATRIX_PATH = Path(
    "provenance/authority/expansions/"
    "full-design-import-render-v1/compatibility-matrix.json"
)
EXPANSIONS_ROOT = Path("provenance/authority/expansions")
EXPECTED_FILES = {
    "README.md",
    "full-design-import-render-v1/compatibility-matrix.json",
    "full-design-import-render-v1/proposal.json",
    "full-design-import-render-v1/scope-addendum-1.json",
    "full-design-import-render-v1/watch-acknowledgement.json",
}
EXPECTED_README_SHA256 = "6935071cee4c735f401356e625108150251921b8a0dd6f20648d7370fd388894"
EXPECTED_PROPOSAL_SHA256 = "7c2db05e110e7d9834806e08469f6d7cc70f6528b2242d1f6c0255dcdbc0a4c9"
EXPECTED_ADDENDUM_SHA256 = "91bb269ce5a872037fd67e4735125772bcac50d82cf454edfd8c356b11f5a122"
EXPECTED_ACKNOWLEDGEMENT_SHA256 = "877ac4a410e7ac8d5019aa8f2e09d9133a111acaab59eb2abef65c30527b78c8"
EXPECTED_MATRIX_SHA256 = "1792666eb1dd7d3f46dc607f4ee3dccbbc1232a6c2e6ab2331507c4b87122e1c"
EXPECTED_PROPOSED_AT = "2026-08-10T21:55:54Z"
EXPECTED_ADDENDUM_PROPOSED_AT = "2026-08-11T02:52:16Z"
EXPECTED_ACKNOWLEDGED_AT = "2026-08-11T20:54:33Z"
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
            "core/view/include/pulp/view/design_*.hpp",
            "core/view/include/pulp/view/anchor_strategy.hpp",
            "core/view/src/design_*.cpp", "core/view/src/design_*.hpp",
            "core/view/src/anchor_strategy.cpp", "packages/pulp-import-ir/**",
        ],
        "vellum_target_roots": [
            "foundation/", "components/", "fixtures/", "packages/vellum-design-ir/",
            "packages/vellum-ui/",
        ],
        "approved_retained_overlaps": [],
    },
    "render-assets-and-backends": {
        "pulp_selectors": [
            "core/canvas/**", "core/render/**", "core/view/**/*render*",
            "core/view/**/*skia*", "core/view/**/*dawn*",
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
EXPECTED_ADDED_SELECTORS = {
    "design-source-ingest": [
        ".claude/commands/design.md", ".claude/commands/import-design.md",
        "compat/imports.json", "compat/rn.json", "design/**",
        "experimental/pulp-rs/src/cmd/design.rs",
        "experimental/pulp-rs/src/cmd/mod.rs", "experimental/pulp-rs/src/main.rs",
        "tools/figma-plugin/**", "tools/figma-import/**",
        "tools/cli/cmd_design*", "tools/cli/cmd_import*",
        "tools/cli/design_binding*", "tools/cli/import_*",
        "tools/cli/importer_*", "tools/design/**", "tools/design-ab/**",
        "examples/design-tool/**", "examples/design*/**",
        "test/cmake/cli_import_tool_tests.cmake",
        "test/cmake/design_import*.cmake", "test/fixtures/imports/**",
        "test/fixtures/figma/**", "test/fixtures/pencil/**",
        "test/fixtures/rn/**", "test/fixtures/stitch/**",
        "test/fixtures/v0-dev/**", "test/test_cli_import*", "test/test_cli_design*",
        "test/test_design_*", "test/test_design_import*", "test/test_figma_*",
        "test/test_import*",
        "docs/guides/design-*", "docs/guides/figma-plugin.md",
        "docs/guides/importing-designs.md", "docs/reference/compat/imports.md",
        "docs/reference/compat/rn.md",
        "docs/reference/design-*", "docs/reference/design-import*",
        "docs/reference/design-ir*",
        "docs/reference/imports/**",
    ],
    "chromium-authoring-frontend": [
        "test/fixtures/agent-panels/**", "test/fixtures/browser-capture-*/**",
        "test/fixtures/browser_capture_*", "test/fixtures/import-differential/**",
        "test/test_browser_capture*", "test/test_browser_import*",
        "test/test_design_browser_capture.cpp", "test/test_html_intake.cpp",
        "test/test_html_project_stager.cpp",
        "experimental/pulp-rs/src/cmd/chrome_for_testing.rs",
        "experimental/pulp-rs/src/install_import_design.rs",
        "experimental/pulp-rs/tests/chrome_for_testing_tool_test.rs",
        "tools/packages/chrome-for-testing-verification.md",
    ],
    "design-ir-contract": [
        "core/view/include/pulp/view/jsx_lock.hpp",
        "core/view/include/pulp/view/lock_to_source.hpp",
        "core/view/include/pulp/view/recognition_resolver.hpp",
        "core/view/include/pulp/view/token_lock.hpp", "core/view/src/claude_bundle*",
        "core/view/src/jsx_lock.cpp", "core/view/src/lock_to_source.cpp",
        "core/view/src/recognition_resolver.cpp", "core/view/src/token_lock.cpp",
        "core/view/src/widget_bridge/runtime_import_api*",
        "test/fixtures/design_import_*", "test/test_design_ir*",
        "test/test_jsx_lock.cpp", "test/test_lock_to_source.cpp",
        "test/test_recognition_resolver.cpp", "test/test_token_lock.cpp",
        "test/test_widget_bridge_runtime_import.cpp",
    ],
    "render-assets-and-backends": [
        ".agents/skills/skia-gpu-build/**",
        ".github/workflows/non-skia-build-guard.yml", "assets/design-system/**",
        "core/view/**", "packages/pulp-react/**", "test/cmake/*skia*",
        "test/test_skia*", "tools/build-skia*", "tools/cmake/FindSkia.cmake",
        "tools/scripts/*skia*",
    ],
    "visual-proof-harness": [
        "tools/import-validation/**", "tools/harness/**",
        "tools/scripts/figma_import_diff.py",
        "tools/scripts/render-figma-import.sh", "tools/local-ci/*capture*",
        "tools/local-ci/*design*", "test/harness/**",
        "test/visual/**", "test/fixtures/import-fidelity/**",
        "test/cmake/view_widget_bridge_tests.cmake",
        "test/fixtures/fake_screenshot_tool.cpp", "test/test_*screenshot*",
        "test/test_screenshot*", "test/web-compat/test_screenshot*",
        "examples/ui-preview/**",
        ".github/workflows/visual-harness.yml", "ci/visual-harness.Dockerfile",
        "compat.json", "external/fonts/**", "external/skia-build/**",
        "docs/examples/screenshots.md", "docs/reports/harness-coverage.md",
    ],
    "design-output-and-packaging": [
        "tools/import-validation/**", "tools/templates/from-figma/**",
        "tools/templates/from-v0/**", "templates/swiftui-design-host/**",
        "tools/scripts/check_import_provenance.py",
        "tools/scripts/design_import_benchmark.py",
        "tools/scripts/test_design_import_benchmark.py",
        "tools/scripts/package_cli.py", "tools/scripts/test_package_cli.py",
        "tools/packages/test_design_controls.py", "tools/rack/export_design_data.py",
        "experimental/pulp-rs/src/install_import_design.rs",
        ".github/workflows/release-cli.yml", ".github/workflows/release-dry-run.yml",
        "test/cmake/test_installed_sdk_runtime_staging.cmake",
        "docs/status/cli-commands.yaml", "docs/status/tools.yaml",
        "docs/tools/importer-differential-lab.md",
    ],
}
EXPECTED_ADDENDUM_RATIONALES = {
    "design-source-ingest": (
        "Close the compatibility catalog, C++ and Rust command entry points, "
        "command facades, prototypes, providers, facade helpers, fixtures, examples, "
        "tests, and documentation used by Figma file, REST, plugin, Stitch, v0, "
        "Pencil, React Native, JSX, DESIGN.md, and Claude Design import routes."
    ),
    "chromium-authoring-frontend": (
        "Close the agent-HTML corpus, CDP evidence fixtures, native differential "
        "corpus, managed-browser installation, and HTML intake tests that prove "
        "the Chromium to DesignIR path."
    ),
    "design-ir-contract": (
        "Close non-design-named lock, recognition, Claude bundle, runtime import, "
        "and direct DesignIR evidence paths that participate in normalization and "
        "generated output contracts."
    ),
    "render-assets-and-backends": (
        "Observe the full generic view and React compatibility surface until Phase "
        "5A-P.1 freezes exact rendering ownership; the broad selector is temporary, "
        "watch-only, and does not absorb retained Pulp product integration."
    ),
    "visual-proof-harness": (
        "Close generic screenshot, click and interaction, semantic snapshot, pixel "
        "diff, corpus, deterministic font and Skia pin, workflow, and coverage-report "
        "evidence."
    ),
    "design-output-and-packaging": (
        "Close validation schemas and tools, generated project templates, installed "
        "browser runtime staging, CLI archive packaging, benchmark, registry, and "
        "release proof surfaces."
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


def expansion_files(root: Path) -> tuple[set[str], set[str]]:
    base = root / EXPANSIONS_ROOT
    symlinks: set[str] = set()
    filesystem_files: set[str] = set()
    for path in base.rglob("*"):
        relative = path.relative_to(base).as_posix()
        if path.is_symlink():
            filesystem_files.add(relative)
            symlinks.add(relative)
        elif path.is_file():
            filesystem_files.add(relative)
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", EXPANSIONS_ROOT.as_posix()],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return filesystem_files, symlinks
    if completed.returncode != 0:
        return filesystem_files, symlinks
    prefix = EXPANSIONS_ROOT.as_posix() + "/"
    files = {
        item.decode("utf-8", errors="surrogateescape").removeprefix(prefix)
        for item in completed.stdout.split(b"\0")
        if item
    }
    incidental_names = {".DS_Store", ".gitkeep", "Thumbs.db"}
    incidental_suffixes = {".swp", ".swo"}
    files.update(
        relative
        for relative in filesystem_files - files
        if Path(relative).name not in incidental_names
        and Path(relative).suffix.lower() not in incidental_suffixes
        and not Path(relative).name.endswith("~")
    )
    files.update(symlinks)
    return files, symlinks


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


def validate_addendum(data: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "addendum_id", "proposal_id", "state",
        "proposed_at", "proposed_by", "coordinates", "scope_mode",
        "authority_effect", "implementation_authority", "audit",
        "added_capability_family_selectors", "gates",
    }
    if not exact_keys(data, top, "addendum", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "authority-expansion-watch-scope-addendum",
        "addendum_id": "full-design-import-render-v1-scope-addendum-1",
        "proposal_id": "full-design-import-render-v1",
        "state": "proposed",
        "proposed_at": EXPECTED_ADDENDUM_PROPOSED_AT,
        "proposed_by": "@danielraffel",
        "scope_mode": "additive-watch-only-capability-family-selectors",
        "authority_effect": "none",
        "implementation_authority": "forbidden-until-exact-boundary-acknowledged",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"addendum.{key}: expected {expected!r}")
    try:
        stamp = data["proposed_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("addendum.proposed_at: expected pinned UTC timestamp ending in Z")

    expected_coordinates = {
        "pulp_repository": "Generous-Corp/pulp",
        "pulp_audit_commit": "190c463a0b320f28420c9af177244af04ef84233",
        "vellum_work_repository": "danielraffel/vellum",
        "vellum_proposal_merge_commit": "bf0559eca2547f9242405cc388888890e80bbd87",
        "proposal_path": PROPOSAL_PATH.as_posix(),
        "proposal_sha256": EXPECTED_PROPOSAL_SHA256,
    }
    coordinates = data["coordinates"]
    if (
        not isinstance(coordinates, dict)
        or set(coordinates) != set(expected_coordinates)
        or any(
            not exact_scalar(coordinates[key], expected)
            for key, expected in expected_coordinates.items()
        )
    ):
        errors.append("addendum.coordinates: differs from pinned proposal and audit coordinates")
    elif not SHA40.fullmatch(coordinates["pulp_audit_commit"]) or not SHA40.fullmatch(
        coordinates["vellum_proposal_merge_commit"]
    ):
        errors.append("addendum.coordinates: expected full commit SHAs")

    expected_audit = {
        "method": (
            "tracked-file inventory, build-manifest tracing, tool-registry closure, "
            "Chromium-introduction history, and adversarial negative controls"
        ),
        "finding": (
            "The original proposal omitted capability-bearing import compatibility, "
            "import validation, generic visual harness, Figma plugin, Chromium "
            "evidence, C++ and Rust CLI helpers, fixture, packaging, and "
            "non-design-named rendering paths."
        ),
        "coverage_policy": (
            "Selectors are deliberately conservative during the temporary watch-only "
            "phase; overlap records change evidence and does not assign implementation "
            "ownership."
        ),
        "retained_boundaries_unchanged": True,
    }
    audit = data["audit"]
    if (
        not isinstance(audit, dict)
        or set(audit) != set(expected_audit)
        or any(
            not exact_scalar(audit[key], expected)
            for key, expected in expected_audit.items()
        )
    ):
        errors.append("addendum.audit: differs from pinned omission audit")

    rows = data["added_capability_family_selectors"]
    seen: set[str] = set()
    if not isinstance(rows, list):
        errors.append("addendum.added_capability_family_selectors: expected array")
    else:
        for index, row in enumerate(rows):
            where = f"addendum.added_capability_family_selectors[{index}]"
            if not exact_keys(row, {"id", "pulp_selectors", "rationale"}, where, errors):
                continue
            family = row["id"]
            if not isinstance(family, str) or family in seen:
                errors.append(f"{where}.id: missing or duplicate")
                continue
            seen.add(family)
            if row["pulp_selectors"] != EXPECTED_ADDED_SELECTORS.get(family):
                errors.append(f"{where}.pulp_selectors: differs from pinned addendum")
            if row["rationale"] != EXPECTED_ADDENDUM_RATIONALES.get(family):
                errors.append(f"{where}.rationale: differs from pinned rationale")
            selectors = row["pulp_selectors"]
            if strings(selectors, f"{where}.pulp_selectors", errors):
                for selector in selectors:
                    safe_path(selector, f"{where}.pulp_selectors", errors)
        if seen != set(EXPECTED_ADDED_SELECTORS):
            errors.append(
                "addendum.added_capability_family_selectors: IDs differ from proposal families"
            )

    expected_gates = {
        "addendum_may_transfer_authority": False,
        "source_work_before_exact_boundary_acknowledgement": False,
        "pulp_acceptance_must_bind_proposal_and_addendum": True,
        "exact_paths_required_for_authority": True,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(
            not exact_scalar(gates[key], expected)
            for key, expected in expected_gates.items()
        )
    ):
        errors.append("addendum.gates: differs from fail-closed pinned gates")
    return errors


def validate_acknowledgement(data: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "acknowledgement_id", "proposal_id", "state",
        "acknowledged_at", "acknowledged_by", "coordinates", "scope_mode",
        "authority_effect", "implementation_authority", "gates",
    }
    if not exact_keys(data, top, "acknowledgement", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "authority-expansion-watch-acknowledgement",
        "acknowledgement_id": "full-design-import-render-v1-watch-acknowledgement",
        "proposal_id": "full-design-import-render-v1",
        "state": "acknowledged",
        "acknowledged_by": "@danielraffel",
        "scope_mode": "watch-only-capability-families",
        "authority_effect": "none",
        "implementation_authority": "forbidden-until-exact-boundary-acknowledged",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"acknowledgement.{key}: expected {expected!r}")
    if not OWNER.fullmatch(str(data["acknowledged_by"])):
        errors.append("acknowledgement.acknowledged_by: expected individual GitHub handle")
    try:
        stamp = data["acknowledged_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("acknowledgement.acknowledged_at: expected UTC timestamp ending in Z")
    if data["acknowledged_at"] != EXPECTED_ACKNOWLEDGED_AT:
        errors.append(
            "acknowledgement.acknowledged_at: differs from pinned acknowledgement timestamp"
        )

    expected_coordinates = {
        "pulp_repository": "Generous-Corp/pulp",
        "pulp_acceptance_merge_commit": "a494429f4cf29a2d45c45fce12debfa0417ced21",
        "pulp_acceptance_path": (
            ".github/vellum-expansion-watch/full-design-import-render-v1/acceptance.json"
        ),
        "pulp_acceptance_sha256": (
            "1535d76e34dfc80eda55247c1b0d47b9f47b3e58a08b6f1ab4b749692e6056fd"
        ),
        "vellum_work_repository": "danielraffel/vellum",
        "vellum_proposal_merge_commit": "bf0559eca2547f9242405cc388888890e80bbd87",
        "proposal_path": PROPOSAL_PATH.as_posix(),
        "proposal_sha256": EXPECTED_PROPOSAL_SHA256,
        "vellum_scope_addendum_merge_commit": (
            "8608fac0922577f9f6e87f51b3e6b9eed395d243"
        ),
        "scope_addendum_path": ADDENDUM_PATH.as_posix(),
        "scope_addendum_sha256": EXPECTED_ADDENDUM_SHA256,
    }
    coordinates = data["coordinates"]
    if (
        not isinstance(coordinates, dict)
        or set(coordinates) != set(expected_coordinates)
        or any(
            not exact_scalar(coordinates[key], expected)
            for key, expected in expected_coordinates.items()
        )
    ):
        errors.append("acknowledgement.coordinates: differs from pinned handshake coordinates")
    elif not all(
        SHA40.fullmatch(coordinates[key])
        for key in (
            "pulp_acceptance_merge_commit",
            "vellum_proposal_merge_commit",
            "vellum_scope_addendum_merge_commit",
        )
    ):
        errors.append("acknowledgement.coordinates: expected full commit SHAs")

    expected_gates = {
        "watch_acknowledgement_may_transfer_authority": False,
        "source_work_before_exact_boundary_acknowledgement": False,
        "exact_paths_required_for_authority": True,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(
            not exact_scalar(gates[key], expected)
            for key, expected in expected_gates.items()
        )
    ):
        errors.append("acknowledgement.gates: differs from fail-closed pinned gates")
    return errors


def validate_matrix(data: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "matrix_id", "proposal_id", "state",
        "frozen_at", "frozen_by", "authority_effect", "implementation_authority",
        "coordinates", "status_values", "status_semantics",
        "path_semantics", "design_ir_relationship", "cells", "gates",
    }
    if not exact_keys(data, top, "matrix", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "full-design-import-render-compatibility-matrix",
        "matrix_id": "full-design-import-render-v1-compatibility-matrix",
        "proposal_id": "full-design-import-render-v1",
        "state": "frozen",
        "frozen_at": "2026-08-11T23:59:00Z",
        "frozen_by": "@danielraffel",
        "authority_effect": "none",
        "implementation_authority": "forbidden-until-exact-boundary-acknowledged",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"matrix.{key}: expected {expected!r}")
    if not OWNER.fullmatch(str(data["frozen_by"])):
        errors.append("matrix.frozen_by: expected individual GitHub handle")

    expected_coordinates = {
        "pulp_repository": "Generous-Corp/pulp",
        "pulp_audit_commit": "34f879e1a71aec8a34cea13f62600586d0eb79a7",
        "vellum_authority_repository": "Generous-Corp/vellum",
        "vellum_work_repository": "danielraffel/vellum",
        "vellum_audit_commit": "da76a684e450b56b10ca26eb4057d58290fea1c2",
        "planning_repository": "danielraffel/pulp-planning",
        "planning_commit": "0aae3f40add2f5f7705eed8909e32d1134fd432c",
    }
    coordinates = data["coordinates"]
    if (
        not isinstance(coordinates, dict)
        or set(coordinates) != set(expected_coordinates)
        or any(
            not exact_scalar(coordinates[key], expected)
            for key, expected in expected_coordinates.items()
        )
    ):
        errors.append("matrix.coordinates: differs from pinned audit coordinates")
    elif not all(
        SHA40.fullmatch(coordinates[key])
        for key in ("pulp_audit_commit", "vellum_audit_commit", "planning_commit")
    ):
        errors.append("matrix.coordinates: expected full commit SHAs")

    statuses = ["supported", "partial", "rejected-by-contract", "not-applicable"]
    if data["status_values"] != statuses:
        errors.append("matrix.status_values: differs from closed status vocabulary")
    expected_semantics = {
        "supported": (
            "The pinned Vellum baseline independently satisfies the pinned Pulp "
            "behavior named by the row."
        ),
        "partial": (
            "A shared sink or bounded subset exists, but the pinned Vellum baseline "
            "does not independently satisfy the pinned Pulp behavior named by the row."
        ),
        "rejected-by-contract": (
            "The capability is intentionally outside Vellum and must remain owned by "
            "the named retained boundary."
        ),
        "not-applicable": (
            "The capability is a consumer integration choice, not a Vellum framework "
            "implementation obligation."
        ),
    }
    if data["status_semantics"] != expected_semantics:
        errors.append("matrix.status_semantics: differs from pinned semantics")
    expected_path_semantics = {
        "pulp_implementation_and_proof": (
            "Exact evidence paths at the pinned Pulp audit commit; the Pulp "
            "exact-boundary acceptance must verify every path against that commit."
        ),
        "vellum_future_implementation_and_proof": (
            "Exact future ownership destinations. They may be absent for partial rows; "
            "every path in a supported row must exist at the pinned Vellum audit commit."
        ),
    }
    if data["path_semantics"] != expected_path_semantics:
        errors.append("matrix.path_semantics: differs from pinned evidence roles")

    expected_relationship = {
        "canonical_owner": "Generous-Corp/vellum",
        "canonical_schema": "https://vellum.dev/schemas/design-ir/v1",
        "canonical_schema_version": 1,
        "pulp_input_contract": (
            "Pulp DesignIR integer version 1 at "
            "34f879e1a71aec8a34cea13f62600586d0eb79a7"
        ),
        "relationship": "versioned-compatibility-adapter-not-schema-identity",
        "adapter_direction": (
            "Pulp source adapters and legacy Pulp DesignIR to canonical Vellum DesignIR v1"
        ),
        "compatibility_rule": (
            "Vellum owns the canonical schema. Pulp wire fields are accepted through an "
            "explicit, version-pinned compatibility adapter; unknown or lossy fields produce "
            "deterministic diagnostics and may not be silently discarded."
        ),
        "versioning_rule": (
            "A change to either schema or semantic mapping requires a new adapter version, "
            "fixtures for old and new inputs, and reimport stability proof. Pulp may not "
            "redefine the canonical Vellum schema through its facade."
        ),
        "runtime_rule": (
            "Canonical DesignIR and packaged render assets must execute without Chromium; "
            "Chromium is an authoring-only frontend."
        ),
    }
    if data["design_ir_relationship"] != expected_relationship:
        errors.append("matrix.design_ir_relationship: differs from pinned version relationship")

    required_ids = {
        "source.canonical-design-ir", "source.pulp-design-ir-v1-compat",
        "source.detection-and-routing",
        "source.figma-plugin-json", "source.figma-plugin-zip",
        "source.figma-local-fig", "source.figma-rest", "source.figma-mcp-context",
        "source.stitch", "source.v0", "source.pencil", "source.claude-design",
        "source.agent-html-chromium", "source.html-project-staging-containment",
        "source.managed-chromium-install-discovery", "source.design-md", "source.jsx-react",
        "source.react-native", "ir.deterministic-normalize-diagnostics",
        "ir.reimport-overlays-stable-identity", "render.layout-and-node-dispatch",
        "ir.lock-to-source-generated-and-jsx", "ir.token-lock-to-design-md",
        "ir.recognition-resolver", "ir.runtime-import-api",
        "render.paint-effects-and-clipping", "render.raster-images-and-assets",
        "render.vector-svg-paths", "render.text-runs-fonts-fallback",
        "render.skia-cpu", "render.skia-graphite-dawn-macos15-arm64",
        "render.interactions-semantics-accessibility", "harness.capture-native-and-web",
        "harness.input-scenarios", "harness.semantic-snapshots-assertions",
        "harness.pixel-diff-goldens-montage", "harness.source-vs-skia-fidelity",
        "output.live-js", "output.canonical-design-ir", "output.baked-cpp",
        "output.baked-swiftui", "output.cli-and-immutable-runtime-assets",
        "boundary.runtime-engine-selection", "boundary.non-macos-arm64-platform-adoption",
        "boundary.audio-dsp-harness",
        "boundary.pulp-cli-control-product-integration",
        "boundary.pulp-forge-product-integration",
    }
    families = {
        "design-source-ingest", "chromium-authoring-frontend", "design-ir-contract",
        "render-assets-and-backends", "visual-proof-harness",
        "design-output-and-packaging", "retained-boundary",
    }
    cells = data["cells"]
    seen: set[str] = set()
    if not isinstance(cells, list):
        errors.append("matrix.cells: expected array")
    else:
        for index, cell in enumerate(cells):
            where = f"matrix.cells[{index}]"
            cell_keys = {
                "id", "family", "status", "target_status", "pulp_implementation",
                "pulp_proof", "vellum_future_implementation", "vellum_future_proof", "gap",
            }
            if not exact_keys(cell, cell_keys, where, errors):
                continue
            cell_id = cell["id"]
            if not isinstance(cell_id, str) or not cell_id or cell_id in seen:
                errors.append(f"{where}.id: missing or duplicate")
            else:
                seen.add(cell_id)
            family = cell["family"]
            status = cell["status"]
            target_status = cell["target_status"]
            if not isinstance(family, str) or family not in families:
                errors.append(f"{where}.family: unknown capability family")
            status_valid = isinstance(status, str) and status in statuses
            target_status_valid = (
                isinstance(target_status, str) and target_status in statuses
            )
            if not status_valid or not target_status_valid:
                errors.append(f"{where}: status outside closed vocabulary")
            if status_valid and target_status_valid:
                if status == "partial" and target_status != "supported":
                    errors.append(
                        f"{where}: partial required capability must target supported"
                    )
                if status in {"rejected-by-contract", "not-applicable"} and (
                    target_status != status
                ):
                    errors.append(
                        f"{where}: retained boundary target must preserve disposition"
                    )
            for key in ("pulp_implementation", "pulp_proof"):
                if strings(cell[key], f"{where}.{key}", errors):
                    for path in cell[key]:
                        safe_path(path, f"{where}.{key}", errors)
            allow_empty = status == "rejected-by-contract"
            for key in ("vellum_future_implementation", "vellum_future_proof"):
                if strings(cell[key], f"{where}.{key}", errors, empty=allow_empty):
                    for path in cell[key]:
                        safe_path(path, f"{where}.{key}", errors)
            if not isinstance(cell["gap"], str) or not cell["gap"]:
                errors.append(f"{where}.gap: expected non-empty string")
        if seen != required_ids:
            errors.append(
                "matrix.cells: IDs differ from frozen compatibility surface; "
                f"missing={sorted(required_ids - seen)} unexpected={sorted(seen - required_ids)}"
            )

    expected_gates = {
        "matrix_may_transfer_authority": False,
        "all_required_cells_must_reach_target_before_release": True,
        "source_work_before_exact_boundary_acknowledgement": False,
        "chromium_may_be_required_at_runtime": False,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(
            not exact_scalar(gates[key], expected)
            for key, expected in expected_gates.items()
        )
    ):
        errors.append("matrix.gates: differs from fail-closed pinned gates")
    return errors


def validate_matrix_repository_paths(root: Path, data: Any) -> list[str]:
    """Resolve supported Vellum evidence at the matrix's pinned audit commit.

    Production verification requires a Git HEAD, the pinned commit, and every
    supported owner/proof path. Unit fixtures opt out explicitly through verify().
    """
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return [f"matrix repository evidence: cannot invoke git: {exc}"]
    if head.returncode != 0:
        return ["matrix repository evidence: Git HEAD is required for pinned path checks"]
    if not isinstance(data, dict) or not isinstance(data.get("coordinates"), dict):
        return []
    commit = data["coordinates"].get("vellum_audit_commit")
    if not isinstance(commit, str) or not SHA40.fullmatch(commit):
        return []
    pinned = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if pinned.returncode != 0:
        return [f"matrix repository evidence: pinned Vellum commit {commit} is unavailable"]
    errors: list[str] = []
    cells = data.get("cells")
    if not isinstance(cells, list):
        return errors
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("status") != "supported":
            continue
        for key in ("vellum_future_implementation", "vellum_future_proof"):
            paths = cell.get(key)
            if not isinstance(paths, list):
                continue
            for path in paths:
                if not isinstance(path, str):
                    continue
                resolved = subprocess.run(
                    ["git", "-C", str(root), "cat-file", "-e", f"{commit}:{path}"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if resolved.returncode != 0:
                    errors.append(
                        f"matrix.cells[{index}].{key}: supported path does not exist "
                        f"at pinned Vellum commit: {path}"
                    )
    return errors


def validate_release_readiness(data: Any) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        return ["release readiness: compatibility matrix cells are unavailable"]
    incomplete = [
        cell.get("id", f"cell-{index}")
        for index, cell in enumerate(data["cells"])
        if isinstance(cell, dict) and cell.get("status") != cell.get("target_status")
    ]
    if incomplete:
        return [
            "release readiness: required compatibility cells have not reached target: "
            + ", ".join(str(cell_id) for cell_id in incomplete)
        ]
    return []


def verify(
    root: Path, *, repository_checks: bool = True, release_readiness: bool = False
) -> dict[str, Any]:
    closure_errors = []
    try:
        actual_files, symlinks = expansion_files(root)
        if actual_files != EXPECTED_FILES:
            closure_errors.append(
                "expansion artifact set differs; "
                f"missing={sorted(EXPECTED_FILES - actual_files)} "
                f"unexpected={sorted(actual_files - EXPECTED_FILES)}"
            )
        if symlinks:
            closure_errors.append(
                f"expansion artifacts must not be symlinks: {sorted(symlinks)}"
            )
    except OSError as exc:
        closure_errors.append(f"cannot enumerate expansion artifacts: {exc}")
    readme = root / EXPANSIONS_ROOT / "README.md"
    try:
        readme_sha256 = hashlib.sha256(readme.read_bytes()).hexdigest()
        if readme_sha256 != EXPECTED_README_SHA256:
            closure_errors.append("expansion README differs from pinned SHA-256")
    except OSError as exc:
        closure_errors.append(f"cannot read expansion README: {exc}")
    proposal = root / PROPOSAL_PATH
    try:
        proposal_sha256 = hashlib.sha256(proposal.read_bytes()).hexdigest()
        if proposal_sha256 != EXPECTED_PROPOSAL_SHA256:
            closure_errors.append("expansion proposal differs from pinned SHA-256")
    except OSError as exc:
        closure_errors.append(f"cannot read expansion proposal: {exc}")
    addendum = root / ADDENDUM_PATH
    try:
        addendum_sha256 = hashlib.sha256(addendum.read_bytes()).hexdigest()
        if addendum_sha256 != EXPECTED_ADDENDUM_SHA256:
            closure_errors.append("expansion scope addendum differs from pinned SHA-256")
    except OSError as exc:
        closure_errors.append(f"cannot read expansion scope addendum: {exc}")
    acknowledgement = root / ACKNOWLEDGEMENT_PATH
    try:
        acknowledgement_sha256 = hashlib.sha256(acknowledgement.read_bytes()).hexdigest()
        if acknowledgement_sha256 != EXPECTED_ACKNOWLEDGEMENT_SHA256:
            closure_errors.append("expansion watch acknowledgement differs from pinned SHA-256")
    except OSError as exc:
        closure_errors.append(f"cannot read expansion watch acknowledgement: {exc}")
    matrix = root / MATRIX_PATH
    try:
        matrix_sha256 = hashlib.sha256(matrix.read_bytes()).hexdigest()
        if matrix_sha256 != EXPECTED_MATRIX_SHA256:
            closure_errors.append("expansion compatibility matrix differs from pinned SHA-256")
    except OSError as exc:
        closure_errors.append(f"cannot read expansion compatibility matrix: {exc}")
    try:
        data = load_json(proposal)
        addendum_data = load_json(addendum)
        acknowledgement_data = load_json(acknowledgement)
        matrix_data = load_json(matrix)
        errors = (
            closure_errors
            + validate(data)
            + validate_addendum(addendum_data)
            + validate_acknowledgement(acknowledgement_data)
            + validate_matrix(matrix_data)
            + (
                validate_matrix_repository_paths(root, matrix_data)
                if repository_checks
                else []
            )
            + (validate_release_readiness(matrix_data) if release_readiness else [])
        )
    except ValueError as exc:
        data, addendum_data, acknowledgement_data, matrix_data = None, None, None, None
        errors = closure_errors + [str(exc)]
    passed = not errors
    return {
        "schema_version": 1,
        "release_readiness_requested": release_readiness,
        "status": "pass" if passed else "fail",
        "proposal": PROPOSAL_PATH.as_posix(),
        "proposal_id": data.get("proposal_id") if passed and isinstance(data, dict) else None,
        "scope_addendum": ADDENDUM_PATH.as_posix(),
        "scope_addendum_id": (
            addendum_data.get("addendum_id")
            if passed and isinstance(addendum_data, dict)
            else None
        ),
        "watch_acknowledgement": ACKNOWLEDGEMENT_PATH.as_posix(),
        "watch_acknowledgement_id": (
            acknowledgement_data.get("acknowledgement_id")
            if passed and isinstance(acknowledgement_data, dict)
            else None
        ),
        "compatibility_matrix": MATRIX_PATH.as_posix(),
        "compatibility_matrix_id": (
            matrix_data.get("matrix_id")
            if passed and isinstance(matrix_data, dict)
            else None
        ),
        "authority_effect": (
            acknowledgement_data.get("authority_effect")
            if passed and isinstance(acknowledgement_data, dict)
            else None
        ),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release-readiness", action="store_true")
    args = parser.parse_args()
    report = verify(args.root.resolve(), release_readiness=args.release_readiness)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
