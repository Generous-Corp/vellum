#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("verify_pulp_observation_scope.py")
SPEC = importlib.util.spec_from_file_location("verify_pulp_observation_scope", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
ROOT = SCRIPT.parents[2]
FROZEN = "aa630815630ca259b743651dacdc335f3d94a39a"


class Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in (
            verifier.SCOPE_PATH,
            verifier.CURSOR_PATH,
            verifier.REPORT_PATH,
            verifier.README_PATH,
            Path(verifier.DECISION),
        ):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite(self, relative: Path, change) -> None:
        path = self.root / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_repository_scope_holds(self) -> None:
        result = verifier.verify(ROOT)
        self.assertEqual(result["frozen_pulp_commit"], FROZEN)
        self.assertEqual(result["health"], "pass")

    def test_copied_tree_passes_before_any_mutation(self) -> None:
        self.assertEqual(verifier.verify(self.root)["status"], "pass")

    def test_advanced_pulp_cursor_is_rejected(self) -> None:
        def advance(cursor):
            cursor["pulp"]["last_scanned_commit"] = "3" * 40
        self.rewrite(verifier.CURSOR_PATH, advance)
        with self.assertRaisesRegex(verifier.ScopeError, "superseding 0011"):
            verifier.verify(self.root)

    def test_vellum_cursor_may_advance(self) -> None:
        def advance(cursor):
            cursor["vellum"]["last_scanned_commit"] = "4" * 40
        self.rewrite(verifier.CURSOR_PATH, advance)
        self.assertEqual(verifier.verify(self.root)["status"], "pass")

    def test_failing_health_is_rejected(self) -> None:
        def fail(report):
            report["health"] = "fail"
        self.rewrite(verifier.REPORT_PATH, fail)
        with self.assertRaisesRegex(verifier.ScopeError, "health"):
            verifier.verify(self.root)

    def test_scope_record_must_be_exact(self) -> None:
        def thaw(scope):
            scope["state"] = "active"
        self.rewrite(verifier.SCOPE_PATH, thaw)
        with self.assertRaisesRegex(verifier.ScopeError, "state"):
            verifier.verify(self.root)

    def test_scope_record_rejects_extra_keys(self) -> None:
        def extend(scope):
            scope["covers_through"] = "main"
        self.rewrite(verifier.SCOPE_PATH, extend)
        with self.assertRaisesRegex(verifier.ScopeError, "keys differ"):
            verifier.verify(self.root)

    def test_missing_scope_record_is_rejected(self) -> None:
        (self.root / verifier.SCOPE_PATH).unlink()
        with self.assertRaisesRegex(verifier.ScopeError, "cannot read"):
            verifier.verify(self.root)

    def test_missing_decision_is_rejected(self) -> None:
        (self.root / verifier.DECISION).unlink()
        with self.assertRaisesRegex(verifier.ScopeError, "missing decision"):
            verifier.verify(self.root)

    def test_readme_must_state_the_scope(self) -> None:
        readme = self.root / verifier.README_PATH
        text = readme.read_text(encoding="utf-8")
        claim = re.compile(r"\s+".join(verifier.README_CLAIM.split()))
        self.assertIsNotNone(claim.search(text))
        readme.write_text(
            claim.sub("Pulp observation is current", text), encoding="utf-8"
        )
        with self.assertRaisesRegex(verifier.ScopeError, "README"):
            verifier.verify(self.root)


if __name__ == "__main__":
    unittest.main()
