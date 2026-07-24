#!/usr/bin/env python3
"""Enforce the normative README structure and claim boundary."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
EXPECTED_BANNER = (
    "**Status: private, experimental, 0.x.** APIs, schemas, CLI names, and the "
    "working name itself change without notice. Exact-pin SDK compatibility "
    "only. Not accepting external users."
)
HEADINGS = [
    "What this is",
    "What this is not",
    "Quick start",
    "Requirements",
    "What was extracted and what stayed in Pulp",
    "Anatomy of a generated application",
    "Capability and platform status",
    "Commands",
    "Ownership, provenance, and attribution",
    "Versioning and support status",
    "License",
]
EXTRACTED_TERMS = {
    "retained scene model", "rendering", "layout", "design import", "scripting",
    "app shell", "testkit", "CLI",
}
PULP_TERMS = {
    "audio", "MIDI", "DSP", "plug-in formats", "plug-in hosting",
    "audio widgets", "audio DesignIR extensions",
}
FORBIDDEN_CLAIMS = [
    re.compile(r"\b(?:Vellum|the framework) imports arbitrary (?:web )?apps?\b", re.I),
    re.compile(r"\b(?:Vellum|the framework) supports arbitrary (?:websites?|sites?)\b", re.I),
    re.compile(r"\bfull DOM(?: and|/)CSS compatibility\b", re.I),
    re.compile(r"\b(?:the )?same renderer (?:on|across|everywhere)\b", re.I),
    re.compile(r"\b(?:supports|runs) (?:every|any) npm package\b", re.I),
    re.compile(r"\b(?:smaller|faster|lower memory) than (?:Electron|Tauri|Flutter|Qt|React Native)\b", re.I),
]


class Error(ValueError):
    pass


def section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise Error(f"missing section: {heading}")
    return match.group(1)


def validate(text: str) -> None:
    if not text.startswith("# Vellum\n\n> "):
        raise Error("README must start with the title and status banner")
    banner_lines = []
    for line in text.splitlines()[2:]:
        if not line.startswith(">"):
            break
        banner_lines.append(line.removeprefix(">").strip())
    if " ".join(banner_lines) != EXPECTED_BANNER:
        raise Error("status banner differs from the required private 0.x text")
    actual = re.findall(r"^## (.+?)\s*$", text, flags=re.MULTILINE)
    if actual != HEADINGS:
        raise Error(f"level-two section order differs: expected={HEADINGS} actual={actual}")
    boundary = section(text, "What was extracted and what stayed in Pulp")
    normalized_boundary = " ".join(boundary.split())
    for term in sorted(EXTRACTED_TERMS | PULP_TERMS):
        if term not in normalized_boundary:
            raise Error(f"boundary section is missing required term: {term}")
    ownership = section(text, "Ownership, provenance, and attribution")
    for path in (
        "docs/ownership.md",
        "provenance/pulp-extraction.json",
        "provenance/ownership-map.yaml",
        "NOTICE.md",
    ):
        if path not in ownership:
            raise Error(f"ownership section is missing required link: {path}")
    for pattern in FORBIDDEN_CLAIMS:
        match = pattern.search(text)
        if match is not None:
            raise Error(f"forbidden claim: {match.group(0)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)
    try:
        validate(args.readme.read_text(encoding="utf-8"))
        print(f"readme-lint: OK ({args.readme})")
        return 0
    except (OSError, Error) as error:
        print(f"readme-lint: FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
