#!/usr/bin/env python3
"""Tests for coalesced Pulp observatory receiver planning."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("receiver.py")
SPEC = importlib.util.spec_from_file_location("vellum_receiver", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.pulp = base / "pulp"
        self.vellum = base / "vellum"
        self.pulp.mkdir()
        self.vellum.mkdir()
        for repo in (self.pulp, self.vellum):
            git(repo, "init", "-q")
            git(repo, "config", "user.name", "Receiver Test")
            git(repo, "config", "user.email", "receiver@example.invalid")
        (self.pulp / "README.md").write_text("base\n", encoding="utf-8")
        self.ownership = {"schema_version": 2, "state": "active"}
        write_json(self.pulp / ".github/vellum-ownership.json", self.ownership)
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", "base")
        self.base = git(self.pulp, "rev-parse", "HEAD")
        self.set_cursor(self.base)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def set_cursor(self, commit: str) -> None:
        write_json(
            self.vellum / "provenance/pulp-observatory/cursor.json",
            {"pulp": {"last_scanned_commit": commit}},
        )

    def add_event(self, name: str, *, kind: str = "change", extra: str = "") -> tuple[str, dict[str, object]]:
        path = f".github/vellum-change-events/{name}.json"
        event = {"schema_version": 1, "event_id": name, "kind": kind}
        write_json(self.pulp / path, event)
        if extra:
            with (self.pulp / "README.md").open("a", encoding="utf-8") as stream:
                stream.write(extra + "\n")
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", name)
        return git(self.pulp, "rev-parse", "HEAD"), event

    def payload(self, commit: str, name: str, event: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_repository": "Generous-Corp/pulp",
            "source_base": self.base,
            "source_commit": commit,
            "source_head": commit,
            "direction": "pulp-to-framework",
            "affected_slices": ["vellum-foundation"],
            "transferred_slices": [],
            "event_refs": [{
                "path": f".github/vellum-change-events/{name}.json",
                "sha256": receiver.canonical_sha256(event),
            }],
            "ownership_projection_sha256": receiver.canonical_line_sha256(
                self.ownership
            ),
        }

    def _vellum_history(self) -> str:
        """A Vellum repository with evidence files committed at `base`; returns base."""
        (self.vellum / "graphics/render.cpp").parent.mkdir(parents=True, exist_ok=True)
        (self.vellum / "graphics/render.cpp").write_text("int v = 1;\n", encoding="utf-8")
        report = self.vellum / "provenance/pulp-observatory/reports/current.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("{}\n", encoding="utf-8")
        self.set_vellum_cursor("0" * 40)
        git(self.vellum, "add", ".")
        git(self.vellum, "commit", "-qm", "vellum base")
        return git(self.vellum, "rev-parse", "HEAD")

    def set_vellum_cursor(self, commit: str) -> None:
        path = self.vellum / "provenance/pulp-observatory/cursor.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["vellum"] = {"last_scanned_commit": commit}
        write_json(path, value)

    def test_vellum_main_ahead_by_an_ordinary_commit_needs_reconcile(self) -> None:
        base = self._vellum_history()
        self.set_vellum_cursor(base)
        self.assertFalse(receiver.vellum_needs_reconcile(self.vellum, self.vellum, base))
        (self.vellum / "graphics/render.cpp").write_text("int v = 2;\n", encoding="utf-8")
        git(self.vellum, "add", "graphics")
        git(self.vellum, "commit", "-qm", "ordinary change")
        main = git(self.vellum, "rev-parse", "HEAD")
        self.assertTrue(receiver.vellum_needs_reconcile(self.vellum, self.vellum, main))
        plan = receiver.plan(
            root=self.vellum, pulp_repo=self.pulp, pulp_main=self.base, lower_bound=self.base,
            payload=None, vellum_repo=self.vellum, vellum_main=main,
        )
        self.assertEqual(plan["status"], "reconcile")
        self.assertTrue(plan["vellum_cursor_lagging"])
        self.assertEqual(plan["reconcile_target"], self.base)

    def test_vellum_evidence_only_tail_does_not_loop_the_receiver(self) -> None:
        base = self._vellum_history()
        self.set_vellum_cursor(base)
        (self.vellum / "provenance/pulp-observatory/reports/current.json").write_text(
            '{"reconciled": true}\n', encoding="utf-8"
        )
        git(self.vellum, "add", "provenance/pulp-observatory/reports")
        git(self.vellum, "commit", "-qm", "evidence only")
        main = git(self.vellum, "rev-parse", "HEAD")
        # The receiver's own evidence commit landed: nothing to do, no loop.
        self.assertFalse(receiver.vellum_needs_reconcile(self.vellum, self.vellum, main))
        plan = receiver.plan(
            root=self.vellum, pulp_repo=self.pulp, pulp_main=self.base, lower_bound=self.base,
            payload=None, vellum_repo=self.vellum, vellum_main=main,
        )
        self.assertEqual(plan["status"], "noop")
        self.assertFalse(plan["vellum_cursor_lagging"])

    def test_vellum_lag_clock_is_the_first_first_parent_landing(self) -> None:
        import os
        base = self._vellum_history()
        self.set_vellum_cursor(base)
        self.assertIsNone(receiver.vellum_lag_started_at(self.vellum, base, base))
        for stamp, text in (("2026-08-09T11:00:00Z", "2"), ("2026-08-09T11:40:00Z", "3")):
            (self.vellum / "graphics/render.cpp").write_text(f"int v = {text};\n", encoding="utf-8")
            git(self.vellum, "add", "graphics")
            subprocess.run(
                ["git", "commit", "-qm", f"landing {text}"], cwd=self.vellum, check=True,
                env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp),
                capture_output=True,
            )
        main = git(self.vellum, "rev-parse", "HEAD")
        self.assertTrue(
            receiver.vellum_lag_started_at(self.vellum, base, main).startswith("2026-08-09T11:00:00")
        )

    def test_vellum_lag_clock_skips_landings_the_cursor_already_covers(self) -> None:
        # R7 delta D4: after the evidence PR lands the cursor stays behind its
        # own evidence merge. The clock must start at the next real landing,
        # not at that evidence commit, or the first change after a quiet
        # period reports an immediate breach.
        import os
        base = self._vellum_history()
        self.set_vellum_cursor(base)
        (self.vellum / "provenance/pulp-observatory/reports/current.json").write_text(
            '{"reconciled": true}\n', encoding="utf-8"
        )
        git(self.vellum, "add", "provenance/pulp-observatory/reports")
        stamp = "2026-08-08T09:00:00Z"
        subprocess.run(
            ["git", "commit", "-qm", "evidence only"], cwd=self.vellum, check=True,
            env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp),
            capture_output=True,
        )
        evidence = git(self.vellum, "rev-parse", "HEAD")
        self.assertIsNone(receiver.vellum_lag_started_at(self.vellum, base, evidence))
        stamp = "2026-08-09T11:00:00Z"
        (self.vellum / "graphics/render.cpp").write_text("int v = 2;\n", encoding="utf-8")
        git(self.vellum, "add", "graphics")
        subprocess.run(
            ["git", "commit", "-qm", "first real landing"], cwd=self.vellum, check=True,
            env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp),
            capture_output=True,
        )
        main = git(self.vellum, "rev-parse", "HEAD")
        started = receiver.vellum_lag_started_at(self.vellum, base, main)
        self.assertTrue(started.startswith("2026-08-09T11:00:00"), started)

    def test_vellum_main_behind_its_cursor_fails_closed(self) -> None:
        base = self._vellum_history()
        (self.vellum / "graphics/render.cpp").write_text("int v = 2;\n", encoding="utf-8")
        git(self.vellum, "add", "graphics")
        git(self.vellum, "commit", "-qm", "ordinary change")
        ahead = git(self.vellum, "rev-parse", "HEAD")
        self.set_vellum_cursor(ahead)
        with self.assertRaisesRegex(receiver.ReceiverError, "not a descendant"):
            receiver.vellum_needs_reconcile(self.vellum, self.vellum, base)

    def test_ordinary_change_reconciles_through_newer_durable_head(self) -> None:
        first, event = self.add_event("ordinary")
        latest, _ = self.add_event("newer")
        result = receiver.plan(
            root=self.vellum,
            pulp_repo=self.pulp,
            pulp_main=latest,
            lower_bound=first,
            payload=self.payload(first, "ordinary", event),
        )
        self.assertEqual(result["status"], "reconcile")
        self.assertEqual(result["reconcile_target"], latest)

    def test_event_range_coalesces_through_a_newer_non_event_main(self) -> None:
        first, event = self.add_event("ordinary")
        (self.pulp / "README.md").write_text("newer main\n", encoding="utf-8")
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", "newer main")
        main = git(self.pulp, "rev-parse", "HEAD")
        result = receiver.plan(
            root=self.vellum,
            pulp_repo=self.pulp,
            pulp_main=main,
            lower_bound=first,
            payload=self.payload(first, "ordinary", event),
        )
        self.assertEqual(result["reconcile_target"], main)
        self.assertEqual(result["oldest_uncovered_durable_event_commit"], first)

    def test_intermediate_event_mutation_fails_append_only_proof(self) -> None:
        first, event = self.add_event("ordinary")
        path = self.pulp / ".github/vellum-change-events/ordinary.json"
        changed = dict(event)
        changed["extra"] = "mutated"
        write_json(path, changed)
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", "mutate durable event")
        main = git(self.pulp, "rev-parse", "HEAD")
        with self.assertRaisesRegex(receiver.ReceiverError, "every history edge"):
            receiver.plan(
                root=self.vellum,
                pulp_repo=self.pulp,
                pulp_main=main,
                lower_bound=first,
                payload=self.payload(first, "ordinary", event),
            )

    def test_covered_duplicate_is_an_exact_noop(self) -> None:
        first, event = self.add_event("ordinary")
        self.set_cursor(first)
        result = receiver.plan(
            root=self.vellum,
            pulp_repo=self.pulp,
            pulp_main=first,
            lower_bound=first,
            payload=self.payload(first, "ordinary", event),
        )
        self.assertEqual(result["status"], "noop")

    def test_old_out_of_order_delivery_still_coalesces(self) -> None:
        old, event = self.add_event("old")
        cursor, _ = self.add_event("cursor")
        latest, _ = self.add_event("latest")
        self.set_cursor(cursor)
        result = receiver.plan(
            root=self.vellum,
            pulp_repo=self.pulp,
            pulp_main=latest,
            lower_bound=old,
            payload=self.payload(old, "old", event),
        )
        self.assertEqual(result["reconcile_target"], latest)

    def test_trusted_replay_must_descend_from_cursor(self) -> None:
        old, _ = self.add_event("old")
        cursor, _ = self.add_event("cursor")
        self.set_cursor(cursor)
        with self.assertRaisesRegex(receiver.ReceiverError, "must descend"):
            receiver.plan(
                root=self.vellum,
                pulp_repo=self.pulp,
                pulp_main=cursor,
                lower_bound=old,
                payload=None,
            )

    def test_divergent_target_fails_closed(self) -> None:
        landed, event = self.add_event("landed")
        landed_branch = git(self.pulp, "branch", "--show-current")
        git(self.pulp, "checkout", "-qb", "divergent", self.base)
        divergent, _ = self.add_event("divergent")
        git(self.pulp, "checkout", "-q", landed_branch)
        with self.assertRaisesRegex(receiver.ReceiverError, "not landed"):
            receiver.plan(
                root=self.vellum,
                pulp_repo=self.pulp,
                pulp_main=landed,
                lower_bound=divergent,
                payload=self.payload(divergent, "landed", event),
            )

    def test_missing_durable_coverage_fails_closed(self) -> None:
        (self.pulp / "README.md").write_text("no event\n", encoding="utf-8")
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", "no event")
        target = git(self.pulp, "rev-parse", "HEAD")
        with self.assertRaisesRegex(receiver.ReceiverError, "does not cover"):
            receiver.plan(
                root=self.vellum,
                pulp_repo=self.pulp,
                pulp_main=target,
                lower_bound=target,
                payload=None,
            )

    def test_invalid_kind_and_hash_fail_closed(self) -> None:
        commit, event = self.add_event("invalid-kind", kind="other")
        payload = self.payload(commit, "invalid-kind", event)
        with self.assertRaisesRegex(receiver.ReceiverError, "kind is invalid"):
            receiver.plan(
                root=self.vellum, pulp_repo=self.pulp, pulp_main=commit,
                lower_bound=commit, payload=payload,
            )
        payload["event_refs"][0]["sha256"] = "b" * 64
        with self.assertRaisesRegex(receiver.ReceiverError, "hash differs"):
            receiver.plan(
                root=self.vellum, pulp_repo=self.pulp, pulp_main=commit,
                lower_bound=commit, payload=payload,
            )

    def test_old_event_ref_cannot_cover_a_new_delivery(self) -> None:
        first, first_event = self.add_event("first")
        second, _ = self.add_event("second")
        payload = self.payload(second, "first", first_event)
        payload["source_base"] = first
        with self.assertRaisesRegex(receiver.ReceiverError, "exactly cover"):
            receiver.plan(
                root=self.vellum,
                pulp_repo=self.pulp,
                pulp_main=second,
                lower_bound=second,
                payload=payload,
            )

    def test_ownership_projection_hash_must_match(self) -> None:
        commit, event = self.add_event("ordinary")
        payload = self.payload(commit, "ordinary", event)
        payload["ownership_projection_sha256"] = "b" * 64
        with self.assertRaisesRegex(receiver.ReceiverError, "projection hash differs"):
            receiver.plan(
                root=self.vellum,
                pulp_repo=self.pulp,
                pulp_main=commit,
                lower_bound=commit,
                payload=payload,
            )

    def test_authority_transition_remains_valid_but_separate(self) -> None:
        commit, event = self.add_event("activation", kind="authority-transition")
        result = receiver.plan(
            root=self.vellum,
            pulp_repo=self.pulp,
            pulp_main=commit,
            lower_bound=commit,
            payload=self.payload(commit, "activation", event),
        )
        self.assertEqual(result["status"], "reconcile")


if __name__ == "__main__":
    unittest.main(verbosity=2)
