#!/usr/bin/env python3
"""Generate and verify README capability status from checked-in YAML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/status/capabilities.yaml"
README = ROOT / "README.md"
SCHEMA = "vellum.docs-status.v1"
STATUSES = {"supported", "experimental", "partial", "planned", "unsupported"}
START = "<!-- docs-sync: capabilities:start -->"
END = "<!-- docs-sync: capabilities:end -->"
IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class Error(ValueError):
    pass


def load(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Error(f"cannot read YAML source {path}: {error}") from error
    if not isinstance(value, dict) or set(value) != {"schema", "rows"}:
        raise Error("status source must contain exactly schema and rows")
    if value["schema"] != SCHEMA or not isinstance(value["rows"], list):
        raise Error(f"status source must use {SCHEMA} with an array of rows")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value["rows"]):
        context = f"rows[{index}]"
        if not isinstance(raw, dict) or set(raw) != {
            "id", "label", "status", "evidence", "boundary",
        }:
            raise Error(f"{context} fields differ")
        identifier = raw["id"]
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise Error(f"{context}.id is invalid")
        if identifier in seen:
            raise Error(f"duplicate status id: {identifier}")
        seen.add(identifier)
        if raw["status"] not in STATUSES:
            raise Error(f"{context}.status is invalid")
        if not all(isinstance(raw[name], str) and raw[name].strip()
                   for name in ("label", "boundary")):
            raise Error(f"{context} label and boundary must be non-empty")
        evidence = raw["evidence"]
        if (not isinstance(evidence, list)
                or not all(isinstance(item, str) and item.strip() for item in evidence)):
            raise Error(f"{context}.evidence must be an array of check names")
        if raw["status"] == "supported" and not evidence:
            raise Error(f"{context}: supported requires a named evidence check")
        rows.append(raw)
    if not rows:
        raise Error("status source must contain at least one row")
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    lines = [
        START,
        "| Capability or target | Status | Evidence check | Honest boundary |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        evidence = ", ".join(f"`{item}`" for item in row["evidence"]) or "none"
        lines.append(
            f"| {row['label']} | {row['status']} | {evidence} | "
            f"{row['boundary']} |"
        )
    lines.append(END)
    return "\n".join(lines)


def replace(text: str, generated: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise Error("README must contain exactly one ordered docs-sync marker pair")
    start = text.index(START)
    end = text.index(END)
    if end <= start:
        raise Error("README docs-sync markers are reversed")
    return text[:start] + generated + text[end + len(END):]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--readme", type=Path, default=README)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        generated = render(load(args.source))
        current = args.readme.read_text(encoding="utf-8")
        expected = replace(current, generated)
        if args.check:
            if current != expected:
                raise Error(
                    "README capability matrix is stale; run "
                    "python3 scripts/docs_sync.py --write"
                )
            print(f"docs-sync: OK ({args.source})")
            return 0
        args.readme.write_text(expected, encoding="utf-8")
        print(f"docs-sync: wrote {args.readme}")
        return 0
    except (OSError, Error) as error:
        print(f"docs-sync: FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
