#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("observatory.py")
SPEC = importlib.util.spec_from_file_location("vellum_observatory", MODULE_PATH)
assert SPEC and SPEC.loader
observatory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observatory)


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(args, cwd=repo, text=True).strip()


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def real_mapping() -> dict[str, object]:
    return observatory.load_json(observatory.ROOT / observatory.MAP_PATH)


def init_repo(path: Path, first_path: str) -> tuple[str, str]:
    path.mkdir()
    run(path, "git", "init", "-b", "main")
    run(path, "git", "config", "user.email", "observatory@example.invalid")
    run(path, "git", "config", "user.name", "Observatory Test")
    write(path / first_path, "int value = 1;\n")
    run(path, "git", "add", ".")
    run(path, "git", "commit", "-m", "base")
    base = run(path, "git", "rev-parse", "HEAD")
    write(path / first_path, "int value = 2;\n")
    run(path, "git", "add", ".")
    run(path, "git", "commit", "-m", "mapped change")
    return base, run(path, "git", "rev-parse", "HEAD")


def make_state(root: Path, pulp_base: str, vellum_base: str) -> None:
    write(
        root / "product/budgets.yaml",
        """schema_version: 1
observatory:
  security_or_p0_classification_hours: 24
  ordinary_framework_classification_business_days: 3
  other_classification_days: 7
  maximum_pending_events: 20
  maximum_overdue_events: 0
  maximum_pending_event_age_days: 7
  maximum_repeated_generic_fixes: 2
  maximum_framework_effort_percent: 15
""",
    )
    write_json(
        root / "provenance/pulp-observatory/provenance.lock",
        {
            "schema_version": 2,
            "state": "prepared",
            "pulp_repository": "Generous-Corp/pulp",
            "pulp_extraction_base": pulp_base,
            "vellum_repository": "Generous-Corp/vellum",
            "vellum_history_seed": vellum_base,
            "vellum_authority_start_commit": None,
            "vellum_authority_record_commit": None,
            "pulp_activation_commit": None,
            "ownership_schema_version": 1,
            "event_schema_version": 1,
            "cursor_schema_version": 2,
            "source_projection": "../cut-manifest.json",
            "transfer_plan": "../authority/transfer-plan.v1.json",
            "policy": {
                "synchronized_editable_copies_allowed": False,
                "automatic_patch_application_allowed": False,
                "one_active_authority_required": True,
            },
        },
    )
    write_json(
        root / "provenance/pulp-observatory/legacy-path-map.yaml",
        {
            "schema_version": 2,
            "state": "prepared-no-transfer",
            "source_manifest": "../cut-manifest.json",
            "mappings": [
                {
                    "id": "render",
                    "pulp_paths": ["core/render/"],
                    "vellum_paths": ["graphics/"],
                    "symbols": [],
                    "targets": [],
                    "schemas": [],
                    "platform_hosts": [],
                    "contract_tests": ["render-contract"],
                    "authority": "pulp-authoritative-untransferred",
                }
            ],
            "transitive_path_rules": ["CMakeLists.txt"],
        },
    )
    write_json(
        root / "provenance/pulp-observatory/cursor.json",
        {
            "schema_version": 2,
            "state": "prepared",
            "pulp": {
                "repository": "Generous-Corp/pulp",
                "scan_base_commit": pulp_base,
                "last_scanned_commit": pulp_base,
                "last_dispatch_event": None,
            },
            "vellum": {
                "repository": "Generous-Corp/vellum",
                "scan_base_commit": vellum_base,
                "last_scanned_commit": vellum_base,
                "last_dispatch_event": None,
            },
            "effort_window": {
                "started_at": None,
                "framework_effort_minutes": 0,
                "observatory_effort_minutes": 0,
            },
            "reconciled_at": None,
        },
    )
    write_json(
        root / "provenance/authority/trust-policy.v1.json",
        {
            "schema_version": 1,
            "state": "disabled",
            "repositories": {
                "pulp": {"repository_id": None, "reader_app_id": None, "required_check_app_ids": {}},
                "vellum": {"repository_id": None, "reader_app_id": None, "required_check_app_ids": {}},
            },
        },
    )
    initial = observatory.build_report(
        root=root,
        lock=observatory.load_json(root / observatory.LOCK_PATH),
        cursor=observatory.load_json(root / observatory.CURSOR_PATH),
        events=[],
        budgets=observatory.load_budgets(root / observatory.BUDGETS_PATH),
        now=None,
        coverage_gaps=[],
    )
    write_json(root / observatory.REPORT_JSON_PATH, initial)
    write(root / observatory.REPORT_MD_PATH, observatory.render_markdown(initial))


class ObservatoryTests(unittest.TestCase):
    def test_pulp_root_cmake_is_transitive_only_with_direct_mapped_change(self) -> None:
        mapping = {
            "id": "render",
            "pulp_paths": ["core/render/"],
            "vellum_paths": ["graphics/"],
            "contract_tests": ["render-contract"],
        }
        direct_and_root = observatory.mapped_change(
            [
                {"status": "M", "path": "core/render/render.cpp"},
                {"status": "M", "path": "CMakeLists.txt"},
            ],
            [mapping],
            "pulp",
            ["CMakeLists.txt"],
        )
        self.assertIsNotNone(direct_and_root)
        assert direct_and_root is not None
        self.assertEqual(direct_and_root["mapped_paths"], ["core/render/render.cpp"])
        self.assertEqual(direct_and_root["transitive_paths"], ["CMakeLists.txt"])

        root_only = observatory.mapped_change(
            [{"status": "M", "path": "CMakeLists.txt"}],
            [mapping],
            "pulp",
            ["CMakeLists.txt"],
        )
        self.assertIsNone(root_only)

    def test_new_macos_platform_file_maps_shell_and_gpu_contract(self) -> None:
        mapping = real_mapping()
        mapped = observatory.mapped_change(
            [{"status": "A", "path": "core/view/platform/mac/multi_window_coordinator.mm"}],
            mapping["mappings"],
            "pulp",
            mapping["transitive_path_rules"],
        )
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertEqual(mapped["mapped_contracts"], ["macos-shell", "retained-ui-kernel"])
        self.assertIn("vellum.gpu-native", mapped["contract_tests"])

    def test_pulp_upstream_tooling_paths_are_observed_without_release_blocker(self) -> None:
        mapping = real_mapping()
        cases = {
            "tools/figma-plugin/src/serialize.ts": ["pulp-figma-exporter"],
            "tools/import-design/pulp_import_design.cpp": ["pulp-design-import-tooling"],
            "tools/screenshot/pulp_screenshot.cpp": ["pulp-screenshot-harness"],
            "tools/import-design/montage.py": [
                "pulp-design-import-tooling",
                "pulp-screenshot-harness",
            ],
        }
        for path, expected_contracts in cases.items():
            with self.subTest(path=path):
                mapped = observatory.mapped_change(
                    [{"status": "M", "path": path}],
                    mapping["mappings"],
                    "pulp",
                    mapping["transitive_path_rules"],
                )
                self.assertIsNotNone(mapped)
                assert mapped is not None
                self.assertEqual(mapped["mapped_contracts"], expected_contracts)

        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary) / "pulp"
            pulp_base, pulp_head = init_repo(pulp, "tools/figma-plugin/src/serialize.ts")
            event = observatory.observation_for_commit(
                source="pulp",
                repository="Generous-Corp/pulp",
                repo=pulp,
                commit=pulp_head,
                cursor_from=pulp_base,
                cursor_to=pulp_head,
                discovered_at="2026-07-22T20:00:00Z",
                mappings=mapping["mappings"],
                transitive_rules=mapping["transitive_path_rules"],
            )
            self.assertIsNotNone(event)
            assert event is not None
            self.assertFalse(event["shared_contract_release_blocker"])

    def test_reconcile_then_verify_is_positive_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/render/render.cpp")
            vellum_base, vellum_head = init_repo(vellum, "graphics/render.cpp")
            make_state(root, pulp_base, vellum_base)

            result = observatory.reconcile(
                root=root,
                pulp_repo=pulp,
                vellum_repo=vellum,
                pulp_target=pulp_head,
                vellum_target=vellum_head,
                now_text="2026-07-22T20:00:00Z",
                write=True,
            )

            self.assertEqual(len(result["new_events"]), 2)
            report = observatory.verify(
                root=root,
                pulp_repo=pulp,
                vellum_repo=vellum,
                git_base=None,
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["pending"], 2)
            self.assertEqual(report["coverage_gaps"], [])

    def test_derived_event_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/render/render.cpp")
            vellum_base, _ = init_repo(vellum, "unmapped/file.cpp")
            make_state(root, pulp_base, vellum_base)
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=vellum_base,
                now_text="2026-07-22T20:00:00Z", write=True,
            )
            event_path = next((root / observatory.EVENTS_PATH).glob("*.yaml"))
            event = observatory.load_json(event_path)
            event["mapped_paths"] = ["core/render/not-the-changed-file.cpp"]
            write_json(event_path, event)

            with self.assertRaisesRegex(observatory.ObservatoryError, "derived fields differ"):
                observatory.coverage_gaps(
                    root=root,
                    mapping=observatory.load_json(root / observatory.MAP_PATH),
                    cursor=observatory.load_json(root / observatory.CURSOR_PATH),
                    events=observatory.load_events(root),
                    pulp_repo=pulp,
                    vellum_repo=vellum,
                )

    def test_cursor_cannot_advance_without_event_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/render/render.cpp")
            vellum_base, _ = init_repo(vellum, "unmapped/file.cpp")
            make_state(root, pulp_base, vellum_base)
            cursor = observatory.load_json(root / observatory.CURSOR_PATH)
            cursor["pulp"]["last_scanned_commit"] = pulp_head
            write_json(root / observatory.CURSOR_PATH, cursor)

            gaps = observatory.coverage_gaps(
                root=root,
                mapping=observatory.load_json(root / observatory.MAP_PATH),
                cursor=cursor,
                events=[],
                pulp_repo=pulp,
                vellum_repo=vellum,
            )

            self.assertEqual(gaps, [{"source": "pulp", "commit": pulp_head, "reason": "mapped-commit-has-no-event"}])

    def test_overdue_budget_has_positive_and_negative_control(self) -> None:
        event = {
            "schema_version": 1,
            "event_id": "pulp-" + "a" * 40,
            "kind": "observation",
            "source_repository": "Generous-Corp/pulp",
            "source_commit": "a" * 40,
            "discovered_at": "2026-07-20T00:00:00Z",
            "scan_cursor": {"from_commit": "b" * 40, "to_commit": "c" * 40},
            "direction": "Pulp-to-framework",
            "mapped_contracts": ["render"],
            "mapped_paths": ["core/render/render.cpp"],
            "transitive_paths": [],
            "rename_candidates": [],
            "patch_id": None,
            "include_dependency_deltas": [],
            "schema_api_deltas": [],
            "class": "security",
            "severity": None,
            "disposition": "pending",
            "rationale": "Review required.",
            "owner": "@danielraffel",
            "linked_commits": [],
            "linked_pull_requests": [],
            "contract_tests": ["render-contract"],
            "contract_keys": ["render"],
            "shared_contract_release_blocker": False,
            "effort_minutes": 0,
        }
        budgets = {
            "security_or_p0_classification_hours": 24,
            "ordinary_framework_classification_business_days": 3,
            "other_classification_days": 7,
            "maximum_pending_events": 20,
            "maximum_overdue_events": 0,
            "maximum_pending_event_age_days": 7,
            "maximum_repeated_generic_fixes": 2,
            "maximum_framework_effort_percent": 15,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(
                root / "provenance/authority/trust-policy.v1.json",
                {"schema_version": 1, "state": "disabled", "repositories": {
                    "pulp": {"repository_id": None, "reader_app_id": None, "required_check_app_ids": {}},
                    "vellum": {"repository_id": None, "reader_app_id": None, "required_check_app_ids": {}},
                }},
            )
            lock = {"state": "prepared", "vellum_authority_start_commit": None, "pulp_activation_commit": None}
            cursor = {"effort_window": {"framework_effort_minutes": 0, "observatory_effort_minutes": 0}}
            positive = observatory.build_report(
                root=root, lock=lock, cursor=cursor, events=[(Path("event"), event)], budgets=budgets,
                now=observatory.parse_utc("2026-07-20T12:00:00Z", "now"), coverage_gaps=[],
            )
            self.assertEqual(positive["health"], "pass")
            negative = observatory.build_report(
                root=root, lock=lock, cursor=cursor, events=[(Path("event"), event)], budgets=budgets,
                now=observatory.parse_utc("2026-07-22T12:00:00Z", "now"), coverage_gaps=[],
            )
            self.assertEqual(negative["health"], "fail")
            self.assertIn("overdue-event-count-exceeded", negative["budget_violations"])
            self.assertEqual(negative["release_blockers"], [event["event_id"]])

    def test_event_resolution_is_append_only_effective_state(self) -> None:
        observation = {
            "schema_version": 1, "event_id": "pulp-" + "a" * 40, "kind": "observation",
            "source_repository": "Generous-Corp/pulp", "source_commit": "a" * 40,
            "discovered_at": "2026-07-20T00:00:00Z", "scan_cursor": {"from_commit": "b" * 40, "to_commit": "c" * 40},
            "direction": "Pulp-to-framework", "mapped_contracts": ["render"],
            "mapped_paths": ["core/render/a.cpp"], "transitive_paths": [], "rename_candidates": [],
            "patch_id": None, "include_dependency_deltas": [], "schema_api_deltas": [],
            "class": "correctness", "severity": None, "disposition": "pending", "rationale": "Review.",
            "owner": "@danielraffel", "linked_commits": [], "linked_pull_requests": [],
            "contract_tests": ["render-contract"], "contract_keys": ["render"],
            "shared_contract_release_blocker": False, "effort_minutes": 0,
        }
        resolution = {
            "schema_version": 1, "event_id": "resolution-render-a", "kind": "resolution",
            "created_at": "2026-07-21T00:00:00Z", "resolves": observation["event_id"],
            "disposition": "ported", "rationale": "Ported with contract proof.", "owner": "@danielraffel",
            "linked_commits": ["d" * 40], "linked_pull_requests": [], "contract_tests": ["render-contract"],
            "shared_contract_release_blocker": False, "effort_minutes": 15,
        }
        effective = observatory.effective_observations([(Path("a"), observation), (Path("b"), resolution)])
        self.assertEqual(effective[0]["disposition"], "ported")
        self.assertEqual(effective[0]["resolution_event"], "resolution-render-a")


if __name__ == "__main__":
    unittest.main()
