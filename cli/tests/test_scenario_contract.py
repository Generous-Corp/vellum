from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cli"))
try:
    from vellum_native_backend import scenario_arguments
    from vellum_scenario import (
        SCENARIO_V2_ACTIONS,
        ScenarioValidationError,
        validate_scenario_document,
    )
finally:
    sys.path.pop(0)


STEPS = {
    "wait-for-idle": {"action": "wait-for-idle"},
    "press": {"action": "press", "target": "button"},
    "touch": {"action": "touch", "target": "button", "event": {"payload": {}}},
    "focus": {"action": "focus", "target": "input"},
    "input": {"action": "input", "target": "input", "value": "value"},
    "key": {"action": "key", "target": "input", "value": "Enter"},
    "compose": {"action": "compose", "target": "input", "value": "日本語"},
    "command": {"action": "command", "target": "save"},
    "service-result": {
        "action": "service-result", "target": "load", "service": {"ok": True}
    },
    "assert-text": {"action": "assert-text", "target": "label", "expect": "ready"},
    "assert-accessibility": {
        "action": "assert-accessibility", "target": "input", "expect": {}
    },
    "capture": {"action": "capture", "name": "proof"},
    "throw": {"action": "throw", "target": "fail", "expect": "expected"},
}
WEB_PATTERNS = {
    "wait-for-idle": "step.action === 'wait-for-idle'",
    "press": "step.action === 'press'",
    "touch": "step.action === 'touch'",
    "focus": "step.action === 'focus'",
    "input": "step.action === 'input'",
    "key": "step.action === 'key'",
    "compose": "step.action === 'compose'",
    "command": "step.action === 'command'",
    "service-result": "step.action === 'service-result'",
    "assert-text": "step.action === 'assert-text'",
    "assert-accessibility": "step.action === 'assert-accessibility'",
    "capture": "step.action === 'capture'",
    "throw": "step.action === 'throw'",
}


class Tests(unittest.TestCase):
    def test_schema_validator_native_and_browser_action_sets_match(self) -> None:
        schema = json.loads(
            (ROOT / "authoring/schema/scenario-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        schema_actions = set(
            schema["$defs"]["step"]["properties"]["action"]["enum"]
        )
        self.assertEqual(schema_actions, set(SCENARIO_V2_ACTIONS))
        self.assertEqual(schema_actions, set(STEPS))
        self.assertEqual(schema_actions, set(WEB_PATTERNS))

    def test_every_v2_action_lowers_to_the_native_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = root / "tests/scenarios/action.json"
            scenario.parent.mkdir(parents=True)
            for action, step in STEPS.items():
                with self.subTest(action=action):
                    scenario.write_text(json.dumps({
                        "schema": "vellum.scenario.v2",
                        "name": action,
                        "steps": [step],
                    }), encoding="utf-8")
                    _, name = scenario_arguments({"root": root}, "action")
                    self.assertEqual(name, action)

    def test_every_v2_action_has_a_browser_handler(self) -> None:
        host = (ROOT / "web/consumer/vellum_host.js").read_text(encoding="utf-8")
        for action, pattern in WEB_PATTERNS.items():
            with self.subTest(action=action):
                self.assertIn(pattern, host)

    def test_v2_validator_rejects_actions_outside_the_declared_set(self) -> None:
        with self.assertRaisesRegex(
            ScenarioValidationError,
            "Unsupported scenario action: click",
        ):
            validate_scenario_document({
                "schema": "vellum.scenario.v2",
                "name": "undeclared alias",
                "steps": [{"action": "click", "target": "button"}],
            })
        for invalid in (None, {}, [], "x" * (64 * 1024 + 1)):
            with self.subTest(expect=type(invalid).__name__), self.assertRaisesRegex(
                ScenarioValidationError,
                "invalid fields",
            ):
                validate_scenario_document({
                    "schema": "vellum.scenario.v2",
                    "name": "invalid expectation",
                    "steps": [{
                        "action": "assert-text",
                        "target": "label",
                        "expect": invalid,
                    }],
                })


if __name__ == "__main__":
    unittest.main(verbosity=2)
