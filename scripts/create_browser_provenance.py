#!/usr/bin/env python3
"""Record the exact browser executable used by a Vellum web proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))
from vellum_browser import ACTION_REF_RE, SCHEMA, browser_version, sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--requested-version", required=True)
    parser.add_argument("--source-action", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    browser = args.browser.resolve()
    if not browser.is_file() or not browser.stat().st_mode & 0o111:
        parser.error("--browser must be an executable file")
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", args.requested_version) is None:
        parser.error("--requested-version must be a four-part version")
    if ACTION_REF_RE.fullmatch(args.source_action) is None:
        parser.error("--source-action must identify an action at an immutable 40-hex ref")
    version = browser_version(browser)
    if version != args.requested_version:
        parser.error(f"browser reported {version}, expected {args.requested_version}")
    record = {
        "schema": SCHEMA,
        "product": "Google Chrome",
        "version": version,
        "requested_version": args.requested_version,
        "executable_sha256": sha256(browser),
        "source_action": args.source_action,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

