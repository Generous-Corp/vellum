"""Fail-closed parser for the committed live-delivery switch."""

from __future__ import annotations

import json
import re
from pathlib import Path


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate live-delivery key")
        result[key] = value
    return result


def enabled(path: Path) -> bool:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeError, ValueError, RecursionError, TypeError):
        return False
    return isinstance(value, dict) and value.get("enabled") is True


def validate_enabled_barrier(switch: object, scope: object, cursor: object) -> None:
    if not isinstance(switch, dict) or switch.get("enabled") is not True:
        return
    if isinstance(scope, dict) and scope.get("state") == "frozen":
        raise ValueError("live delivery cannot enable while observation scope is frozen")
    barrier = switch.get("barrier_pulp_commit")
    committed = cursor.get("pulp", {}).get("last_scanned_commit") if isinstance(cursor, dict) else None
    if not isinstance(barrier, str) or re.fullmatch(r"[0-9a-f]{40}", barrier) is None:
        raise ValueError("enabled live delivery requires a full-SHA barrier")
    if barrier != committed:
        raise ValueError("live delivery barrier must equal the committed Pulp cursor")
