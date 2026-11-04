#!/usr/bin/env python3
"""Decide whether a change can affect the Locked macOS GPU proof.

    classify_gpu_proof.py --files0-from PATH   # NUL-separated changed paths
    classify_gpu_proof.py PATH...

Prints `required` or `not-required` and exits 0; exit 2 means it was invoked
wrongly, and a caller treats anything but `not-required` as `required`.

The proof is required unless every changed path is observatory evidence that
nothing the proof runs reads: the cursor, the generated reports, and event
records under provenance/pulp-observatory/. The receiver's coalesced evidence
pull requests change only these. The proof reads provenance.lock from that
directory, so the allowlist names files, not the directory.

The workflow runs this file as it exists in the merged tree's first parent
(the base), never the pull request's own copy, so a pull request cannot widen
the allowlist or rewrite the rules that judge it. Any change to this file or to
a workflow is required by rule, and an empty change list is required too.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Observatory evidence the proof never reads (traced through every script and
# test gpu-macos.yml runs; the SDK payload carries no repository provenance).
EVIDENCE = (
    re.compile(r"provenance/pulp-observatory/cursor\.json"),
    re.compile(r"provenance/pulp-observatory/reports/[^/]+"),
    re.compile(r"provenance/pulp-observatory/events/[^/]+\.json"),
)
# Changes that always need the proof, whatever the allowlist says.
ALWAYS = (
    re.compile(r"scripts/classify_gpu_proof\.py"),
    re.compile(r"scripts/test_classify_gpu_proof\.py"),
    re.compile(r"\.github/.*"),
)


def classify(paths: list[str]) -> tuple[bool, str]:
    """Return (required, reason)."""
    if not paths:
        return True, "no changed paths were supplied"
    for path in paths:
        if not path or path.startswith("/") or ".." in path.split("/"):
            return True, f"unexpected path {path!r}"
        if any(rule.fullmatch(path) for rule in ALWAYS):
            return True, f"{path} always needs the proof"
        if not any(rule.fullmatch(path) for rule in EVIDENCE):
            return True, f"{path} can affect the proof"
    return False, f"all {len(paths)} changed paths are observatory evidence"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files0-from", type=Path)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    paths = list(args.paths)
    if args.files0_from is not None:
        paths += [item for item in args.files0_from.read_bytes().decode("utf-8").split("\0") if item]
    required, reason = classify(paths)
    print(f"classify: {reason}", file=sys.stderr)
    print("required" if required else "not-required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
