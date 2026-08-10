#!/usr/bin/env python3
"""Verify current-state documentation against active provenance artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"[0-9a-f]{40}\Z")
ACTIVE_REQUIRED = {
    "README.md": (
        "Source authority for the selected mapped framework slices is active",
        "published, immutable `v0.1.7`",
    ),
    "docs/ownership.md": (
        "Source authority for the selected mapped framework slices is active",
        "Pulp dependency-adoption gate",
    ),
    "provenance/authority/README.md": (
        "Source authority is active.",
        "complete two-repository handshake",
    ),
    "provenance/pulp-observatory/README.md": (
        "The active cursor's",
        "Authority is active",
    ),
    "packages/vellum-ui/README.md": (
        "The installed web lane runs",
        "IME composition",
    ),
    "docs/cli/install-artifact.md": (
        "published, immutable `v0.1.7`",
        "Private tagged-release installation",
    ),
}
ACTIVE_FORBIDDEN = (
    "authority handoff is prepared, not activated",
    "No authority-transfer handshake is active",
    "Authority is not active",
    "no source authority is active",
    "Browser JavaScript driving the shared Wasm core remains a\nplanned validation lane",
    "The renderer is intentionally not yet connected",
    "not installed in this extraction milestone",
)


class VerificationError(ValueError):
    pass


def load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"{path} must contain an object")
    return value


def active_coordinates(root: Path) -> tuple[str, str]:
    lock = load_object(root / "provenance/pulp-observatory/provenance.lock")
    extraction = load_object(root / "provenance/pulp-extraction.json")
    authority = extraction.get("authority")
    if (
        lock.get("state") != "active"
        or extraction.get("status") != "active"
        or not isinstance(authority, dict)
        or authority.get("state") != "active"
    ):
        raise VerificationError("current provenance artifacts are not uniformly active")
    record = lock.get("vellum_authority_record_commit")
    pulp = lock.get("pulp_activation_commit")
    if (
        not isinstance(record, str)
        or not SHA.fullmatch(record)
        or not isinstance(pulp, str)
        or not SHA.fullmatch(pulp)
        or authority.get("authority_record_commit") != record
        or authority.get("pulp_activation_commit") != pulp
    ):
        raise VerificationError("active provenance coordinates disagree")
    ownership = (root / "provenance/ownership-map.yaml").read_text(encoding="utf-8")
    for field, value in (
        ("vellum_authority_record_commit", record),
        ("pulp_activation_commit", pulp),
    ):
        if re.search(rf"^  {field}: {value}$", ownership, re.MULTILINE) is None:
            raise VerificationError(f"ownership map disagrees at {field}")
    return record, pulp


def verify_documents(root: Path) -> None:
    record, pulp = active_coordinates(root)
    documents: dict[str, str] = {}
    for relative in ACTIVE_REQUIRED:
        try:
            documents[relative] = (root / relative).read_text(encoding="utf-8")
        except OSError as error:
            raise VerificationError(f"cannot read {relative}: {error}") from error
    documents["provenance/ownership-map.yaml"] = (
        root / "provenance/ownership-map.yaml"
    ).read_text(encoding="utf-8")
    documents["docs/architecture/gpu-boundary.md"] = (
        root / "docs/architecture/gpu-boundary.md"
    ).read_text(encoding="utf-8")
    documents["docs/cli/contract.md"] = (
        root / "docs/cli/contract.md"
    ).read_text(encoding="utf-8")
    combined = " ".join("\n".join(documents.values()).split())
    for phrase in ACTIVE_FORBIDDEN:
        if " ".join(phrase.split()) in combined:
            raise VerificationError(f"active documentation retains stale claim: {phrase!r}")
    for relative, phrases in ACTIVE_REQUIRED.items():
        normalized = " ".join(documents[relative].split())
        for phrase in phrases:
            if " ".join(phrase.split()) not in normalized:
                raise VerificationError(
                    f"{relative} is missing active-state claim: {phrase!r}"
                )
    for relative in (
        "docs/ownership.md",
        "provenance/authority/README.md",
    ):
        if record not in documents[relative] or pulp not in documents[relative]:
            raise VerificationError(f"{relative} is missing active coordinates")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        verify_documents(args.root.resolve())
    except (OSError, VerificationError) as error:
        print(f"current-state-docs: FAIL: {error}", file=sys.stderr)
        return 1
    print("current-state-docs: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
