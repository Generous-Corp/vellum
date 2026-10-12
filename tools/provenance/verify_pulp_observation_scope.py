#!/usr/bin/env python3
"""Verify that Pulp observation stays scoped as Decision 0011 records."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OBSERVATORY = Path("provenance/pulp-observatory")
SCOPE_PATH = OBSERVATORY / "pulp-observation-scope.json"
CURSOR_PATH = OBSERVATORY / "cursor.json"
REPORT_PATH = OBSERVATORY / "reports/current.json"
README_PATH = OBSERVATORY / "README.md"
SCHEMA = "vellum.pulp-observation-scope.v1"
DECISION = "decisions/0011-scope-pulp-observation-to-the-activation-window.md"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
SCOPE_KEYS = {
    "schema", "decision", "state", "pulp_repository", "frozen_pulp_commit",
    "later_history_covered_by",
}
README_CLAIM = "Pulp observation covers history only through that commit"


class ScopeError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScopeError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ScopeError(f"{path} must contain an object")
    return value


def verify(root: Path) -> dict[str, Any]:
    scope = load_object(root / SCOPE_PATH)
    if set(scope) != SCOPE_KEYS:
        raise ScopeError(
            f"scope record keys differ: missing={sorted(SCOPE_KEYS - set(scope))} "
            f"unexpected={sorted(set(scope) - SCOPE_KEYS)}"
        )
    expected = {
        "schema": SCHEMA,
        "decision": DECISION,
        "state": "frozen",
        "pulp_repository": "Generous-Corp/pulp",
    }
    for key, value in expected.items():
        if scope[key] != value:
            raise ScopeError(f"scope record {key} is {scope[key]!r}, expected {value!r}")
    frozen = scope["frozen_pulp_commit"]
    if not isinstance(frozen, str) or FULL_SHA.fullmatch(frozen) is None:
        raise ScopeError("scope record frozen_pulp_commit is not a full commit SHA")
    covered = scope["later_history_covered_by"]
    if not isinstance(covered, list) or not covered or not all(
        isinstance(item, str) and item for item in covered
    ):
        raise ScopeError("scope record later_history_covered_by must list its coverage")
    if not (root / DECISION).is_file():
        raise ScopeError(f"scope record names a missing decision: {DECISION}")

    cursor = load_object(root / CURSOR_PATH)
    pulp = cursor.get("pulp")
    if not isinstance(pulp, dict) or pulp.get("repository") != "Generous-Corp/pulp":
        raise ScopeError("observatory cursor has no Generous-Corp/pulp section")
    if pulp.get("last_scanned_commit") != frozen:
        raise ScopeError(
            "observatory Pulp cursor "
            f"{pulp.get('last_scanned_commit')!r} differs from the frozen commit "
            f"{frozen!r}; advancing it requires a decision superseding 0011"
        )

    report = load_object(root / REPORT_PATH)
    if report.get("health") != "pass":
        raise ScopeError(f"observatory report health is {report.get('health')!r}")

    try:
        readme = (root / README_PATH).read_text(encoding="utf-8")
    except OSError as error:
        raise ScopeError(f"cannot read {README_PATH}: {error}") from error
    normalized = " ".join(readme.split())
    if README_CLAIM not in normalized or frozen not in normalized:
        raise ScopeError("observatory README does not state the Pulp observation scope")

    return {
        "schema": "vellum.pulp-observation-scope-report.v1",
        "status": "pass",
        "frozen_pulp_commit": frozen,
        "health": report["health"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root.resolve())
    except ScopeError as error:
        print(f"pulp-observation-scope: FAIL: {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print("pulp-observation-scope: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
