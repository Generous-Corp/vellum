#!/usr/bin/env python3
"""Validate a non-authoritative later-expansion proposal."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
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
BOUNDARY_AMENDMENT_PATH = Path(
    "provenance/authority/expansions/"
    "full-design-import-render-v1/exact-boundary-amendment-1.json"
)
EXPANSIONS_ROOT = Path("provenance/authority/expansions")
EXPECTED_FILES = {
    "README.md",
    "full-design-import-render-v1/compatibility-matrix.json",
    "full-design-import-render-v1/exact-boundary-amendment-1.json",
    "full-design-import-render-v1/proposal.json",
    "full-design-import-render-v1/scope-addendum-1.json",
    "full-design-import-render-v1/watch-acknowledgement.json",
}
OPTIONAL_FUTURE_FILES = {
    "full-design-import-render-v1/exact-boundary-acknowledgement-1.json",
    "full-design-import-render-v1/parity-completion-1.json",
}
PARITY_COMPLETION_PATH = EXPANSIONS_ROOT / (
    "full-design-import-render-v1/parity-completion-1.json"
)
PULP_OWNERSHIP_PATH = ".github/vellum-ownership.json"
REQUIRED_PARITY_CHECKS = [
    {"name": "clean-release", "app_id": 15368,
     "workflow_path": ".github/workflows/readme-quick-start.yml"},
    {"name": "forbidden-deps", "app_id": 15368,
     "workflow_path": ".github/workflows/provenance.yml"},
    {"name": "gpu-macos-arm64", "app_id": 15368,
     "workflow_path": ".github/workflows/gpu-macos.yml"},
    {"name": "product-quality", "app_id": 15368,
     "workflow_path": ".github/workflows/product-quality.yml"},
    {"name": "sterile-consumer", "app_id": 15368,
     "workflow_path": ".github/workflows/gpu-macos.yml"},
]
REQUIRED_PULP_ROUTER_CASES = [
    "authority-load",
    "cli-contract",
    "conflicting-owner-fail-closed",
    "exact-projection-expansion",
    "generic-pulp-route",
    "mixed-multi-path-route",
    "pulp-specific-route",
    "vellum-generic-route",
]
CPP_PROOF_TEST_IDS = {
    "graphics/tests/design_ir_renderer_test.cpp": "vellum.gpu.design-ir-renderer",
    "graphics/tests/gpu_style_test.cpp": "vellum.gpu.style-fixtures",
    "graphics/tests/skia_raster_surface_test.cpp": "vellum.gpu.skia-raster-surface",
    "graphics/tests/text_shaping_concurrency_test.cpp": (
        "vellum.gpu.text-shaping-concurrency"
    ),
}
ARGUMENT_DRIVEN_PROOF_TEST_IDS = {
    "apps/app-host/test_phase3_scenario.py": "vellum.app-host-phase3-scenario",
    "apps/app-host/test_text_semantics.py": "vellum.app-host-text-ime-accessibility",
    "web/tests/run_text_semantics_browser.py": "vellum.web.text-semantics-proofs",
}
FIXTURE_PROOF_CONSUMERS = {
    "fixtures/design-ir/pulp-emitter-generic.pulp.zip": "cli/tests/test_pulp_zip.py",
}
EXPECTED_README_SHA256 = "6935071cee4c735f401356e625108150251921b8a0dd6f20648d7370fd388894"
EXPECTED_PROPOSAL_SHA256 = "7c2db05e110e7d9834806e08469f6d7cc70f6528b2242d1f6c0255dcdbc0a4c9"
EXPECTED_ADDENDUM_SHA256 = "91bb269ce5a872037fd67e4735125772bcac50d82cf454edfd8c356b11f5a122"
EXPECTED_ACKNOWLEDGEMENT_SHA256 = "877ac4a410e7ac8d5019aa8f2e09d9133a111acaab59eb2abef65c30527b78c8"
EXPECTED_MATRIX_SHA256 = "1792666eb1dd7d3f46dc607f4ee3dccbbc1232a6c2e6ab2331507c4b87122e1c"
EXPECTED_BOUNDARY_AMENDMENT_SHA256 = (
    "cf9b07233e9c66763e1f68391d2df4252dca01bbc7de5acac10dca006fbf5287"
)
EXPECTED_PROPOSED_AT = "2026-08-10T21:55:54Z"
EXPECTED_ADDENDUM_PROPOSED_AT = "2026-08-11T02:52:16Z"
EXPECTED_ACKNOWLEDGED_AT = "2026-08-11T20:54:33Z"
EXPECTED_BOUNDARY_PROPOSED_AT = "2026-08-12T02:08:00Z"
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
        return load_json_bytes(path.read_bytes(), str(path))
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
        raise ValueError(f"{path}: cannot load strict JSON: {exc}") from exc


def load_json_bytes(content: bytes, where: str) -> Any:
    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
        raise ValueError(f"{where}: cannot load strict JSON: {exc}") from exc


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


def exact_route_path(value: Any, where: str, errors: list[str]) -> None:
    safe_path(value, where, errors)
    if isinstance(value, str) and (
        value.endswith("/") or any(marker in value for marker in ("*", "?", "[", "]"))
    ):
        errors.append(f"{where}: expected exact path without prefix or glob semantics")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def exact_route_rows(matrix: Any, amendment: Any) -> list[dict[str, Any]]:
    if not isinstance(matrix, dict) or not isinstance(matrix.get("cells"), list):
        raise ValueError("exact routes: compatibility matrix cells are unavailable")
    expansions = (
        amendment.get("exact_path_expansions")
        if isinstance(amendment, dict)
        else None
    )
    if not isinstance(expansions, list):
        raise ValueError("exact routes: exact path expansions are unavailable")
    expanded: dict[tuple[str, str, str], list[str]] = {}
    for row in expansions:
        if not isinstance(row, dict) or not isinstance(row.get("exact_paths"), list):
            raise ValueError("exact routes: malformed exact path expansion")
        expanded[(str(row.get("cell_id")), str(row.get("role")), str(row.get("matrix_path")))] = row["exact_paths"]
    owners = {
        "vellum_future_implementation": "Generous-Corp/vellum",
        "vellum_future_proof": "Generous-Corp/vellum",
        "pulp_implementation": "Generous-Corp/pulp",
        "pulp_proof": "Generous-Corp/pulp",
    }
    routes: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for cell in matrix["cells"]:
        if not isinstance(cell, dict) or cell.get("family") == "retained-boundary":
            continue
        cell_id = str(cell.get("id"))
        for role, repository in owners.items():
            paths = cell.get(role)
            if not isinstance(paths, list):
                raise ValueError(f"exact routes: {cell_id}.{role} is unavailable")
            for matrix_path in paths:
                if not isinstance(matrix_path, str):
                    raise ValueError(f"exact routes: {cell_id}.{role} has a non-string path")
                resolved = expanded.get((cell_id, role, matrix_path), [matrix_path])
                for path in resolved:
                    routes.setdefault((repository, path), set()).add((cell_id, role))
    return [
        {
            "repository": repository,
            "path": path,
            "owner": repository,
            "cell_roles": [
                {"cell_id": cell_id, "role": role}
                for cell_id, role in sorted(cell_roles)
            ],
        }
        for (repository, path), cell_roles in sorted(routes.items())
    ]


def git_output(root: Path, *args: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return completed.stdout if completed.returncode == 0 else None


def git_commit_tree(
    root: Path, commit: Any, where: str, errors: list[str]
) -> str | None:
    if not isinstance(commit, str) or not SHA40.fullmatch(commit):
        errors.append(f"{where}: expected full commit SHA")
        return None
    resolved = git_output(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved is None or resolved.decode().strip() != commit:
        errors.append(f"{where}: commit is unavailable in the checked-out repository")
        return None
    tree = git_output(root, "show", "-s", "--format=%T", commit)
    if tree is None:
        errors.append(f"{where}: cannot resolve commit tree")
        return None
    return tree.decode().strip()


def git_blob(root: Path, commit: str, path: str) -> bytes | None:
    if not SHA40.fullmatch(commit):
        return None
    return git_output(root, "show", f"{commit}:{path}")


def git_regular_blob(root: Path, commit: str, path: str) -> bool:
    row = git_output(root, "ls-tree", commit, "--", path)
    if row is None:
        return False
    fields = row.decode(errors="replace").split(None, 3)
    return len(fields) == 4 and fields[0] in {"100644", "100755"} and fields[1] == "blob"


def require_git_ancestor(
    root: Path, ancestor: str, descendant: str, where: str, errors: list[str]
) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        errors.append(f"{where}: commit is not on the authoritative history")


def validate_authority_promotion_attestation(data: Any, amendment: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "attestation_id", "state", "attested_at",
        "attested_by", "authority_repository", "delivery_repository", "promotion_mode",
        "authority_commit", "delivery_commit", "authority_tree", "delivery_tree",
        "exact_boundary_amendment_id", "exact_boundary_amendment_sha256",
        "exact_boundary_acknowledgement_id", "exact_boundary_acknowledgement_sha256",
        "parity_completion_id", "parity_completion_sha256",
        "parity_release_source_commit",
    }
    if not exact_keys(data, top, "authority_promotion", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "vellum-authority-promotion-attestation",
        "attestation_id": "full-design-import-render-v1-authority-promotion-1",
        "state": "attested",
        "authority_repository": "Generous-Corp/vellum",
        "delivery_repository": "danielraffel/vellum",
        "exact_boundary_amendment_id": (
            "full-design-import-render-v1-exact-boundary-amendment-1"
        ),
        "exact_boundary_amendment_sha256": EXPECTED_BOUNDARY_AMENDMENT_SHA256,
        "exact_boundary_acknowledgement_id": (
            "full-design-import-render-v1-exact-boundary-acknowledgement-1"
        ),
        "parity_completion_id": "full-design-import-render-v1-parity-completion-1",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"authority_promotion.{key}: expected {expected!r}")
    if not isinstance(data["promotion_mode"], str) or data["promotion_mode"] not in {
        "repository-transfer", "exact-mirror"
    }:
        errors.append("authority_promotion.promotion_mode: expected closed promotion mode")
    if not OWNER.fullmatch(str(data["attested_by"])):
        errors.append("authority_promotion.attested_by: expected individual GitHub handle")
    try:
        stamp = data["attested_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("authority_promotion.attested_at: expected UTC timestamp ending in Z")
    sha_fields = (
        "authority_commit", "delivery_commit", "authority_tree", "delivery_tree",
        "parity_release_source_commit",
    )
    for key in sha_fields:
        value = data[key]
        if not isinstance(value, str) or not SHA40.fullmatch(value):
            errors.append(f"authority_promotion.{key}: expected full commit or tree SHA")
    for key in (
        "exact_boundary_acknowledgement_sha256", "parity_completion_sha256"
    ):
        digest = data[key]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"authority_promotion.{key}: expected SHA-256")
    if data["authority_commit"] != data["delivery_commit"]:
        errors.append("authority_promotion: authority and delivery commits must match exactly")
    if data["authority_tree"] != data["delivery_tree"]:
        errors.append("authority_promotion: authority and delivery trees must match exactly")
    if data["parity_release_source_commit"] != data["authority_commit"]:
        errors.append("authority_promotion: release source must be the promoted authority commit")
    required_ack = (
        amendment.get("repository_roles", {}).get(
            "required_exact_boundary_acknowledgement_id"
        )
        if isinstance(amendment, dict)
        else None
    )
    if data["exact_boundary_acknowledgement_id"] != required_ack:
        errors.append("authority_promotion: acknowledgement differs from amendment requirement")
    return errors


def validate_promotion_acknowledgement_digest(
    root: Path, data: Any
) -> list[str]:
    if not isinstance(data, dict):
        return ["authority_promotion: expected attestation object"]
    acknowledgement = (
        root
        / EXPANSIONS_ROOT
        / "full-design-import-render-v1/exact-boundary-acknowledgement-1.json"
    )
    if not acknowledgement.is_file():
        return ["authority_promotion: exact-boundary acknowledgement artifact is missing"]
    try:
        acknowledgement_data = load_json(acknowledgement)
        errors = validate_exact_boundary_acknowledgement(acknowledgement_data)
        actual = hashlib.sha256(acknowledgement.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        return [f"authority_promotion: cannot read exact-boundary acknowledgement: {exc}"]
    if errors:
        return ["authority_promotion: " + error for error in errors]
    if data.get("exact_boundary_acknowledgement_id") != acknowledgement_data.get(
        "acknowledgement_id"
    ):
        return ["authority_promotion: acknowledgement ID does not match artifact"]
    if data.get("exact_boundary_acknowledgement_sha256") != actual:
        return ["authority_promotion: acknowledgement digest does not match artifact"]
    return []


def validate_promotion_completion_digest(
    root: Path, data: Any, release_source_commit: str | None
) -> list[str]:
    if not isinstance(data, dict):
        return ["authority_promotion: expected attestation object"]
    if release_source_commit is None or not git_regular_blob(
        root, release_source_commit, PARITY_COMPLETION_PATH.as_posix()
    ):
        return ["authority_promotion: parity completion artifact is missing"]
    completion = git_blob(
        root, release_source_commit, PARITY_COMPLETION_PATH.as_posix()
    )
    if completion is None:
        return ["authority_promotion: parity completion artifact is missing"]
    try:
        completion_data = load_json_bytes(completion, "tagged parity completion")
        actual = hashlib.sha256(completion).hexdigest()
    except ValueError as exc:
        return [f"authority_promotion: cannot read parity completion: {exc}"]
    if data.get("parity_completion_id") != completion_data.get("completion_id"):
        return ["authority_promotion: parity completion ID does not match artifact"]
    if data.get("parity_completion_sha256") != actual:
        return ["authority_promotion: parity completion digest does not match artifact"]
    return []


def validate_promotion_repository_evidence(
    authority_root: Path,
    delivery_root: Path | None,
    data: Any,
    *,
    repository: str | None,
    release_source_commit: str | None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["authority_promotion evidence: expected attestation object"]
    if repository != "Generous-Corp/vellum":
        errors.append(
            "authority_promotion evidence: parity release must run in Generous-Corp/vellum"
        )
    authority_commit = data.get("authority_commit")
    authority_tree = git_commit_tree(
        authority_root, authority_commit, "authority_promotion authority_commit", errors
    )
    if authority_tree is not None and authority_tree != data.get("authority_tree"):
        errors.append("authority_promotion evidence: authority tree differs from Git")
    authority_head = git_output(authority_root, "rev-parse", "HEAD")
    authority_head_sha = authority_head.decode().strip() if authority_head is not None else None
    expected_release = release_source_commit or authority_head_sha
    if expected_release != data.get("parity_release_source_commit"):
        errors.append("authority_promotion evidence: checked release source differs from attestation")
    if authority_head_sha != expected_release:
        errors.append("authority_promotion evidence: authority checkout HEAD differs from release source")
    if delivery_root is None:
        errors.append(
            "authority_promotion evidence: --delivery-root is required for exact promotion proof"
        )
        return errors
    delivery_commit = data.get("delivery_commit")
    delivery_tree = git_commit_tree(
        delivery_root, delivery_commit, "authority_promotion delivery_commit", errors
    )
    if delivery_tree is not None and delivery_tree != data.get("delivery_tree"):
        errors.append("authority_promotion evidence: delivery tree differs from Git")
    delivery_head = git_output(delivery_root, "rev-parse", "HEAD")
    if delivery_head is None or delivery_head.decode().strip() != delivery_commit:
        errors.append("authority_promotion evidence: delivery checkout HEAD differs from attestation")
    return errors


def validate_exact_boundary_acknowledgement(data: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "acknowledgement_id", "amendment_id", "state",
        "acknowledged_at", "acknowledged_by", "authority_effect",
        "implementation_authority", "coordinates", "repository_roles", "gates",
    }
    if not exact_keys(data, top, "exact_boundary_acknowledgement", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "full-design-import-render-exact-boundary-acknowledgement",
        "acknowledgement_id": (
            "full-design-import-render-v1-exact-boundary-acknowledgement-1"
        ),
        "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
        "state": "acknowledged",
        "acknowledged_by": "@danielraffel",
        "authority_effect": "exact-path-implementation-authority-activated",
        "implementation_authority": "authorized-for-matrix-exact-routes",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"exact_boundary_acknowledgement.{key}: expected {expected!r}")
    if not OWNER.fullmatch(str(data["acknowledged_by"])):
        errors.append(
            "exact_boundary_acknowledgement.acknowledged_by: expected individual handle"
        )
    try:
        stamp = data["acknowledged_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append(
            "exact_boundary_acknowledgement.acknowledged_at: expected UTC timestamp"
        )
    coordinates = data["coordinates"]
    coordinate_keys = {
        "pulp_repository", "pulp_acceptance_merge_commit", "pulp_acceptance_path",
        "pulp_acceptance_sha256", "vellum_delivery_repository",
        "vellum_amendment_merge_commit", "amendment_path", "amendment_sha256",
        "matrix_merge_commit", "matrix_path", "matrix_sha256",
    }
    if not exact_keys(
        coordinates, coordinate_keys, "exact_boundary_acknowledgement.coordinates", errors
    ):
        coordinates = {}
    expected_coordinate_scalars = {
        "pulp_repository": "Generous-Corp/pulp",
        "pulp_acceptance_path": (
            ".github/vellum-expansion-watch/full-design-import-render-v1/"
            "exact-boundary-acceptance-1.json"
        ),
        "vellum_delivery_repository": "danielraffel/vellum",
        "amendment_path": BOUNDARY_AMENDMENT_PATH.as_posix(),
        "amendment_sha256": EXPECTED_BOUNDARY_AMENDMENT_SHA256,
        "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
        "matrix_path": MATRIX_PATH.as_posix(),
        "matrix_sha256": EXPECTED_MATRIX_SHA256,
    }
    for key, expected in expected_coordinate_scalars.items():
        if not exact_scalar(coordinates.get(key), expected):
            errors.append(f"exact_boundary_acknowledgement.coordinates.{key}: drift")
    for key in ("pulp_acceptance_merge_commit", "vellum_amendment_merge_commit"):
        value = coordinates.get(key)
        if not isinstance(value, str) or not SHA40.fullmatch(value):
            errors.append(f"exact_boundary_acknowledgement.coordinates.{key}: expected SHA")
    digest = coordinates.get("pulp_acceptance_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append(
            "exact_boundary_acknowledgement.coordinates.pulp_acceptance_sha256: expected SHA-256"
        )
    expected_roles = {
        "authority_repository": "Generous-Corp/vellum",
        "temporary_private_delivery_repository": "danielraffel/vellum",
        "delivery_repository_is_authority": False,
    }
    roles = data["repository_roles"]
    if (
        not isinstance(roles, dict)
        or set(roles) != set(expected_roles)
        or any(not exact_scalar(roles[key], expected) for key, expected in expected_roles.items())
    ):
        errors.append("exact_boundary_acknowledgement.repository_roles: drift")
    expected_gates = {
        "only_matrix_exact_routes_authorized": True,
        "unlisted_pulp_paths_remain_pulp_owned": True,
        "retained_boundary_cells_remain_pulp_owned": True,
        "promotion_attestation_required_before_parity_release": True,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(not exact_scalar(gates[key], expected) for key, expected in expected_gates.items())
    ):
        errors.append("exact_boundary_acknowledgement.gates: drift")
    return errors


def validate_pulp_exact_boundary_acceptance(
    data: Any, acknowledgement: Any, amendment: Any
) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "acceptance_id", "state", "accepted_at",
        "accepted_by", "pulp_repository", "counterpart", "refresh",
        "routing_projection", "authority_effect", "implementation_authority", "gates",
    }
    if not exact_keys(data, top, "pulp_exact_boundary_acceptance", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "full-design-import-render-exact-boundary-acceptance",
        "acceptance_id": "full-design-import-render-v1-exact-boundary-acceptance-1",
        "state": "accepted",
        "accepted_by": "@danielraffel",
        "pulp_repository": "Generous-Corp/pulp",
        "authority_effect": "none",
        "implementation_authority": (
            "forbidden-until-vellum-exact-boundary-acknowledged"
        ),
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"pulp_exact_boundary_acceptance.{key}: expected {expected!r}")
    try:
        stamp = data["accepted_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("pulp_exact_boundary_acceptance.accepted_at: expected UTC timestamp")
    counterpart = data["counterpart"]
    counterpart_keys = {
        "repository", "amendment_id", "amendment_merge_commit", "amendment_path",
        "amendment_sha256", "matrix_merge_commit", "matrix_path", "matrix_sha256",
    }
    if not exact_keys(
        counterpart, counterpart_keys, "pulp_exact_boundary_acceptance.counterpart", errors
    ):
        counterpart = {}
    coordinates = (
        acknowledgement.get("coordinates", {})
        if isinstance(acknowledgement, dict)
        else {}
    )
    expected_counterpart = {
        "repository": "danielraffel/vellum",
        "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
        "amendment_merge_commit": coordinates.get("vellum_amendment_merge_commit"),
        "amendment_path": BOUNDARY_AMENDMENT_PATH.as_posix(),
        "amendment_sha256": EXPECTED_BOUNDARY_AMENDMENT_SHA256,
        "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
        "matrix_path": MATRIX_PATH.as_posix(),
        "matrix_sha256": EXPECTED_MATRIX_SHA256,
    }
    for key, expected in expected_counterpart.items():
        if not exact_scalar(counterpart.get(key), expected):
            errors.append(f"pulp_exact_boundary_acceptance.counterpart.{key}: drift")
    projection = data["routing_projection"]
    projection_keys = {
        "path", "sha256", "schema_version", "expansion_id", "route_set_sha256",
        "router_path", "router_sha256", "router_contract_test_path",
        "router_contract_test_sha256", "router_dependency_path",
        "router_dependency_sha256", "router_contract_check",
    }
    if not exact_keys(
        projection, projection_keys, "pulp_exact_boundary_acceptance.routing_projection", errors
    ):
        projection = {}
    expected_projection = {
        "path": PULP_OWNERSHIP_PATH,
        "schema_version": 3,
        "expansion_id": "full-design-import-render-v1",
        "router_path": (
            ".agents/skills/pulp-vellum-change-routing/scripts/route_change.py"
        ),
        "router_contract_test_path": (
            ".agents/skills/pulp-vellum-change-routing/scripts/test_route_change.py"
        ),
        "router_dependency_path": (
            ".agents/skills/pulp-vellum-change-routing/scripts/routing_evidence.py"
        ),
    }
    for key, expected in expected_projection.items():
        if not exact_scalar(projection.get(key), expected):
            errors.append(f"pulp_exact_boundary_acceptance.routing_projection.{key}: drift")
    for key in (
        "sha256", "route_set_sha256", "router_sha256",
        "router_contract_test_sha256", "router_dependency_sha256",
    ):
        if not isinstance(projection.get(key), str) or not re.fullmatch(
            r"[0-9a-f]{64}", projection[key]
        ):
            errors.append(
                f"pulp_exact_boundary_acceptance.routing_projection.{key}: expected SHA-256"
            )
    contract_check = projection.get("router_contract_check")
    expected_contract_check = {
        "name": "vellum-routing-contract",
        "app_id": 15368,
        "workflow_path": ".github/workflows/vellum-routing-contract.yml",
        "event": "push",
        "branch": "main",
        "contract_scope": "full-bound-router-contract-suite",
        "required_case_ids": REQUIRED_PULP_ROUTER_CASES,
    }
    if (
        not isinstance(contract_check, dict)
        or set(contract_check) != set(expected_contract_check)
        or any(
            not exact_scalar(contract_check.get(key), expected)
            for key, expected in expected_contract_check.items()
        )
    ):
        errors.append(
            "pulp_exact_boundary_acceptance.routing_projection.router_contract_check: drift"
        )
    refresh = data["refresh"]
    refresh_keys = {
        "audited_at", "pulp_main_commit", "open_pr_audit_complete",
        "open_pr_rows", "open_vellum_overlap_count",
    }
    if not exact_keys(refresh, refresh_keys, "pulp_exact_boundary_acceptance.refresh", errors):
        refresh = {}
    try:
        stamp = refresh.get("audited_at")
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("pulp_exact_boundary_acceptance.refresh.audited_at: expected UTC timestamp")
    if data.get("accepted_at") != refresh.get("audited_at"):
        errors.append(
            "pulp_exact_boundary_acceptance: audit and acceptance timestamps must match"
        )
    if not isinstance(refresh.get("pulp_main_commit"), str) or not SHA40.fullmatch(
        refresh["pulp_main_commit"]
    ):
        errors.append("pulp_exact_boundary_acceptance.refresh.pulp_main_commit: expected SHA")
    if refresh.get("open_pr_audit_complete") is not True:
        errors.append("pulp_exact_boundary_acceptance.refresh: open PR audit must be complete")
    rows = refresh.get("open_pr_rows")
    if not isinstance(rows, list):
        errors.append("pulp_exact_boundary_acceptance.refresh.open_pr_rows: expected array")
        rows = []
    unresolved_rows = 0
    row_keys = {
        "repository", "pull_request", "merge_base_commit", "head_commit", "paths", "disposition",
        "resolution",
    }
    allowed_repositories = {
        "Generous-Corp/pulp", "Generous-Corp/vellum", "danielraffel/vellum"
    }
    allowed_resolutions = {
        "pulp-retained", "pulp-counterpart", "vellum-routed-coordinated", "unresolved"
    }
    for index, row in enumerate(rows):
        where = f"pulp_exact_boundary_acceptance.refresh.open_pr_rows[{index}]"
        if not exact_keys(row, row_keys, where, errors):
            continue
        if not isinstance(row["repository"], str) or row["repository"] not in allowed_repositories:
            errors.append(f"{where}.repository: unexpected repository")
        if (
            not isinstance(row["pull_request"], int)
            or isinstance(row["pull_request"], bool)
            or row["pull_request"] <= 0
        ):
            errors.append(f"{where}.pull_request: expected positive integer")
        for field in ("merge_base_commit", "head_commit"):
            if not isinstance(row[field], str) or not SHA40.fullmatch(row[field]):
                errors.append(f"{where}.{field}: expected SHA")
        if strings(row["paths"], f"{where}.paths", errors):
            for path_index, path in enumerate(row["paths"]):
                exact_route_path(path, f"{where}.paths[{path_index}]", errors)
        if not isinstance(row["disposition"], str) or not row["disposition"]:
            errors.append(f"{where}.disposition: expected non-empty string")
        if not isinstance(row["resolution"], str) or row["resolution"] not in allowed_resolutions:
            errors.append(f"{where}.resolution: unexpected resolution")
        elif row["resolution"] == "unresolved":
            unresolved_rows += 1
    claimed_unresolved = refresh.get("open_vellum_overlap_count")
    if exact_scalar(claimed_unresolved, 0) and unresolved_rows != claimed_unresolved:
        errors.append(
            "pulp_exact_boundary_acceptance.refresh: unresolved overlap count "
            "contradicts open PR rows"
        )
    if not exact_scalar(claimed_unresolved, 0):
        errors.append(
            "pulp_exact_boundary_acceptance.refresh.open_vellum_overlap_count: "
            "expected zero unresolved overlaps"
        )
    expected_gates = {
        "exact_matrix_routes_accepted": True,
        "refreshed_open_pr_audit_complete": True,
        "vellum_acknowledgement_required": True,
        "source_work_authorized": False,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(not exact_scalar(gates[key], expected) for key, expected in expected_gates.items())
    ):
        errors.append("pulp_exact_boundary_acceptance.gates: drift")
    if not isinstance(amendment, dict) or amendment.get("authority_effect") != "none":
        errors.append("pulp_exact_boundary_acceptance: amendment is not inert")
    return errors


def validate_pulp_ownership_projection(
    projection: Any, acceptance: Any, matrix: Any, amendment: Any
) -> list[str]:
    errors: list[str] = []
    if not isinstance(projection, dict):
        return ["Pulp ownership projection: expected object"]
    if projection.get("schema_version") != 3:
        errors.append("Pulp ownership projection: schema_version must be 3")
    if projection.get("framework_repository") != "Generous-Corp/vellum":
        errors.append("Pulp ownership projection: framework repository drifted")
    expansions = projection.get("expansions")
    if not isinstance(expansions, list):
        return errors + ["Pulp ownership projection: expansions must be an array"]
    matches = [
        row for row in expansions
        if isinstance(row, dict) and row.get("id") == "full-design-import-render-v1"
    ]
    if len(matches) != 1:
        return errors + ["Pulp ownership projection: expected exactly one accepted expansion"]
    expansion = matches[0]
    expected_keys = {
        "id", "state", "accepted_at", "accepted_by", "amendment_id",
        "matrix_id", "matrix_sha256", "route_set_sha256", "routes",
    }
    if not exact_keys(expansion, expected_keys, "Pulp ownership projection expansion", errors):
        return errors
    expected_routes = exact_route_rows(matrix, amendment)
    expected_route_digest = canonical_sha256(expected_routes)
    expected_scalars = {
        "id": "full-design-import-render-v1",
        "state": "accepted-pending-vellum-acknowledgement",
        "accepted_at": acceptance.get("accepted_at") if isinstance(acceptance, dict) else None,
        "accepted_by": "@danielraffel",
        "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
        "matrix_id": "full-design-import-render-v1-compatibility-matrix",
        "matrix_sha256": EXPECTED_MATRIX_SHA256,
        "route_set_sha256": expected_route_digest,
    }
    for key, expected in expected_scalars.items():
        if not exact_scalar(expansion.get(key), expected):
            errors.append(f"Pulp ownership projection expansion.{key}: drift")
    if expansion.get("routes") != expected_routes:
        errors.append("Pulp ownership projection expansion.routes: differs from exact routes")
    bound = (
        acceptance.get("routing_projection", {})
        if isinstance(acceptance, dict)
        else {}
    )
    if bound.get("route_set_sha256") != expected_route_digest:
        errors.append("Pulp ownership projection: acceptance route-set digest differs")
    return errors


def validate_pulp_router_check_evidence(
    check_runs: Any, pulp_commit: str, acceptance: Any
) -> list[str]:
    """Bind Pulp's full router contract to its own exact CI run.

    Vellum treats all Pulp-owned source as evidence and never executes it.
    """
    errors: list[str] = []
    if (
        not isinstance(check_runs, dict)
        or not isinstance(check_runs.get("check_runs"), list)
        or not isinstance(check_runs.get("workflow_runs"), list)
    ):
        return ["Pulp router contract evidence: check evidence unavailable"]
    matches = [
        row for row in check_runs["check_runs"]
        if isinstance(row, dict)
        and row.get("name") == "vellum-routing-contract"
        and row.get("head_sha") == pulp_commit
        and row.get("conclusion") == "success"
        and isinstance(row.get("app"), dict)
        and row["app"].get("id") == 15368
    ]
    for match in matches:
        details_url = match.get("details_url")
        run_match = (
            re.fullmatch(
                r"https://github\.com/Generous-Corp/pulp/actions/runs/([0-9]+)/job/[0-9]+",
                details_url,
            )
            if isinstance(details_url, str)
            else None
        )
        if run_match is None:
            continue
        run_id = int(run_match.group(1))
        if any(
            isinstance(run, dict)
            and run.get("id") == run_id
            and run.get("path") == ".github/workflows/vellum-routing-contract.yml"
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("head_sha") == pulp_commit
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("repository"), dict)
            and run["repository"].get("full_name") == "Generous-Corp/pulp"
            for run in check_runs["workflow_runs"]
        ):
            receipts = check_runs.get("pulp_router_contract_receipts")
            if not isinstance(receipts, list):
                break
            projection = (
                acceptance.get("routing_projection", {})
                if isinstance(acceptance, dict)
                else {}
            )
            expected = {
                "schema_version": 1,
                "kind": "pulp-vellum-routing-contract-execution",
                "repository": "Generous-Corp/pulp",
                "head_sha": pulp_commit,
                "run_id": run_id,
                "workflow_path": ".github/workflows/vellum-routing-contract.yml",
                "status": "pass",
                "route_set_sha256": projection.get("route_set_sha256"),
                "router_sha256": projection.get("router_sha256"),
                "router_contract_test_sha256": projection.get(
                    "router_contract_test_sha256"
                ),
                "router_dependency_sha256": projection.get(
                    "router_dependency_sha256"
                ),
            }
            for receipt in receipts:
                if (
                    isinstance(receipt, dict)
                    and set(receipt) == set(expected) | {"case_results"}
                    and all(exact_scalar(receipt.get(key), value) for key, value in expected.items())
                    and receipt.get("case_results") == [
                        {"case_id": case_id, "status": "pass"}
                        for case_id in REQUIRED_PULP_ROUTER_CASES
                    ]
                ):
                    return []
    errors.append(
        "Pulp router contract evidence: missing exact workflow-bound push-main "
        "vellum-routing-contract check and digest-bound execution receipt"
    )
    return errors


def validate_open_pr_snapshot(
    snapshot: Any, acceptance: Any, matrix: Any, amendment: Any
) -> list[str]:
    """Compare the accepted overlap audit with a fresh GitHub API snapshot."""
    errors: list[str] = []
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema_version", "kind", "repositories"
    }:
        return ["open PR snapshot: expected strict snapshot object"]
    if not exact_scalar(snapshot.get("schema_version"), 2) or not exact_scalar(
        snapshot.get("kind"), "github-open-pull-request-snapshot"
    ):
        errors.append("open PR snapshot: schema or kind differs")
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, list):
        return errors + ["open PR snapshot.repositories: expected array"]
    expected_repositories = {
        "Generous-Corp/pulp", "Generous-Corp/vellum", "danielraffel/vellum"
    }
    seen_repositories: set[str] = set()
    live_overlaps: dict[tuple[str, int], tuple[str, str, list[str]]] = {}
    try:
        exact_routes = exact_route_rows(matrix, amendment)
    except ValueError as exc:
        return errors + [f"open PR snapshot: cannot resolve exact routes: {exc}"]
    routed_paths = {
        "Generous-Corp/pulp": {
            row["path"] for row in exact_routes
            if row["repository"] == "Generous-Corp/pulp"
        },
        "Generous-Corp/vellum": {
            row["path"] for row in exact_routes
            if row["repository"] == "Generous-Corp/vellum"
        },
        "danielraffel/vellum": {
            row["path"] for row in exact_routes
            if row["repository"] == "Generous-Corp/vellum"
        },
    }
    for repository_index, repository_row in enumerate(repositories):
        where = f"open PR snapshot.repositories[{repository_index}]"
        if not isinstance(repository_row, dict) or set(repository_row) != {
            "repository", "pulls"
        }:
            errors.append(f"{where}: expected strict repository object")
            continue
        repository = repository_row.get("repository")
        if repository not in expected_repositories or repository in seen_repositories:
            errors.append(f"{where}.repository: unexpected or duplicate repository")
            continue
        seen_repositories.add(repository)
        pulls = repository_row.get("pulls")
        if not isinstance(pulls, list):
            errors.append(f"{where}.pulls: expected array")
            continue
        seen_numbers: set[int] = set()
        previous_number = 0
        for pull_index, pull in enumerate(pulls):
            pull_where = f"{where}.pulls[{pull_index}]"
            if not isinstance(pull, dict) or set(pull) != {
                "number", "base_commit", "merge_base_commit", "head_commit", "paths",
                "diff_path_count",
            }:
                errors.append(f"{pull_where}: expected strict pull request object")
                continue
            number = pull.get("number")
            if (
                not isinstance(number, int) or isinstance(number, bool) or number <= 0
                or number in seen_numbers or number <= previous_number
            ):
                errors.append(f"{pull_where}.number: expected unique ascending positive integer")
                continue
            seen_numbers.add(number)
            previous_number = number
            base_commit = pull.get("base_commit")
            merge_base_commit = pull.get("merge_base_commit")
            head_commit = pull.get("head_commit")
            if (
                not isinstance(base_commit, str)
                or SHA40.fullmatch(base_commit) is None
                or not isinstance(merge_base_commit, str)
                or SHA40.fullmatch(merge_base_commit) is None
                or not isinstance(head_commit, str)
                or SHA40.fullmatch(head_commit) is None
            ):
                errors.append(f"{pull_where}: expected full base, merge-base, and head SHAs")
                continue
            diff_path_count = pull.get("diff_path_count")
            if (
                not isinstance(diff_path_count, int)
                or isinstance(diff_path_count, bool)
                or diff_path_count < 0
            ):
                errors.append(f"{pull_where}.diff_path_count: expected nonnegative integer")
                continue
            paths = pull.get("paths")
            if (
                not isinstance(paths, list)
                or not all(isinstance(path, str) for path in paths)
                or paths != sorted(set(paths))
            ):
                errors.append(f"{pull_where}.paths: expected sorted unique array")
                continue
            if diff_path_count != len(paths):
                errors.append(f"{pull_where}: tree diff path count differs")
                continue
            invalid_path = False
            for path_index, path in enumerate(paths):
                before = len(errors)
                exact_route_path(path, f"{pull_where}.paths[{path_index}]", errors)
                invalid_path = invalid_path or len(errors) != before
            if invalid_path:
                continue
            overlap_paths = sorted(set(paths) & routed_paths[repository])
            if overlap_paths:
                live_overlaps[(repository, number)] = (
                    merge_base_commit, head_commit, overlap_paths
                )
    if seen_repositories != expected_repositories:
        errors.append(
            "open PR snapshot: repository coverage differs; "
            f"missing={sorted(expected_repositories - seen_repositories)}"
        )
    refresh = acceptance.get("refresh", {}) if isinstance(acceptance, dict) else {}
    accepted_rows = refresh.get("open_pr_rows") if isinstance(refresh, dict) else None
    if not isinstance(accepted_rows, list):
        return errors + ["open PR snapshot: accepted open PR rows unavailable"]
    accepted_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for index, row in enumerate(accepted_rows):
        if not isinstance(row, dict):
            continue
        key = (row.get("repository"), row.get("pull_request"))
        if key in accepted_by_key:
            errors.append(
                f"open PR snapshot: duplicate accepted row for {key[0]}#{key[1]}"
            )
        else:
            accepted_by_key[key] = row
    for key, (merge_base_commit, head_commit, paths) in live_overlaps.items():
        row = accepted_by_key.get(key)
        label = f"{key[0]}#{key[1]}"
        if row is None:
            errors.append(f"open PR snapshot: live overlap {label} is absent from acceptance")
            continue
        if row.get("merge_base_commit") != merge_base_commit:
            errors.append(f"open PR snapshot: live overlap {label} merge base differs")
        if row.get("head_commit") != head_commit:
            errors.append(f"open PR snapshot: live overlap {label} head differs")
        if row.get("paths") != paths:
            errors.append(f"open PR snapshot: live overlap {label} paths differ")
    for key in accepted_by_key.keys() - live_overlaps.keys():
        errors.append(
            f"open PR snapshot: accepted overlap {key[0]}#{key[1]} is not currently open"
        )
    return errors


def validate_exact_boundary_repository_evidence(
    root: Path, pulp_root: Path | None, data: Any, amendment: Any,
    check_runs: Any = None, open_pr_snapshot: Any = None,
    defer_live_open_pr_audit: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("coordinates"), dict):
        return ["exact_boundary_acknowledgement evidence: coordinates unavailable"]
    coordinates = data["coordinates"]
    amendment_commit = coordinates.get("vellum_amendment_merge_commit")
    amendment_path = coordinates.get("amendment_path")
    if isinstance(amendment_commit, str) and isinstance(amendment_path, str):
        if git_commit_tree(root, amendment_commit, "exact boundary amendment merge", errors):
            require_git_ancestor(
                root,
                amendment_commit,
                "HEAD",
                "exact boundary amendment merge",
                errors,
            )
            content = git_blob(root, amendment_commit, amendment_path)
            if content is None:
                errors.append("exact boundary amendment merge: artifact is unavailable")
            elif hashlib.sha256(content).hexdigest() != coordinates.get("amendment_sha256"):
                errors.append("exact boundary amendment merge: artifact digest differs")
    matrix_commit = coordinates.get("matrix_merge_commit")
    matrix_path = coordinates.get("matrix_path")
    if isinstance(matrix_commit, str) and isinstance(matrix_path, str):
        if git_commit_tree(root, matrix_commit, "compatibility matrix merge", errors):
            require_git_ancestor(
                root,
                matrix_commit,
                "HEAD",
                "compatibility matrix merge",
                errors,
            )
            content = git_blob(root, matrix_commit, matrix_path)
            if content is None:
                errors.append("compatibility matrix merge: artifact is unavailable")
            elif hashlib.sha256(content).hexdigest() != coordinates.get("matrix_sha256"):
                errors.append("compatibility matrix merge: artifact digest differs")
    if pulp_root is None:
        errors.append(
            "exact boundary acknowledgement: --pulp-root is required to verify acceptance"
        )
        return errors
    pulp_commit = coordinates.get("pulp_acceptance_merge_commit")
    pulp_path = coordinates.get("pulp_acceptance_path")
    if not isinstance(pulp_commit, str) or not isinstance(pulp_path, str):
        errors.append("exact boundary acknowledgement: Pulp acceptance coordinates unavailable")
        return errors
    if git_commit_tree(pulp_root, pulp_commit, "Pulp acceptance merge", errors) is None:
        return errors
    head = git_output(pulp_root, "rev-parse", "HEAD")
    main_head = head.decode().strip() if head is not None else None
    on_main = subprocess.run(
        ["git", "-C", str(pulp_root), "merge-base", "--is-ancestor", pulp_commit, "HEAD"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if main_head is None or on_main.returncode != 0:
        errors.append("Pulp acceptance merge: commit is not on authoritative Pulp main")
    content = git_blob(pulp_root, pulp_commit, pulp_path)
    if content is None:
        errors.append("Pulp acceptance merge: acceptance artifact is unavailable")
        return errors
    if hashlib.sha256(content).hexdigest() != coordinates.get("pulp_acceptance_sha256"):
        errors.append("Pulp acceptance merge: acceptance artifact digest differs")
    try:
        acceptance = load_json_bytes(content, "Pulp acceptance merge artifact")
    except ValueError as exc:
        errors.append(f"Pulp acceptance merge: invalid acceptance JSON: {exc}")
        return errors
    acceptance_errors = validate_pulp_exact_boundary_acceptance(
        acceptance, data, amendment
    )
    errors.extend(acceptance_errors)
    if acceptance_errors:
        return errors
    if not defer_live_open_pr_audit:
        errors.extend(
            validate_open_pr_snapshot(
                open_pr_snapshot, acceptance, load_json(root / MATRIX_PATH), amendment
            )
        )
    if check_runs is not None:
        errors.extend(
            validate_pulp_router_check_evidence(check_runs, pulp_commit, acceptance)
        )
    projection_coordinates = (
        acceptance.get("routing_projection", {})
        if isinstance(acceptance, dict)
        else {}
    )
    projection_path = projection_coordinates.get("path")
    if not isinstance(projection_path, str):
        errors.append("Pulp acceptance merge: routing projection path unavailable")
    else:
        projection_blob = git_blob(pulp_root, pulp_commit, projection_path)
        if projection_blob is None:
            errors.append("Pulp acceptance merge: routing projection is unavailable")
        elif hashlib.sha256(projection_blob).hexdigest() != projection_coordinates.get(
            "sha256"
        ):
            errors.append("Pulp acceptance merge: routing projection digest differs")
        else:
            try:
                projection_data = load_json_bytes(
                    projection_blob, "Pulp acceptance routing projection"
                )
                matrix_data = load_json(root / MATRIX_PATH)
                projection_errors = validate_pulp_ownership_projection(
                    projection_data, acceptance, matrix_data, amendment
                )
                errors.extend(projection_errors)
                for label in ("router", "router_contract_test", "router_dependency"):
                    bound_path = projection_coordinates.get(f"{label}_path")
                    bound_digest = projection_coordinates.get(f"{label}_sha256")
                    blob = (
                        git_blob(pulp_root, pulp_commit, bound_path)
                        if isinstance(bound_path, str)
                        else None
                    )
                    if blob is None:
                        errors.append(f"Pulp acceptance merge: {label} blob is unavailable")
                    elif hashlib.sha256(blob).hexdigest() != bound_digest:
                        errors.append(f"Pulp acceptance merge: {label} digest differs")
                    if isinstance(bound_path, str) and main_head is not None:
                        current_blob = git_blob(pulp_root, main_head, bound_path)
                        if current_blob != blob:
                            errors.append(
                                f"authoritative Pulp main: {label} differs from accepted blob"
                            )
                if main_head is not None:
                    current_projection = git_blob(
                        pulp_root, main_head, projection_path
                    )
                    if current_projection != projection_blob:
                        errors.append(
                            "authoritative Pulp main: routing projection differs from accepted blob"
                        )
            except ValueError as exc:
                errors.append(f"Pulp acceptance merge: invalid routing projection: {exc}")
    refresh_commit = (
        acceptance.get("refresh", {}).get("pulp_main_commit")
        if isinstance(acceptance, dict)
        and isinstance(acceptance.get("refresh"), dict)
        else None
    )
    if git_commit_tree(
        pulp_root, refresh_commit, "Pulp refreshed overlap audit commit", errors
    ) is not None:
        parents = git_output(pulp_root, "rev-list", "--parents", "-n", "1", pulp_commit)
        parent_fields = parents.decode().split() if parents is not None else []
        first_parent = parent_fields[1] if len(parent_fields) >= 2 else None
        if refresh_commit != first_parent:
            errors.append(
                "Pulp refreshed overlap audit commit: must be the acceptance merge first parent"
            )
    return errors


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


def validate_boundary_amendment(data: Any, matrix: Any) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "amendment_id", "proposal_id", "state",
        "proposed_at", "proposed_by", "authority_effect",
        "implementation_authority", "coordinates", "repository_roles",
        "exact_path_routes", "exact_path_expansions", "interim_maintenance",
        "open_overlap_audit", "gates",
    }
    if not exact_keys(data, top, "boundary_amendment", errors):
        return errors
    scalars = {
        "schema_version": 1,
        "kind": "full-design-import-render-exact-boundary-amendment",
        "amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
        "proposal_id": "full-design-import-render-v1",
        "state": "proposed",
        "proposed_at": EXPECTED_BOUNDARY_PROPOSED_AT,
        "proposed_by": "@danielraffel",
        "authority_effect": "none",
        "implementation_authority": "forbidden-until-exact-boundary-acknowledged",
    }
    for key, expected in scalars.items():
        if not exact_scalar(data[key], expected):
            errors.append(f"boundary_amendment.{key}: expected {expected!r}")
    if not OWNER.fullmatch(str(data["proposed_by"])):
        errors.append("boundary_amendment.proposed_by: expected individual GitHub handle")

    expected_coordinates = {
        "pulp_repository": "Generous-Corp/pulp",
        "pulp_audit_commit": "34f879e1a71aec8a34cea13f62600586d0eb79a7",
        "vellum_authority_repository": "Generous-Corp/vellum",
        "vellum_delivery_repository": "danielraffel/vellum",
        "vellum_matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
        "matrix_path": MATRIX_PATH.as_posix(),
        "matrix_sha256": EXPECTED_MATRIX_SHA256,
        "planning_repository": "danielraffel/pulp-planning",
        "planning_commit": "0455a500c3ca6645c69cf6cad3f600b4313594bf",
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
        errors.append("boundary_amendment.coordinates: differs from pinned matrix")
    elif not all(
        SHA40.fullmatch(coordinates[key])
        for key in (
            "pulp_audit_commit", "vellum_matrix_merge_commit", "planning_commit"
        )
    ):
        errors.append("boundary_amendment.coordinates: expected full commit SHAs")

    expected_roles = {
        "authority_repository": "Generous-Corp/vellum",
        "temporary_private_delivery_repository": "danielraffel/vellum",
        "delivery_repository_is_authority": False,
        "delivery_work_authorized_by_this_amendment": False,
        "required_exact_boundary_acknowledgement_id": (
            "full-design-import-render-v1-exact-boundary-acknowledgement-1"
        ),
        "transfer_or_exact_mirror_required_before_parity_release": True,
        "release_may_be_published_from_delivery_repository": False,
    }
    roles = data["repository_roles"]
    if (
        not isinstance(roles, dict)
        or set(roles) != set(expected_roles)
        or any(not exact_scalar(roles[key], expected) for key, expected in expected_roles.items())
    ):
        errors.append("boundary_amendment.repository_roles: differs from pinned roles")

    retained_ids = [
        "boundary.runtime-engine-selection",
        "boundary.non-macos-arm64-platform-adoption",
        "boundary.audio-dsp-harness",
        "boundary.pulp-cli-control-product-integration",
        "boundary.pulp-forge-product-integration",
    ]
    expected_routes = {
        "source": "compatibility-matrix-cells",
        "routed_cell_filter": "family-not-retained-boundary",
        "route_key": "repository-and-exact-path",
        "path_match": "exact-only-no-prefix-routing",
        "same_owner_duplicate_paths": "coalesced-with-all-cell-roles",
        "retained_boundary_path_fields": "semantic-evidence-not-path-routes",
        "framework_implementation_field": "vellum_future_implementation",
        "framework_proof_field": "vellum_future_proof",
        "pulp_counterpart_implementation_field": "pulp_implementation",
        "pulp_counterpart_proof_field": "pulp_proof",
        "framework_owner": "Generous-Corp/vellum",
        "pulp_counterpart_owner": "Generous-Corp/pulp",
        "pulp_counterpart_disposition": (
            "rollback-and-product-integration-until-authorized-consumption"
        ),
        "generic_change_origin": "Generous-Corp/vellum",
        "temporary_delivery_origin": "danielraffel/vellum",
        "unlisted_pulp_path_owner": "Generous-Corp/pulp",
        "duplicate_generic_implementation_in_pulp": "event-and-disposition-required",
        "retained_boundary_cells": retained_ids,
    }
    routes = data["exact_path_routes"]
    if (
        not isinstance(routes, dict)
        or set(routes) != set(expected_routes)
        or any(not exact_scalar(routes[key], expected) for key, expected in expected_routes.items())
    ):
        errors.append("boundary_amendment.exact_path_routes: differs from frozen routes")

    expected_expansions = [{
        "cell_id": "render.text-runs-fonts-fallback",
        "matrix_path": "runtime/assets/fonts",
        "repository": "Generous-Corp/vellum",
        "role": "vellum_future_implementation",
        "exact_paths": [
            "runtime/assets/fonts/Inter-Regular.ttf",
            "runtime/assets/fonts/Jost-Bold.ttf",
            "runtime/assets/fonts/Jost-Medium.ttf",
            "runtime/assets/fonts/Jost-Regular.ttf",
            "runtime/assets/fonts/Jost-SemiBold.ttf",
            "runtime/assets/fonts/NotoSansArabic-Variable.ttf",
            "runtime/assets/fonts/NotoSansJP-Variable.ttf",
            "runtime/assets/fonts/README.md",
        ],
    }]
    if data["exact_path_expansions"] != expected_expansions:
        errors.append("boundary_amendment.exact_path_expansions: differs from pinned files")
    expansion_keys = {
        (row["cell_id"], row["role"], row["matrix_path"]): row["exact_paths"]
        for row in expected_expansions
    }
    for index, path in enumerate(expected_expansions[0]["exact_paths"]):
        exact_route_path(path, f"boundary_amendment.exact_path_expansions[0].exact_paths[{index}]", errors)

    matrix_cells = matrix.get("cells") if isinstance(matrix, dict) else None
    if not isinstance(matrix_cells, list):
        errors.append("boundary_amendment: compatibility matrix cells unavailable")
    else:
        actual_retained = [
            cell.get("id") for cell in matrix_cells
            if isinstance(cell, dict) and cell.get("family") == "retained-boundary"
        ]
        if actual_retained != retained_ids:
            errors.append("boundary_amendment: retained routes differ from matrix")
        route_index: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for index, cell in enumerate(matrix_cells):
            if not isinstance(cell, dict):
                continue
            if cell.get("family") == "retained-boundary":
                continue
            field_owners = {
                "vellum_future_implementation": "Generous-Corp/vellum",
                "vellum_future_proof": "Generous-Corp/vellum",
                "pulp_implementation": "Generous-Corp/pulp",
                "pulp_proof": "Generous-Corp/pulp",
            }
            for field, repository in field_owners.items():
                paths = cell.get(field)
                if not isinstance(paths, list):
                    errors.append(f"boundary_amendment.matrix.cells[{index}].{field}: unavailable")
                    continue
                for path in paths:
                    if not isinstance(path, str):
                        exact_route_path(
                            path,
                            f"boundary_amendment.matrix.cells[{index}].{field}",
                            errors,
                        )
                        continue
                    expansion = expansion_keys.get(
                        (str(cell.get("id")), field, path)
                    )
                    if expansion is not None:
                        for expanded_path in expansion:
                            route_index.setdefault(
                                (repository, expanded_path), set()
                            ).add((str(cell.get("id")), field))
                        continue
                    exact_route_path(
                        path,
                        f"boundary_amendment.matrix.cells[{index}].{field}",
                        errors,
                    )
                    if (
                        isinstance(path, str)
                        and "." not in path.rsplit("/", 1)[-1]
                        and path != "cli/vellum"
                    ):
                        errors.append(
                            f"boundary_amendment.matrix.cells[{index}].{field}: "
                            "directory-shaped path requires exact expansion"
                        )
                    if isinstance(path, str):
                        route_index.setdefault((repository, path), set()).add(
                            (str(cell.get("id")), field)
                        )

    expected_maintenance = [{
        "id": "capture-primitives-unimplemented-in-vellum",
        "pulp_paths": EXPECTED_MAINTENANCE_PATHS,
        "disposition": (
            "prospective-ordinary-pulp-maintenance-after-exact-boundary-acknowledgement"
        ),
        "authorized_by_this_amendment": False,
        "expires_at_gate": "5A-P.3-independent-pixel-and-semantic-proof",
    }]
    if data["interim_maintenance"] != expected_maintenance:
        errors.append("boundary_amendment.interim_maintenance: differs from pinned exception")

    expected_overlap = {
        "audited_at": EXPECTED_BOUNDARY_PROPOSED_AT,
        "evidence_mode": "proposal-snapshot-requires-counterpart-refresh",
        "pulp_acceptance_must_refresh_open_prs": True,
        "vellum_acknowledgement_must_bind_refreshed_acceptance": True,
        "pulp_main_commit": "34f879e1a71aec8a34cea13f62600586d0eb79a7",
        "vellum_main_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
        "rows": [
            {
                "repository": "Generous-Corp/pulp", "pull_request": 7398,
                "head_commit": "c4219fa137c206d9d221723f078bc2b3d741794a",
                "paths": ["tools/import-design/browser_capture/settle.test.mjs"],
                "disposition": "pulp-counterpart-test-do-not-duplicate",
            },
            {
                "repository": "Generous-Corp/pulp", "pull_request": 7399,
                "head_commit": "408d028d6fc88ef6fa6f5702edf1a49473ce0939",
                "paths": [
                    "core/view/include/pulp/view/visualization_bridge.hpp",
                    "core/view/src/visualization_bridge.cpp",
                    "test/test_visualization.cpp",
                ],
                "disposition": "pulp-product-runtime-retained",
            },
            {
                "repository": "Generous-Corp/pulp", "pull_request": 7386,
                "head_commit": "8ddb714f191f32d21e91795ac0e0a0e09efa36b1",
                "paths": [
                    "core/view/include/pulp/view/motion.hpp",
                    "core/view/src/motion.cpp", "core/view/src/view.cpp",
                ],
                "disposition": "pulp-product-runtime-retained",
            },
            {
                "repository": "Generous-Corp/pulp", "pull_request": 7219,
                "head_commit": "e9a2b604aec11a3b45edaaef56e2e40f03218329",
                "paths": [
                    "core/view/include/pulp/view/script_inspector_bridge.hpp",
                    "core/view/include/pulp/view/scripted_ui.hpp",
                    "core/view/src/script_inspector_bridge.cpp",
                    "core/view/src/scripted_ui.cpp",
                ],
                "disposition": "pulp-engine-and-inspector-integration-retained",
            },
        ],
        "open_vellum_overlap_count": 0,
    }
    if data["open_overlap_audit"] != expected_overlap:
        errors.append("boundary_amendment.open_overlap_audit: differs from pinned audit")

    expected_gates = {
        "amendment_may_transfer_authority": False,
        "source_work_before_exact_boundary_acknowledgement": False,
        "personal_delivery_repository_may_become_authority_implicitly": False,
        "transfer_or_exact_mirror_before_parity_release": True,
        "authority_promotion_attestation_required_for_release": True,
        "matrix_release_readiness_still_required": True,
        "pulp_consumption_authorized": False,
    }
    gates = data["gates"]
    if (
        not isinstance(gates, dict)
        or set(gates) != set(expected_gates)
        or any(not exact_scalar(gates[key], expected) for key, expected in expected_gates.items())
    ):
        errors.append("boundary_amendment.gates: differs from fail-closed pinned gates")
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


def validate_parity_completion(
    root: Path, data: Any, matrix: Any, amendment: Any,
    *, release_source_commit: str | None = None, check_runs: Any = None,
) -> list[str]:
    errors: list[str] = []
    top = {
        "schema_version", "kind", "completion_id", "state", "completed_at",
        "completed_by", "matrix_id", "matrix_merge_commit", "matrix_sha256",
        "required_check_runs", "cells",
    }
    if not exact_keys(data, top, "parity_completion", errors):
        return errors
    expected = {
        "schema_version": 1,
        "kind": "full-design-import-render-parity-completion",
        "completion_id": "full-design-import-render-v1-parity-completion-1",
        "state": "complete",
        "completed_by": "@danielraffel",
        "matrix_id": "full-design-import-render-v1-compatibility-matrix",
        "matrix_merge_commit": "bbe187d581f3f021a25b3ebd01332f89bbde142e",
        "matrix_sha256": EXPECTED_MATRIX_SHA256,
    }
    for key, value in expected.items():
        if not exact_scalar(data.get(key), value):
            errors.append(f"parity_completion.{key}: drift")
    if data.get("required_check_runs") != REQUIRED_PARITY_CHECKS:
        errors.append("parity_completion.required_check_runs: differs from closed check set")
    try:
        stamp = data["completed_at"]
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not stamp.endswith("Z") or parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append("parity_completion.completed_at: expected UTC timestamp")
    matrix_cells = matrix.get("cells") if isinstance(matrix, dict) else None
    if not isinstance(matrix_cells, list):
        return errors + ["parity_completion: matrix cells unavailable"]
    required = {
        str(cell["id"]): cell
        for cell in matrix_cells
        if isinstance(cell, dict) and cell.get("status") != cell.get("target_status")
    }
    rows = data.get("cells")
    if not isinstance(rows, list):
        return errors + ["parity_completion.cells: expected array"]
    route_rows = exact_route_rows(matrix, amendment)
    expected_paths: dict[tuple[str, str], list[str]] = {}
    for cell_id in required:
        for role, output_key in (
            ("vellum_future_implementation", "implementation_paths"),
            ("vellum_future_proof", "proof_paths"),
        ):
            expected_paths[(cell_id, output_key)] = sorted(
                route["path"]
                for route in route_rows
                if route["repository"] == "Generous-Corp/vellum"
                and any(
                    item == {"cell_id": cell_id, "role": role}
                    for item in route["cell_roles"]
                )
            )
    seen: set[str] = set()
    row_keys = {
        "cell_id", "achieved_status", "implementation_paths", "proof_paths",
        "required_checks", "proof_executions",
    }
    for index, row in enumerate(rows):
        where = f"parity_completion.cells[{index}]"
        if not exact_keys(row, row_keys, where, errors):
            continue
        cell_id = row["cell_id"]
        if not isinstance(cell_id, str) or cell_id not in required or cell_id in seen:
            errors.append(f"{where}.cell_id: unexpected or duplicate")
            continue
        seen.add(cell_id)
        if row["achieved_status"] != required[cell_id].get("target_status"):
            errors.append(f"{where}.achieved_status: target not reached")
        for key in ("implementation_paths", "proof_paths"):
            if row[key] != expected_paths[(cell_id, key)]:
                errors.append(f"{where}.{key}: differs from frozen exact routes")
            for path in row[key] if isinstance(row[key], list) else []:
                if release_source_commit is not None and not git_regular_blob(
                    root, release_source_commit, path
                ):
                    errors.append(
                        f"{where}.{key}: tagged release commit lacks regular blob {path}"
                    )
        expected_check_names = [item["name"] for item in REQUIRED_PARITY_CHECKS]
        if row["required_checks"] != expected_check_names:
            errors.append(f"{where}.required_checks: differs from closed check set")
        executions = row.get("proof_executions")
        if not isinstance(executions, list):
            errors.append(f"{where}.proof_executions: expected array")
        else:
            execution_paths: set[str] = set()
            for execution_index, execution in enumerate(executions):
                execution_where = f"{where}.proof_executions[{execution_index}]"
                execution_keys = {"path", "sha256", "check", "test_id", "runner"}
                if not exact_keys(execution, execution_keys, execution_where, errors):
                    continue
                path = execution.get("path")
                if path not in row.get("proof_paths", []) or path in execution_paths:
                    errors.append(f"{execution_where}.path: unexpected or duplicate")
                elif isinstance(path, str):
                    execution_paths.add(path)
                    blob = (
                        git_blob(root, release_source_commit, path)
                        if release_source_commit is not None
                        else (root / path).read_bytes() if (root / path).is_file() else None
                    )
                    if blob is None or hashlib.sha256(blob).hexdigest() != execution.get(
                        "sha256"
                    ):
                        errors.append(f"{execution_where}.sha256: proof digest differs")
                if execution.get("check") != "gpu-macos-arm64":
                    errors.append(f"{execution_where}.check: must use proof-execution lane")
                if not isinstance(execution.get("test_id"), str) or not execution[
                    "test_id"
                ]:
                    errors.append(f"{execution_where}.test_id: expected non-empty string")
                suffix = Path(path).suffix if isinstance(path, str) else ""
                expected_runner = {
                    ".py": "python-file",
                    ".js": "node-test-file",
                    ".mjs": "node-test-file",
                    ".cpp": "ctest-case",
                    ".zip": "fixture-consumer",
                }.get(suffix)
                if path in ARGUMENT_DRIVEN_PROOF_TEST_IDS:
                    expected_runner = "ctest-case"
                if execution.get("runner") != expected_runner:
                    errors.append(f"{execution_where}.runner: differs from proof type")
                if suffix == ".cpp" and execution.get("test_id") != CPP_PROOF_TEST_IDS.get(path):
                    errors.append(f"{execution_where}.test_id: differs from closed CTest mapping")
                if path in ARGUMENT_DRIVEN_PROOF_TEST_IDS and execution.get(
                    "test_id"
                ) != ARGUMENT_DRIVEN_PROOF_TEST_IDS[path]:
                    errors.append(
                        f"{execution_where}.test_id: differs from argument-driven CTest mapping"
                    )
                if suffix == ".zip" and execution.get("test_id") != FIXTURE_PROOF_CONSUMERS.get(path):
                    errors.append(f"{execution_where}.test_id: differs from closed fixture consumer")
            if execution_paths != set(row.get("proof_paths", [])):
                errors.append(f"{where}.proof_executions: must cover every proof path once")
    if seen != set(required):
        errors.append(
            "parity_completion.cells: required cell IDs differ; "
            f"missing={sorted(set(required) - seen)} unexpected={sorted(seen - set(required))}"
        )
    if check_runs is not None:
        if (
            not isinstance(check_runs, dict)
            or not isinstance(check_runs.get("check_runs"), list)
            or not isinstance(check_runs.get("workflow_runs"), list)
        ):
            errors.append(
                "parity_completion check evidence: expected check_runs and workflow_runs arrays"
            )
        elif release_source_commit is None:
            errors.append("parity_completion check evidence: release source commit unavailable")
        else:
            bound_runs: dict[str, set[int]] = {}
            for required_check in REQUIRED_PARITY_CHECKS:
                matches = [
                    row for row in check_runs["check_runs"]
                    if isinstance(row, dict)
                    and row.get("name") == required_check["name"]
                    and row.get("head_sha") == release_source_commit
                    and row.get("conclusion") == "success"
                    and isinstance(row.get("app"), dict)
                    and row["app"].get("id") == required_check["app_id"]
                ]
                bound = False
                for match in matches:
                    details_url = match.get("details_url")
                    run_match = (
                        re.fullmatch(
                            r"https://github\.com/Generous-Corp/vellum/actions/runs/([0-9]+)/job/[0-9]+",
                            details_url,
                        )
                        if isinstance(details_url, str)
                        else None
                    )
                    if run_match is None:
                        continue
                    run_id = int(run_match.group(1))
                    if any(
                        isinstance(run, dict)
                        and run.get("id") == run_id
                        and run.get("path") == required_check["workflow_path"]
                        and run.get("event") == "push"
                        and run.get("head_branch") == "main"
                        and run.get("head_sha") == release_source_commit
                        and run.get("status") == "completed"
                        and run.get("conclusion") == "success"
                        for run in check_runs["workflow_runs"]
                    ):
                        bound = True
                        bound_runs.setdefault(required_check["name"], set()).add(run_id)
                if not bound:
                    errors.append(
                        "parity_completion check evidence: missing exact workflow-bound push-main "
                        + required_check["name"]
                    )
            receipts = check_runs.get("parity_proof_execution_receipts")
            if not isinstance(receipts, list):
                errors.append("parity_completion proof evidence: receipts unavailable")
            else:
                receipt_proofs: set[tuple[str, str, str, str, str]] = set()
                for receipt in receipts:
                    if not isinstance(receipt, dict):
                        continue
                    check_name = receipt.get("check_name")
                    required = next(
                        (item for item in REQUIRED_PARITY_CHECKS if item["name"] == check_name),
                        None,
                    )
                    expected_receipt = {
                        "schema_version": 1,
                        "kind": "vellum-parity-proof-execution",
                        "repository": "Generous-Corp/vellum",
                        "head_sha": release_source_commit,
                        "check_name": check_name,
                        "workflow_path": required.get("workflow_path") if required else None,
                        "status": "pass",
                    }
                    if (
                        required is None
                        or set(receipt) != set(expected_receipt) | {"proofs"}
                        | {"run_id"}
                        or receipt.get("run_id") not in bound_runs.get(check_name, set())
                        or any(
                            not exact_scalar(receipt.get(key), value)
                            for key, value in expected_receipt.items()
                        )
                        or not isinstance(receipt.get("proofs"), list)
                    ):
                        continue
                    for proof in receipt["proofs"]:
                        if (
                            isinstance(proof, dict)
                            and set(proof) == {
                                "path", "sha256", "test_id", "runner", "status"
                            }
                            and proof.get("status") == "pass"
                            and all(
                                isinstance(proof.get(key), str)
                                for key in ("path", "sha256", "test_id", "runner")
                            )
                        ):
                            receipt_proofs.add(
                                (
                                    check_name, proof.get("path"), proof.get("sha256"),
                                    proof.get("test_id"), proof.get("runner"),
                                )
                            )
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for execution in row.get("proof_executions", []):
                        if not isinstance(execution, dict) or not all(
                            isinstance(execution.get(key), str)
                            for key in ("check", "path", "sha256", "test_id", "runner")
                        ):
                            continue
                        key = (
                            execution.get("check"), execution.get("path"),
                            execution.get("sha256"), execution.get("test_id"),
                            execution.get("runner"),
                        )
                        if key not in receipt_proofs:
                            errors.append(
                                "parity_completion proof evidence: missing executed proof "
                                + str(execution.get("path"))
                            )
    return errors


def validate_release_readiness(
    root: Path,
    data: Any,
    amendment: Any,
    *,
    promotion_attestation: Path | None = None,
    delivery_root: Path | None = None,
    repository: str | None = None,
    release_source_commit: str | None = None,
    check_runs: Any = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        return ["release readiness: compatibility matrix cells are unavailable"]
    if check_runs is None:
        errors.append("release readiness: missing exact workflow-run evidence")
    completion_blob = (
        git_blob(root, release_source_commit, PARITY_COMPLETION_PATH.as_posix())
        if release_source_commit is not None
        and git_regular_blob(root, release_source_commit, PARITY_COMPLETION_PATH.as_posix())
        else None
    )
    if completion_blob is None:
        errors.append("release readiness: missing versioned parity completion evidence")
    else:
        try:
            completion_data = load_json_bytes(
                completion_blob, "tagged parity completion"
            )
            errors.extend(
                "release readiness: " + error
                for error in validate_parity_completion(
                    root, completion_data, data, amendment,
                    release_source_commit=release_source_commit,
                    check_runs=check_runs,
                )
            )
        except ValueError as exc:
            errors.append(f"release readiness: invalid parity completion evidence: {exc}")
    promotion_required = (
        isinstance(amendment, dict)
        and isinstance(amendment.get("gates"), dict)
        and amendment["gates"].get(
            "authority_promotion_attestation_required_for_release"
        ) is True
    )
    if not promotion_required:
        errors.append(
            "release readiness: exact-boundary amendment does not require authority promotion"
        )
    if promotion_attestation is None or not promotion_attestation.is_file():
        errors.append(
            "release readiness: missing signed-tag authority promotion attestation"
        )
    else:
        try:
            promotion_data = load_json(promotion_attestation)
            errors.extend(
                "release readiness: " + error
                for error in validate_authority_promotion_attestation(
                    promotion_data, amendment
                )
            )
            errors.extend(
                "release readiness: " + error
                for error in validate_promotion_acknowledgement_digest(
                    root, promotion_data
                )
            )
            errors.extend(
                "release readiness: " + error
                for error in validate_promotion_completion_digest(
                    root, promotion_data, release_source_commit
                )
            )
            errors.extend(
                "release readiness: " + error
                for error in validate_promotion_repository_evidence(
                    root,
                    delivery_root,
                    promotion_data,
                    repository=repository,
                    release_source_commit=release_source_commit,
                )
            )
        except ValueError as exc:
            errors.append(f"release readiness: invalid authority promotion attestation: {exc}")
    return errors


def verify(
    root: Path,
    *,
    repository_checks: bool = True,
    release_readiness: bool = False,
    pulp_root: Path | None = None,
    promotion_attestation: Path | None = None,
    delivery_root: Path | None = None,
    repository: str | None = None,
    release_source_commit: str | None = None,
    check_runs: Any = None,
    open_pr_snapshot: Any = None,
    defer_live_open_pr_audit: bool = False,
) -> dict[str, Any]:
    closure_errors = []
    try:
        actual_files, symlinks = expansion_files(root)
        if not EXPECTED_FILES.issubset(actual_files) or not actual_files.issubset(
            EXPECTED_FILES | OPTIONAL_FUTURE_FILES
        ):
            closure_errors.append(
                "expansion artifact set differs; "
                f"missing={sorted(EXPECTED_FILES - actual_files)} "
                f"unexpected={sorted(actual_files - EXPECTED_FILES - OPTIONAL_FUTURE_FILES)}"
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
    boundary_amendment = root / BOUNDARY_AMENDMENT_PATH
    try:
        boundary_amendment_sha256 = hashlib.sha256(
            boundary_amendment.read_bytes()
        ).hexdigest()
        if boundary_amendment_sha256 != EXPECTED_BOUNDARY_AMENDMENT_SHA256:
            closure_errors.append("expansion exact-boundary amendment differs from pinned SHA-256")
    except OSError as exc:
        closure_errors.append(f"cannot read expansion exact-boundary amendment: {exc}")
    try:
        data = load_json(proposal)
        addendum_data = load_json(addendum)
        acknowledgement_data = load_json(acknowledgement)
        matrix_data = load_json(matrix)
        boundary_amendment_data = load_json(boundary_amendment)
        promotion_errors = []
        exact_acknowledgement_path = (
            root
            / EXPANSIONS_ROOT
            / "full-design-import-render-v1/exact-boundary-acknowledgement-1.json"
        )
        if exact_acknowledgement_path.is_file():
            exact_acknowledgement_data = load_json(exact_acknowledgement_path)
            promotion_errors += validate_exact_boundary_acknowledgement(
                exact_acknowledgement_data
            )
            promotion_errors += validate_exact_boundary_repository_evidence(
                root,
                pulp_root,
                exact_acknowledgement_data,
                boundary_amendment_data,
                check_runs,
                open_pr_snapshot,
                defer_live_open_pr_audit,
            )
        parity_completion_path = root / PARITY_COMPLETION_PATH
        if parity_completion_path.is_file():
            parity_completion_data = load_json(parity_completion_path)
            promotion_errors += validate_parity_completion(
                root, parity_completion_data, matrix_data, boundary_amendment_data
            )
        errors = (
            closure_errors
            + validate(data)
            + validate_addendum(addendum_data)
            + validate_acknowledgement(acknowledgement_data)
            + validate_matrix(matrix_data)
            + validate_boundary_amendment(boundary_amendment_data, matrix_data)
            + promotion_errors
            + (
                validate_matrix_repository_paths(root, matrix_data)
                if repository_checks
                else []
            )
            + (
                validate_release_readiness(
                    root,
                    matrix_data,
                    boundary_amendment_data,
                    promotion_attestation=promotion_attestation,
                    delivery_root=delivery_root,
                    repository=repository,
                    release_source_commit=release_source_commit,
                    check_runs=check_runs,
                )
                if release_readiness
                else []
            )
        )
        if release_readiness and defer_live_open_pr_audit:
            errors.append(
                "release readiness: live open-PR audit cannot be deferred"
            )
    except ValueError as exc:
        data = addendum_data = acknowledgement_data = matrix_data = None
        boundary_amendment_data = None
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
        "exact_boundary_amendment": BOUNDARY_AMENDMENT_PATH.as_posix(),
        "exact_boundary_amendment_id": (
            boundary_amendment_data.get("amendment_id")
            if passed and isinstance(boundary_amendment_data, dict)
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
    parser.add_argument("--pulp-root", type=Path)
    parser.add_argument("--promotion-attestation", type=Path)
    parser.add_argument("--delivery-root", type=Path)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument(
        "--release-source-commit", default=os.environ.get("GITHUB_SHA")
    )
    parser.add_argument("--check-runs-json", type=Path)
    parser.add_argument("--open-pr-snapshot-json", type=Path)
    parser.add_argument("--defer-live-open-pr-audit", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release-readiness", action="store_true")
    args = parser.parse_args()
    report = verify(
        args.root.resolve(),
        release_readiness=args.release_readiness,
        pulp_root=args.pulp_root.resolve() if args.pulp_root else None,
        promotion_attestation=(
            args.promotion_attestation.resolve() if args.promotion_attestation else None
        ),
        delivery_root=args.delivery_root.resolve() if args.delivery_root else None,
        repository=args.repository,
        release_source_commit=args.release_source_commit,
        check_runs=(load_json(args.check_runs_json.resolve()) if args.check_runs_json else None),
        open_pr_snapshot=(
            load_json(args.open_pr_snapshot_json.resolve())
            if args.open_pr_snapshot_json else None
        ),
        defer_live_open_pr_audit=args.defer_live_open_pr_audit,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
