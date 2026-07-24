#!/usr/bin/env python3
"""Integration tests for durable authority reconciliation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("finalize_authority_reconciliation.py")
SPEC = importlib.util.spec_from_file_location("finalize_authority_reconciliation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
finalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalizer)
SOURCE_ROOT = SCRIPT.parents[2]
SLICES = [
    "canvas-kernel",
    "capture-primitives",
    "design-schema-compiler",
    "macos-shell",
    "render-skia-dawn",
    "retained-ui-kernel",
]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.root = temporary / "vellum"
        self.pulp = temporary / "pulp"
        self.root.mkdir()
        self.pulp.mkdir()
        shutil.copytree(SOURCE_ROOT / "provenance", self.root / "provenance")
        shutil.copytree(SOURCE_ROOT / "product", self.root / "product")
        shutil.rmtree(self.root / finalizer.observatory.EVENTS_PATH)
        (self.root / finalizer.observatory.EVENTS_PATH).mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Reconciliation Test")
        git(self.root, "config", "user.email", "reconciliation@example.invalid")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "historical Vellum cursor")
        self.vellum_seed = git(self.root, "rev-parse", "HEAD")

        git(self.pulp, "init", "-q")
        git(self.pulp, "config", "user.name", "Pulp Activation Test")
        git(self.pulp, "config", "user.email", "pulp-activation@example.invalid")
        candidate_slices = []
        candidate_projection = {}
        historical_projection = {}
        for index, slice_id in enumerate(SLICES):
            path = f"core/{slice_id}/source.cpp"
            source = self.pulp / path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"int source_{index};\n", encoding="utf-8")
            candidate_slices.append(
                {
                    "id": slice_id,
                    "state": "pulp-authoritative-untransferred",
                    "paths": [path],
                    "authority": None,
                }
            )
        prepared = {
            "schema_version": 2,
            "framework_repository": "Generous-Corp/vellum",
            "freeze_owner": "@danielraffel",
            "activation": {
                "state": "prepared",
                "pulp_extraction_base": "a" * 40,
                "vellum_authority_commit": None,
                "authority_record_path": None,
                "initial_transition_event": None,
                "accepted_by": None,
                "accepted_at": None,
            },
            "slices": candidate_slices,
        }
        write_json(self.pulp / ".github/vellum-ownership.json", prepared)
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", "prepared candidate")
        self.candidate = git(self.pulp, "rev-parse", "HEAD")
        cursor = finalizer.load_json(self.root / finalizer.CURSOR_PATH)
        cursor["pulp"]["scan_base_commit"] = self.candidate
        cursor["pulp"]["last_scanned_commit"] = self.candidate
        cursor["vellum"]["scan_base_commit"] = self.vellum_seed
        cursor["vellum"]["last_scanned_commit"] = self.vellum_seed
        write_json(self.root / finalizer.CURSOR_PATH, cursor)
        git(self.root, "add", finalizer.CURSOR_PATH.as_posix())
        git(self.root, "commit", "-qm", "authority start")
        self.authority_start = git(self.root, "rev-parse", "HEAD")
        ownership_blob = git(
            self.pulp, "rev-parse", f"{self.candidate}:.github/vellum-ownership.json"
        )
        for item in candidate_slices:
            path = item["paths"][0]
            metadata = {
                "blob": git(self.pulp, "rev-parse", f"{self.candidate}:{path}"),
                "mode": "100644",
            }
            candidate_projection[path] = metadata
            historical_projection[path] = {
                **metadata,
                "classification": "framework-core",
            }
        self.record_path = "provenance/authority/records/native-design-kernel-v1.json"
        self.record = {
            "schema_version": 2,
            "state": "pending-pulp-activation",
            "source_repository": "Generous-Corp/pulp",
            "framework_repository": "Generous-Corp/vellum",
            "pulp_extraction_base": "a" * 40,
            "historical_seed_commit": "b" * 40,
            "pulp_candidate_commit": self.candidate,
            "pulp_ownership_projection_blob": ownership_blob,
            "authority_start_commit": self.authority_start,
            "authority_record_ref": "refs/tags/authority/native-design-kernel-v1",
            "cut_manifest_sha256": "c" * 64,
            "authority_groups": [
                {
                    "id": "native-design-kernel-v1",
                    "lineage_mode": "history-seed-ancestor-active-reimplementation",
                    "pulp_legacy_slices": SLICES,
                    "pulp_historical_seed_projection": historical_projection,
                    "pulp_activation_candidate_projection": candidate_projection,
                    "vellum_implementation_projection": {
                        "runtime/view.cpp": {"blob": "d" * 40, "mode": "100644"}
                    },
                }
            ],
            "pulp_activation": None,
            "approved_by": "@danielraffel",
            "approved_at": "2026-07-24T01:00:00Z",
        }
        write_json(self.root / self.record_path, self.record)
        git(self.root, "add", self.record_path)
        git(self.root, "commit", "-qm", "pending record")
        self.record_commit = git(self.root, "rev-parse", "HEAD")

        event_id = "20260724-authority-activation"
        event_path = f".github/vellum-change-events/{event_id}.json"
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "kind": "authority-transition",
            "created_at": "2026-07-24T02:00:00Z",
            "slices": SLICES,
            "rationale": "Activate independently verified Vellum authority.",
            "tests": ["Vellum freeze", "Vellum trusted freeze"],
            "transition": "activate",
            "vellum_authority_commit": self.record_commit,
            "approved_by": "@danielraffel",
            "counterpart": self.record_path,
        }
        authority_metadata = {
            "event_id": event_id,
            "vellum_commit": self.record_commit,
            "counterpart": self.record_path,
            "accepted_by": "@danielraffel",
            "accepted_at": event["created_at"],
        }
        active = json.loads(json.dumps(prepared))
        active["activation"].update(
            {
                "state": "active",
                "vellum_authority_commit": self.record_commit,
                "authority_record_path": self.record_path,
                "initial_transition_event": event_id,
                "accepted_by": "@danielraffel",
                "accepted_at": event["created_at"],
            }
        )
        for item in active["slices"]:
            item["state"] = "framework-authoritative-transferred"
            item["authority"] = authority_metadata
        write_json(self.pulp / ".github/vellum-ownership.json", active)
        write_json(self.pulp / event_path, event)
        git(self.pulp, "add", ".")
        git(self.pulp, "commit", "-qm", "activate authority")
        self.activation = git(self.pulp, "rev-parse", "HEAD")
        checks = [
            {
                "name": name,
                "head_sha": self.activation,
                "conclusion": "success",
                "app_id": 15368,
                "check_run_id": str(index + 1),
                "details_url": f"https://example.invalid/check/{index + 1}",
            }
            for index, name in enumerate(["Vellum freeze", "Vellum trusted freeze"])
        ]
        self.evidence = {
            "schema_version": 1,
            "state": "landed-pulp-activation-evidence",
            "pulp_activation_commit": self.activation,
            "ownership_projection_path": ".github/vellum-ownership.json",
            "ownership_projection_blob": git(
                self.pulp,
                "rev-parse",
                f"{self.activation}:.github/vellum-ownership.json",
            ),
            "authority_event_path": event_path,
            "authority_event_blob": git(
                self.pulp, "rev-parse", f"{self.activation}:{event_path}"
            ),
            "checks": checks,
            "branch_protection": {
                "strict": True,
                "required_contexts": ["Vellum freeze", "Vellum trusted freeze"],
            },
            "retrieved_at": "2026-07-24T03:00:00Z",
        }
        self.proof = {
            "status": "pass",
            "authority_start_commit": self.authority_start,
            "authority_record_commit": self.record_commit,
            "pulp_activation_commit": self.activation,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self):
        with mock.patch.object(
            finalizer.authority, "verify_pending_record", return_value={"status": "pass"}
        ):
            return finalizer.build_state(
                root=self.root,
                pulp_repo=self.pulp,
                vellum_repo=self.root,
                record_path=self.record_path,
                record_commit=self.record_commit,
                evidence=self.evidence,
                active_proof=self.proof,
            )

    def test_build_is_deterministic_and_materializes_complete_active_state(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        finalizer.write_transaction(self.root, first)
        lock = finalizer.load_json(self.root / finalizer.LOCK_PATH)
        legacy = finalizer.load_json(self.root / finalizer.LEGACY_MAP_PATH)
        cursor = finalizer.load_json(self.root / finalizer.CURSOR_PATH)
        extraction = finalizer.load_json(self.root / finalizer.EXTRACTION_PATH)
        report = finalizer.load_json(self.root / finalizer.REPORT_JSON_PATH)
        self.assertEqual(lock["state"], "active")
        self.assertEqual(lock["vellum_authority_record_commit"], self.record_commit)
        self.assertEqual(lock["pulp_activation_commit"], self.activation)
        self.assertEqual(cursor["state"], "active")
        self.assertEqual(cursor["pulp"]["last_scanned_commit"], self.activation)
        self.assertEqual(cursor["vellum"]["last_scanned_commit"], self.record_commit)
        self.assertEqual(extraction["authority"]["state"], "active")
        self.assertEqual(
            extraction["authority"]["authority_record_commit"], self.record_commit
        )
        self.assertEqual(report["state"], "active")
        self.assertEqual(report["activation_blockers"], [])
        ownership = (self.root / finalizer.OWNERSHIP_PATH).read_text(encoding="utf-8")
        self.assertTrue(ownership.startswith("schema_version: 2\n"))
        self.assertEqual(
            ownership.count("    state: framework-authoritative-active\n"), 6
        )
        self.assertEqual(
            ownership.count("    state: framework-reimplemented-no-transfer\n"), 1
        )
        transferred = {
            item["id"]
            for item in legacy["mappings"]
            if item["authority"] == finalizer.TRANSFERRED_AUTHORITY
        }
        self.assertEqual(transferred, set(SLICES))
        finalizer.observatory.validate_lock_map_cursor(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "finalize authority reconciliation")
        reconciliation_commit = git(self.root, "rev-parse", "HEAD")
        finalizer.observatory.verify_vellum_observatory_tail(
            self.root, self.record_commit, reconciliation_commit
        )

    def test_wrong_live_proof_fails_before_any_write(self) -> None:
        before = {
            path: (self.root / path).read_bytes()
            for path in (
                finalizer.OWNERSHIP_PATH,
                finalizer.EXTRACTION_PATH,
                finalizer.LOCK_PATH,
                finalizer.LEGACY_MAP_PATH,
                finalizer.CURSOR_PATH,
                finalizer.REPORT_JSON_PATH,
                finalizer.REPORT_MD_PATH,
            )
        }
        self.proof["pulp_activation_commit"] = "f" * 40
        with self.assertRaisesRegex(finalizer.ReconciliationError, "proof"):
            self.build()
        self.assertEqual(
            before, {path: (self.root / path).read_bytes() for path in before}
        )

    def test_wrong_check_producer_is_rejected(self) -> None:
        self.evidence["checks"][0]["app_id"] = 999
        with self.assertRaisesRegex(finalizer.ReconciliationError, "App-bound"):
            self.build()

    def test_transaction_rolls_back_every_replaced_file(self) -> None:
        outputs = self.build()
        before = {path: (self.root / path).read_bytes() for path in outputs}
        real_replace = finalizer.os.replace
        calls = 0

        def fail_midway(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("injected replace failure")
            real_replace(source, destination)

        with mock.patch.object(finalizer.os, "replace", side_effect=fail_midway):
            with self.assertRaisesRegex(OSError, "injected"):
                finalizer.write_transaction(self.root, outputs)
        self.assertEqual(
            before, {path: (self.root / path).read_bytes() for path in outputs}
        )


if __name__ == "__main__":
    unittest.main()
