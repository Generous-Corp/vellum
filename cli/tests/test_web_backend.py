from __future__ import annotations

from pathlib import Path
import runpy
import sys
import unittest


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "cli/vellum_web_backend.py"
sys.path.insert(0, str(REPO / "cli"))
try:
    BACKEND_MODULE = runpy.run_path(str(BACKEND))
finally:
    sys.path.pop(0)

validate_scenario_evidence = BACKEND_MODULE["validate_scenario_evidence"]
BackendFailure = BACKEND_MODULE["BackendFailure"]


class WebScenarioEvidenceTests(unittest.TestCase):
    @staticmethod
    def evidence(*, presses: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema": "vellum.web-proof.v1",
            "backend": "wasm-shared-cpp-core+canvas2d-shell",
            "authoringRuntime": "browser JavaScript",
            "initial": {"digest": 1, "commandCount": 4},
            "final": {"digest": 1, "commandCount": 4},
            "captures": [{"name": "imported-design"}],
            "presses": presses,
            "canvasDataBytes": 4096,
        }

    def test_static_boot_and_render_scenario_is_valid(self) -> None:
        validate_scenario_evidence(self.evidence(presses=[]))

    def test_every_semantic_press_must_change_rendered_state(self) -> None:
        with self.assertRaises(BackendFailure) as caught:
            validate_scenario_evidence(self.evidence(
                presses=[{"target": "save", "changed": False}],
            ))
        self.assertEqual(caught.exception.status, "test_failed")

    def test_changed_semantic_press_is_valid(self) -> None:
        validate_scenario_evidence(self.evidence(
            presses=[{"target": "save", "changed": True}],
        ))

    def test_missing_press_evidence_is_rejected(self) -> None:
        evidence = self.evidence(presses=[])
        del evidence["presses"]
        with self.assertRaises(BackendFailure):
            validate_scenario_evidence(evidence)

    def test_missing_render_evidence_is_rejected(self) -> None:
        evidence = self.evidence(presses=[])
        del evidence["canvasDataBytes"]
        with self.assertRaises(BackendFailure):
            validate_scenario_evidence(evidence)


if __name__ == "__main__":
    unittest.main()
