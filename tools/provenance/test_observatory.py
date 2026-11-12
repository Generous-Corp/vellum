#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock

try:
    from .git_test_environment import disable_auto_maintenance
except ImportError:  # Direct script execution.
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))  # -I drops the script dir; append keeps the standard library first
    from git_test_environment import disable_auto_maintenance

disable_auto_maintenance()


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


def commit_all(repo: Path, message: str) -> str:
    run(repo, "git", "add", ".")
    run(repo, "git", "commit", "-m", message)
    return run(repo, "git", "rev-parse", "HEAD")


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
    def test_markdown_surfaces_release_blockers(self) -> None:
        report = {
            "state": "active",
            "health": "pass",
            "pending": 0,
            "overdue": 0,
            "observatory_effort_percent": 0.0,
            "events": [],
            "release_blockers": ["pulp-a", "pulp-b"],
            "activation_blockers": [],
        }

        markdown = observatory.render_markdown(report)

        self.assertIn("- Release blockers: 2", markdown)
        self.assertIn("## Release blockers\n\n- `pulp-a`\n- `pulp-b`", markdown)

    def test_public_api_delegates_to_focused_observatory_modules(self) -> None:
        ownership = {
            observatory.load_json: "observatory_common",
            observatory.observation_for_commit: "observatory_git",
            observatory.validate_event: "observatory_events",
            observatory.effective_observations: "observatory_events",
            observatory.build_report: "observatory_report",
            observatory.render_markdown: "observatory_report",
        }
        for function, expected_module in ownership.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(function.__module__, expected_module)

    def test_active_cursors_may_advance_beyond_authority_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum"
            _, pulp_coordinate = init_repo(pulp, "unmapped/pulp.cpp")
            vellum_base, vellum_coordinate = init_repo(
                vellum, "unmapped/vellum.cpp"
            )
            write(pulp / "unmapped/pulp.cpp", "int value = 3;\n")
            pulp_head = commit_all(pulp, "post-activation pulp change")
            write(vellum / "unmapped/vellum.cpp", "int value = 3;\n")
            vellum_head = commit_all(vellum, "post-activation vellum change")
            lock = {
                "state": "active",
                "pulp_activation_commit": pulp_coordinate,
                "vellum_authority_record_commit": vellum_coordinate,
            }
            cursor = {
                "pulp": {"last_scanned_commit": pulp_head},
                "vellum": {"last_scanned_commit": vellum_head},
            }

            observatory.verify_active_cursor_ancestry(
                lock, cursor, pulp, vellum
            )

            cursor["vellum"]["last_scanned_commit"] = vellum_base
            with self.assertRaisesRegex(
                observatory.ObservatoryError,
                "must include its authority coordinate",
            ):
                observatory.verify_active_cursor_ancestry(
                    lock,
                    cursor,
                    pulp,
                    vellum,
                )

    def test_ci_verifies_the_checked_out_commit_on_every_event(self) -> None:
        # On pull_request the checkout is the merge ref, which carries main's
        # events for landings the cursor does not cover yet; a head-only target
        # fails "report is stale" for every PR behind main while the cursor lags.
        workflow = (
            observatory.ROOT / ".github/workflows/provenance.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("VELLUM_TARGET_SHA: ${{ github.sha }}", workflow)
        self.assertNotIn("github.event.pull_request.head.sha", workflow)
        # A merge-queue run verifies the queue's merge commit (github.sha) against
        # the queue base; a push run falls back to the pre-push ref.
        self.assertIn(
            "VELLUM_GIT_BASE: ${{ github.event.pull_request.base.sha || "
            "github.event.merge_group.base_sha || github.event.before }}",
            workflow,
        )
        self.assertIn("  merge_group:\n    branches: [main]\n", workflow)
        self.assertIn(
            'observatory_vellum_target="$VELLUM_TARGET_SHA"', workflow
        )
        self.assertIn(
            'observatory_vellum_target="$AUTHORITY_START_SHA"', workflow
        )
        self.assertIn(
            '--vellum-target "$observatory_vellum_target"', workflow
        )
        self.assertIn(
            'git merge-base "$observatory_git_base" HEAD',
            workflow,
        )
        self.assertIn('--git-base "$observatory_git_base"', workflow)
        self.assertIn('--vellum-tail-policy "$observatory_tail_policy"', workflow)
        self.assertIn('observatory_pending_event_scope=all', workflow)
        self.assertIn('observatory_pending_event_scope=introduced', workflow)
        self.assertIn('--pending-event-scope "$observatory_pending_event_scope"', workflow)
        self.assertIn('observatory_tail_policy=reconciled', workflow)

    def test_pending_budget_scope_ignores_base_debt_but_rejects_too_many_new_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run(root, "git", "init", "-q", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            base_events = []
            for index in range(20):
                path = root / observatory.EVENTS_PATH / f"base-{index}.yaml"
                value = {"event_id": f"base-{index}", "kind": "observation", "disposition": "pending",
                         "discovered_at": "2026-01-01T00:00:00Z"}
                write_json(path, value)
                base_events.append((path, value))
            base = commit_all(root, "base pending events")

            branch_events = list(base_events)
            for index in range(3):
                path = root / observatory.EVENTS_PATH / f"introduced-{index}.yaml"
                value = {"event_id": f"introduced-{index}", "kind": "observation", "disposition": "pending",
                         "discovered_at": "2026-01-01T00:00:00Z"}
                write_json(path, value)
                branch_events.append((path, value))
            self.assertEqual(observatory.pending_event_count(root, base, branch_events, "all"), 23)
            self.assertEqual(observatory.pending_event_count(root, base, branch_events, "introduced"), 3)
            self.assertLess(3, 20)

            for index in range(3, 24):
                path = root / observatory.EVENTS_PATH / f"introduced-{index}.yaml"
                value = {"event_id": f"introduced-{index}", "kind": "observation", "disposition": "pending",
                         "discovered_at": "2026-01-01T00:00:00Z"}
                write_json(path, value)
                branch_events.append((path, value))
            self.assertEqual(observatory.pending_event_count(root, base, branch_events, "introduced"), 24)
            self.assertGreater(24, 20)

    def test_existing_event_mutations_are_rejected_against_git_base(self) -> None:
        for operation in ("modify", "delete", "rename"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run(root, "git", "init", "-b", "main")
                run(root, "git", "config", "user.email", "observatory@example.invalid")
                run(root, "git", "config", "user.name", "Observatory Test")
                event = root / observatory.EVENTS_PATH / "event.yaml"
                write(event, '{"event_id":"event","disposition":"pending"}\n')
                run(root, "git", "add", ".")
                run(root, "git", "commit", "-m", "base event")
                base = run(root, "git", "rev-parse", "HEAD")
                if operation == "modify":
                    write(event, '{"event_id":"event","disposition":"ported"}\n')
                elif operation == "delete":
                    event.unlink()
                elif operation == "rename":
                    event.rename(event.with_name("renamed.yaml"))
                run(root, "git", "add", "-A")
                run(root, "git", "commit", "-m", f"{operation} event")
                with self.assertRaisesRegex(
                    observatory.ObservatoryError, "append-only"
                ):
                    observatory.verify_append_only(root, base)

    def test_similar_new_event_is_allowed_against_git_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run(root, "git", "init", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            event = root / observatory.EVENTS_PATH / "event.yaml"
            write(event, '{"event_id":"event","disposition":"pending"}\n')
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "base event")
            base = run(root, "git", "rev-parse", "HEAD")
            write(
                event.with_name("similar-event.yaml"),
                '{"event_id":"similar-event","disposition":"pending"}\n',
            )
            run(root, "git", "add", "-A")
            run(root, "git", "commit", "-m", "append similar event")

            observatory.verify_append_only(root, base)

    def test_restored_event_mutation_is_rejected_against_git_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run(root, "git", "init", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            event = root / observatory.EVENTS_PATH / "event.yaml"
            original = '{"event_id":"event","disposition":"pending"}\n'
            write(event, original)
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "base event")
            base = run(root, "git", "rev-parse", "HEAD")
            write(event, '{"event_id":"event","disposition":"ported"}\n')
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "mutate event")
            write(event, original)
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "restore event")

            with self.assertRaisesRegex(
                observatory.ObservatoryError, "append-only"
            ):
                observatory.verify_append_only(root, base)

    def test_deleted_and_readded_cursor_is_rejected_against_git_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run(root, "git", "init", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            cursor = {
                "pulp": {"last_scanned_commit": "a" * 40},
                "vellum": {"last_scanned_commit": "b" * 40},
            }
            write_json(root / observatory.CURSOR_PATH, cursor)
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "base cursor")
            base = run(root, "git", "rev-parse", "HEAD")
            (root / observatory.CURSOR_PATH).unlink()
            run(root, "git", "add", "-A")
            run(root, "git", "commit", "-m", "delete cursor")
            write_json(root / observatory.CURSOR_PATH, cursor)
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "restore cursor")

            with self.assertRaisesRegex(
                observatory.ObservatoryError,
                "cursor must exist on every history edge",
            ):
                observatory.verify_append_only(root, base)

    def test_pulp_cursor_cannot_move_backward_against_git_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            pulp_base, pulp_head = init_repo(pulp, "unmapped/pulp.cpp")
            root = top / "vellum"
            root.mkdir()
            run(root, "git", "init", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            cursor = {
                "pulp": {"last_scanned_commit": pulp_head},
                "vellum": {"last_scanned_commit": "a" * 40},
            }
            write_json(root / observatory.CURSOR_PATH, cursor)
            base = commit_all(root, "forward cursor")
            cursor["pulp"]["last_scanned_commit"] = pulp_base
            write_json(root / observatory.CURSOR_PATH, cursor)
            commit_all(root, "rewind Pulp cursor")

            with self.assertRaisesRegex(
                observatory.ObservatoryError,
                "cursor.pulp cannot move backward",
            ):
                observatory.verify_append_only(
                    root,
                    base,
                    pulp_repo=pulp,
                )

    def test_merge_cursor_may_follow_either_reconciled_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            source = top / "source"
            _, source_initial = init_repo(source, "unmapped/source.cpp")
            run(source, "git", "checkout", "-b", "source-feature")
            write(source / "unmapped/source.cpp", "int value = 3;\n")
            source_feature = commit_all(source, "feature source scan")
            run(source, "git", "checkout", "main")
            write(source / "unmapped/source.cpp", "int value = 4;\n")
            source_main = commit_all(source, "main source scan")

            root = top / "observatory"
            root.mkdir()
            run(root, "git", "init", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            cursor = {
                "pulp": {"last_scanned_commit": source_initial},
                "vellum": {"last_scanned_commit": source_initial},
            }
            write_json(root / observatory.CURSOR_PATH, cursor)
            base = commit_all(root, "base cursor")

            run(root, "git", "checkout", "-b", "feature")
            cursor["vellum"]["last_scanned_commit"] = source_feature
            write_json(root / observatory.CURSOR_PATH, cursor)
            commit_all(root, "feature cursor")

            run(root, "git", "checkout", "main")
            cursor["vellum"]["last_scanned_commit"] = source_main
            write_json(root / observatory.CURSOR_PATH, cursor)
            commit_all(root, "main cursor")

            subprocess.run(
                ["git", "merge", "--no-ff", "feature", "-m", "merge reconciled branches"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            cursor["vellum"]["last_scanned_commit"] = source_feature
            write_json(root / observatory.CURSOR_PATH, cursor)
            run(root, "git", "add", ".")
            run(root, "git", "commit", "-m", "resolve merge cursor from feature parent")

            observatory.verify_append_only(root, base, vellum_repo=source)

    def test_authority_record_is_rejected_as_an_ordinary_tail_but_start_passes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            _, authority_start = init_repo(repo, "graphics/render.cpp")
            record = (
                repo
                / "provenance/authority/records/native-design-kernel-v1.json"
            )
            write(record, '{"schema_version": 2}\n')
            record_commit = commit_all(repo, "add authority record")

            with self.assertRaisesRegex(
                observatory.ObservatoryError, "non-evidence change"
            ):
                observatory.verify_vellum_observatory_tail(
                    repo, authority_start, record_commit
                )

            observatory.verify_vellum_observatory_tail(
                repo, authority_start, authority_start
            )

    def test_vellum_evidence_only_tail_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            init_repo(repo, "unmapped/source.cpp")
            write(repo / observatory.CURSOR_PATH, "{}\n")
            write(repo / observatory.REPORT_JSON_PATH, "{}\n")
            write(repo / observatory.REPORT_MD_PATH, "current\n")
            commit_all(repo, "seed observatory evidence")
            write(repo / "graphics/render.cpp", "int value = 1;\n")
            source = commit_all(repo, "mapped source change")
            write(repo / observatory.CURSOR_PATH, '{"advanced": true}\n')
            write(repo / observatory.REPORT_JSON_PATH, '{"health": "pass"}\n')
            write(repo / observatory.REPORT_MD_PATH, "updated\n")
            write(repo / observatory.EVENTS_PATH / f"vellum-{source}.yaml", "{}\n")
            evidence = commit_all(repo, "record observatory evidence")

            observatory.verify_vellum_observatory_tail(repo, source, evidence)

    def test_tree_identical_merge_does_not_duplicate_observation_or_break_tail(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            init_repo(repo, "unmapped/source.cpp")
            write(repo / observatory.REPORT_MD_PATH, "current\n")
            cursor = commit_all(repo, "seed report")
            run(repo, "git", "checkout", "-b", "evidence")
            write(repo / observatory.REPORT_MD_PATH, "updated\n")
            evidence = commit_all(repo, "record evidence")
            run(repo, "git", "checkout", "main")
            run(repo, "git", "merge", "--no-ff", "evidence", "-m", "merge evidence")
            merged = run(repo, "git", "rev-parse", "HEAD")

            self.assertEqual(
                observatory.tree_identical_scanned_parent(
                    repo, merged, cursor
                ),
                evidence,
            )
            self.assertIsNone(
                observatory.observation_for_commit(
                    source="vellum",
                    repository="Generous-Corp/vellum",
                    repo=repo,
                    commit=merged,
                    cursor_from=cursor,
                    cursor_to=merged,
                    discovered_at="2026-07-24T00:00:00Z",
                    mappings=[{
                        "id": "render",
                        "pulp_paths": ["core/render/"],
                        "vellum_paths": ["graphics/"],
                        "contract_tests": ["render-contract"],
                    }],
                    transitive_rules=[],
                )
            )
            observatory.verify_vellum_observatory_tail(
                repo, cursor, merged
            )

    def test_vellum_evidence_tail_rejects_unmapped_product_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            _, source = init_repo(repo, "graphics/render.cpp")
            write(repo / "README.md", "unobserved product documentation\n")
            target = commit_all(repo, "unmapped product change")

            with self.assertRaisesRegex(
                observatory.ObservatoryError, "non-evidence change"
            ):
                observatory.verify_vellum_observatory_tail(repo, source, target)

    def test_pull_request_ignores_its_own_code_after_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            _, cursor = init_repo(repo, "graphics/render.cpp")
            write(repo / "graphics/pr-change.cpp", "int value = 3;\n")
            target = commit_all(repo, "ordinary pull request change")

            observatory.verify_vellum_observatory_pr_tail(
                repo, cursor, cursor, target
            )

    def test_queue_merge_uses_base_to_ignore_pr_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            _, cursor = init_repo(repo, "graphics/render.cpp")
            run(repo, "git", "checkout", "-b", "pull-request")
            write(repo / "graphics/pr-change.cpp", "int value = 3;\n")
            pr_head = commit_all(repo, "ordinary pull request change")
            run(repo, "git", "checkout", "main")
            run(
                repo,
                "git",
                "merge",
                "--no-ff",
                pr_head,
                "-m",
                "synthetic merge queue commit",
            )
            queue_merge = run(repo, "git", "rev-parse", "HEAD")

            observatory.verify_vellum_observatory_pr_tail(
                repo, cursor, cursor, queue_merge
            )
            with self.assertRaisesRegex(
                observatory.ObservatoryError, "non-evidence change"
            ):
                observatory.verify_vellum_observatory_tail(
                    repo, cursor, queue_merge
                )

    def test_pull_request_missing_current_main_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            main_base, main_head = init_repo(repo, "graphics/render.cpp")
            run(repo, "git", "checkout", "-b", "pull-request", main_base)
            write(repo / "graphics/pr-change.cpp", "int value = 3;\n")
            target = commit_all(repo, "ordinary pull request change")

            with self.assertRaisesRegex(
                observatory.ObservatoryError,
                "pull-request target contains current main",
            ):
                observatory.verify_vellum_observatory_pr_tail(
                    repo, main_base, main_head, target
                )

    def test_vellum_evidence_tail_rejects_event_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "vellum"
            _, source = init_repo(repo, "graphics/render.cpp")
            event = repo / observatory.EVENTS_PATH / "vellum-event.yaml"
            write(event, "{}\n")
            added = commit_all(repo, "add evidence")
            write(event, '{"tampered": true}\n')
            target = commit_all(repo, "tamper evidence")

            observatory.verify_vellum_observatory_tail(repo, source, added)
            with self.assertRaisesRegex(
                observatory.ObservatoryError, "non-evidence change"
            ):
                observatory.verify_vellum_observatory_tail(repo, source, target)

    def _coverage_state(self, top: Path):
        """A reconciled state plus a Vellum pull-request branch with one mapped commit."""
        pulp = top / "pulp"
        vellum = top / "vellum-source"
        root = top / "state"
        pulp_base, pulp_head = init_repo(pulp, "unmapped/pulp.cpp")
        vellum_base, _ = init_repo(vellum, "graphics/render.cpp")
        write(vellum / "graphics/shared.cpp", "int shared = 1;\n")
        vellum_main = commit_all(vellum, "shared mapped file")
        make_state(root, pulp_base, vellum_base)
        observatory.reconcile(
            root=root, pulp_repo=pulp, vellum_repo=vellum,
            pulp_target=pulp_head, vellum_target=vellum_main,
            now_text="2026-10-11T00:00:00Z", write=True,
        )
        run(vellum, "git", "checkout", "-q", "-b", "feature")
        write(vellum / "graphics/render.cpp", "int value = 3;\n")
        feature = commit_all(vellum, "feature mapped change")
        return pulp, vellum, root, pulp_head, vellum_main, feature

    def test_coverage_gap_remedy_names_the_right_owner(self) -> None:
        cursor = {"pulp": {"last_scanned_commit": "c" * 40}}
        self.assertEqual(observatory.describe_coverage_gaps([], cursor), "")
        vellum = observatory.describe_coverage_gaps(
            [{"source": "vellum", "commit": "a" * 40,
              "reason": "mapped-commit-after-reconciled-cursor"}], cursor,
        )
        self.assertIn("--events-only", vellum)
        self.assertIn(f"--pulp-target {'c' * 40}", vellum)
        self.assertNotIn("automation/pulp-observatory-coalesced", vellum)
        pulp = observatory.describe_coverage_gaps(
            [{"source": "pulp", "commit": "b" * 40,
              "reason": "mapped-commit-after-reconciled-cursor"}], cursor,
        )
        self.assertIn(f"pulp {'b' * 40}: mapped-commit-after-reconciled-cursor", pulp)
        self.assertIn("automation/pulp-observatory-coalesced", pulp)
        self.assertNotIn("--events-only", pulp)
        other = observatory.describe_coverage_gaps(
            [{"source": "pulp", "commit": "unverified",
              "reason": "source-repository-not-supplied"}], {},
        )
        self.assertEqual(other, "\n  pulp unverified: source-repository-not-supplied")
        many = observatory.describe_coverage_gaps(
            [{"source": "vellum", "commit": f"{index:040x}",
              "reason": "mapped-commit-after-reconciled-cursor"} for index in range(25)], {},
        )
        self.assertIn("... and 5 more", many)
        self.assertIn("--pulp-target <committed-pulp-cursor-sha>", many)

    def test_coverage_policy_accepts_events_only_pull_request_and_rejects_missing_event(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            cursor_before = observatory.load_json(root / observatory.CURSOR_PATH)
            report_before = (root / observatory.REPORT_JSON_PATH).read_bytes()

            # Negative control: the pull request carries no event for its mapped commit.
            with self.assertRaisesRegex(
                observatory.ObservatoryError, "health budgets failed"
            ) as raised:
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=feature,
                    vellum_tail_policy="coverage",
                )
            # The failure names the uncovered commit and the command that
            # closes the gap, at the committed Pulp cursor.
            message = str(raised.exception)
            # A gap the pull request introduces stays fatal when time budgets
            # are only reported, as they are on pull requests.
            with self.assertRaisesRegex(observatory.ObservatoryError, "cursor-event-coverage-gap"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=feature,
                    vellum_tail_policy="coverage", time_budgets="report",
                )
            self.assertIn(f"vellum {feature}: mapped-commit-after-reconciled-cursor", message)
            self.assertIn("Re-running will not help", message)
            self.assertIn(
                f"--pulp-target {cursor_before['pulp']['last_scanned_commit']}", message
            )
            self.assertIn("--write --events-only", message)
            self.assertNotIn("explicit commit", message)
            # The reconciled policy rejects the same tree for a different reason:
            # the tail is not evidence-only.
            with self.assertRaisesRegex(observatory.ObservatoryError, "evidence tail"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=feature,
                )

            result = observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            self.assertEqual(result["new_events"], [f"vellum-{feature}"])
            self.assertTrue((root / observatory.EVENTS_PATH / f"vellum-{feature}.yaml").is_file())
            # Events only: cursor and report are exactly as the receiver left them.
            self.assertEqual(observatory.load_json(root / observatory.CURSOR_PATH), cursor_before)
            self.assertEqual((root / observatory.REPORT_JSON_PATH).read_bytes(), report_before)

            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=feature,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["coverage_gaps"], [])
            # The #56 main-head hint is ancestry-only under Option A; a
            # lagging main tail is not required to be evidence-only.
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=feature,
                vellum_tail_policy="coverage", vellum_pr_main=main,
            )
            self.assertEqual(report["health"], "pass")
            # Still not acceptable to the reconciled policy: nothing relaxed there.
            with self.assertRaisesRegex(observatory.ObservatoryError, "evidence tail"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=feature,
                )

    def test_events_only_reconcile_leaves_mains_commits_to_main(self) -> None:
        # A pull request that merged main after the receiver's cursor records
        # only its own commits once it names main; without --vellum-main it
        # also records main's (the control), which collides with main's own
        # receiver on the next merge.
        for name_main in (False, True):
            with self.subTest(vellum_main=name_main), tempfile.TemporaryDirectory() as temporary:
                pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
                run(vellum, "git", "checkout", "-q", "main")
                write(vellum / "graphics/shared.cpp", "int shared = 2;\n")
                main_later = commit_all(vellum, "main lands a mapped change after the cursor")
                run(vellum, "git", "checkout", "-q", "feature")
                run(vellum, "git", "merge", "--no-edit", "main")
                head = run(vellum, "git", "rev-parse", "HEAD")
                result = observatory.reconcile(
                    root=root, pulp_repo=pulp, vellum_repo=vellum,
                    pulp_target=pulp_head, vellum_target=head,
                    now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
                    vellum_main=main_later if name_main else None,
                )
                self.assertIn(f"vellum-{feature}", result["new_events"])
                if name_main:
                    self.assertNotIn(f"vellum-{main_later}", result["new_events"])
                    self.assertFalse((root / observatory.EVENTS_PATH / f"vellum-{main_later}.yaml").exists())
                else:
                    self.assertIn(f"vellum-{main_later}", result["new_events"])

    def _main_with_merges(self, top: Path):
        """A reconciled state, then main lands a pull request through a covered
        integration merge, then a second through a merge that adds a mapped
        change of its own."""
        pulp = top / "pulp"
        vellum = top / "vellum-source"
        root = top / "state"
        pulp_base, pulp_head = init_repo(pulp, "unmapped/pulp.cpp")
        vellum_base, vellum_head = init_repo(vellum, "graphics/render.cpp")
        make_state(root, pulp_base, vellum_base)
        observatory.reconcile(root=root, pulp_repo=pulp, vellum_repo=vellum, pulp_target=pulp_head,
                              vellum_target=vellum_head, now_text="2026-10-11T00:00:00Z", write=True)
        run(vellum, "git", "checkout", "-q", "-b", "feature")
        write(vellum / "graphics/feature.cpp", "int feature = 1;\n")
        feature = commit_all(vellum, "feature mapped change")
        run(vellum, "git", "checkout", "-q", "main")
        # Main moves on meanwhile, so the merge's tree matches neither parent
        # and the merge maps like any real integration merge.
        write(vellum / "unmapped/notes.md", "main moved\n")
        commit_all(vellum, "main moves on")
        run(vellum, "git", "merge", "-q", "--no-ff", "--no-edit", "feature")
        covered = run(vellum, "git", "rev-parse", "HEAD")
        run(vellum, "git", "checkout", "-q", "-b", "second")
        write(vellum / "graphics/second.cpp", "int second = 1;\n")
        second = commit_all(vellum, "second mapped change")
        run(vellum, "git", "checkout", "-q", "main")
        write(vellum / "unmapped/notes.md", "main moved again\n")
        commit_all(vellum, "main moves on again")
        run(vellum, "git", "merge", "-q", "--no-ff", "--no-commit", "second")
        write(vellum / "graphics/render.cpp", "int value = 9;\n")  # the merge's own change
        evil = commit_all(vellum, "merge second with a change of its own")
        return pulp, vellum, root, pulp_head, feature, covered, second, evil

    def test_the_receiver_records_no_event_for_a_covered_integration_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, feature, covered, second, evil = self._main_with_merges(Path(temporary))
            mapping = observatory.load_json(root / observatory.MAP_PATH)
            self.assertTrue(observatory.integration_merge_covered(
                vellum, covered, mapping["mappings"], mapping.get("transitive_path_rules", [])))
            self.assertFalse(observatory.integration_merge_covered(
                vellum, evil, mapping["mappings"], mapping.get("transitive_path_rules", [])))
            result = observatory.reconcile(root=root, pulp_repo=pulp, vellum_repo=vellum, pulp_target=pulp_head,
                                           vellum_target=evil, now_text="2026-10-11T00:05:00Z", write=True)
            self.assertIn(f"vellum-{feature}", result["new_events"])
            self.assertIn(f"vellum-{second}", result["new_events"])
            self.assertIn(f"vellum-{evil}", result["new_events"])  # its own change still needs an event
            self.assertNotIn(f"vellum-{covered}", result["new_events"])
            self.assertFalse((root / observatory.EVENTS_PATH / f"vellum-{covered}.yaml").exists())
            # The reconciled range verifies without an event for the covered merge.
            report = observatory.verify(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                                        allow_no_git_base=True, pulp_target=pulp_head, vellum_target=evil)
            self.assertEqual(report["coverage_gaps"], [])

    def test_a_merge_with_its_own_mapped_change_still_needs_its_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, feature, covered, second, evil = self._main_with_merges(Path(temporary))
            observatory.reconcile(root=root, pulp_repo=pulp, vellum_repo=vellum, pulp_target=pulp_head,
                                  vellum_target=evil, now_text="2026-10-11T00:05:00Z", write=True)
            lock, mapping, cursor, budgets = observatory.validate_lock_map_cursor(root)
            events = [(path, event) for path, event in observatory.load_events(root)
                      if event["event_id"] != f"vellum-{evil}"]
            gaps = observatory.coverage_gaps(root=root, mapping=mapping, cursor=cursor, events=events,
                                             pulp_repo=pulp, vellum_repo=vellum)
            self.assertEqual(gaps, [{"source": "vellum", "commit": evil, "reason": "mapped-commit-has-no-event"}])

    def test_an_existing_event_for_a_covered_merge_still_verifies(self) -> None:
        # Ledgers written before the receiver skipped covered merges carry such
        # events; they stay valid and keep being checked against Git.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, feature, covered, second, evil = self._main_with_merges(Path(temporary))
            observatory.reconcile(root=root, pulp_repo=pulp, vellum_repo=vellum, pulp_target=pulp_head,
                                  vellum_target=evil, now_text="2026-10-11T00:05:00Z", write=True)
            lock, mapping, cursor, budgets = observatory.validate_lock_map_cursor(root)
            event = observatory.observation_for_commit(
                source="vellum", repository="Generous-Corp/vellum", repo=vellum, commit=covered,
                cursor_from=str(cursor["vellum"]["scan_base_commit"]), cursor_to=evil,
                discovered_at="2026-10-11T00:05:00Z", mappings=mapping["mappings"],
                transitive_rules=mapping.get("transitive_path_rules", []))
            self.assertIsNotNone(event)
            observatory.verify_event_against_git(event, "vellum", vellum, mapping["mappings"],
                                                 mapping.get("transitive_path_rules", []))

    def _branch_with_main_merged(self, top: Path):
        """`_coverage_state` plus a later mapped commit on main that the branch merged."""
        pulp, vellum, root, pulp_head, main, feature = self._coverage_state(top)
        run(vellum, "git", "checkout", "-q", "main")
        write(vellum / "graphics/shared.cpp", "int shared = 2;\n")
        main_later = commit_all(vellum, "main lands a mapped change after the cursor")
        run(vellum, "git", "checkout", "-q", "feature")
        run(vellum, "git", "merge", "--no-edit", "main")
        head = run(vellum, "git", "rev-parse", "HEAD")
        return pulp, vellum, root, pulp_head, main_later, feature, head

    def _cli_reconcile(self, root: Path, pulp: Path, vellum: Path, pulp_target: str, vellum_target: str,
                       *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = observatory.main([
                "--root", str(root), "reconcile", "--pulp-repo", str(pulp), "--vellum-repo", str(vellum),
                "--pulp-target", pulp_target, "--vellum-target", vellum_target,
                "--now", "2026-10-11T00:05:00Z", "--write", *extra,
            ])
        return code, err.getvalue()

    def _event_files(self, root: Path) -> set[str]:
        return {path.name for path in (root / observatory.EVENTS_PATH).glob("*.yaml")}

    def test_events_only_on_the_command_line_records_only_the_branchs_commits_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main_later, feature, head = self._branch_with_main_merged(
                Path(temporary))
            run(vellum, "git", "update-ref", observatory.VELLUM_MAIN_REF, main_later)
            before = self._event_files(root)
            code, err = self._cli_reconcile(root, pulp, vellum, pulp_head, head, "--events-only")
            self.assertEqual(code, 0, err)
            added = self._event_files(root) - before
            self.assertIn(f"vellum-{feature}.yaml", added)
            self.assertNotIn(f"vellum-{main_later}.yaml", added)

    def test_events_only_records_mains_commits_only_when_asked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main_later, feature, head = self._branch_with_main_merged(
                Path(temporary))
            run(vellum, "git", "update-ref", observatory.VELLUM_MAIN_REF, main_later)
            code, err = self._cli_reconcile(root, pulp, vellum, pulp_head, head, "--events-only",
                                            "--include-main-commits")
            self.assertEqual(code, 0, err)
            self.assertIn(f"vellum-{main_later}.yaml", self._event_files(root))
            self.assertIn(f"vellum-{feature}.yaml", self._event_files(root))

    def test_events_only_without_origin_main_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main_later, feature, head = self._branch_with_main_merged(
                Path(temporary))
            before = self._event_files(root)
            code, err = self._cli_reconcile(root, pulp, vellum, pulp_head, head, "--events-only")
            self.assertEqual(code, 1)
            self.assertIn("cannot resolve origin/main", err)
            self.assertEqual(self._event_files(root), before)  # nothing written
            # An explicit main still works without the remote ref.
            code, err = self._cli_reconcile(root, pulp, vellum, pulp_head, head, "--events-only",
                                            "--vellum-main", main_later)
            self.assertEqual(code, 0, err)
            self.assertNotIn(f"vellum-{main_later}.yaml", self._event_files(root))

    def test_a_full_reconcile_needs_the_receivers_flag_and_is_otherwise_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main_later, feature, head = self._branch_with_main_merged(
                Path(temporary))
            run(vellum, "git", "checkout", "-q", "main")
            cursor_before = (root / observatory.CURSOR_PATH).read_bytes()
            before = self._event_files(root)
            code, err = self._cli_reconcile(root, pulp, vellum, pulp_head, main_later)
            self.assertEqual(code, 1)
            self.assertIn("--include-main-commits", err)
            self.assertEqual((root / observatory.CURSOR_PATH).read_bytes(), cursor_before)
            self.assertEqual(self._event_files(root), before)
            # With the flag, the receiver's reconcile writes exactly what the
            # function always did, and needs no origin/main.
            twin = Path(temporary) / "twin"
            shutil.copytree(root, twin)
            code, err = self._cli_reconcile(root, pulp, vellum, pulp_head, main_later, "--include-main-commits")
            self.assertEqual(code, 0, err)
            observatory.reconcile(root=twin, pulp_repo=pulp, vellum_repo=vellum, pulp_target=pulp_head,
                                  vellum_target=main_later, now_text="2026-10-11T00:05:00Z", write=True)
            for relative in (observatory.CURSOR_PATH, observatory.REPORT_JSON_PATH):
                self.assertEqual((root / relative).read_bytes(), (twin / relative).read_bytes())
            self.assertEqual(self._event_files(root), self._event_files(twin))
            self.assertIn(f"vellum-{main_later}.yaml", self._event_files(root))

    def test_the_receiver_passes_the_include_main_commits_flag(self) -> None:
        workflow = (Path(__file__).resolve().parents[2]
                    / ".github/workflows/pulp-observatory-receiver.yml").read_text(encoding="utf-8")
        reconcile = workflow[workflow.index("observatory.py reconcile"):]
        reconcile = reconcile[:reconcile.index("--output")]
        self.assertIn("--include-main-commits", reconcile)
        self.assertNotIn("--events-only", reconcile)

    def test_vellum_main_and_include_main_commits_are_exclusive(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = observatory.main([
                "reconcile", "--pulp-repo", ".", "--vellum-repo", ".", "--pulp-target", "x",
                "--vellum-target", "y", "--now", "2026-10-11T00:00:00Z", "--write", "--events-only",
                "--vellum-main", "z", "--include-main-commits",
            ])
        self.assertEqual(code, 1)
        self.assertIn("are exclusive", err.getvalue())

    def test_verify_without_a_git_base_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            with self.assertRaisesRegex(observatory.ObservatoryError, "needs --git-base"):
                observatory.verify(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                                   pulp_target=pulp_head, vellum_target=main)
            observatory.verify(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                               allow_no_git_base=True, pulp_target=pulp_head, vellum_target=main)
            cli = ["--root", str(root), "verify", "--pulp-repo", str(pulp), "--vellum-repo", str(vellum),
                   "--pulp-target", pulp_head, "--vellum-target", main, "--now", "2026-10-11T00:05:00Z"]
            self.assertEqual(observatory.main(cli), 1)
            self.assertEqual(observatory.main(cli + ["--no-git-base"]), 0)
            self.assertEqual(observatory.main(cli + ["--no-git-base", "--git-base", main]), 1)

    def test_vellum_main_requires_events_only(self) -> None:
        self.assertEqual(observatory.main([
            "reconcile", "--pulp-repo", ".", "--vellum-repo", ".", "--pulp-target", "x",
            "--vellum-target", "y", "--now", "2026-10-11T00:00:00Z", "--write",
            "--vellum-main", "z",
        ]), 1)

    def test_coverage_policy_accepts_a_resolution_of_an_unreconciled_observation(
        self,
    ) -> None:
        # A pull request may classify the observation it records before the
        # receiver advances the cursor; the committed report excludes the
        # resolution together with the observation it resolves.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            observation = observatory.load_json(
                root / observatory.EVENTS_PATH / f"vellum-{feature}.yaml"
            )
            resolution = {
                "schema_version": 1, "event_id": f"resolution-vellum-{feature}",
                "kind": "resolution", "created_at": "2026-10-11T00:10:00Z",
                "resolves": observation["event_id"], "disposition": "framework-only",
                "rationale": "Vellum-owned change with no Pulp counterpart.",
                "owner": "@danielraffel", "linked_commits": [feature],
                "linked_pull_requests": [], "contract_tests": observation["contract_tests"],
                "shared_contract_release_blocker": False, "effort_minutes": 0,
            }
            observatory.write_json_atomic(
                root / observatory.EVENTS_PATH / f"resolution-vellum-{feature}.yaml",
                resolution,
            )
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=feature,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["coverage_gaps"], [])

    def test_coverage_policy_exempts_a_clean_queue_merge_but_not_a_resolved_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            # main moves on with an unrelated mapped file while the PR waits, and
            # the receiver reconciles main there (the PR's event is not on main).
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "graphics/other.cpp", "int other = 1;\n")
            other = commit_all(vellum, "main mapped change elsewhere")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=other,
                now_text="2026-10-11T00:06:00Z", write=True,
            )
            # The PR records its own observation without touching the cursor.
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            # The queue's merge commit: main tip + PR head, no resolution edits.
            run(vellum, "git", "merge", "-q", "--no-ff", "feature", "-m", "queue merge")
            queued = run(vellum, "git", "rev-parse", "HEAD")
            self.assertTrue(observatory.integration_merge_covered(vellum, queued))
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=queued,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])

            # Negative control: a merge whose resolution writes content neither
            # parent carries is a change of its own and needs its own event.
            run(vellum, "git", "checkout", "-q", "-b", "conflict", other)
            write(vellum / "graphics/render.cpp", "int value = 4;\n")
            commit_all(vellum, "main-side change to the same file")
            merge = subprocess.run(
                ["git", "merge", "--no-ff", "feature", "-m", "resolved merge"],
                cwd=vellum, check=False, capture_output=True, text=True,
            )
            self.assertNotEqual(merge.returncode, 0)
            write(vellum / "graphics/render.cpp", "int value = 5;\n")
            run(vellum, "git", "add", ".")
            run(vellum, "git", "commit", "-q", "-m", "resolved merge")
            resolved = run(vellum, "git", "rev-parse", "HEAD")
            self.assertFalse(observatory.integration_merge_covered(vellum, resolved))
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=resolved,
                    vellum_tail_policy="coverage",
                )
            # Recording the merge's own observation closes the gap.
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=resolved,
                now_text="2026-10-11T00:07:00Z", write=True, events_only=True,
            )
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=resolved,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])

            # Negative control: a merge that smuggles a path the pull request
            # never touched is not covered by the pull request's events.
            run(vellum, "git", "checkout", "-q", "-b", "smuggle", other)
            run(vellum, "git", "merge", "-q", "--no-ff", "--no-commit", "feature")
            write(vellum / "graphics/smuggled.cpp", "int smuggled = 1;\n")
            run(vellum, "git", "add", ".")
            run(vellum, "git", "commit", "-q", "-m", "merge with an extra file")
            smuggled = run(vellum, "git", "rev-parse", "HEAD")
            # The conflict branch's events are not in this tree: drop the
            # events of commits that `smuggled` does not contain.
            for event_path in (root / observatory.EVENTS_PATH).glob("vellum-*.yaml"):
                commit = event_path.stem.split("-", 1)[1]
                contained = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", commit, smuggled],
                    cwd=vellum, capture_output=True,
                ).returncode == 0
                if not contained:
                    event_path.unlink()
            self.assertFalse(observatory.integration_merge_covered(vellum, smuggled))
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=smuggled,
                    vellum_tail_policy="coverage",
                )

            # Negative control: a merge that quietly reverts main's own change
            # (taking the pull request's older blob for a path the pull request
            # never touched) is not an integration merge either.
            run(vellum, "git", "checkout", "-q", "-b", "revert", other)
            write(vellum / "graphics/shared.cpp", "int shared = 2;\n")
            commit_all(vellum, "main-side change to a shared file")
            run(vellum, "git", "merge", "-q", "--no-ff", "--no-commit", "feature")
            run(vellum, "git", "checkout", "-q", "feature", "--", "graphics/shared.cpp")
            run(vellum, "git", "commit", "-q", "-m", "merge that reverts main's change")
            reverting = run(vellum, "git", "rev-parse", "HEAD")
            self.assertFalse(observatory.integration_merge_covered(vellum, reverting))

    def test_coverage_policy_accepts_an_evidence_commit_landing_behind_a_moved_main(
        self,
    ) -> None:
        # The receiver reconciles main at X and opens an evidence PR; by the time
        # the queue merges it, main has moved to Y (a clean merge of a PR whose
        # events are present). The evidence commit still passes.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            run(vellum, "git", "merge", "-q", "--no-ff", "feature", "-m", "land feature")
            landed = run(vellum, "git", "rev-parse", "HEAD")
            # Receiver reconciles main at `landed` (cursor + report move).
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=landed,
                now_text="2026-10-11T00:06:00Z", write=True,
            )
            # Meanwhile another covered PR lands on main.
            run(vellum, "git", "checkout", "-q", "-b", "second", landed)
            write(vellum / "graphics/second.cpp", "int second = 1;\n")
            second = commit_all(vellum, "second mapped change")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=second,
                now_text="2026-10-11T00:07:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            run(vellum, "git", "merge", "-q", "--no-ff", "second", "-m", "land second")
            moved = run(vellum, "git", "rev-parse", "HEAD")
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=moved,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])
            self.assertEqual(
                observatory.load_json(root / observatory.CURSOR_PATH)["vellum"]["last_scanned_commit"],
                landed,
            )

    def test_merge_that_overwrites_mains_change_to_a_mapped_file_needs_its_own_event(
        self,
    ) -> None:
        # R7 E1b: the PR changes graphics/render.cpp, main lands its own change
        # to the same file (with an event), and the PR's hand-resolved merge
        # takes the PR's blob. That reverts main's fix and must not be covered.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "graphics/render.cpp", "int value = 99; // main fix\n")
            m1 = commit_all(vellum, "main fix to the same file")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=m1,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            write(vellum / "unmapped/readme.txt", "unrelated\n")
            commit_all(vellum, "unrelated main commit")
            run(vellum, "git", "checkout", "-q", "feature")
            merge = subprocess.run(
                ["git", "merge", "--no-ff", "main", "-m", "resolve by taking the PR side"],
                cwd=vellum, check=False, capture_output=True, text=True,
            )
            run(vellum, "git", "checkout", "-q", "feature", "--", "graphics/render.cpp")
            if merge.returncode != 0:
                run(vellum, "git", "add", ".")
                run(vellum, "git", "commit", "-q", "-m", "resolve by taking the PR side")
            else:
                run(vellum, "git", "commit", "-q", "--amend", "--no-edit", "-a")
            reverting = run(vellum, "git", "rev-parse", "HEAD")
            self.assertEqual(
                run(vellum, "git", "show", "HEAD:graphics/render.cpp").strip(), "int value = 3;"
            )
            self.assertFalse(observatory.integration_merge_covered(vellum, reverting))
            # Author direction (first parent = the PR): the first-parent diff
            # shows no mapped change, so no event can describe the drop; the
            # gap names it and the author must make the revert an explicit commit.
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed") as raised:
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=reverting,
                    vellum_tail_policy="coverage",
                )
            self.assertIn(
                f"vellum {reverting}: merge-changes-other-side-without-first-parent-diff",
                str(raised.exception),
            )
            self.assertIn("make that change an explicit commit", str(raised.exception))
            gaps = observatory.coverage_gaps(
                root=root, mapping=observatory.load_json(root / observatory.MAP_PATH),
                cursor=observatory.load_json(root / observatory.CURSOR_PATH),
                events=observatory.load_events(root), pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=reverting, vellum_tail_policy="coverage",
            )
            self.assertEqual(
                [gap["reason"] for gap in gaps if gap["commit"] == reverting],
                ["merge-changes-other-side-without-first-parent-diff"],
            )
            # Queue direction (first parent = main), R7's literal E1b: the diff
            # against main shows the revert, so it needs an event and gets one.
            main_tip = run(vellum, "git", "rev-parse", "main")
            run(vellum, "git", "checkout", "-q", "-b", "queue-shape", main_tip)
            merge = subprocess.run(
                ["git", "merge", "--no-ff", str(feature), "-m", "queue-shaped resolve taking the PR side"],
                cwd=vellum, check=False, capture_output=True, text=True,
            )
            run(vellum, "git", "checkout", "-q", str(feature), "--", "graphics/render.cpp")
            if merge.returncode != 0:
                run(vellum, "git", "add", ".")
                run(vellum, "git", "commit", "-q", "-m", "queue-shaped resolve taking the PR side")
            else:
                run(vellum, "git", "commit", "-q", "--amend", "--no-edit", "-a")
            queue_shaped = run(vellum, "git", "rev-parse", "HEAD")
            self.assertFalse(observatory.integration_merge_covered(vellum, queue_shaped))
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=queue_shaped,
                    vellum_tail_policy="coverage",
                )
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=queue_shaped,
                now_text="2026-10-11T00:07:00Z", write=True, events_only=True,
            )
            self.assertTrue((root / observatory.EVENTS_PATH / f"vellum-{queue_shaped}.yaml").is_file())
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=queue_shaped,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])

    def _resolved_merge(self, vellum: Path, onto: str, other: str, keep_from: str,
                        path: str, message: str) -> str:
        """Merge `other` into `onto`, resolving `path` to `keep_from`'s version."""
        run(vellum, "git", "checkout", "-q", onto)
        merge = subprocess.run(
            ["git", "merge", "--no-ff", other, "-m", message],
            cwd=vellum, check=False, capture_output=True, text=True,
        )
        run(vellum, "git", "checkout", "-q", keep_from, "--", path)
        if merge.returncode != 0:
            run(vellum, "git", "add", ".")
            run(vellum, "git", "commit", "-q", "-m", message)
        else:
            run(vellum, "git", "commit", "-q", "--amend", "--no-edit", "-a")
        return run(vellum, "git", "rev-parse", "HEAD")

    def test_merge_that_resets_mains_mode_change_needs_its_own_event(self) -> None:
        # R7 delta D1 (exp5 A2): main makes a mapped file executable, with an
        # event; the merge keeps the blob but resets the mode. Content is
        # unchanged, so a blob-only rule would call it covered.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            os.chmod(vellum / "graphics/shared.cpp", 0o755)
            m1 = commit_all(vellum, "main makes the shared file executable")
            self.assertIn("100755", run(vellum, "git", "ls-tree", m1, "--", "graphics/shared.cpp"))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=m1,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            for onto, other in (("feature", "main"), ("main", "feature")):
                run(vellum, "git", "checkout", "-q", "-B", f"shape-{onto}", onto)
                merge = self._resolved_merge(
                    vellum, f"shape-{onto}", other, "feature", "graphics/shared.cpp",
                    f"resolve with the pull request's mode, onto {onto}",
                )
                entry = run(vellum, "git", "ls-tree", merge, "--", "graphics/shared.cpp")
                self.assertIn("100644", entry)
                self.assertEqual(
                    run(vellum, "git", "show", f"{merge}:graphics/shared.cpp"),
                    run(vellum, "git", "show", f"{m1}:graphics/shared.cpp"),
                )
                with self.subTest(first_parent=onto):
                    self.assertFalse(observatory.integration_merge_covered(vellum, merge))
                    with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                        observatory.verify(
                            root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                            pulp_target=pulp_head, vellum_target=merge,
                            vellum_tail_policy="coverage",
                        )
            # Control: a merge that keeps main's mode is a plain integration merge.
            run(vellum, "git", "checkout", "-q", "-B", "clean", "main")
            clean = self._resolved_merge(
                vellum, "clean", "feature", "main", "graphics/shared.cpp", "keep main's mode",
            )
            self.assertTrue(observatory.integration_merge_covered(vellum, clean))

    def test_identical_change_on_both_sides_is_covered(self) -> None:
        # R7 delta D2: a hotfix cherry-picked into the pull request that main
        # also landed. Both sides and the merge hold the same entry, so the
        # merge adds nothing and must not eject the entry from the queue.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            write(vellum / "graphics/shared.cpp", "int shared = 2; // hotfix\n")
            hotfix = commit_all(vellum, "hotfix on the pull request")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=hotfix,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "graphics/shared.cpp", "int shared = 2; // hotfix\n")
            m1 = commit_all(vellum, "the same hotfix on main")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=m1,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "-B", "queue", "main")
            run(vellum, "git", "merge", "--no-ff", "-q", "feature", "-m", "queue merge")
            queue = run(vellum, "git", "rev-parse", "HEAD")
            self.assertTrue(observatory.integration_merge_covered(vellum, queue))
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=queue, vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])
            # Negative control: the same shape with a different resolution on
            # main is a change of the merge's own.
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "graphics/shared.cpp", "int shared = 3; // different\n")
            m2 = commit_all(vellum, "main resolves differently")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=m2,
                now_text="2026-10-11T00:07:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "-B", "queue2", "main")
            differing = self._resolved_merge(
                vellum, "queue2", "feature", "main", "graphics/shared.cpp", "resolve to main",
            )
            self.assertFalse(observatory.integration_merge_covered(vellum, differing))

    def test_disjoint_hunks_of_one_file_from_both_sides_are_covered(self) -> None:
        # Two pull requests edit different hunks of one mapped file. The queue
        # merge's blob matches neither side, but it is exactly git's clean
        # three-way merge of them, so each side's own event covers it and the
        # entry must not be ejected for a gap no branch can record.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, _feature = self._coverage_state(Path(temporary))
            lines = [f"int line_{index} = {index};" for index in range(12)]
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "graphics/shared.cpp", "\n".join(lines) + "\n")
            base = commit_all(vellum, "a longer shared file")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=base,
                now_text="2026-10-11T00:04:00Z", write=True,
            )
            run(vellum, "git", "checkout", "-q", "-b", "top-edit", base)
            write(vellum / "graphics/shared.cpp",
                  "\n".join(["int line_0 = 100;", *lines[1:]]) + "\n")
            top = commit_all(vellum, "edit the top hunk")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=top,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "graphics/shared.cpp",
                  "\n".join([*lines[:-1], "int line_11 = 1100;"]) + "\n")
            bottom = commit_all(vellum, "main edits the bottom hunk")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=bottom,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "-B", "queue", "main")
            run(vellum, "git", "merge", "--no-ff", "-q", "top-edit", "-m", "queue merge")
            queue = run(vellum, "git", "rev-parse", "HEAD")
            mapping = observatory.load_json(root / observatory.MAP_PATH)
            rules = mapping.get("transitive_path_rules", [])
            self.assertTrue(observatory.integration_merge_covered(vellum, queue))
            self.assertTrue(observatory.integration_merge_covered(
                vellum, queue, mapping["mappings"], rules))
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=queue, vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])
            # Negative control: the same merge with one extra line of its own
            # is not the clean merge of its sides, so it needs its own event.
            run(vellum, "git", "checkout", "-q", "-B", "queue-edit", "main")
            run(vellum, "git", "merge", "--no-ff", "--no-commit", "-q", "top-edit")
            with (vellum / "graphics/shared.cpp").open("a", encoding="utf-8") as handle:
                handle.write("int smuggled = 1;\n")
            run(vellum, "git", "add", ".")
            run(vellum, "git", "commit", "-q", "-m", "queue merge with a line of its own")
            edited = run(vellum, "git", "rev-parse", "HEAD")
            self.assertFalse(observatory.integration_merge_covered(vellum, edited))
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=edited, vellum_tail_policy="coverage",
                )
            # Negative control: a mode both sides changed differently is a
            # change of the merge's own even when the content merges cleanly.
            run(vellum, "git", "checkout", "-q", "-B", "mode-pr", base)
            write(vellum / "graphics/shared.cpp",
                  "\n".join(["int line_0 = 7;", *lines[1:]]) + "\n")
            commit_all(vellum, "pr hunk")
            run(vellum, "git", "checkout", "-q", "-B", "mode-main", base)
            write(vellum / "graphics/shared.cpp",
                  "\n".join([*lines[:-1], "int line_11 = 7;"]) + "\n")
            os.chmod(vellum / "graphics/shared.cpp", 0o755)
            commit_all(vellum, "main hunk and an executable bit")
            run(vellum, "git", "merge", "--no-ff", "--no-commit", "-q", "mode-pr")
            os.chmod(vellum / "graphics/shared.cpp", 0o644)
            run(vellum, "git", "add", ".")
            run(vellum, "git", "commit", "-q", "-m", "merge that drops main's mode")
            reset = run(vellum, "git", "rev-parse", "HEAD")
            self.assertFalse(observatory.integration_merge_covered(vellum, reset))

    def test_merge_that_reverts_a_transitive_build_file_needs_its_own_event(self) -> None:
        # R7 delta D5: with a file-level mapping (cli/backend.py), cli/CMakeLists.txt
        # is transitive-only: never mapped on its own, but listed under
        # transitive_paths when it lands next to the mapped file. A merge that
        # drops main's change to it while keeping the mapped change must not be
        # covered; a per-path mapping filter cannot see the build file at all.
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp, vellum, root = top / "pulp", top / "vellum-source", top / "state"
            pulp_base, pulp_head = init_repo(pulp, "unmapped/pulp.cpp")
            vellum_base, _ = init_repo(vellum, "graphics/render.cpp")
            write(vellum / "cli/backend.py", "VERSION = 1\n")
            write(vellum / "cli/CMakeLists.txt", "add_subdirectory(backend)\n")
            vellum_main = commit_all(vellum, "cli mapped file and its build file")
            make_state(root, pulp_base, vellum_base)
            mapping_path = root / observatory.MAP_PATH
            mapping = observatory.load_json(mapping_path)
            mapping["mappings"].append({
                "id": "cli", "pulp_paths": ["tools/cli/"], "vellum_paths": ["cli/backend.py"],
                "symbols": [], "targets": [], "schemas": [], "platform_hosts": [],
                "contract_tests": ["cli-contract"], "authority": "pulp-authoritative-untransferred",
            })
            write_json(mapping_path, mapping)
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=vellum_main,
                now_text="2026-10-11T00:00:00Z", write=True,
            )
            rules = mapping["transitive_path_rules"]
            # The build file alone never maps; next to the mapped file it is transitive.
            self.assertIsNone(observatory.mapped_change(
                [{"status": "M", "path": "cli/CMakeLists.txt"}], mapping["mappings"], "vellum", rules,
            ))
            run(vellum, "git", "checkout", "-q", "-b", "feature")
            write(vellum / "graphics/render.cpp", "int value = 3;\n")
            feature = commit_all(vellum, "feature mapped change")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "cli/backend.py", "VERSION = 2\n")
            write(vellum / "cli/CMakeLists.txt", "add_subdirectory(backend)\nadd_test(backend)\n")
            m1 = commit_all(vellum, "main changes the build file with a mapped change")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=m1,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            event = observatory.load_json(root / observatory.EVENTS_PATH / f"vellum-{m1}.yaml")
            self.assertEqual(event["transitive_paths"], ["cli/CMakeLists.txt"])
            run(vellum, "git", "checkout", "-q", "-B", "queue", "main")
            run(vellum, "git", "merge", "--no-ff", "-q", "feature", "-m", "queue merge")
            run(vellum, "git", "checkout", "-q", str(vellum_main), "--", "cli/CMakeLists.txt")
            run(vellum, "git", "commit", "-q", "--amend", "--no-edit")
            reverting = run(vellum, "git", "rev-parse", "HEAD")
            self.assertEqual(run(vellum, "git", "show", f"{reverting}:cli/backend.py").strip(), "VERSION = 2")
            self.assertEqual(
                run(vellum, "git", "show", f"{reverting}:cli/CMakeLists.txt").strip(), "add_subdirectory(backend)",
            )
            self.assertFalse(
                observatory.integration_merge_covered(vellum, reverting, mapping["mappings"], rules)
            )
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=reverting,
                    vellum_tail_policy="coverage",
                )
            # Control: carrying main's build file over is a plain integration merge.
            run(vellum, "git", "checkout", "-q", "-B", "clean", "main")
            run(vellum, "git", "merge", "--no-ff", "-q", "feature", "-m", "clean queue merge")
            clean = run(vellum, "git", "rev-parse", "HEAD")
            self.assertTrue(
                observatory.integration_merge_covered(vellum, clean, mapping["mappings"], rules)
            )
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=clean, vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])

    def test_deleting_a_mapped_file_lands_through_a_moved_main(self) -> None:
        # R7 E2b: the PR deletes graphics/shared.cpp; main moves with an
        # unmapped commit; the clean queue merge must be covered.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            run(vellum, "git", "rm", "-q", "graphics/shared.cpp")
            deleted = commit_all(vellum, "delete a mapped file")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=deleted,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "unmapped/readme.txt", "unrelated\n")
            commit_all(vellum, "unrelated main commit")
            run(vellum, "git", "merge", "-q", "--no-ff", "feature", "-m", "queue merge")
            queued = run(vellum, "git", "rev-parse", "HEAD")
            self.assertTrue(observatory.integration_merge_covered(vellum, queued))
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=queued,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])

    def test_batch_entries_sharing_only_unmapped_files_are_both_covered(self) -> None:
        # R7 E3b: two PRs each edit a different line of an unmapped file plus
        # their own mapped file. Entry 2 of the batch must still be covered.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            mapping = observatory.load_json(root / observatory.MAP_PATH)
            rules = mapping.get("transitive_path_rules", [])
            write(vellum / "unmapped/readme.txt", "line one from PR1\nline two\n")
            pr1 = commit_all(vellum, "PR1 touches the unmapped file")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=pr1,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "-b", "pr2", main)
            write(vellum / "graphics/other.cpp", "int other = 1;\n")
            write(vellum / "unmapped/readme.txt", "line one\nline two from PR2\n")
            pr2 = commit_all(vellum, "PR2 touches the unmapped file")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=pr2,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            run(vellum, "git", "merge", "-q", "--no-ff", "feature", "-m", "queue entry 1")
            q1 = run(vellum, "git", "rev-parse", "HEAD")
            merge = subprocess.run(
                ["git", "merge", "--no-ff", "pr2", "-m", "queue entry 2"],
                cwd=vellum, check=False, capture_output=True, text=True,
            )
            if merge.returncode != 0:
                # GitHub resolves line-disjoint edits automatically; emulate it.
                write(vellum / "unmapped/readme.txt", "line one from PR1\nline two from PR2\n")
                run(vellum, "git", "add", ".")
                run(vellum, "git", "commit", "-q", "-m", "queue entry 2")
            q2 = run(vellum, "git", "rev-parse", "HEAD")
            self.assertTrue(observatory.integration_merge_covered(vellum, q1, mapping["mappings"], rules))
            self.assertTrue(observatory.integration_merge_covered(vellum, q2, mapping["mappings"], rules))
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=q2,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])

    def test_pull_request_behind_a_lagging_main_passes_on_its_merge_ref(self) -> None:
        # R7 E4b/E4c: PR A lands with its event (cursor now lags); PR B is
        # behind main. Judged on its merge ref (what CI checks out and the
        # queue builds) B passes; judged on its bare head it reports a stale
        # report, which is why the workflow targets github.sha.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, a_head = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=a_head,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "-b", "b", main)
            write(vellum / "graphics/other.cpp", "int other = 1;\n")
            b_head = commit_all(vellum, "PR B mapped change")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=b_head,
                now_text="2026-10-11T00:06:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            run(vellum, "git", "merge", "-q", "--no-ff", "feature", "-m", "land A")
            run(vellum, "git", "checkout", "-q", "-b", "b-merge-ref", "main")
            run(vellum, "git", "merge", "-q", "--no-ff", "b", "-m", "refs/pull/B/merge")
            merge_ref = run(vellum, "git", "rev-parse", "HEAD")
            report = observatory.verify(
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                pulp_target=pulp_head, vellum_target=merge_ref,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])
            with self.assertRaisesRegex(observatory.ObservatoryError, "stale|outside"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=b_head,
                    vellum_tail_policy="coverage",
                )

    def test_merge_that_rewrites_a_pull_request_only_path_is_not_covered(self) -> None:
        # R7 M3 guard: only the PR changed graphics/render.cpp, main did not,
        # yet the merge carries a third blob (a resolution edit). The blob rule
        # is the only thing that sees this.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            run(vellum, "git", "checkout", "-q", "main")
            write(vellum / "unmapped/readme.txt", "unrelated\n")
            commit_all(vellum, "unrelated main commit")
            run(vellum, "git", "merge", "-q", "--no-ff", "--no-commit", "feature")
            write(vellum / "graphics/render.cpp", "int value = 7; // edited in the merge\n")
            run(vellum, "git", "add", ".")
            run(vellum, "git", "commit", "-q", "-m", "merge with an edited PR-only path")
            edited = run(vellum, "git", "rev-parse", "HEAD")
            self.assertFalse(observatory.integration_merge_covered(vellum, edited))
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True,
                    pulp_target=pulp_head, vellum_target=edited,
                    vellum_tail_policy="coverage",
                )

    def test_octopus_merge_is_never_covered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            run(vellum, "git", "checkout", "-q", "-b", "second", main)
            write(vellum / "graphics/other.cpp", "int other = 1;\n")
            commit_all(vellum, "second branch")
            run(vellum, "git", "checkout", "-q", "main")
            run(vellum, "git", "merge", "-q", "--no-ff", "feature", "second", "-m", "octopus")
            octopus = run(vellum, "git", "rev-parse", "HEAD")
            self.assertEqual(len(run(vellum, "git", "rev-list", "--parents", "-n", "1", octopus).split()), 4)
            self.assertFalse(observatory.integration_merge_covered(vellum, octopus))

    def test_cli_default_tail_policy_is_reconciled(self) -> None:
        # R7 M19: without the flag a non-evidence tail must still be rejected.
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=feature,
                now_text="2026-10-11T00:05:00Z", write=True, events_only=True,
            )
            common = [
                "--root", str(root), "verify", "--pulp-repo", str(pulp),
                "--vellum-repo", str(vellum), "--pulp-target", pulp_head,
                "--vellum-target", feature, "--now", "2026-10-11T00:05:00Z", "--no-git-base",
            ]
            self.assertEqual(observatory.main(common), 1)
            self.assertEqual(observatory.main(common + ["--vellum-tail-policy", "coverage"]), 0)

    def test_ci_routes_tags_to_reconciled_and_everything_else_to_coverage(self) -> None:
        # R7 M15: run the workflow's own shell fragment under each ref kind.
        workflow = (
            observatory.ROOT / ".github/workflows/provenance.yml"
        ).read_text(encoding="utf-8")
        start = workflow.index("observatory_tail_policy=coverage")
        end = workflow.index("fi", start) + 2
        fragment = "\n".join(line.strip() for line in workflow[start:end].splitlines())
        for ref, expected in (
            ("refs/tags/v0.1.8", "reconciled"),
            ("refs/tags/authority/x", "reconciled"),
            ("refs/pull/7/merge", "coverage"),
            ("refs/heads/gh-readonly-queue/main/pr-7-abc", "coverage"),
            ("refs/heads/main", "coverage"),
        ):
            with self.subTest(ref=ref):
                out = subprocess.run(
                    ["bash", "-c", fragment + '\necho "$observatory_tail_policy"'],
                    env={"GITHUB_REF": ref, "PATH": "/usr/bin:/bin"},
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                self.assertEqual(out, expected)
        self.assertIn('--vellum-tail-policy "$observatory_tail_policy"', workflow)

    def test_ci_judges_a_tag_at_its_commit_time_and_everything_else_now(self) -> None:
        # A tag's tree is frozen, so its health is judged at the tagged commit's
        # committer time; a re-run after a deadline must not start failing.
        workflow = (
            observatory.ROOT / ".github/workflows/provenance.yml"
        ).read_text(encoding="utf-8")
        start = workflow.index("observatory_now_args=()")
        end = workflow.index("fi", start) + 2
        fragment = "\n".join(line.strip() for line in workflow[start:end].splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            run(repo, "git", "init", "-q")
            env = {"PATH": "/usr/bin:/bin", "HOME": temporary, "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_COMMITTER_DATE": "2026-07-22T20:00:00+02:00", "GIT_AUTHOR_DATE": "2026-07-22T20:00:00+02:00"}
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                            "--allow-empty", "-m", "tagged"], cwd=repo, env=env, check=True)
            head = run(repo, "git", "rev-parse", "HEAD")
            for ref, expected in (
                ("refs/tags/v0.1.8", "--now 2026-07-22T18:00:00Z"),
                ("refs/tags/authority/x", "--now 2026-07-22T18:00:00Z"),
                ("refs/pull/7/merge", ""),
                ("refs/heads/main", ""),
            ):
                with self.subTest(ref=ref):
                    out = subprocess.run(
                        ["bash", "-c", fragment + '\necho "${observatory_now_args[*]}"'],
                        env={"GITHUB_REF": ref, "PATH": "/usr/bin:/bin", "observatory_vellum_target": head},
                        cwd=repo, capture_output=True, text=True, check=True,
                    ).stdout.strip()
                    self.assertEqual(out, expected)
        self.assertIn('"${observatory_now_args[@]}"', workflow)

    def test_events_only_requires_write_on_the_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "unmapped/pulp.cpp")
            vellum_base, vellum_head = init_repo(vellum, "unmapped/file.cpp")
            make_state(root, pulp_base, vellum_base)
            self.assertEqual(
                observatory.main([
                    "--root", str(root), "reconcile",
                    "--pulp-repo", str(pulp), "--vellum-repo", str(vellum),
                    "--pulp-target", pulp_head, "--vellum-target", vellum_head,
                    "--now", "2026-10-11T00:00:00Z", "--events-only",
                ]),
                1,
            )

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
                git_base=None, allow_no_git_base=True,
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["pending"], 2)
            self.assertEqual(report["coverage_gaps"], [])

    def test_verify_judges_overdue_events_at_the_time_it_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/render/render.cpp")
            vellum_base, vellum_head = init_repo(vellum, "graphics/render.cpp")
            make_state(root, pulp_base, vellum_base)
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=vellum_head,
                now_text="2026-07-22T20:00:00Z", write=True,
            )
            arguments = dict(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None, allow_no_git_base=True)
            at_reconcile = observatory.parse_utc("2026-07-22T20:00:00Z", "now")
            self.assertEqual(observatory.verify(**arguments, check_now=at_reconcile)["health"], "pass")
            # Without check_now the budgets are judged at reconciled_at, as before.
            self.assertEqual(observatory.verify(**arguments)["health"], "pass")
            # A year on, both pending observations are long past their deadline.
            later = observatory.parse_utc("2027-07-22T20:00:00Z", "now")
            with self.assertRaisesRegex(observatory.ObservatoryError, "overdue-event-count-exceeded"):
                observatory.verify(**arguments, check_now=later)
            cli = ["--root", str(root), "verify", "--pulp-repo", str(pulp), "--vellum-repo", str(vellum), "--no-git-base"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(observatory.main([*cli, "--now", "2026-07-22T20:00:00Z"]), 0)
                self.assertNotEqual(observatory.main([*cli, "--now", "2027-07-22T20:00:00Z"]), 0)

    def test_time_budgets_are_reported_on_pull_requests_and_enforced_on_releases(self) -> None:
        # The negative control is the same tree judged twice at the same time:
        # one overdue state, enforced (main, tags) versus reported (pull
        # requests and queue entries, which cannot change the clock).
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/render/render.cpp")
            vellum_base, vellum_head = init_repo(vellum, "graphics/render.cpp")
            make_state(root, pulp_base, vellum_base)
            # The state is a repository, so verify can tell the change's own
            # events from debt that was already on its base.
            run(root, "git", "init", "-q", "-b", "main")
            run(root, "git", "config", "user.email", "observatory@example.invalid")
            run(root, "git", "config", "user.name", "Observatory Test")
            empty_base = commit_all(root, "state without events")
            # Discovery is stamped after the fixture commits, as a real
            # reconcile is; "later" is a year past every deadline.
            discovered = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
            discovered_text = discovered.strftime("%Y-%m-%dT%H:%M:%SZ")
            observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=vellum_head,
                now_text=discovered_text, write=True,
            )
            events_base = commit_all(root, "reconciled events")
            self.assertEqual(observatory.TIME_BUDGET_VIOLATIONS, frozenset({
                "overdue-event-count-exceeded", "pending-event-age-exceeded",
            }))
            arguments = dict(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=events_base)
            later = discovered + timedelta(days=365)
            with self.assertRaisesRegex(observatory.ObservatoryError, "overdue-event-count-exceeded"):
                observatory.verify(**arguments, check_now=later, time_budgets="enforce")
            report = observatory.verify(**arguments, check_now=later, time_budgets="report")
            self.assertEqual(set(report["reported_time_budget_violations"]),
                             {"overdue-event-count-exceeded", "pending-event-age-exceeded"})
            self.assertEqual(report["health"], "fail")
            # Events the change itself adds that are already late are its own
            # debt (for example a backdated discovered_at): still fatal.
            with self.assertRaisesRegex(observatory.ObservatoryError, "new-event-already-late"):
                observatory.verify(**{**arguments, "git_base": empty_base},
                                   check_now=later, time_budgets="report")
            # Without a base to tell them apart, nothing is relaxed.
            with self.assertRaisesRegex(observatory.ObservatoryError, "overdue-event-count-exceeded"):
                observatory.verify(**{**arguments, "git_base": None, "allow_no_git_base": True},
                                   check_now=later, time_budgets="report")
            # Budgets that are not about time stay fatal under report.
            real_build_report = observatory.build_report

            def with_pending_overflow(**kwargs):
                value = real_build_report(**kwargs)
                if kwargs.get("now") != later:
                    return value  # the committed report's staleness check
                return {**value, "health": "fail", "budget_violations": [
                    *value["budget_violations"], "pending-event-count-exceeded"]}

            with mock.patch.object(observatory, "build_report", with_pending_overflow):
                with self.assertRaisesRegex(observatory.ObservatoryError,
                                            "pending-event-count-exceeded"):
                    observatory.verify(**arguments, check_now=later, time_budgets="report")
                # The same ledger-wide debt is already on the PR base. It is
                # visible in the report, but it cannot fail the PR-side gate.
                scoped = observatory.verify(
                    **arguments,
                    check_now=later,
                    time_budgets="report",
                    pending_event_scope="introduced",
                )
                self.assertEqual(scoped["pending_event_budget_scope"], "introduced")
                self.assertEqual(scoped["pending_event_budget_count"], 0)
                # Negative control: an introduced pending count above the
                # configured budget remains fatal even in PR scope.
                with mock.patch.object(
                    observatory, "pending_event_count", return_value=21
                ):
                    with self.assertRaisesRegex(
                        observatory.ObservatoryError,
                        "pending-event-count-exceeded",
                    ):
                        observatory.verify(
                            **arguments,
                            check_now=later,
                            time_budgets="report",
                            pending_event_scope="introduced",
                        )
            # In time, nothing is reported at all.
            on_time = observatory.verify(**arguments, check_now=discovered, time_budgets="report")
            self.assertNotIn("reported_time_budget_violations", on_time)
            cli = ["--root", str(root), "verify", "--pulp-repo", str(pulp), "--vellum-repo", str(vellum),
                   "--git-base", events_base, "--now", later.strftime("%Y-%m-%dT%H:%M:%SZ")]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(observatory.main(cli), 0)
                self.assertNotEqual(observatory.main([*cli, "--time-budgets", "enforce"]), 0)
                self.assertEqual(observatory.main([*cli, "--time-budgets", "report"]), 0)
                with self.assertRaises(SystemExit):
                    observatory.main([*cli, "--time-budgets", "ignore"])
            # A change cannot backdate (or postdate) the events it adds.
            event_path = sorted((root / observatory.EVENTS_PATH).glob("*.yaml"))[0]
            original = event_path.read_text()
            for stamp, message in (("2020-01-01T00:00:00Z", "precedes its commit"),
                                   ((later + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "in the future")):
                with self.subTest(stamp=stamp):
                    event_path.write_text(original.replace(discovered_text, stamp))
                    with self.assertRaisesRegex(observatory.ObservatoryError, message):
                        observatory.verify(**{**arguments, "git_base": empty_base},
                                           check_now=later, time_budgets="report")
            event_path.write_text(original)
            with self.assertRaisesRegex(observatory.ObservatoryError, "unknown time-budget mode"):
                observatory.verify(**arguments, check_now=later, time_budgets="ignore")
            # The watchdog's tracking issue lists exactly these overdue events.
            sys.path.append(str(Path(observatory.__file__).parent))
            import overdue_events  # noqa: PLC0415
            self.assertEqual(overdue_events.overdue(root, discovered)["overdue"], [])
            summary = overdue_events.overdue(root, later)
            late = summary["overdue"]
            self.assertEqual(len(late), 2)
            self.assertTrue(summary["pending_age_exceeded"])
            self.assertEqual(late, sorted(late, key=lambda row: (row["deadline"], row["event_id"])))

    def test_incremental_reconcile_preserves_full_verify_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/render/render.cpp")
            vellum_base, vellum_head = init_repo(vellum, "unmapped/file.cpp")
            make_state(root, pulp_base, vellum_base)

            observatory.reconcile(
                root=root,
                pulp_repo=pulp,
                vellum_repo=vellum,
                pulp_target=pulp_head,
                vellum_target=vellum_head,
                now_text="2026-07-22T20:00:00Z",
                write=True,
            )
            write(pulp / "core/render/render.cpp", "int value = 3;\n")
            second_head = commit_all(pulp, "second mapped change")

            result = observatory.reconcile(
                root=root,
                pulp_repo=pulp,
                vellum_repo=vellum,
                pulp_target=second_head,
                vellum_target=vellum_head,
                now_text="2026-07-22T20:01:00Z",
                write=True,
            )

            self.assertEqual(len(result["new_events"]), 1)
            report = observatory.verify(
                root=root,
                pulp_repo=pulp,
                vellum_repo=vellum,
                git_base=None, allow_no_git_base=True,
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["coverage_gaps"], [])

    def test_mapping_change_replays_history_before_resuming_incremental_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp = top / "pulp"
            vellum = top / "vellum-source"
            root = top / "state"
            pulp_base, pulp_head = init_repo(pulp, "core/newly-mapped/render.cpp")
            vellum_base, vellum_head = init_repo(vellum, "unmapped/file.cpp")
            make_state(root, pulp_base, vellum_base)

            first = observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=vellum_head,
                now_text="2026-07-22T20:00:00Z", write=True,
            )
            self.assertEqual(first["new_events"], [])

            mapping_path = root / observatory.MAP_PATH
            mapping = observatory.load_json(mapping_path)
            mapping["mappings"][0]["pulp_paths"].append("core/newly-mapped/")
            write_json(mapping_path, mapping)

            second = observatory.reconcile(
                root=root, pulp_repo=pulp, vellum_repo=vellum,
                pulp_target=pulp_head, vellum_target=vellum_head,
                now_text="2026-07-22T20:01:00Z", write=True,
            )
            self.assertEqual(len(second["new_events"]), 1)
            self.assertEqual(
                observatory.load_json(root / observatory.CURSOR_PATH)["mapping_sha256"],
                observatory.mapping_fingerprint(mapping),
            )

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

    def test_event_resolution_chain_is_append_only_effective_state(self) -> None:
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
            "disposition": "port-required", "rationale": "Expanded acceptance remains open.", "owner": "@danielraffel",
            "linked_commits": ["d" * 40], "linked_pull_requests": [], "contract_tests": ["render-contract"],
            "shared_contract_release_blocker": True, "effort_minutes": 15,
        }
        closure = {
            **resolution,
            "event_id": "resolution-render-b",
            "created_at": "2026-07-22T00:00:00Z",
            "disposition": "ported",
            "rationale": "Later proof closes the blocker.",
            "linked_commits": ["e" * 40],
            "shared_contract_release_blocker": False,
            "effort_minutes": 20,
        }
        effective = observatory.effective_observations(
            [
                (Path("c"), closure),
                (Path("a"), observation),
                (Path("b"), resolution),
            ]
        )
        self.assertEqual(effective[0]["disposition"], "ported")
        self.assertEqual(effective[0]["resolution_event"], "resolution-render-b")
        self.assertEqual(effective[0]["resolved_at"], "2026-07-22T00:00:00Z")
        self.assertEqual(effective[0]["linked_commits"], ["e" * 40])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / observatory.EVENTS_PATH
            write_json(directory / f"{observation['event_id']}.yaml", observation)
            write_json(directory / f"{resolution['event_id']}.yaml", resolution)
            write_json(directory / f"{closure['event_id']}.yaml", closure)
            loaded = observatory.load_events(root)
            self.assertEqual(len(loaded), 3)

            duplicate_timestamp = {
                **closure,
                "event_id": "resolution-render-c",
                "linked_commits": ["f" * 40],
            }
            write_json(
                directory / f"{duplicate_timestamp['event_id']}.yaml",
                duplicate_timestamp,
            )
            with self.assertRaisesRegex(
                observatory.ObservatoryError,
                "resolution timestamps must be unique",
            ):
                observatory.load_events(root)



class OwnCommitEventTests(unittest.TestCase):
    """A change adds Vellum observations only for its own commits."""

    REPOSITORY = "Generous-Corp/vellum"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        run(self.root, "git", "init", "-b", "main")
        run(self.root, "git", "config", "user.email", "observatory@example.invalid")
        run(self.root, "git", "config", "user.name", "Observatory Test")
        write(self.root / "graphics/render.cpp", "int a;\n")
        run(self.root, "git", "add", ".")
        run(self.root, "git", "commit", "-m", "root")
        self.root_commit = run(self.root, "git", "rev-parse", "HEAD")
        self.write_cursor(self.root_commit)
        run(self.root, "git", "add", ".")
        run(self.root, "git", "commit", "-m", "cursor at root")
        self.cursor_commit = run(self.root, "git", "rev-parse", "HEAD")
        write(self.root / "graphics/render.cpp", "int b;\n")
        run(self.root, "git", "commit", "-am", "a main change after the cursor")
        self.main_change = run(self.root, "git", "rev-parse", "HEAD")
        self.base = self.main_change

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_cursor(self, vellum_commit: str) -> None:
        write_json(self.root / observatory.CURSOR_PATH, {
            "pulp": {"last_scanned_commit": "a" * 40, "repository": "Generous-Corp/pulp"},
            "vellum": {"last_scanned_commit": vellum_commit, "repository": self.REPOSITORY},
        })

    def add_event(self, source_commit: str) -> None:
        event_id = f"vellum-{source_commit}"
        write_json(self.root / observatory.EVENTS_PATH / f"{event_id}.yaml", {
            "event_id": event_id, "kind": "observation",
            "source_repository": self.REPOSITORY, "source_commit": source_commit,
        })

    def check(self) -> None:
        events = [
            (path, json.loads(path.read_text()))
            for path in sorted((self.root / observatory.EVENTS_PATH).glob("*.yaml"))
        ]
        cursor = json.loads((self.root / observatory.CURSOR_PATH).read_text())
        observatory.verify_new_event_sources(self.root, self.base, events, cursor,
                                             vellum_repo=self.root)

    def branch_commit(self) -> str:
        run(self.root, "git", "checkout", "-q", "-b", "branch", self.base)
        write(self.root / "graphics/render.cpp", "int c;\n")
        run(self.root, "git", "commit", "-am", "a branch change")
        return run(self.root, "git", "rev-parse", "HEAD")

    def commit_all(self, message: str) -> None:
        run(self.root, "git", "add", "-A")
        run(self.root, "git", "commit", "-m", message)

    def test_branch_events_for_its_own_commits_pass(self) -> None:
        own = self.branch_commit()
        self.add_event(own)
        self.commit_all("record the branch's own observation")
        self.check()

    def test_branch_event_for_a_main_commit_is_refused(self) -> None:
        own = self.branch_commit()
        self.add_event(own)
        self.add_event(self.main_change)
        self.commit_all("record main's commit as well")
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()

    def test_branch_event_for_a_commit_outside_both_is_refused(self) -> None:
        run(self.root, "git", "checkout", "-q", "-b", "elsewhere", self.base)
        write(self.root / "graphics/render.cpp", "int z;\n")
        run(self.root, "git", "commit", "-am", "a change on another branch")
        elsewhere = run(self.root, "git", "rev-parse", "HEAD")
        run(self.root, "git", "checkout", "-q", "main")
        self.branch_commit()
        self.add_event(elsewhere)
        self.commit_all("record another branch's commit")
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()

    def stale_base_pull_request(self, *, set_origin_main: bool = True) -> str:
        """vellum#277's shape: CI checks out the pull request merged into the
        current main, but its git_base (the event's base.sha) predates an
        evidence commit main gained since. Returns current main's tip."""
        own = self.branch_commit()
        self.add_event(own)
        self.commit_all("record the branch's own observation")
        run(self.root, "git", "checkout", "-q", "main")
        self.add_event(self.main_change)
        self.commit_all("chore(provenance): coalesce Pulp observatory evidence")
        main_tip = run(self.root, "git", "rev-parse", "HEAD")
        if set_origin_main:
            run(self.root, "git", "update-ref", observatory.VELLUM_MAIN_REF, main_tip)
        run(self.root, "git", "checkout", "-q", "--detach", main_tip)
        run(self.root, "git", "merge", "-q", "--no-ff", "--no-edit", "branch")
        return main_tip

    def check_from(self, base: str) -> None:
        """The checks verify runs on the git base it settles on."""
        events = [
            (path, json.loads(path.read_text()))
            for path in sorted((self.root / observatory.EVENTS_PATH).glob("*.yaml"))
        ]
        cursor = json.loads((self.root / observatory.CURSOR_PATH).read_text())
        observatory.verify_append_only(self.root, base)
        observatory.verify_new_event_sources(self.root, base, events, cursor, vellum_repo=self.root)

    def test_the_stale_base_moves_to_the_merge_base_with_current_main(self) -> None:
        main_tip = self.stale_base_pull_request()
        base = observatory.current_main_git_base(self.root, self.base)
        self.assertEqual(base, main_tip)
        self.check_from(base)  # main's newer event is not this change's
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check_from(self.base)  # the stale base alone: #277's false positive

    def test_without_origin_main_the_stale_base_stays(self) -> None:
        self.stale_base_pull_request(set_origin_main=False)
        self.assertEqual(observatory.current_main_git_base(self.root, self.base), self.base)
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check_from(observatory.current_main_git_base(self.root, self.base))

    def test_a_modified_copy_of_a_main_event_is_refused(self) -> None:
        self.stale_base_pull_request()
        path = self.root / observatory.EVENTS_PATH / f"vellum-{self.main_change}.yaml"
        value = json.loads(path.read_text())
        value["rationale"] = "edited on the branch"
        write_json(path, value)
        self.commit_all("edit main's event")
        with self.assertRaisesRegex(observatory.ObservatoryError, "append-only"):
            self.check_from(observatory.current_main_git_base(self.root, self.base))

    def test_a_branch_event_for_a_main_commit_is_still_refused_with_origin_main_set(self) -> None:
        own = self.branch_commit()
        self.add_event(own)
        self.add_event(self.main_change)
        self.commit_all("record main's commit as well")
        run(self.root, "git", "update-ref", observatory.VELLUM_MAIN_REF, self.base)
        base = observatory.current_main_git_base(self.root, self.base)
        self.assertEqual(base, self.base)
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check_from(base)

    def test_verify_judges_from_the_current_main_base(self) -> None:
        class Settled(Exception):
            pass

        def settle(root: Path, git_base: str | None) -> str | None:
            raise Settled(git_base)

        original = observatory.current_main_git_base
        observatory.current_main_git_base = settle
        try:
            with self.assertRaises(Settled):
                observatory.verify(root=self.root, pulp_repo=None, vellum_repo=self.root, git_base=self.base)
        finally:
            observatory.current_main_git_base = original

    def test_the_base_never_moves_backward_or_on_main(self) -> None:
        self.branch_commit()
        # origin/main older than the given base: left alone.
        run(self.root, "git", "update-ref", observatory.VELLUM_MAIN_REF, self.cursor_commit)
        self.assertEqual(observatory.current_main_git_base(self.root, self.base), self.base)
        # A push to main: HEAD is origin/main, so nothing moves.
        run(self.root, "git", "update-ref", observatory.VELLUM_MAIN_REF, "HEAD")
        self.assertEqual(observatory.current_main_git_base(self.root, self.cursor_commit), self.cursor_commit)

    def coalesce(self, *, advance: bool, with_code: bool) -> None:
        # The receiver's shape: one evidence-only commit on main's tip that
        # moves the Vellum cursor over main's commits and records them.
        run(self.root, "git", "checkout", "-q", "-b", "automation/pulp-observatory-coalesced", self.base)
        if advance:
            self.write_cursor(self.base)
        self.add_event(self.main_change)
        if with_code:
            write(self.root / "graphics/render.cpp", "int d;\n")
        self.commit_all("chore(provenance): coalesce Pulp observatory evidence")

    def test_evidence_only_cursor_advance_records_mains_commits(self) -> None:
        self.coalesce(advance=True, with_code=False)
        self.check()

    def test_cursor_advance_with_a_code_change_is_refused(self) -> None:
        self.coalesce(advance=True, with_code=True)
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()

    def test_evidence_only_change_without_a_cursor_advance_is_refused(self) -> None:
        self.coalesce(advance=False, with_code=False)
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()

    def test_cursor_advance_covers_only_the_commits_it_passes(self) -> None:
        # Advancing the cursor to the root commit's successor does not cover
        # main's later change.
        run(self.root, "git", "checkout", "-q", "-b", "automation/pulp-observatory-coalesced", self.base)
        self.write_cursor(self.cursor_commit)
        self.add_event(self.main_change)
        self.commit_all("advance the cursor short of main's change")
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()

    def test_cursor_advanced_beyond_main_is_refused(self) -> None:
        # Evidence-only, but the cursor moves onto a commit main does not
        # hold; the observed main commit lies in that advance.
        run(self.root, "git", "checkout", "-q", "-b", "side", self.base)
        write(self.root / "graphics/render.cpp", "int s;\n")
        run(self.root, "git", "commit", "-am", "a commit main does not hold")
        beyond = run(self.root, "git", "rev-parse", "HEAD")
        run(self.root, "git", "checkout", "-q", "-b", "automation/pulp-observatory-coalesced", self.base)
        self.write_cursor(beyond)
        self.add_event(self.main_change)
        self.commit_all("advance the cursor past main")
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()

    def cursor_ahead_on_main(self) -> tuple[str, str]:
        """Main with its cursor at main_change, a merged side commit, and a later change."""
        run(self.root, "git", "checkout", "-q", "-b", "side", self.root_commit)
        write(self.root / "graphics/side.cpp", "int s;\n")
        run(self.root, "git", "add", ".")
        run(self.root, "git", "commit", "-m", "a side commit")
        side = run(self.root, "git", "rev-parse", "HEAD")
        run(self.root, "git", "checkout", "-q", "main")
        self.write_cursor(self.main_change)
        self.commit_all("the receiver moved the cursor to main_change")
        run(self.root, "git", "merge", "--no-edit", "side")
        write(self.root / "graphics/render.cpp", "int later;\n")
        run(self.root, "git", "commit", "-am", "a later main change")
        later = run(self.root, "git", "rev-parse", "HEAD")
        self.base = later
        return side, later

    def test_cursor_moved_backward_or_sideways_is_refused(self) -> None:
        side, later = self.cursor_ahead_on_main()
        for name, target in (("backward", self.root_commit), ("sideways", side)):
            with self.subTest(name):
                run(self.root, "git", "checkout", "-q", "-f", "-B", "automation/pulp-observatory-coalesced", self.base)
                self.write_cursor(target)
                self.add_event(later)
                self.commit_all(f"move the cursor {name}")
                with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
                    self.check()

    def test_forward_advance_on_main_records_the_later_change(self) -> None:
        # Control for the two refusals above: the same base, the cursor
        # moved forward to main's tip.
        side, later = self.cursor_ahead_on_main()
        run(self.root, "git", "checkout", "-q", "-b", "automation/pulp-observatory-coalesced", self.base)
        self.write_cursor(later)
        self.add_event(later)
        self.commit_all("move the cursor forward on main")
        self.check()

    def test_evidence_allowlist_is_the_receivers(self) -> None:
        # The receiver's "Prepare the evidence-only coalescing PR" step holds
        # its own copy of the allowlist in shell; this pins the two equal.
        workflow = (Path(__file__).resolve().parents[2]
                    / ".github/workflows/pulp-observatory-receiver.yml").read_text(encoding="utf-8")
        block = workflow[workflow.index('case "$path" in'):]
        block = block[:block.index(") ;;")]
        patterns = sorted(p.strip().rstrip("|").strip() for p in block.split("in", 1)[1].split("|\\\n") if p.strip())
        patterns = sorted(item.strip() for chunk in patterns for item in chunk.split("|") if item.strip())
        self.assertEqual(patterns, sorted([
            *observatory.EVIDENCE_MODIFIED_PATHS,
            observatory.EVENTS_PATH.as_posix() + "/*.yaml",
        ]))
        for path in patterns:
            probe = path.replace("*", "x")
            self.assertTrue(observatory.is_evidence_path(probe), probe)
        self.assertFalse(observatory.is_evidence_path("provenance/pulp-observatory/events/x/y.yaml"))
        self.assertFalse(observatory.is_evidence_path("provenance/pulp-observatory/receiver-policy.json"))

    def test_cursor_moved_onto_an_off_main_commit_it_records_is_refused(self) -> None:
        # The boundary-review repro: evidence-only, the cursor moved from
        # main onto a branch commit, and an event for that branch commit.
        # Leaving the cursor alone is refused too (the control).
        run(self.root, "git", "checkout", "-q", "-b", "feature", self.base)
        write(self.root / "graphics/render.cpp", "int f;\n")
        run(self.root, "git", "commit", "-am", "an off-main branch commit")
        off_main = run(self.root, "git", "rev-parse", "HEAD")
        for move in (True, False):
            with self.subTest(move_cursor=move):
                run(self.root, "git", "checkout", "-q", "-f", "-B", "evidence", self.base)
                if move:
                    self.write_cursor(off_main)
                self.add_event(off_main)
                self.commit_all("evidence-only: record an off-main commit")
                with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
                    self.check()

    def test_cursor_on_a_merged_branch_commit_is_refused(self) -> None:
        # An ancestor of main that main never held as its tip: a commit of a
        # merged branch. The receiver only moves the cursor along main's
        # first-parent line.
        run(self.root, "git", "checkout", "-q", "-b", "merged", self.base)
        write(self.root / "graphics/merged.cpp", "int m;\n")
        run(self.root, "git", "add", ".")
        run(self.root, "git", "commit", "-m", "a branch commit main merges")
        merged = run(self.root, "git", "rev-parse", "HEAD")
        run(self.root, "git", "checkout", "-q", "main")
        run(self.root, "git", "merge", "--no-ff", "--no-edit", "merged")
        self.base = run(self.root, "git", "rev-parse", "HEAD")
        run(self.root, "git", "checkout", "-q", "-b", "automation/pulp-observatory-coalesced", self.base)
        self.write_cursor(merged)
        self.add_event(self.main_change)
        self.commit_all("move the cursor to a merged branch commit")
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()
        # Control: the same events with the cursor at main's tip pass.
        self.write_cursor(self.base)
        self.commit_all("move the cursor to main's tip")
        self.check()

    def test_missing_git_base_fails_closed(self) -> None:
        own = self.branch_commit()
        self.add_event(own)
        self.commit_all("record the branch's own observation")
        events = [(path, json.loads(path.read_text()))
                  for path in sorted((self.root / observatory.EVENTS_PATH).glob("*.yaml"))]
        cursor = json.loads((self.root / observatory.CURSOR_PATH).read_text())
        with self.assertRaisesRegex(observatory.ObservatoryError, "needs --git-base"):
            observatory.verify_new_event_sources(self.root, None, events, cursor, vellum_repo=self.root)
        observatory.verify_new_event_sources(self.root, None, events, cursor, vellum_repo=self.root,
                                             allow_no_git_base=True)

    def test_receivers_own_verify_before_committing_passes(self) -> None:
        # pulp-observatory-receiver.yml reconciles main, then verifies with
        # --git-base HEAD before it commits: the advanced cursor and the new
        # events are still in the working tree.
        run(self.root, "git", "checkout", "-q", self.base)
        self.write_cursor(self.base)
        self.add_event(self.main_change)
        self.check()

    def test_branch_name_alone_earns_no_exemption(self) -> None:
        # The coalescing branch name with an events-only reconcile (no cursor
        # move) is refused like any other branch.
        self.coalesce(advance=False, with_code=False)
        self.assertEqual(run(self.root, "git", "rev-parse", "--abbrev-ref", "HEAD"),
                         "automation/pulp-observatory-coalesced")
        with self.assertRaisesRegex(observatory.ObservatoryError, "only for its own commits"):
            self.check()


class VellumNativePathTests(unittest.TestCase):
    """Automatic framework-only resolutions from provenance/pulp-observatory/vellum-native-paths.json."""

    ENTRIES = [{"id": "native-thing", "paths": ["graphics/native.cpp", "graphics/native/"],
                "absent_in_pulp": ["NativeOnlySymbol"], "reason": "Vellum-only."}]

    def native_module(self):
        return sys.modules[observatory.load_native_paths.__module__]

    def observation(self, **overrides):
        event = {"event_id": "vellum-" + "a" * 40, "source_repository": "Generous-Corp/vellum",
                 "source_commit": "a" * 40, "mapped_paths": ["graphics/native.cpp"], "transitive_paths": [],
                 "schema_api_deltas": [], "class": "rendering", "severity": None,
                 "contract_tests": ["vellum.gpu-native"]}
        event.update(overrides)
        return event

    def test_a_fully_native_observation_qualifies_and_one_inherited_path_does_not(self) -> None:
        nat = self.native_module()
        self.assertEqual(nat.qualifying_entries(self.observation(), self.ENTRIES), ["native-thing"])
        self.assertEqual(nat.qualifying_entries(
            self.observation(mapped_paths=["graphics/native/a.cpp", "graphics/native.cpp"]), self.ENTRIES),
            ["native-thing"])
        # Negative control: one path with a Pulp counterpart keeps it pending.
        self.assertIsNone(nat.qualifying_entries(
            self.observation(mapped_paths=["graphics/native.cpp", "graphics/skia_dawn_surface.mm"]), self.ENTRIES))
        self.assertIsNone(nat.qualifying_entries(
            self.observation(transitive_paths=["graphics/CMakeLists.txt"]), self.ENTRIES))

    def test_the_guards_keep_it_pending(self) -> None:
        nat = self.native_module()
        for overrides in ({"schema_api_deltas": ["graphics/native.cpp"]},
                          {"include_dependency_deltas": ["graphics/native/api.hpp"]}, {"class": "security"},
                          {"severity": "P0"}, {"mapped_paths": ["framework/core/native.cpp"]},
                          {"source_repository": "Generous-Corp/pulp"}, {"mapped_paths": []}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(nat.qualifying_entries(self.observation(**overrides), self.ENTRIES))
        self.assertIsNone(nat.qualifying_entries(self.observation(), []))
        # framework/ never qualifies, even if an entry (bypassing the loader) lists it.
        listed = [{"id": "exported", "paths": ["framework/core/"], "absent_in_pulp": [], "reason": "r"}]
        self.assertIsNone(nat.qualifying_entries(self.observation(mapped_paths=["framework/core/x.cpp"]), listed))

    def test_a_header_change_found_by_the_real_delta_path_never_qualifies(self) -> None:
        nat = self.native_module()
        git_module = sys.modules[observatory.load_native_paths.__module__.replace("observatory_native", "observatory_git")]
        paths = ["graphics/native/api.hpp"]
        includes, schemas = git_module.delta_paths(paths)
        self.assertEqual((includes, schemas), (paths, []))  # the header is not a schema/API delta
        header = self.observation(mapped_paths=paths, include_dependency_deltas=includes, schema_api_deltas=schemas)
        self.assertIsNone(nat.qualifying_entries(header, self.ENTRIES))
        includes, schemas = git_module.delta_paths(["graphics/native/impl.cpp"])
        impl = self.observation(mapped_paths=["graphics/native/impl.cpp"], include_dependency_deltas=includes,
                                schema_api_deltas=schemas)
        self.assertEqual(nat.qualifying_entries(impl, self.ENTRIES), ["native-thing"])

    def test_an_entry_with_no_absence_witness_is_rejected(self) -> None:
        nat = self.native_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / nat.NATIVE_PATHS_PATH, {"schema_version": 1, "entries": [
                {**self.ENTRIES[0], "absent_in_pulp": []}]})
            with self.assertRaisesRegex(observatory.ObservatoryError, "at least one identifier"):
                nat.load_native_paths(root)
            write_json(root / nat.NATIVE_PATHS_PATH, {"schema_version": 1, "entries": self.ENTRIES})
            self.assertEqual(nat.load_native_paths(root), self.ENTRIES)

    def test_the_automatic_resolution_names_its_policy_not_a_person(self) -> None:
        nat = self.native_module()
        resolution = nat.auto_resolution(self.observation(), self.ENTRIES, "2026-11-10T00:00:00Z")
        self.assertEqual(resolution["owner"], "auto:vellum-native-paths@native-thing")
        self.assertEqual(resolution["disposition"], "framework-only")
        self.assertNotIn("@danielraffel", json.dumps(resolution))
        self.assertIn("vellum-native-paths.json", resolution["rationale"])
        self.assertIn("df7573679", resolution["rationale"])
        observatory.validate_event(resolution, Path(f"{resolution['event_id']}.yaml"))

    def test_an_automatic_owner_is_refused_for_any_other_disposition_or_kind(self) -> None:
        nat = self.native_module()
        good = nat.auto_resolution(self.observation(), self.ENTRIES, "2026-11-10T00:00:00Z")
        for disposition in sorted(observatory.DISPOSITIONS - {"framework-only", "pending"}):
            with self.subTest(disposition=disposition), self.assertRaisesRegex(
                    observatory.ObservatoryError, "may only resolve framework-only"):
                observatory.validate_event({**good, "disposition": disposition}, Path(f"{good['event_id']}.yaml"))
        with self.assertRaisesRegex(observatory.ObservatoryError, "may only resolve framework-only"):
            nat.validate_owner({"kind": "observation", "owner": good["owner"]}, "x")
        for owner in ("auto:vellum-native-paths@", "auto:other@x-y", "danielraffel", "@", "@bad handle"):
            with self.subTest(owner=owner), self.assertRaises(observatory.ObservatoryError):
                observatory.validate_event({**good, "owner": owner}, Path(f"{good['event_id']}.yaml"))
        nat.validate_owner({"kind": "resolution", "disposition": "superseded", "owner": "@danielraffel"}, "x")

    def test_the_list_is_validated(self) -> None:
        nat = self.native_module()
        bad_paths = (["graphics/*.cpp"], ["framework/core/x.cpp"], ["mirror/x"], ["/abs"], ["a/../b"], ["b", "a"], [])
        for paths in bad_paths:
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_json(root / nat.NATIVE_PATHS_PATH, {"schema_version": 1, "entries": [
                    {"id": "x-y", "paths": paths, "absent_in_pulp": ["SomeSymbol"], "reason": "r"}]})
                with self.assertRaises(observatory.ObservatoryError):
                    nat.load_native_paths(root)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(nat.load_native_paths(root), [])
            twice = [{"id": "x-y", "paths": ["a"], "absent_in_pulp": ["SomeSymbol"], "reason": "r"}] * 2
            write_json(root / nat.NATIVE_PATHS_PATH, {"schema_version": 1, "entries": twice})
            with self.assertRaisesRegex(observatory.ObservatoryError, "repeated"):
                nat.load_native_paths(root)

    def test_no_list_is_committed_until_the_data_follow_up(self) -> None:
        nat = self.native_module()
        self.assertFalse((observatory.ROOT / nat.NATIVE_PATHS_PATH).exists())
        self.assertEqual(nat.load_native_paths(observatory.ROOT), [])

    def test_an_entry_goes_stale_when_pulp_gains_its_symbol(self) -> None:
        nat = self.native_module()
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary) / "pulp"
            base, head = init_repo(pulp, "core/canvas/canvas.cpp")
            self.assertEqual(nat.stale_entries(self.ENTRIES, pulp, head), [])
            write(pulp / "core/canvas/native.hpp", "struct NativeOnlySymbol {};\n")
            gained = commit_all(pulp, "Pulp gains a counterpart")
            problems = nat.stale_entries(self.ENTRIES, pulp, gained)
            self.assertEqual(len(problems), 1)
            self.assertIn("native-thing is stale", problems[0])
            self.assertEqual(nat.stale_entries(self.ENTRIES, pulp, head), [])  # judged at the given target
            with self.assertRaises(observatory.ObservatoryError):
                nat.stale_entries(self.ENTRIES, pulp, "f" * 40)
            self.assertTrue(nat.stale_entries(self.ENTRIES, None, head))  # cannot check: fails
            # A search that errors is never read as "absent".
            real_run = subprocess.run

            def grep_fails(command, *args, **kwargs):
                if list(command[:2]) == ["git", "grep"]:
                    return subprocess.CompletedProcess(command, 128, "", "fatal: broken")
                return real_run(command, *args, **kwargs)

            with mock.patch.object(nat.subprocess, "run", side_effect=grep_fails):
                with self.assertRaisesRegex(observatory.ObservatoryError, "cannot search Pulp"):
                    nat.stale_entries(self.ENTRIES, pulp, head)

    def test_an_automatic_resolution_the_list_no_longer_supports_fails_until_a_person_resolves(self) -> None:
        nat = self.native_module()
        observation = {**self.observation(), "disposition": "framework-only",
                       "owner": "auto:vellum-native-paths@native-thing"}
        self.assertEqual(nat.invalid_auto_resolutions([observation], self.ENTRIES), [])
        narrowed = [{**self.ENTRIES[0], "paths": ["graphics/native/"]}]
        self.assertTrue(nat.invalid_auto_resolutions([observation], narrowed))
        # A later resolution by a person is effective instead, and the check is satisfied.
        self.assertEqual(nat.invalid_auto_resolutions([{**observation, "owner": "@danielraffel"}], narrowed), [])
        forged = {**self.observation(mapped_paths=["graphics/skia_dawn_surface.mm"]), "disposition": "framework-only",
                  "owner": "auto:vellum-native-paths@native-thing"}
        self.assertTrue(nat.invalid_auto_resolutions([forged], self.ENTRIES))

    def test_reconcile_writes_the_automatic_resolution_beside_a_native_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            top = Path(temporary)
            pulp, vellum, root, pulp_head, main, feature = ObservatoryTests._coverage_state(self, top)
            write_json(root / self.native_module().NATIVE_PATHS_PATH,
                       {"schema_version": 1, "entries": self.ENTRIES})
            write(vellum / "graphics/native.cpp", "int native = 1;\n")
            native_commit = commit_all(vellum, "a Vellum-native change")
            result = observatory.reconcile(root=root, pulp_repo=pulp, vellum_repo=vellum, pulp_target=pulp_head,
                                           vellum_target=native_commit, now_text="2026-10-11T00:05:00Z",
                                           write=True, events_only=True)
            self.assertIn(f"vellum-{native_commit}", result["new_events"])
            self.assertIn(f"resolution-vellum-{native_commit}", result["new_events"])
            # Negative control: the inherited render.cpp commit stays pending, with no resolution.
            self.assertIn(f"vellum-{feature}", result["new_events"])
            self.assertNotIn(f"resolution-vellum-{feature}", result["new_events"])
            resolution = observatory.load_json(root / observatory.EVENTS_PATH / f"resolution-vellum-{native_commit}.yaml")
            self.assertEqual(resolution["owner"], "auto:vellum-native-paths@native-thing")
            effective = {e["event_id"]: e for e in observatory.effective_observations(observatory.load_events(root))}
            self.assertEqual(effective[f"vellum-{native_commit}"]["disposition"], "framework-only")
            self.assertEqual(effective[f"vellum-{feature}"]["disposition"], "pending")
            report = observatory.verify(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                                        allow_no_git_base=True, pulp_target=pulp_head,
                                        vellum_target=native_commit, vellum_tail_policy="coverage")
            self.assertEqual(report["auto_classified"], 1)
            # Narrow the list: the committed automatic resolution no longer qualifies, so verify fails.
            write_json(root / self.native_module().NATIVE_PATHS_PATH, {"schema_version": 1, "entries": [
                {**self.ENTRIES[0], "paths": ["graphics/native/"]}]})
            with self.assertRaisesRegex(observatory.ObservatoryError, "no longer supported"):
                observatory.verify(root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                                   allow_no_git_base=True, pulp_target=pulp_head,
                                   vellum_target=native_commit, vellum_tail_policy="coverage")

class ReportReceiverOnlyTests(unittest.TestCase):
    """Only the receiver commits reports/current.*; changes leave it as their base had it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        top = Path(self.temporary.name)
        self.pulp = top / "pulp"
        self.vellum = top / "vellum-source"
        self.root = top / "state"
        pulp_base, self.pulp_head = init_repo(self.pulp, "unmapped/pulp.cpp")
        vellum_base, vellum_head = init_repo(self.vellum, "graphics/render.cpp")
        write(self.vellum / "graphics/second.cpp", "int second = 1;\n")
        self.vellum_head = commit_all(self.vellum, "a second mapped change")
        make_state(self.root, pulp_base, vellum_base)
        # The receiver's reconcile: writes events, advances the cursor, writes the report.
        observatory.reconcile(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum,
                              pulp_target=self.pulp_head, vellum_target=self.vellum_head,
                              now_text="2026-10-11T00:00:00Z", write=True)
        run(self.root, "git", "init", "-q", "-b", "main")
        run(self.root, "git", "config", "user.email", "observatory@example.invalid")
        run(self.root, "git", "config", "user.name", "Observatory Test")
        self.base = commit_all(self.root, "main with the receiver's report")
        pending = [e for e in observatory.effective_observations(observatory.load_events(self.root))
                   if e["disposition"] == "pending" and e["source_repository"].endswith("/vellum")]
        self.assertGreaterEqual(len(pending), 2)
        self.pending = pending

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def resolve(self, observation: dict, created_at: str) -> None:
        resolution = {
            "schema_version": 1, "event_id": f"resolution-{observation['event_id']}", "kind": "resolution",
            "created_at": created_at, "resolves": observation["event_id"], "disposition": "framework-only",
            "rationale": "Vellum-native.", "owner": "@danielraffel",
            "linked_commits": [observation["source_commit"]], "linked_pull_requests": [],
            "contract_tests": observation["contract_tests"], "shared_contract_release_blocker": False,
            "effort_minutes": 0,
        }
        write_json(self.root / observatory.EVENTS_PATH / f"{resolution['event_id']}.yaml", resolution)

    def branch(self, name: str, observation: dict, created_at: str) -> str:
        run(self.root, "git", "checkout", "-q", "-b", name, self.base)
        self.resolve(observation, created_at)
        return commit_all(self.root, f"{name}: classify one observation")

    def verify(self, git_base: str, policy: str = "coverage") -> dict:
        return observatory.verify(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum,
                                  git_base=git_base, pulp_target=self.pulp_head,
                                  vellum_target=self.vellum_head, vellum_tail_policy=policy,
                                  check_now=datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc),
                                  time_budgets="report")

    def test_two_classification_changes_merge_cleanly_in_either_order(self) -> None:
        first = self.branch("first", self.pending[0], "2026-10-11T00:10:00Z")
        second = self.branch("second", self.pending[1], "2026-10-11T00:11:00Z")
        for order in ((first, second), (second, first)):
            with self.subTest(order=order):
                run(self.root, "git", "checkout", "-q", "--detach", self.base)
                for head in order:
                    merged = subprocess.run(["git", "merge", "-q", "--no-ff", "--no-edit", head], cwd=self.root,
                                            capture_output=True, text=True)
                    self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
                report = self.verify(self.base)
                self.assertEqual(report["pending"], len(self.pending) - 2)  # health judged on the live ledger

    def test_a_change_that_rewrites_the_report_is_refused(self) -> None:
        self.branch("rewrite", self.pending[0], "2026-10-11T00:10:00Z")
        self.verify(self.base)  # control: the same change without the report passes
        path = self.root / observatory.REPORT_JSON_PATH
        value = json.loads(path.read_text())
        value["pending"] = 0
        write_json(path, value)
        commit_all(self.root, "regenerate the report on the branch")
        with self.assertRaisesRegex(observatory.ObservatoryError, "may not commit provenance/pulp-observatory/reports"):
            self.verify(self.base)

    def test_the_receivers_reconciliation_may_rewrite_the_report(self) -> None:
        run(self.root, "git", "checkout", "-q", "-b", "automation/pulp-observatory-coalesced", self.base)
        write(self.vellum / "graphics/third.cpp", "int third = 1;\n")
        third = commit_all(self.vellum, "main lands another mapped change")
        now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=2)
        observatory.reconcile(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum,
                              pulp_target=self.pulp_head, vellum_target=third,
                              now_text=now.strftime("%Y-%m-%dT%H:%M:%SZ"), write=True)
        commit_all(self.root, "chore(provenance): coalesce Pulp observatory evidence")
        for policy in ("coverage", "reconciled"):
            with self.subTest(policy=policy):
                observatory.verify(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum, git_base=self.base,
                                   pulp_target=self.pulp_head, vellum_target=third, vellum_tail_policy=policy,
                                   check_now=now + timedelta(minutes=1), time_budgets="report")

    def test_a_net_zero_report_edit_on_the_branch_does_not_become_the_anchor(self) -> None:
        # The branch edits the report in a commit dated in the future, next to a
        # resolution, then restores the report. Its net diff leaves the report
        # alone, but the restoring commit is the report's last writer in HEAD's
        # history, where the ledger already holds the branch's resolution.
        run(self.root, "git", "checkout", "-q", "-b", "spoof", self.base)
        future = {**os.environ, "GIT_AUTHOR_DATE": "2030-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2030-01-01T00:00:00Z"}
        self.resolve(self.pending[0], "2026-10-11T00:10:00Z")
        path = self.root / observatory.REPORT_JSON_PATH
        original = path.read_text()
        write_json(path, {**json.loads(original), "pending": 0})
        run(self.root, "git", "add", "-A")
        subprocess.run(["git", "commit", "-q", "-m", "edit the report"], cwd=self.root, env=future, check=True)
        path.write_text(original)
        run(self.root, "git", "add", "-A")
        later = {**future, "GIT_COMMITTER_DATE": "2030-01-01T00:00:01Z", "GIT_AUTHOR_DATE": "2030-01-01T00:00:01Z"}
        subprocess.run(["git", "commit", "-q", "-m", "restore the report"], cwd=self.root, env=later, check=True)
        restore = run(self.root, "git", "rev-parse", "HEAD")
        self.assertEqual(run(self.root, "git", "log", "-1", "--format=%H", "--", str(observatory.REPORT_JSON_PATH)),
                         restore)  # HEAD's history would anchor here
        self.verify(self.base)  # anchored on the base's writer instead, so the branch's resolution is left out

    def test_a_report_lagging_on_main_passes_coverage_and_fails_a_tag(self) -> None:
        self.branch("lands", self.pending[0], "2026-10-11T00:10:00Z")
        self.verify(self.base)  # a pull request, or main before the next coalesce
        with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
            self.verify(self.base, policy="reconciled")  # a tag judges the committed report exactly

    def observe_on_branch(self, name: str, path: str) -> tuple[str, str]:
        """A pull request with one mapped commit, carrying its own observation event."""
        run(self.vellum, "git", "checkout", "-q", "-b", name, self.vellum_head)
        write(self.vellum / path, f"int {name} = 1;\n")
        source = commit_all(self.vellum, f"{name}: mapped change")
        run(self.root, "git", "checkout", "-q", "-b", name, self.base)
        now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=2)
        observatory.reconcile(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum,
                              pulp_target=self.pulp_head, vellum_target=source,
                              now_text=now.strftime("%Y-%m-%dT%H:%M:%SZ"), write=True, events_only=True,
                              vellum_main=self.vellum_head)
        self.assertTrue((self.root / observatory.EVENTS_PATH / f"vellum-{source}.yaml").exists())
        return source, commit_all(self.root, f"{name}: record its observation")

    def test_two_observation_changes_merge_cleanly_in_either_order(self) -> None:
        first_source, first = self.observe_on_branch("first", "graphics/first.cpp")
        second_source, second = self.observe_on_branch("second", "graphics/second_pr.cpp")
        # Vellum main after both pull requests landed through queue merges.
        run(self.vellum, "git", "checkout", "-q", "--detach", self.vellum_head)
        for branch in ("first", "second"):
            run(self.vellum, "git", "merge", "-q", "--no-ff", "--no-edit", branch)
        landed = run(self.vellum, "git", "rev-parse", "HEAD")
        check_now = datetime.now(timezone.utc) + timedelta(minutes=1)
        for order in ((first, second), (second, first)):
            with self.subTest(order=order):
                run(self.root, "git", "checkout", "-q", "--detach", self.base)
                for head in order:
                    merged = subprocess.run(["git", "merge", "-q", "--no-ff", "--no-edit", head], cwd=self.root,
                                            capture_output=True, text=True)
                    self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
                report = observatory.verify(
                    root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum, git_base=self.base,
                    pulp_target=self.pulp_head, vellum_target=landed, vellum_tail_policy="coverage",
                    check_now=check_now, time_budgets="report",
                )
                self.assertEqual(report["coverage_gaps"], [])
                self.assertEqual(report["pending"], len(self.pending) + 2)  # both observations count live
        # Negative control: the same landing with only one side's event is a coverage gap.
        run(self.root, "git", "checkout", "-q", "--detach", self.base)
        run(self.root, "git", "merge", "-q", "--no-ff", "--no-edit", first)
        with self.assertRaisesRegex(observatory.ObservatoryError, "cursor-event-coverage-gap"):
            observatory.verify(
                root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum, git_base=self.base,
                pulp_target=self.pulp_head, vellum_target=landed, vellum_tail_policy="coverage",
                check_now=check_now, time_budgets="report",
            )

    def lengthen_deadlines(self) -> None:
        path = self.root / observatory.BUDGETS_PATH
        text = path.read_text()
        for old, new in (("hours: 24", "hours: 48"), ("business_days: 3", "business_days: 4"),
                         ("other_classification_days: 7", "other_classification_days: 8")):
            self.assertIn(old, text)
            text = text.replace(old, new)
        path.write_text(text)

    def test_a_change_to_the_report_inputs_leaves_the_report_to_the_receiver(self) -> None:
        # A change to the budgets (or the generator) alters every deadline a
        # fresh regenerate prints, but only the receiver may write the report.
        run(self.root, "git", "checkout", "-q", "-b", "budgets", self.base)
        self.lengthen_deadlines()
        self.resolve(self.pending[0], "2026-10-11T00:10:00Z")
        changed = commit_all(self.root, "lengthen the classification deadlines")
        self.assertEqual(observatory.report_inputs_changed_since(self.root, self.base),
                         [observatory.BUDGETS_PATH.as_posix()])
        self.verify(self.base)  # a pull request
        # main after it lands, and a later unrelated landing before the coalesce
        run(self.root, "git", "checkout", "-q", "--detach", self.base)
        run(self.root, "git", "merge", "-q", "--no-ff", "--no-edit", changed)
        landed = run(self.root, "git", "rev-parse", "HEAD")
        self.verify(run(self.root, "git", "rev-parse", "HEAD^1"))
        self.resolve(self.pending[1], "2026-10-11T00:11:00Z")
        commit_all(self.root, "a later classification")
        self.verify(landed)
        # A tag still judges the report exactly, and fails until the receiver runs.
        with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
            self.verify(self.base, policy="reconciled")
        # The receiver's regenerate is judged exactly and passes.
        write(self.vellum / "graphics/third.cpp", "int third = 1;\n")
        third = commit_all(self.vellum, "main lands another mapped change")
        now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=2)
        observatory.reconcile(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum,
                              pulp_target=self.pulp_head, vellum_target=third,
                              now_text=now.strftime("%Y-%m-%dT%H:%M:%SZ"), write=True)
        receiver_base = run(self.root, "git", "rev-parse", "HEAD")
        commit_all(self.root, "chore(provenance): coalesce Pulp observatory evidence")
        for policy in ("coverage", "reconciled"):
            with self.subTest(policy=policy):
                observatory.verify(root=self.root, pulp_repo=self.pulp, vellum_repo=self.vellum,
                                   git_base=receiver_base, pulp_target=self.pulp_head, vellum_target=third,
                                   vellum_tail_policy=policy, check_now=now + timedelta(minutes=1),
                                   time_budgets="report")

    def test_an_unrelated_prefixed_path_is_not_a_report_input(self) -> None:
        run(self.root, "git", "checkout", "-q", "-b", "unrelated-observatory", self.base)
        write(self.root / "tools/provenance/observatory-unrelated.txt", "not a generator\n")
        report = self.root / observatory.REPORT_JSON_PATH
        write_json(report, {**json.loads(report.read_text()), "pending": 0})
        commit_all(self.root, "add an unrelated observatory-prefixed file")
        self.assertEqual(observatory.report_inputs_changed_since(self.root, self.base), [])
        with self.assertRaises(observatory.ObservatoryError):
            self.verify(self.base)

    def test_an_input_change_does_not_excuse_a_report_that_differs_from_its_writers(self) -> None:
        # Negative control: the inputs changed, but the checked-out report is not
        # the copy its last writer committed (a local edit nobody committed).
        # A generator change excuses any difference it could explain, so only
        # the report pin can refuse this.
        run(self.root, "git", "checkout", "-q", "-b", "budgets", self.base)
        write(self.root / "tools/provenance/observatory_report.py", "# a new report field\n")
        commit_all(self.root, "teach the generator a new field")
        self.verify(self.base)  # control
        path = self.root / observatory.REPORT_MD_PATH
        path.write_text(path.read_text() + "\nedited\n")
        self.assertFalse(observatory.same_blob_as(self.root, self.base, observatory.REPORT_PATHS))
        with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.md is stale"):
            self.verify(self.base)

    def test_a_stale_report_without_an_input_change_still_fails(self) -> None:
        # Negative control: main's report does not match its ledger and nothing
        # the report is computed from changed, so verify stays exact.
        path = self.root / observatory.REPORT_JSON_PATH
        write_json(path, {**json.loads(path.read_text()), "pending": 0})
        write(self.root / "docs/note.md", "unrelated\n")
        bad_base = commit_all(self.root, "a writer that is not the receiver")
        run(self.root, "git", "checkout", "-q", "-b", "classify", bad_base)
        self.resolve(self.pending[0], "2026-10-11T00:10:00Z")
        commit_all(self.root, "classify one observation")
        self.assertEqual(observatory.report_inputs_changed_since(self.root, bad_base), [])
        with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
            self.verify(bad_base)
        # A budgets change that does not account for the difference does not excuse it:
        # rebuilt with the writer's budgets, the report still differs.
        path = self.root / observatory.BUDGETS_PATH
        path.write_text(path.read_text() + "# reviewed\n")
        commit_all(self.root, "touch the budgets")
        self.assertEqual(observatory.report_inputs_changed_since(self.root, bad_base),
                         [observatory.BUDGETS_PATH.as_posix()])
        with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
            self.verify(bad_base)

    def test_an_input_change_does_not_excuse_a_cursor_edit(self) -> None:
        # Only the receiver moves the cursor. A generator change, which excuses
        # a difference it cannot be rebuilt to explain, must not let a cursor
        # edit through beside it.
        run(self.root, "git", "checkout", "-q", "-b", "cursor", self.base)
        write(self.root / "tools/provenance/observatory_report.py", "# a new report field\n")
        commit_all(self.root, "teach the generator a new field")
        self.verify(self.base)  # control
        path = self.root / observatory.CURSOR_PATH
        write_json(path, {**json.loads(path.read_text()), "reconciled_at": "2026-10-01T00:00:00Z"})
        commit_all(self.root, "move the cursor's clock")
        with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
            self.verify(self.base)

    def test_a_generator_change_with_a_new_budget_key_is_accepted(self) -> None:
        # The common pairing: the generator learns a budget key and budgets.yaml
        # gains it. The writer's budgets no longer load, so no rebuild is tried.
        run(self.root, "git", "checkout", "-q", "-b", "new-budget", self.base)
        required = observatory.load_budgets.__globals__["REQUIRED_BUDGETS"] | {"new_budget"}
        with mock.patch.dict(observatory.load_budgets.__globals__, {"REQUIRED_BUDGETS": required}):
            self.lengthen_deadlines()
            path = self.root / observatory.BUDGETS_PATH
            path.write_text(path.read_text().replace("observatory:\n", "observatory:\n  new_budget: 1\n"))
            commit_all(self.root, "a new budget")
            with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
                self.verify(self.base)  # control: budgets alone are rebuilt, and the writer's no longer load
            write(self.root / "tools/provenance/observatory_common.py", "# reads new_budget\n")
            commit_all(self.root, "the generator reads it")
            self.verify(self.base)

    def test_a_generator_change_leaves_the_report_to_the_receiver(self) -> None:
        # A generator change cannot be rebuilt from the writer's tree, so its
        # presence since the writer is what excuses the difference.
        original = observatory.build_report

        def new_generator(**kwargs):
            return {**original(**kwargs), "native_policy": "framework-only"}

        run(self.root, "git", "checkout", "-q", "-b", "generator", self.base)
        self.resolve(self.pending[0], "2026-10-11T00:10:00Z")
        commit_all(self.root, "classify one observation")
        with mock.patch.object(observatory, "build_report", new_generator):
            with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
                self.verify(self.base)  # control: the generator's file is unchanged
            write(self.root / "tools/provenance/observatory_report.py", "# a new report field\n")
            commit_all(self.root, "teach the generator a new field")
            self.assertEqual(observatory.report_inputs_changed_since(self.root, self.base),
                             ["tools/provenance/observatory_report.py"])
            self.verify(self.base)
            with self.assertRaisesRegex(observatory.ObservatoryError, "reports/current.json is stale"):
                self.verify(self.base, policy="reconciled")

if __name__ == "__main__":
    unittest.main()
