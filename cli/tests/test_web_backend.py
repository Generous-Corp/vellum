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
run_chrome_scenario = BACKEND_MODULE["run_chrome_scenario"]
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

    def test_chrome_stops_before_its_temporary_profile_is_removed(self) -> None:
        events: list[str] = []

        class Profile:
            def __enter__(self) -> str:
                events.append("profile-enter")
                return "/temporary/chrome-profile"

            def __exit__(self, *args: object) -> None:
                events.append("profile-exit")

        class Process:
            running = True

            def poll(self) -> int | None:
                return None if self.running else 0

            def terminate(self) -> None:
                events.append("terminate")
                self.running = False

            def wait(self, timeout: int) -> int:
                events.append(f"wait-{timeout}")
                return 0

            def kill(self) -> None:
                events.append("kill")
                self.running = False

        class Received:
            def wait(self, timeout: int) -> bool:
                events.append(f"evidence-{timeout}")
                return True

        process = Process()
        run_chrome_scenario(
            "http://127.0.0.1/example",
            Received(),
            chrome="/fake/chrome",
            profile_factory=lambda **_kwargs: Profile(),
            process_factory=lambda *_args, **_kwargs: process,
        )
        self.assertEqual(
            events,
            [
                "profile-enter",
                "evidence-20",
                "terminate",
                "wait-5",
                "profile-exit",
            ],
        )

    def test_chrome_timeout_still_stops_before_profile_cleanup(self) -> None:
        events: list[str] = []

        class Profile:
            def __enter__(self) -> str:
                events.append("profile-enter")
                return "/temporary/chrome-profile"

            def __exit__(self, *args: object) -> None:
                events.append("profile-exit")

        class Process:
            running = True

            def poll(self) -> int | None:
                return None if self.running else 0

            def terminate(self) -> None:
                events.append("terminate")
                self.running = False

            def wait(self, _timeout: int) -> int:
                events.append("wait")
                return 0

            def kill(self) -> None:
                events.append("kill")
                self.running = False

        class Received:
            def wait(self, _timeout: int) -> bool:
                events.append("timeout")
                return False

        process = Process()
        with self.assertRaisesRegex(BackendFailure, "timed out"):
            run_chrome_scenario(
                "http://127.0.0.1/example",
                Received(),
                chrome="/fake/chrome",
                profile_factory=lambda **_kwargs: Profile(),
                process_factory=lambda *_args, **_kwargs: process,
            )
        self.assertEqual(
            events,
            ["profile-enter", "timeout", "terminate", "wait", "profile-exit"],
        )


if __name__ == "__main__":
    unittest.main()
