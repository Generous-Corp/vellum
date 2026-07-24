"""Canonical validation for scenarios shared by native and browser adapters."""

from __future__ import annotations

from typing import Any


SCENARIO_SCHEMAS = {"vellum.scenario.v1", "vellum.scenario.v2"}
SCENARIO_V2_ACTIONS = frozenset({
    "wait-for-idle", "press", "touch", "focus", "input", "key", "compose",
    "command", "service-result", "assert-text", "assert-accessibility",
    "capture", "throw",
})
MAX_SCENARIO_STEPS = 1000
MAX_SCENARIO_TARGET_BYTES = 1024
MAX_SCENARIO_INPUT_BYTES = 64 * 1024
SUPPORTED_SCENARIO_KEYS = {
    "Enter", "Escape", "Backspace", "Tab", "ArrowUp", "ArrowDown",
    "ArrowLeft", "ArrowRight", "Home", "End", "Delete",
}


class ScenarioValidationError(ValueError):
    pass


def _bounded(value: object, maximum: int, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str) or "\0" in value or (not value and not allow_empty):
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeEncodeError:
        return False


def validate_scenario_document(scenario: dict[str, Any]) -> None:
    if scenario.get("schema") not in SCENARIO_SCHEMAS:
        raise ScenarioValidationError("Unsupported scenario schema")
    if set(scenario) - {"schema", "name", "viewport", "steps"}:
        raise ScenarioValidationError("Scenario contains unknown fields")
    if not _bounded(scenario.get("name"), 256):
        raise ScenarioValidationError("Scenario name is invalid")

    viewport = scenario.get("viewport")
    if scenario["schema"] == "vellum.scenario.v1" and viewport is None:
        raise ScenarioValidationError("Scenario viewport is required")
    if viewport is not None:
        if not isinstance(viewport, dict) or set(viewport) != {"width", "height"}:
            raise ScenarioValidationError("Scenario viewport is invalid")
        if any(
            not isinstance(viewport.get(field), int) or
            isinstance(viewport[field], bool) or
            not 0 < viewport[field] <= 16384
            for field in ("width", "height")
        ):
            raise ScenarioValidationError("Scenario viewport dimension is invalid")

    steps = scenario.get("steps")
    if not isinstance(steps, list) or len(steps) > MAX_SCENARIO_STEPS:
        raise ScenarioValidationError(
            "Scenario steps must be an array of at most 1000 actions"
        )
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise ScenarioValidationError(f"Scenario step {index} is invalid")
        action = step["action"]
        if (
            scenario["schema"] == "vellum.scenario.v2"
            and action not in SCENARIO_V2_ACTIONS
        ):
            raise ScenarioValidationError(f"Unsupported scenario action: {action}")
        valid = False
        if action == "wait-for-idle":
            valid = set(step) == {"action"}
        elif action == "capture":
            valid = set(step) in (
                {"action"}, {"action", "name"}, {"action", "value"}
            ) and (
                not ({"name", "value"} & set(step))
                or _bounded(step.get("name") or step.get("value"), 256)
            )
        elif action in {"press", "click"}:
            valid = set(step) in (
                {"action", "target"}, {"action", "id"}
            ) and _bounded(
                step.get("target") or step.get("id"), MAX_SCENARIO_TARGET_BYTES
            )
        elif action == "focus":
            valid = set(step) == {"action", "target"} and _bounded(
                step.get("target"), MAX_SCENARIO_TARGET_BYTES
            )
        elif action in {"input", "compose"}:
            value_key = "text" if scenario["schema"] == "vellum.scenario.v1" else "value"
            valid = (
                (action == "input" or scenario["schema"] == "vellum.scenario.v2")
                and set(step) == {"action", "target", value_key}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
                and _bounded(
                    step.get(value_key), MAX_SCENARIO_INPUT_BYTES, allow_empty=True
                )
            )
        elif action == "key":
            value_key = "key" if scenario["schema"] == "vellum.scenario.v1" else "value"
            valid = (
                set(step) == {"action", "target", value_key}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
                and isinstance(step.get(value_key), str)
                and step[value_key] in SUPPORTED_SCENARIO_KEYS
            )
        elif action == "assert-accessibility":
            expected = step.get("expect")
            valid = (
                set(step) == {"action", "target", "expect"}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
                and isinstance(expected, dict)
                and not set(expected) - {"label", "role", "value"}
                and all(isinstance(value, str) for value in expected.values())
            )
        elif action == "command":
            valid = (
                scenario["schema"] == "vellum.scenario.v2"
                and set(step) == {"action", "target"}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
            )
        elif action in {"assert-text", "throw"}:
            valid = (
                scenario["schema"] == "vellum.scenario.v2"
                and set(step) == {"action", "target", "expect"}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
                and _bounded(
                    step.get("expect"),
                    MAX_SCENARIO_INPUT_BYTES,
                    allow_empty=True,
                )
            )
        elif action == "touch":
            valid = (
                scenario["schema"] == "vellum.scenario.v2"
                and set(step) == {"action", "target", "event"}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
                and isinstance(step.get("event"), dict)
            )
        elif action == "service-result":
            valid = (
                scenario["schema"] == "vellum.scenario.v2"
                and set(step) == {"action", "target", "service"}
                and _bounded(step.get("target"), MAX_SCENARIO_TARGET_BYTES)
                and isinstance(step.get("service"), dict)
            )
        else:
            raise ScenarioValidationError(f"Unsupported scenario action: {action}")
        if not valid:
            raise ScenarioValidationError(
                f"Scenario step {index} has invalid fields"
            )
