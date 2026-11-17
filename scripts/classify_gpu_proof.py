#!/usr/bin/env python3
"""Decide whether a change can affect the Locked macOS GPU proof.

    classify_gpu_proof.py --files0-from PATH   # NUL-separated changed paths
    classify_gpu_proof.py PATH...

Prints `required` or `not-required` and exits 0; exit 2 means it was invoked
wrongly, and a caller treats anything but `not-required` as `required`.

With --early-proof it instead prints `early` or `cheap` for a pull request:
`early` when any changed path can only be proved on a GPU (GPU_RELEVANT) or
the list is empty or malformed, and `cheap` otherwise. A base without this flag exits 2, which the workflow reads as
`early`. The merge group runs the full proof whatever this says.

The proof is required unless every changed path is observatory evidence that
nothing the proof runs reads: the cursor, the generated reports, and event
records under provenance/pulp-observatory/. The receiver's coalesced evidence
pull requests change only these. The proof reads provenance.lock from that
directory, so the allowlist names files, not the directory.

The workflow runs this file as it exists in the base (a pull request's merge
ref's first parent; a merge-queue entry's `merge_group.base_sha`, so every
entry of a batch is judged, not only the last), never the change's own copy,
so a change cannot widen the allowlist or rewrite the rules that judge it. Any change to this file or to
a workflow is required by rule, and an empty change list is required too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Observatory evidence the proof never reads (traced through every script and
# test gpu-macos.yml runs; the SDK payload carries no repository provenance).
EVIDENCE = (
    re.compile(r"provenance/pulp-observatory/cursor\.json"),
    re.compile(r"provenance/pulp-observatory/reports/[^/]+"),
    re.compile(r"provenance/pulp-observatory/events/[^/]+\.yaml"),
)
# Changes that always need the proof, whatever the allowlist says.
ALWAYS = (
    re.compile(r"scripts/classify_gpu_proof\.py"),
    re.compile(r"scripts/test_classify_gpu_proof\.py"),
    re.compile(r"\.github/.*"),
)


# The rules that judge a change are proved by the full proof early too.
ALWAYS_EARLY = (
    re.compile(r"scripts/classify_gpu_proof\.py"),
    re.compile(r"scripts/test_classify_gpu_proof\.py"),
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


# Paths whose change can only be caught by a GPU adapter: the Skia/Dawn
# graphics backend and its tests, the pinned renderer runtime, the apps that
# open a window or capture a frame, and the workflow that runs the proof. A pull request
# touching any of them runs the full proof now; anything else gets the cheap
# lane and the merge group runs the full proof before main moves.
GPU_RELEVANT = (
    re.compile(r"graphics/.*"),
    re.compile(r"gpu-runtime/.*"),
    re.compile(r"apps/.*"),
    re.compile(r"packages/vellum-swift/.*"),
    re.compile(r".*\.(metal|wgsl)"),
    re.compile(r"provenance/third-party-lock\.json"),
    re.compile(r"\.github/workflows/gpu-macos\.yml"),
    re.compile(r"scripts/(run_parity_proofs|build_web_payload|select_reusable_[a-z_]*proof|classify_gpu_proof)\.py"),
)


def has_pr_fast_tests(classes_text: str | None) -> bool:
    """Whether the base's test class manifest lists at least one pr-fast test.
    The cheap lane runs `ctest -L pr-fast`, so without such a test it proves
    nothing; an absent, unreadable or empty manifest says no."""
    try:
        document = json.loads(classes_text or "")
        names = document["classes"]["pr-fast"]
        return (document["schema"] == "vellum.ctest-classes.v1" and isinstance(names, list)
                and any(isinstance(name, str) and name for name in names))
    except (ValueError, KeyError, TypeError):
        return False


def early_proof(paths: list[str], classes_text: str | None) -> tuple[bool, str]:
    """Return (early, reason): whether a pull request should run the full GPU
    proof now instead of the cheap lane, leaving it to the merge group.

    Fails closed on an empty list or a malformed path, on any path in
    GPU_RELEVANT or ALWAYS, and when the base has no test class manifest with a
    pr-fast test (`classes_text` is the base's scripts/ctest_classes.json).
    """
    if not has_pr_fast_tests(classes_text):
        return True, "the base has no test class manifest with a pr-fast test"
    if not paths:
        return True, "no changed paths were supplied"
    for path in paths:
        if not path or path.startswith("/") or ".." in path.split("/"):
            return True, f"unexpected path {path!r}"
        if any(rule.fullmatch(path) for rule in ALWAYS_EARLY + GPU_RELEVANT):
            return True, f"{path} can only be proved on a GPU"
    return False, f"none of the {len(paths)} changed paths needs a GPU adapter"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files0-from", type=Path)
    parser.add_argument(
        "--early-proof",
        action="store_true",
        help="print `early` or `cheap`: whether a pull request should run the full proof now",
    )
    parser.add_argument(
        "--classes-file", type=Path,
        help="the base's scripts/ctest_classes.json, read by --early-proof; a missing or empty file means no cheap lane",
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    paths = list(args.paths)
    if args.files0_from is not None:
        paths += [item for item in args.files0_from.read_bytes().decode("utf-8").split("\0") if item]
    if args.early_proof:
        classes_text = None
        if args.classes_file is not None:
            try:
                classes_text = args.classes_file.read_text(encoding="utf-8")
            except OSError:
                classes_text = None
        early, reason = early_proof(paths, classes_text)
        print(f"classify: {reason}", file=sys.stderr)
        print("early" if early else "cheap")
        return 0
    required, reason = classify(paths)
    print(f"classify: {reason}", file=sys.stderr)
    print("required" if required else "not-required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
