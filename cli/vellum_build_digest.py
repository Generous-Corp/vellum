#!/usr/bin/env python3
"""The source digest a build records and `--no-build` checks it against.

`vellum dev` fingerprints the project sources with `snapshot`. A build stores
that digest beside its output; a later run that skips the build compares it
with the current tree, so a stale build is never driven without a refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

from vellum_dev import snapshot

SOURCE_DIGEST_NAME = "source-digest.json"
SOURCE_DIGEST_SCHEMA = "vellum.build-source-digest.v1"


def write_source_digest(directory: Path, project: Path) -> None:
    (directory / SOURCE_DIGEST_NAME).write_text(json.dumps({
        "schema": SOURCE_DIGEST_SCHEMA, "digest": snapshot(project)[0],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def staleness(directory: Path, project: Path) -> str | None:
    """Why the build in `directory` cannot be proven fresh for `project`, or
    None when its recorded digest matches the current sources."""
    try:
        recorded = json.loads((directory / SOURCE_DIGEST_NAME).read_text(encoding="utf-8"))
        built = recorded["digest"]
        valid = recorded["schema"] == SOURCE_DIGEST_SCHEMA and isinstance(built, str)
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        return ("Build records no source digest, so its freshness cannot be proven; "
                "run vellum build")
    current = snapshot(project)[0]
    if built != current:
        return (f"Build is stale: it was built from source digest {built[:12]} but the "
                f"project is now {current[:12]}; run vellum build")
    return None
