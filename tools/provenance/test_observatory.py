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
        self.assertIn('observatory_tail_policy=reconciled', workflow)

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

    def test_coverage_policy_accepts_events_only_pull_request_and_rejects_missing_event(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp, vellum, root, pulp_head, main, feature = self._coverage_state(Path(temporary))
            cursor_before = observatory.load_json(root / observatory.CURSOR_PATH)
            report_before = (root / observatory.REPORT_JSON_PATH).read_bytes()

            # Negative control: the pull request carries no event for its mapped commit.
            with self.assertRaisesRegex(observatory.ObservatoryError, "health budgets failed"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                    pulp_target=pulp_head, vellum_target=feature,
                    vellum_tail_policy="coverage",
                )
            # The reconciled policy rejects the same tree for a different reason:
            # the tail is not evidence-only.
            with self.assertRaisesRegex(observatory.ObservatoryError, "evidence tail"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                pulp_target=pulp_head, vellum_target=feature,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["coverage_gaps"], [])
            # Still not acceptable to the reconciled policy: nothing relaxed there.
            with self.assertRaisesRegex(observatory.ObservatoryError, "evidence tail"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                    pulp_target=pulp_head, vellum_target=feature,
                )

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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                    pulp_target=pulp_head, vellum_target=reverting,
                    vellum_tail_policy="coverage",
                )
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
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                pulp_target=pulp_head, vellum_target=queue_shaped,
                vellum_tail_policy="coverage",
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
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
                root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                pulp_target=pulp_head, vellum_target=merge_ref,
                vellum_tail_policy="coverage",
            )
            self.assertEqual(report["coverage_gaps"], [])
            with self.assertRaisesRegex(observatory.ObservatoryError, "stale|outside"):
                observatory.verify(
                    root=root, pulp_repo=pulp, vellum_repo=vellum, git_base=None,
                    pulp_target=pulp_head, vellum_target=b_head,
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
                "--vellum-target", feature,
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
                git_base=None,
            )
            self.assertEqual(report["health"], "pass")
            self.assertEqual(report["pending"], 2)
            self.assertEqual(report["coverage_gaps"], [])

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
                git_base=None,
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


if __name__ == "__main__":
    unittest.main()
