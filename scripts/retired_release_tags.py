#!/usr/bin/env python3
"""The one reader of provenance/retired-release-tags.json.

A retired tag name once identified bytes that no longer exist or were never
published. Reusing the name would give it different contents, so every release
path refuses it, and every one of them reads the record through this module.
The record fails closed: a missing, malformed or unexpected-schema file is an
error, never an empty set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RELATIVE_PATH = "provenance/retired-release-tags.json"
PATH = Path(__file__).resolve().parents[1] / RELATIVE_PATH
SCHEMA = "vellum.retired-release-tags.v1"


class RetiredTagsError(ValueError):
    pass


def tag_names(payload: Any, where: str = RELATIVE_PATH) -> set[str]:
    """The retired tag names in a parsed record."""
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise RetiredTagsError(f"retired release-tag record {where} does not declare {SCHEMA}")
    rows = payload.get("tags")
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("tag"), str) and row["tag"]
        for row in rows
    ):
        raise RetiredTagsError(f"retired release-tag record {where} has malformed tag entries")
    return {row["tag"] for row in rows}


def load(path: Path = PATH) -> set[str]:
    """The retired tag names in the record at ``path``."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RetiredTagsError(f"retired release-tag record {path} is unreadable: {error}") from error
    return tag_names(payload, str(path))
