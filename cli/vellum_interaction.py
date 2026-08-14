"""Bounded semantic interaction plans for the Vellum browser capture lane."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


SCHEMA = "vellum.browser-interaction-plan.v1"
MAX_STEPS = 128
MAX_TARGET_BYTES = 512
MAX_VALUE_BYTES = 64 * 1024
MAX_STYLES = 64
MAX_STYLE_BYTES = 128
INTERACTION_KEYS = frozenset({
    "Enter", "Escape", "Tab", "Backspace", "Delete", "Home", "End",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
})
ACTION_FIELDS = {
    "navigate": {"action", "url"},
    "click": {"action", "target"},
    "focus": {"action", "target"},
    "input": {"action", "target", "value"},
    "key": {"action", "target", "key"},
    "snapshot": {"action", "name", "computedStyles"},
}


class InteractionPlanError(ValueError):
    """Raised when a browser interaction plan is malformed or unsafe."""


def _bounded_text(value: object, maximum: int, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str) or "\0" in value or (not value and not allow_empty):
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeEncodeError:
        return False


def _loopback_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    if parsed.fragment or parsed.query or parsed.hostname is None:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port is not None and not 0 < port <= 65535:
        return False
    try:
        import ipaddress
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def validate_interaction_plan(plan: object) -> dict[str, Any]:
    """Validate and return a plan without accepting unknown fields."""
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise InteractionPlanError(f"Unsupported interaction plan schema; expected {SCHEMA}")
    if set(plan) != {"schema", "name", "steps"}:
        raise InteractionPlanError("Interaction plan contains unknown fields")
    if not _bounded_text(plan.get("name"), 256):
        raise InteractionPlanError("Interaction plan name is invalid")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise InteractionPlanError(f"Interaction plan must contain 1-{MAX_STEPS} steps")
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise InteractionPlanError(f"Interaction step {index} is invalid")
        action = step["action"]
        if action not in ACTION_FIELDS:
            raise InteractionPlanError(f"Unsupported interaction action: {action}")
        if action == "snapshot":
            if set(step) not in ({"action"}, {"action", "name"}, {"action", "computedStyles"}, {"action", "name", "computedStyles"}):
                raise InteractionPlanError(f"Interaction step {index} has invalid fields")
            name = step.get("name", f"snapshot-{index}")
            styles = step.get("computedStyles", ["display", "visibility", "color", "font-size"])
            if not _bounded_text(name, 256) or not isinstance(styles, list) or not styles or len(styles) > MAX_STYLES:
                raise InteractionPlanError(f"Interaction snapshot step {index} is outside the bounded range")
            if not all(_bounded_text(style, MAX_STYLE_BYTES) for style in styles):
                raise InteractionPlanError(f"Interaction snapshot step {index} has malformed computed styles")
            normalized.append({"action": action, "name": name, "computedStyles": list(styles)})
            continue
        if set(step) != ACTION_FIELDS[action]:
            raise InteractionPlanError(f"Interaction step {index} has invalid fields")
        if action == "navigate":
            if not _loopback_url(step["url"]):
                raise InteractionPlanError(f"Interaction step {index} navigation must use numeric loopback HTTP(S)")
        elif action in {"click", "focus", "input", "key"}:
            if not _bounded_text(step["target"], MAX_TARGET_BYTES):
                raise InteractionPlanError(f"Interaction step {index} target is invalid")
        if action == "input" and not _bounded_text(step["value"], MAX_VALUE_BYTES, allow_empty=True):
            raise InteractionPlanError(f"Interaction step {index} input is invalid")
        if action == "key" and step["key"] not in INTERACTION_KEYS:
            raise InteractionPlanError(f"Interaction step {index} key is not allowed")
        normalized.append(dict(step))
    return {"schema": SCHEMA, "name": plan["name"], "steps": normalized}
