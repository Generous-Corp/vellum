#!/usr/bin/env python3
"""Turn a verify_authority_expansion.py report into advisory PR annotations.

The advisory provenance step runs the verifier against live Pulp main with
--pulp-main-ref. This prints one GitHub warning annotation for each
"authoritative Pulp main" error in that report, and always exits 0: the
comparison informs a pull request, it never gates one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

TITLE = "Pulp main boundary (advisory)"
DRIFT_MARKER = "authoritative Pulp main"


def escape(text: str) -> str:
    """Escape a workflow-command message so it stays one annotation."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotations(report_path: Path, live_main: str) -> list[str]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"::warning title={TITLE}::{escape(f'no verifier report to read: {exc}')}"]
    errors = report.get("errors") if isinstance(report, dict) else None
    if not isinstance(errors, list):
        return [f"::warning title={TITLE}::the verifier report has no error list"]
    drift = [error for error in errors if isinstance(error, str) and DRIFT_MARKER in error]
    if not drift:
        return [f"Pulp main {live_main} matches the accepted boundary evidence"]
    lines = [
        f"::warning title={TITLE}::{escape(f'Pulp main {live_main[:12]}: {error}')}"
        for error in drift
    ]
    lines.append(
        f"::warning title={TITLE}::the next push to Vellum main will fail "
        "provenance-verify until the Pulp boundary evidence is refreshed"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("live_main")
    args = parser.parse_args(argv)
    for line in annotations(args.report, args.live_main):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
