#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest

import verify_release_tag_ruleset as verifier


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
        malformed_update = self.row()
        malformed_update["rules"][2].pop("parameters")
        mutations.append([malformed_update])
        mutations.append([self.row(), copy.deepcopy(self.row())])
        for rows in mutations:
            with self.subTest(rows=rows), self.assertRaises(verifier.TagRulesetError):
                verifier.verify(
                    rows, repository="Generous-Corp/vellum", tag="v0.2.0"
                )


if __name__ == "__main__":
    unittest.main()
