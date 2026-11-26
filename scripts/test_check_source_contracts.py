#!/usr/bin/env python3
"""Self-tests for the source-contract registry checker (schema, shape, open items,
detection foreign keys, surface refs and recheck cadence)."""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECKER_PATH = Path(__file__).with_name("check_source_contracts.py")
SPEC = importlib.util.spec_from_file_location("check_source_contracts", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

ROOT = checker.REPO_ROOT
REGISTRY_PATH = checker.DEFAULT_REGISTRY
TODAY = dt.date(2026, 8, 12)
# Subprocess runs of the checker pin the clock so date literals in a test never expire.
FIXED_TODAY = "2026-11-12"


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _write_registry(tmp: Path, registry: dict) -> Path:
    path = tmp / "source-contracts.json"
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return path


def _contract(registry: dict, source: str) -> dict:
    for entry in registry["contracts"]:
        if entry["source"] == source:
            return entry
    raise AssertionError(f"missing source: {source}")


def _findings(registry: dict, tmp: Path) -> list:
    path = _write_registry(tmp, registry)
    return checker.compute_findings(root=ROOT, registry_path=path, today=TODAY)


def _codes(findings: list) -> set[str]:
    return {finding.code for finding in findings}


def _sources_for(findings: list, code: str) -> set[str]:
    return {finding.source for finding in findings if finding.code == code}


class SourceContractsTest(unittest.TestCase):
    def mutate(self, source: str) -> tuple[dict, dict]:
        registry = copy.deepcopy(_load_registry())
        return registry, _contract(registry, source)

    def assert_has_code(self, registry: dict, code: str) -> None:
        with tempfile.TemporaryDirectory() as td:
            findings = _findings(registry, Path(td))
        self.assertIn(code, _codes(findings), [str(f) for f in findings])

    def test_real_registry_has_no_warning_or_error_findings(self) -> None:
        findings = checker.compute_findings(root=ROOT, registry_path=REGISTRY_PATH, today=TODAY)
        warning_pairs = {
            (finding.source, finding.code)
            for finding in findings
            if finding.severity in {"warning", "error"}
        }
        self.assertEqual(warning_pairs, set(), [str(f) for f in findings])

    def test_schema_version_is_required(self) -> None:
        registry = _load_registry()
        registry.pop("registry-schema-version")
        self.assert_has_code(registry, "missing-schema-version")

    def test_kind_and_dispatch_enums_are_checked(self) -> None:
        registry, entry = self.mutate("figma")
        entry["kind"] = "unknown-kind"
        entry["dispatch"]["kind"] = "unknown-dispatch"
        with tempfile.TemporaryDirectory() as td:
            codes = _codes(_findings(registry, Path(td)))
        self.assertIn("invalid-kind", codes)
        self.assertIn("invalid-dispatch-kind", codes)

    def test_stale_source_contract_warns(self) -> None:
        registry, entry = self.mutate("stitch")
        entry["last_verified"] = "2020-01-01"
        entry["recheck_interval_days"] = 1
        with tempfile.TemporaryDirectory() as td:
            findings = _findings(registry, Path(td))
        stale = [finding for finding in findings if finding.code == "stale-source-contract"]
        self.assertEqual(len(stale), 1, [str(f) for f in findings])
        self.assertEqual(stale[0].severity, "info")

    def test_unverified_source_contract_warns(self) -> None:
        registry, entry = self.mutate("figma")
        entry["last_verified"] = "unverified"
        entry.pop("recheck_interval_days", None)
        self.assert_has_code(registry, "unverified-source")

    def test_compat_source_and_format_foreign_keys_are_checked(self) -> None:
        registry, entry = self.mutate("stitch")
        entry["compat"]["source"] = "missing-source"
        self.assert_has_code(registry, "missing-compat-source")

        registry, entry = self.mutate("stitch")
        entry["format_versions"][0]["version"] = "2099.01"
        self.assert_has_code(registry, "missing-compat-format")

    def test_runtime_surface_refs_have_known_owners(self) -> None:
        registry, entry = self.mutate("figma")
        entry["runtime_requirements"]["surface_refs"].append("not-a-known-owner")
        self.assert_has_code(registry, "invalid-surface-ref")

    def test_rn_not_applicable_compat_is_exempt(self) -> None:
        findings = checker.compute_findings(root=ROOT, registry_path=REGISTRY_PATH, today=TODAY)
        rn_codes = {finding.code for finding in findings if finding.source == "rn"}
        self.assertNotIn("missing-compat-source", rn_codes)
        self.assertNotIn("missing-compat-format", rn_codes)

    def test_shape_checker_reports_invalid_modes_and_missing_fields(self) -> None:
        findings: list = []
        checker._check_entry_shape(
            findings,
            {
                "source": 123,
                "kind": "bad-kind",
                "trust": "bad-trust",
                "compat": {"mode": "bad-mode"},
                "dispatch": {"kind": "bad-dispatch"},
                "validation": {"render_reference": {"status": "bad-status"}},
            },
        )
        codes = _codes(findings)
        self.assertEqual(len(findings), 14)
        self.assertIn("missing-required-field", codes)
        self.assertIn("invalid-kind", codes)
        self.assertIn("invalid-trust", codes)
        self.assertIn("invalid-compat-mode", codes)
        self.assertIn("invalid-dispatch-kind", codes)
        self.assertIn("invalid-reference-status", codes)
        self.assertEqual({finding.source for finding in findings}, {"<unknown>"})

    def test_cadence_checker_covers_manual_future_invalid_and_overdue(self) -> None:
        today = dt.date(2026, 5, 22)
        findings: list = []
        checker._check_cadence(findings, {"source": "manual", "last_verified": "2020-01-01", "recheck_interval": "manual"}, today)
        checker._check_cadence(findings, {"source": "future", "last_verified": "2026-05-20", "recheck_interval_days": 10}, today)
        checker._check_cadence(findings, {"source": "missing-date", "recheck_interval_days": 10}, today)
        checker._check_cadence(findings, {"source": "bad-date", "last_verified": "nope", "recheck_interval_days": 10}, today)
        checker._check_cadence(findings, {"source": "old", "last_verified": "2026-01-01", "recheck_interval_days": 1}, today)
        self.assertEqual(
            [(finding.source, finding.code) for finding in findings],
            [
                ("missing-date", "invalid-last-verified"),
                ("bad-date", "invalid-last-verified"),
                ("old", "stale-source-contract"),
            ],
        )

    def test_recheck_requires_a_verification_note_naming_the_date(self) -> None:
        today = dt.date(2026, 11, 12)
        base = {"recheck_interval_days": 90, "last_verified": "2026-11-11"}
        cases = {
            "no-note": {},
            "empty-note": {"verification_note": "  "},
            "other-date": {"verification_note": "2026-11-09: parse_x (3 cases) passed offline"},
            "bare-date": {"verification_note": "2026-11-11"},
            "evidenced": {"verification_note": "2026-11-11: parse_x (3 cases) passed offline"},
        }
        findings: list = []
        for name, extra in cases.items():
            checker._check_cadence(findings, {"source": name, **base, **extra}, today)
        # Before the rule's start date a note is not required: older rechecks stay as they were.
        checker._check_cadence(findings, {"source": "pre-rule", "recheck_interval_days": 90, "last_verified": "2026-11-01"}, today)
        self.assertEqual(
            [(finding.source, finding.code) for finding in findings],
            [
                ("no-note", "unevidenced-verification"),
                ("empty-note", "unevidenced-verification"),
                ("other-date", "unevidenced-verification"),
                ("bare-date", "unevidenced-verification"),
            ],
        )

    def test_cadence_warns_within_the_window_before_the_due_date(self) -> None:
        today = dt.date(2026, 11, 3)
        entry = {"source": "figma", "last_verified": "2026-08-12", "recheck_interval_days": 90}
        findings: list = []
        checker._check_cadence(findings, entry, today)
        self.assertEqual([f.code for f in findings], ["source-contract-recheck-due"])
        self.assertEqual(findings[0].severity, "info")
        self.assertIn("due in 7 days", findings[0].message)
        self.assertIn("due 2026-11-10", findings[0].message)
        # Negative control: one day earlier is outside the 7-day window.
        early: list = []
        checker._check_cadence(early, entry, dt.date(2026, 11, 2))
        self.assertEqual(early, [])
        # The due date itself still warns; the day after is overdue.
        on_day: list = []
        checker._check_cadence(on_day, entry, dt.date(2026, 11, 10))
        self.assertIn("due today", on_day[0].message)
        after: list = []
        checker._check_cadence(after, entry, dt.date(2026, 11, 11))
        self.assertEqual([f.code for f in after], ["stale-source-contract"])
        # The window is configurable.
        wide: list = []
        checker._check_cadence(wide, entry, dt.date(2026, 10, 27), warn_days=14)
        self.assertEqual([f.code for f in wide], ["source-contract-recheck-due"])

    def test_subprocess_checker_runs_pin_the_clock(self) -> None:
        # Poka-yoke: every checker subprocess command must carry --today.
        src = Path(__file__).read_text(encoding="utf-8")
        launches = src.count("str(CHECKER_PATH),\n")
        pinned = src.count('str(CHECKER_PATH),\n                "--today",')
        pinned += src.count('str(CHECKER_PATH),\n            "--today",')
        self.assertEqual(launches, pinned)

    def test_today_override_is_deterministic_regardless_of_real_clock(self) -> None:
        registry = _load_registry()
        entry = _contract(registry, "figma")
        entry["recheck_interval_days"] = 3
        entry["last_verified"] = "2026-11-09"
        with tempfile.TemporaryDirectory() as td:
            path = _write_registry(Path(td), registry)
            def run(today: str) -> str:
                return subprocess.run(
                    [sys.executable, str(CHECKER_PATH), "--root", str(ROOT),
                     "--registry", str(path), "--today", today],
                    check=False, capture_output=True, text=True, encoding="utf-8",
                ).stdout
            self.assertIn("source-contract-recheck-due figma", run("2026-11-12"))
            self.assertIn("stale-source-contract figma", run("2027-11-12"))

    def test_due_or_overdue_rechecks_never_fail_strict_and_annotate_on_actions(self) -> None:
        registry = _load_registry()
        # The subprocess clock is pinned below, so derive the fixture from that same date.
        # Using the runner's real date made this test fail when it lagged the pinned date.
        # Due in one day keeps it a warning rather than overdue on any day this runs. Its
        # verification_note names the date, so the evidence rule still accepts it.
        due_soon = (
            dt.date.fromisoformat(FIXED_TODAY) - dt.timedelta(days=2)
        ).isoformat()
        for source, verified in (("stitch", "2020-01-01"), ("figma", due_soon)):
            entry = _contract(registry, source)
            entry["recheck_interval_days"] = 3
            entry["last_verified"] = verified
            if source == "figma":
                entry["verification_note"] = f"Recheck pinned to {verified} for the cadence annotation test."
        with tempfile.TemporaryDirectory() as td:
            registry_path = _write_registry(Path(td), registry)
            cmd = [
                sys.executable,
                str(CHECKER_PATH),
                "--today",
                FIXED_TODAY,
                "--root",
                str(ROOT),
                "--registry",
                str(registry_path),
                "--strict",
            ]
            env = dict(__import__("os").environ, GITHUB_ACTIONS="true")
            on_actions = subprocess.run(
                cmd, check=False, capture_output=True, text=True, encoding="utf-8", env=env
            )
            env.pop("GITHUB_ACTIONS")
            local = subprocess.run(
                cmd, check=False, capture_output=True, text=True, encoding="utf-8", env=env
            )
        self.assertEqual(on_actions.returncode, 0, on_actions.stdout + on_actions.stderr)
        self.assertIn("[info] stale-source-contract stitch", on_actions.stdout)
        self.assertIn("[info] source-contract-recheck-due figma", on_actions.stdout)
        self.assertIn("::warning title=stale-source-contract stitch::", on_actions.stdout)
        self.assertIn("::warning title=source-contract-recheck-due figma::", on_actions.stdout)
        # Negative control: no annotations outside GitHub Actions.
        self.assertEqual(local.returncode, 0)
        self.assertNotIn("::warning", local.stdout)

    def test_annotations_cover_only_cadence_findings(self) -> None:
        findings = [
            checker.Finding("info", "stale-source-contract", "v0", "overdue: due 2026-11-10"),
            checker.Finding("warning", "missing-test-file", "v0", "gone"),
        ]
        text = checker.github_annotations(findings)
        self.assertEqual(text.count("::warning"), 1)
        self.assertIn("::overdue: due 2026-11-10", text)

    def test_report_rendering_escapes_markdown_and_handles_empty(self) -> None:
        finding = checker.Finding(
            "warning",
            "pipe-code",
            "source",
            "message with | pipe",
            "path/file.tsx",
        )
        self.assertEqual(checker.render_report([], fmt="text"), "source-contracts: no findings\n")
        self.assertEqual(
            checker.render_report([], fmt="markdown"),
            "Source-contract check: no findings.\n",
        )
        text = checker.render_report([finding], fmt="text")
        markdown = checker.render_report([finding], fmt="markdown")
        self.assertTrue(markdown.startswith("| Severity | Code | Source | Path | Message |"))
        self.assertNotIn("message with | pipe", markdown)
        self.assertIn("[warning] pipe-code source", text)
        self.assertIn("(path/file.tsx)", text)
        self.assertIn("message with \\| pipe", markdown)
        self.assertIn("`pipe-code`", markdown)

    def test_strict_rejects_unverified_contract(self) -> None:
        registry = _load_registry()
        _contract(registry, "figma")["last_verified"] = "unverified"
        with tempfile.TemporaryDirectory() as td:
            registry_path = _write_registry(Path(td), registry)
            base_cmd = [
                sys.executable,
                str(CHECKER_PATH),
                "--today",
                FIXED_TODAY,
                "--root",
                str(ROOT),
                "--registry",
                str(registry_path),
            ]
            non_strict = subprocess.run(base_cmd, check=False, capture_output=True, text=True)
            strict = subprocess.run(base_cmd + ["--strict"], check=False, capture_output=True, text=True)
        self.assertEqual(non_strict.returncode, 0, non_strict.stdout + non_strict.stderr)
        self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_strict_reports_overdue_contract_but_exits_zero(self) -> None:
        registry = _load_registry()
        entry = _contract(registry, "stitch")
        entry["last_verified"] = "2020-01-01"
        entry["recheck_interval_days"] = 1
        with tempfile.TemporaryDirectory() as td:
            registry_path = _write_registry(Path(td), registry)
            cmd = [
                sys.executable,
                str(CHECKER_PATH),
                "--today",
                FIXED_TODAY,
                "--root",
                str(ROOT),
                "--registry",
                str(registry_path),
                "--strict",
            ]
            result = subprocess.run(
                cmd, check=False, capture_output=True, text=True, encoding="utf-8"
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[info] stale-source-contract stitch", result.stdout)

    def test_strict_rejects_malformed_contract_date(self) -> None:
        registry = _load_registry()
        _contract(registry, "stitch")["last_verified"] = "not-a-date"
        with tempfile.TemporaryDirectory() as td:
            registry_path = _write_registry(Path(td), registry)
            cmd = [
                sys.executable,
                str(CHECKER_PATH),
                "--today",
                FIXED_TODAY,
                "--root",
                str(ROOT),
                "--registry",
                str(registry_path),
                "--strict",
            ]
            result = subprocess.run(
                cmd, check=False, capture_output=True, text=True, encoding="utf-8"
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("invalid-last-verified", result.stdout)

    def test_surface_refs_must_exist(self) -> None:
        registry, entry = self.mutate("figma")
        entry["runtime_requirements"]["surface_refs"].append("framework/core/view/missing-dir")
        self.assert_has_code(registry, "missing-surface-ref")
        registry, entry = self.mutate("figma")
        entry["runtime_requirements"]["sdk_min"] = "v0.74.1"
        self.assert_has_code(registry, "missing-sdk-min")

    def test_pending_items_need_an_item_a_reason_and_a_pull_request_or_null(self) -> None:
        registry, entry = self.mutate("stitch")
        entry["pending"].append({"check": "tests", "item": "x", "reason": "r",
                                 "pr": "https://example.com/not-a-pr"})
        self.assert_has_code(registry, "invalid-pending-item")
        registry, entry = self.mutate("stitch")
        entry["pending"].append({"check": "tests", "item": "", "reason": "r", "pr": None})
        self.assert_has_code(registry, "invalid-pending-item")
        registry, entry = self.mutate("stitch")
        entry["pending"].append({"check": "unknown", "item": "x", "reason": "r", "pr": None})
        self.assert_has_code(registry, "invalid-pending-item")

    def test_deferred_items_need_a_real_reason(self) -> None:
        registry, entry = self.mutate("rn")
        entry["deferred"].append({"check": "tests", "reason": "later"})
        self.assert_has_code(registry, "invalid-deferred-item")
        registry, entry = self.mutate("rn")
        entry["deferred"] = "none"
        self.assert_has_code(registry, "invalid-deferred-item")

    def test_a_pending_reference_must_be_explained(self) -> None:
        registry, entry = self.mutate("stitch")
        entry["pending"] = [item for item in entry["pending"] if item["check"] != "render_reference"]
        self.assert_has_code(registry, "unexplained-pending-reference")

    def test_every_open_item_is_counted_and_listed(self) -> None:
        registry = _load_registry()
        pending, deferred = checker.open_items(registry)
        expected_pending = sum(len(entry["pending"]) for entry in registry["contracts"])
        expected_deferred = sum(len(entry["deferred"]) for entry in registry["contracts"])
        self.assertEqual((len(pending), len(deferred)), (expected_pending, expected_deferred))
        self.assertGreater(len(deferred), 0)
        result = subprocess.run(
            [
                sys.executable,
                str(CHECKER_PATH),
                "--today",
                FIXED_TODAY,
                "--registry", str(REGISTRY_PATH), "--list-open"],
            check=False, capture_output=True, text=True, encoding="utf-8")
        self.assertIn(f"source-contracts: {expected_pending} pending, {expected_deferred} deferred",
                      result.stdout)
        self.assertEqual(result.stdout.count("\n  pending  "), expected_pending)
        self.assertEqual(result.stdout.count("\n  deferred "), expected_deferred)

    def test_registry_carries_no_provider_acquisition_notes(self) -> None:
        for entry in _load_registry()["contracts"]:
            self.assertNotIn("mcp", entry, entry["source"])

    def test_nested_fields_are_type_and_presence_checked(self) -> None:
        mutations = {
            "dispatch label removed": lambda e: e["dispatch"].pop("label"),
            "parser file removed": lambda e: e["parser"].pop("file"),
            "parser static not a string": lambda e: e["parser"].__setitem__("static", 7),
            "upstream entry without url": lambda e: e["upstream"][0].pop("url"),
            "upstream emptied": lambda e: e.__setitem__("upstream", []),
            "upstream not a list": lambda e: e.__setitem__("upstream", {"url": "x"}),
            "validation tests not a list": lambda e: e["validation"].__setitem__("tests", "ctest:x"),
            "validation tests holds a number": lambda e: e["validation"]["tests"].append(3),
            "validation journey not a string": lambda e: e["validation"].__setitem__("journey", 1),
            "render reference without status": lambda e: e["validation"]["render_reference"].pop("status"),
            "compat owner missing": lambda e: e["compat"].pop("parser_version_owner"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                registry, entry = self.mutate("stitch")
                mutate(entry)
                self.assert_has_code(registry, "invalid-field")

    def test_malformed_format_versions_cannot_skip_the_foreign_key_check(self) -> None:
        for bad in ({"version": "2025.04"}, "2025.04", [{"version": 2025}], [{"fixture_paths": []}]):
            with self.subTest(value=bad):
                registry, entry = self.mutate("stitch")
                entry["format_versions"] = bad
                self.assert_has_code(registry, "invalid-field")

    def test_malformed_surface_refs_cannot_skip_the_owner_check(self) -> None:
        for bad in ("framework/core/view", {"ref": "framework/core/view"}, [], [7]):
            with self.subTest(value=bad):
                registry, entry = self.mutate("stitch")
                entry["runtime_requirements"]["surface_refs"] = bad
                self.assert_has_code(registry, "invalid-field")

    def test_surface_refs_stay_inside_the_checkout(self) -> None:
        for bad in ("framework/../../outside", "/framework/core/view", "framework/core/view/../../../x"):
            with self.subTest(ref=bad):
                registry, entry = self.mutate("stitch")
                entry["runtime_requirements"]["surface_refs"] = [bad]
                self.assert_has_code(registry, "invalid-surface-ref")
        self.assertIsNone(checker.contained_path(ROOT, "/etc"))
        self.assertIsNone(checker.contained_path(ROOT, "product/../../x"))
        self.assertIsNotNone(checker.contained_path(ROOT, "product/source-contracts.json"))

    def test_strict_real_registry_has_clean_exit(self) -> None:
        cmd = [
            sys.executable,
            str(CHECKER_PATH),
            "--today",
            FIXED_TODAY,
            "--root",
            str(ROOT),
            "--registry",
            str(REGISTRY_PATH),
            "--strict",
        ]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
