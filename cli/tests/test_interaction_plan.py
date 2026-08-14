from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
from vellum_cdp_client import CdpClient, CdpClientError
from vellum_interaction import InteractionPlanError, validate_interaction_plan
sys.path.pop(0)


def plan(*steps: dict[str, object]) -> dict[str, object]:
    return {"schema": "vellum.browser-interaction-plan.v1", "name": "smoke", "steps": list(steps)}


class InteractionPlanTests(unittest.TestCase):
    def test_validation_normalizes_snapshot_defaults(self) -> None:
        value = validate_interaction_plan(plan({"action": "snapshot"}))
        self.assertEqual(value["steps"][0]["computedStyles"], ["display", "visibility", "color", "font-size"])

    def test_validation_accepts_only_numeric_loopback_navigation(self) -> None:
        value = validate_interaction_plan(plan({"action": "navigate", "url": "http://127.0.0.1:8000/"}))
        self.assertEqual(value["steps"][0]["url"], "http://127.0.0.1:8000/")
        for url in ("https://example.com/", "http://localhost:8000/", "http://127.0.0.1:8000/?x=1", "http://127.0.0.1:bad/"):
            with self.assertRaises(InteractionPlanError):
                validate_interaction_plan(plan({"action": "navigate", "url": url}))

    def test_validation_rejects_unknown_actions_and_unbounded_values(self) -> None:
        for step in (
            {"action": "evaluate", "expression": "document.body"},
            {"action": "key", "target": "#title", "key": "Control+A"},
            {"action": "input", "target": "#title", "value": "x" * (64 * 1024 + 1)},
        ):
            with self.assertRaises(InteractionPlanError):
                validate_interaction_plan(plan(step))

    def test_execution_is_limited_to_fixed_cdp_operations(self) -> None:
        client = object.__new__(CdpClient)
        calls: list[tuple[str, dict[str, object]]] = []

        def command(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            calls.append((method, params or {}))
            if method == "DOM.getDocument":
                return {"root": {"nodeId": 1}}
            if method == "DOM.querySelector":
                return {"nodeId": 2}
            if method == "DOM.getBoxModel":
                return {"model": {"content": [0, 0, 100, 0, 100, 50, 0, 50]}}
            if method == "DOMSnapshot.captureSnapshot":
                return {"documents": []}
            return {}

        client.command = command  # type: ignore[method-assign]
        evidence = client.execute_interaction_plan(plan(
            {"action": "focus", "target": "[data-vellum-id='title']"},
            {"action": "input", "target": "[data-vellum-id='title']", "value": "Roadmap"},
            {"action": "key", "target": "[data-vellum-id='title']", "key": "Enter"},
            {"action": "click", "target": "[data-vellum-id='save']"},
            {"action": "snapshot", "name": "saved"},
        ))
        self.assertEqual(evidence["schema"], "vellum.browser-interaction-evidence.v1")
        self.assertNotIn("Runtime.evaluate", [method for method, _ in calls])
        self.assertIn("DOMSnapshot.captureSnapshot", [method for method, _ in calls])

    def test_execution_rejects_malformed_plan_as_cdp_error(self) -> None:
        client = object.__new__(CdpClient)
        with self.assertRaises(CdpClientError):
            client.execute_interaction_plan({"schema": "vellum.browser-interaction-plan.v1"})


if __name__ == "__main__":
    unittest.main()
