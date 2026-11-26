#!/usr/bin/env python3
"""Self-tests for the source-contract reference checks: parser files and symbols,
dispatch labels, fixtures and evidence, test references, journeys, landed
pending items and label coverage."""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

CHECKER_PATH = Path(__file__).with_name("check_source_contracts.py")
SPEC = importlib.util.spec_from_file_location("check_source_contracts_refs", CHECKER_PATH)
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

ROOT = checker.REPO_ROOT
REGISTRY = json.loads(checker.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
TODAY = dt.date(2026, 8, 12)


def contract(registry: dict, source: str) -> dict:
    return next(entry for entry in registry["contracts"] if entry["source"] == source)


def codes(registry: dict) -> set[str]:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "source-contracts.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        findings = checker.compute_findings(root=ROOT, registry_path=path, today=TODAY)
    return {finding.code for finding in findings if finding.code != "stale-source-contract"}


class SourceContractReferenceTests(unittest.TestCase):
    def mutate(self, source: str) -> tuple[dict, dict]:
        registry = copy.deepcopy(REGISTRY)
        return registry, contract(registry, source)

    def assert_code(self, registry: dict, code: str) -> None:
        self.assertIn(code, codes(registry))

    def test_real_registry_references_resolve(self) -> None:
        found = codes(REGISTRY)
        self.assertEqual(found - {"source-contract-recheck-due", "pending-item-landed"}, set())

    def test_parser_files_symbols_and_labels_must_resolve(self) -> None:
        registry, entry = self.mutate("stitch")
        entry["parser"]["file"] = "framework/core/view/src/missing.cpp"
        self.assert_code(registry, "missing-parser-file")
        registry, entry = self.mutate("stitch")
        entry["parser"]["static"] = "parse_stitch_html_missing"
        self.assert_code(registry, "missing-parser-symbol")
        registry, entry = self.mutate("stitch")
        entry["dispatch"]["label"] = "stitch-missing"
        self.assert_code(registry, "missing-design-source-label")
        registry, entry = self.mutate("rn")
        entry["dispatch"]["parser"] = "parse_react_native_missing"
        self.assert_code(registry, "missing-explicit-runtime-parser")

    def test_fixtures_expected_files_and_evidence_must_exist(self) -> None:
        registry, entry = self.mutate("claude")
        entry["validation"]["fixtures"].append("fixtures/source-detection/missing.html")
        self.assert_code(registry, "missing-fixture")
        registry, entry = self.mutate("stitch")
        entry["format_versions"][0]["expected_paths"] = ["fixtures/source-detection/none.json"]
        self.assert_code(registry, "missing-expected-file")
        registry, entry = self.mutate("claude")
        entry["upstream"][0]["url"] = "fixtures/source-detection/claude/missing.html"
        self.assert_code(registry, "missing-evidence")
        registry, entry = self.mutate("claude")
        entry["validation"]["render_reference"] = {"status": "present", "path": "fixtures/none.png"}
        self.assert_code(registry, "missing-reference-screenshot")

    def test_test_references_must_resolve_by_kind(self) -> None:
        for reference in ("ctest:vellum.no-such-test", "lane:cli/tests/test_missing.py",
                          "lane:scripts/check_source_contracts.py",
                          "package:packages/vellum-design-ir/test/missing.test.js",
                          "catch2:[view][import]"):
            with self.subTest(reference=reference):
                registry, entry = self.mutate("claude")
                entry["validation"]["tests"].append(reference)
                self.assert_code(registry, "missing-test")
        ctests = checker.ctest_names(ROOT)
        self.assertIn("vellum.source-contracts", ctests)
        self.assertIn("vellum.framework.design-import-jsx-runtime", ctests)
        self.assertTrue(checker.test_resolves(ROOT, "lane:cli/tests/test_import_backend.py", ctests))
        self.assertTrue(checker.test_resolves(
            ROOT, "package:packages/vellum-design-ir/test/figma-plugin.test.js", ctests))

    def test_a_row_without_tests_needs_an_open_item(self) -> None:
        registry, entry = self.mutate("claude")
        entry["validation"]["tests"] = []
        self.assert_code(registry, "missing-tests")
        registry, entry = self.mutate("claude")
        entry["validation"]["tests"] = []
        entry["pending"].append({"check": "tests", "item": "x", "reason": "r", "pr": None})
        self.assertNotIn("missing-tests", codes(registry))

    def test_journeys_must_resolve_and_match_the_manifest(self) -> None:
        registry, entry = self.mutate("designmd")
        entry["validation"]["journey"] = "scripts/validate_installed_sdk.py::validate_missing"
        self.assert_code(registry, "missing-journey")
        registry, entry = self.mutate("designmd")
        entry["validation"]["journey"] = (
            "scripts/validate_installed_sdk.py::validate_installed_source_imports[nope]")
        self.assert_code(registry, "missing-journey")
        registry, entry = self.mutate("pencil")
        entry["validation"]["journey"] = None
        self.assert_code(registry, "missing-journey")
        findings: list = []
        entry = copy.deepcopy(contract(REGISTRY, "designmd"))
        checker._check_references(
            findings, ROOT, entry, checker._design_source_labels(ROOT), checker.ctest_names(ROOT),
            {"designmd": "scripts/validate_installed_sdk.py::validate_installed_source_imports[pencil]"})
        self.assertIn("journey-drift", {finding.code for finding in findings})

    def test_a_landed_pending_item_is_reported(self) -> None:
        registry, entry = self.mutate("rn")
        entry["pending"].append({"check": "tests", "item": "ctest:vellum.design-pipeline",
                                 "reason": "already there", "pr": None})
        self.assert_code(registry, "pending-item-landed")

    def test_every_dispatch_label_has_a_row(self) -> None:
        registry = copy.deepcopy(REGISTRY)
        registry["contracts"] = [entry for entry in registry["contracts"] if entry["source"] != "pencil"]
        self.assert_code(registry, "missing-source-row")
        self.assertIn("pencil", checker._design_source_labels(ROOT))


if __name__ == "__main__":
    unittest.main()
