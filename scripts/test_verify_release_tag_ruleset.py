#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import verify_release_tag_ruleset as verifier

# What GitHub returned for GET repos/Generous-Corp/vellum/rulesets/26335054
# after release-tag POSTed the freeze for v0.1.9. GitHub drops the default
# `update_allows_fetch_and_merge: false`, so the update rule reads back bare.
READBACK = Path(__file__).with_name("fixtures") / "freeze-v0.1.9-ruleset-readback.json"


class Tests(unittest.TestCase):
    def row(self) -> dict:
        return {
            "id": 42,
            "name": "Freeze v0.2.0",
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {
                "ref_name": {"include": ["refs/tags/v0.2.0"], "exclude": []}
            },
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "update",
                    "parameters": {"update_allows_fetch_and_merge": False},
                },
            ],
        }

    def test_exact_active_no_bypass_ruleset_passes(self) -> None:
        report = verifier.verify(
            [[self.row()]], repository="Generous-Corp/vellum", tag="v0.2.0"
        )
        self.assertEqual(report["ruleset_id"], 42)
        self.assertEqual(report["required_rules"], sorted(verifier.REQUIRED_RULES))

    def test_github_read_back_shape_passes(self) -> None:
        # GitHub stores and returns the update rule without parameters.
        row = self.row()
        row["rules"][2] = {"type": "update"}
        report = verifier.verify(
            [[row]], repository="Generous-Corp/vellum", tag="v0.2.0"
        )
        self.assertEqual(report["ruleset_id"], 42)

    def test_update_rule_shapes(self) -> None:
        forbids = verifier.update_rule_forbids_fetch_and_merge
        self.assertTrue(forbids({"type": "update"}))
        self.assertTrue(forbids({"type": "update", "parameters": {"update_allows_fetch_and_merge": False}}))
        self.assertFalse(forbids({"type": "deletion"}))
        self.assertFalse(forbids({"type": "update", "parameters": {"update_allows_fetch_and_merge": True}}))

    def test_live_freeze_read_back_passes(self) -> None:
        live = json.loads(READBACK.read_text(encoding="utf-8"))
        self.assertEqual(live["rules"][2], {"type": "update"})
        report = verifier.verify(
            [live], repository="Generous-Corp/vellum", tag="v0.1.9"
        )
        self.assertEqual(report["ruleset_id"], 26335054)
        self.assertEqual(report["ruleset_name"], "freeze-v0.1.9")

    def test_live_read_back_that_allows_fetch_and_merge_fails(self) -> None:
        live = json.loads(READBACK.read_text(encoding="utf-8"))
        live["rules"][2] = {
            "type": "update",
            "parameters": {"update_allows_fetch_and_merge": True},
        }
        with self.assertRaises(verifier.TagRulesetError):
            verifier.verify([live], repository="Generous-Corp/vellum", tag="v0.1.9")

    def test_missing_rule_bypass_wrong_ref_and_ambiguity_fail(self) -> None:
        mutations = []
        missing = self.row()
        missing["rules"].pop()
        mutations.append([missing])
        bypass = self.row()
        bypass["bypass_actors"] = [{"actor_type": "OrganizationAdmin"}]
        mutations.append([bypass])
        wrong_ref = self.row()
        wrong_ref["conditions"]["ref_name"]["include"] = ["refs/tags/v0.2.*"]
        mutations.append([wrong_ref])
        update_exception = self.row()
        update_exception["rules"][2]["parameters"][
            "update_allows_fetch_and_merge"
        ] = True
        mutations.append([update_exception])
        for parameters in (
            {},
            {"update_allows_fetch_and_merge": None},
            {"update_allows_fetch_and_merge": False, "unexpected": True},
            [],
            None,
        ):
            malformed_update = self.row()
            malformed_update["rules"][2]["parameters"] = parameters
            mutations.append([malformed_update])
        update_with_extra_key = self.row()
        update_with_extra_key["rules"][2] = {"type": "update", "unexpected": True}
        mutations.append([update_with_extra_key])
        mutations.append([self.row(), copy.deepcopy(self.row())])
        for rows in mutations:
            with self.subTest(rows=rows), self.assertRaises(verifier.TagRulesetError):
                verifier.verify(
                    rows, repository="Generous-Corp/vellum", tag="v0.2.0"
                )


if __name__ == "__main__":
    unittest.main()
