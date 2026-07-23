#!/usr/bin/env python3
"""Fail-closed verification for a Vellum SDK archive and its payload manifest.

`install_core.py` is the self-contained canonical artifact-contract authority
used by both release installation and this standalone verification surface.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from install_core import (  # noqa: E402
    InstallError as CanonicalInstallError,
    payload_contamination_findings,
    should_scan_payload_content,
    verify_archive_contract,
)

FRAMEWORK_VERSION = "0.1.6"
CLI_VERSION = FRAMEWORK_VERSION


class VerificationError(RuntimeError):
    pass


def verify(archive: Path, checksums: Path) -> dict[str, object]:
    try:
        result = verify_archive_contract(archive, checksums)
    except CanonicalInstallError as error:
        raise VerificationError(str(error)) from error
    if result["framework_version"] != FRAMEWORK_VERSION:
        raise VerificationError(
            "SDK framework version does not match this immutable verifier"
        )
    if result["cli_version"] != CLI_VERSION:
        raise VerificationError(
            "SDK CLI version does not match this immutable verifier"
        )
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify(args.archive, args.checksums)
    except (OSError, UnicodeError, json.JSONDecodeError, VerificationError) as error:
        print(f"vellum-sdk-verify: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Verified SHA-256: {result['sha256']}")
        print(f"Verified {result['file_count']} SDK payload files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
