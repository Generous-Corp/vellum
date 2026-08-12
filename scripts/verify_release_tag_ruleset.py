#!/usr/bin/env python3
"""Require an active no-bypass ruleset that freezes one release tag."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
REQUIRED_RULES = {"deletion", "non_fast_forward", "update"}


class TagRulesetError(RuntimeError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def flatten_pages(payload: Any) -> list[Any]:
    if not isinstance(payload, list):
        raise TagRulesetError("ruleset API response must be an array")
    if payload and all(isinstance(page, list) for page in payload):
        return [row for page in payload for row in page]
    return payload


def verify(payload: Any, *, repository: str, tag: str) -> dict[str, Any]:
    if REPOSITORY.fullmatch(repository) is None:
        raise TagRulesetError("repository is invalid")
    if TAG.fullmatch(tag) is None:
        raise TagRulesetError("release tag is invalid")
    expected_ref = f"refs/tags/{tag}"
    matches: list[dict[str, Any]] = []
    for row in flatten_pages(payload):
        if not isinstance(row, dict):
            continue
        conditions = row.get("conditions")
        ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
        if (
            row.get("target") != "tag"
            or row.get("enforcement") != "active"
            or not isinstance(ref_name, dict)
            or ref_name.get("include") != [expected_ref]
            or ref_name.get("exclude") != []
        ):
            continue
        bypass = row.get("bypass_actors")
        rules = row.get("rules")
        if bypass != [] or not isinstance(rules, list):
            continue
        rule_rows = {
            rule.get("type"): rule for rule in rules
            if isinstance(rule, dict) and isinstance(rule.get("type"), str)
        }
        if len(rule_rows) != len(rules) or not REQUIRED_RULES.issubset(rule_rows):
            continue
        if rule_rows["deletion"] != {"type": "deletion"} or rule_rows[
            "non_fast_forward"
        ] != {"type": "non_fast_forward"}:
            continue
        if rule_rows["update"] != {
            "type": "update",
            "parameters": {"update_allows_fetch_and_merge": False},
        }:
            continue
        if (
            not isinstance(row.get("id"), int)
            or isinstance(row.get("id"), bool)
            or row["id"] <= 0
            or not isinstance(row.get("name"), str)
            or not row["name"]
        ):
            continue
        matches.append(row)
    if len(matches) != 1:
        raise TagRulesetError(
            "expected exactly one active exact-tag ruleset with no bypass actors "
            f"and rules {sorted(REQUIRED_RULES)}, found {len(matches)}"
        )
    match = matches[0]
    return {
        "schema": "vellum.release-tag-protection.v1",
        "repository": repository,
        "tag": tag,
        "ruleset_id": match["id"],
        "ruleset_name": match["name"],
        "enforcement": "active",
        "ref": expected_ref,
        "bypass_actors": [],
        "required_rules": sorted(REQUIRED_RULES),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(
            args.input.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
        report = verify(payload, repository=args.repository, tag=args.tag)
    except (OSError, ValueError, TagRulesetError) as error:
        print(f"release tag ruleset verification failed: {error}", file=sys.stderr)
        return 1
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
