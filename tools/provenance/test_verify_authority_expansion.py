#!/usr/bin/env python3
"""Negative controls for the non-authoritative expansion proposal."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify_authority_expansion.py")
SPEC = importlib.util.spec_from_file_location("verify_authority_expansion", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
ROOT = Path(__file__).resolve().parents[2]


class Tests(unittest.TestCase):
    def test_workflow_runs_and_retains_expansion_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/provenance.yml").read_text()
        self.assertIn(
            "python3 tools/provenance/test_verify_authority_expansion.py",
            workflow,
        )
        self.assertIn(
            "python3 tools/provenance/verify_authority_expansion.py", workflow
        )
        self.assertGreaterEqual(workflow.count("authority-expansion-report.json"), 2)

    def copy(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        target = root / verifier.PROPOSAL_PATH
        target.parent.mkdir(parents=True)
        shutil.copy2(ROOT / verifier.PROPOSAL_PATH, target)
        shutil.copy2(
            ROOT / verifier.EXPANSIONS_ROOT / "README.md",
            root / verifier.EXPANSIONS_ROOT / "README.md",
        )
        return temporary, root, target

    def mutate(self, callback) -> dict:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        data = json.loads(target.read_text())
        callback(data)
        target.write_text(json.dumps(data, indent=2) + "\n")
        return verifier.verify(root)

    def test_committed_proposal_passes_without_authority(self) -> None:
        report = verifier.verify(ROOT)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["authority_effect"], "none")

    def test_duplicate_key_fails(self) -> None:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        target.write_text('{"schema_version":1,"schema_version":1}\n')
        self.assertEqual(verifier.verify(root)["status"], "fail")

    def test_authority_claim_fails(self) -> None:
        report = self.mutate(lambda d: d.update(authority_effect="transferred"))
        self.assertEqual(report["status"], "fail")
        self.assertIsNone(report["authority_effect"])
        self.assertIsNone(report["proposal_id"])

    def test_coordinate_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["coordinates"].update(pulp_baseline_commit="0" * 40)
        )
        self.assertEqual(report["status"], "fail")

    def test_proposal_timestamp_drift_fails(self) -> None:
        report = self.mutate(lambda d: d.update(proposed_at="2026-08-10T22:00:00Z"))
        self.assertEqual(report["status"], "fail")

    def test_family_selector_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0]["pulp_selectors"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_family_target_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0]["vellum_target_roots"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_family_title_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0].update(title=None)
        )
        self.assertEqual(report["status"], "fail")

    def test_retained_boundary_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["retained_boundaries"][0]["pulp_selectors"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_retained_rationale_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["retained_boundaries"][0].update(rationale="inverted")
        )
        self.assertEqual(report["status"], "fail")

    def test_maintenance_path_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0]["pulp_paths"].pop()
        )
        self.assertEqual(report["status"], "fail")

    def test_path_traversal_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0]["pulp_paths"].__setitem__(
                0, "core/../../outside"
            )
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("safely repository-relative" in e for e in report["errors"]))

    def test_truncated_state_machine_fails(self) -> None:
        report = self.mutate(lambda d: d["required_transitions"].pop())
        self.assertEqual(report["status"], "fail")

    def test_gate_relaxation_fails(self) -> None:
        report = self.mutate(
            lambda d: d["gates"].update(
                source_work_before_exact_boundary_acknowledgement=True
            )
        )
        self.assertEqual(report["status"], "fail")

    def test_boolean_schema_version_does_not_equal_integer(self) -> None:
        report = self.mutate(lambda d: d.update(schema_version=True))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("schema_version" in e for e in report["errors"]))

    def test_integer_gate_does_not_equal_boolean(self) -> None:
        report = self.mutate(
            lambda d: d["gates"].update(proposal_may_transfer_authority=0)
        )
        self.assertEqual(report["status"], "fail")

    def test_integer_coordinate_does_not_equal_boolean(self) -> None:
        report = self.mutate(
            lambda d: d["coordinates"].update(vellum_work_repository_is_temporary=1)
        )
        self.assertEqual(report["status"], "fail")

    def test_maintenance_expiry_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0].update(expires_at_gate="never")
        )
        self.assertEqual(report["status"], "fail")

    def test_maintenance_rationale_drift_fails(self) -> None:
        report = self.mutate(
            lambda d: d["interim_maintenance"][0].update(rationale="rewritten")
        )
        self.assertEqual(report["status"], "fail")

    def test_non_string_nested_path_returns_report(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0].update(pulp_selectors=[{}])
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["errors"])

    def test_unhashable_nested_target_returns_report(self) -> None:
        report = self.mutate(
            lambda d: d["capability_families"][0].update(vellum_target_roots=[{}])
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["errors"])

    def test_unknown_expansion_artifact_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        unknown = root / verifier.EXPANSIONS_ROOT / "unreviewed-acceptance.json"
        unknown.write_text('{"authority_effect":"transferred"}\n')
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("artifact set differs" in e for e in report["errors"]))

    def test_filesystem_closure_rejects_untracked_artifact_in_git_checkout(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "add", verifier.EXPANSIONS_ROOT.as_posix()],
            check=True,
        )
        unknown = root / verifier.EXPANSIONS_ROOT / "untracked-acceptance.json"
        unknown.write_text('{"authority_effect":"transferred"}\n')
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("untracked-acceptance.json" in e for e in report["errors"]))

    def test_dangling_symlink_artifact_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        unknown = root / verifier.EXPANSIONS_ROOT / "pulp-watch-acceptance.json"
        unknown.symlink_to(root / "missing-acceptance.json")
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("must not be symlinks" in e for e in report["errors"]))

    def test_symlinked_directory_artifact_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        external = root / "external-acceptance"
        external.mkdir()
        unknown = root / verifier.EXPANSIONS_ROOT / "acceptance"
        unknown.symlink_to(external, target_is_directory=True)
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("must not be symlinks" in e for e in report["errors"]))

    def test_formatting_only_proposal_drift_fails_closed(self) -> None:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        target.write_text(target.read_text() + "\n")
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("proposal differs" in e for e in report["errors"]))

    def test_expansion_readme_drift_fails_closed(self) -> None:
        temporary, root, _ = self.copy()
        self.addCleanup(temporary.cleanup)
        readme = root / verifier.EXPANSIONS_ROOT / "README.md"
        readme.write_text(readme.read_text() + "\nWatch acceptance authorizes source.\n")
        report = verifier.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("README differs" in e for e in report["errors"]))

    def test_cli_failure_writes_report_and_exits_nonzero(self) -> None:
        temporary, root, target = self.copy()
        self.addCleanup(temporary.cleanup)
        data = json.loads(target.read_text())
        data["authority_effect"] = "transferred"
        target.write_text(json.dumps(data, indent=2) + "\n")
        output = root / "reports" / "authority-expansion.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(output),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = json.loads(output.read_text())
        self.assertEqual(report["status"], "fail")

    def test_malformed_nested_types_return_report(self) -> None:
        report = self.mutate(lambda d: d.update(capability_families=None))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main()
