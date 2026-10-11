#!/usr/bin/env python3
"""Fail closed when a provenance target does not contain the requested base."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


class AncestryError(RuntimeError):
    pass


def verify(repo: Path, base: str, target: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, target],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or "base is not an ancestor of target"
    raise AncestryError(f"provenance target does not contain base {base}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        verify(args.repo, args.base, args.target)
    except AncestryError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
