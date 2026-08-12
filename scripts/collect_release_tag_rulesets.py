#!/usr/bin/env python3
"""Join a paginated ruleset index with every fetched detail response."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def collect(index: Any, details: list[Any]) -> list[dict[str, Any]]:
    if not isinstance(index, list) or not all(isinstance(page, list) for page in index):
        raise ValueError("ruleset index must be a paginated array")
    summaries = [row for page in index for row in page]
    ids = [row.get("id") for row in summaries if isinstance(row, dict)]
    if (
        len(ids) != len(summaries)
        or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("ruleset index contains missing, invalid, or duplicate IDs")
    by_id: dict[int, dict[str, Any]] = {}
    for detail in details:
        if not isinstance(detail, dict):
            raise ValueError("ruleset detail must be an object")
        detail_id = detail.get("id")
        if detail_id in by_id:
            raise ValueError(f"duplicate ruleset detail: {detail_id}")
        by_id[detail_id] = detail
    if set(by_id) != set(ids):
        raise ValueError(
            "ruleset detail coverage differs from index: "
            f"missing={sorted(set(ids) - set(by_id))} "
            f"unexpected={sorted(set(by_id) - set(ids))}"
        )
    return [by_id[value] for value in sorted(ids)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--details-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        index = json.loads(
            args.index.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
        details = [
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
            for path in sorted(args.details_dir.glob("*.json"))
        ]
        result = collect(index, details)
    except (OSError, ValueError) as error:
        print(f"release tag ruleset collection failed: {error}", file=sys.stderr)
        return 1
    args.output.write_text(
        json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
