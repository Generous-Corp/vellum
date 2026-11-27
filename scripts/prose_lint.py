#!/usr/bin/env python3
"""Flag em dashes, workflow breadcrumbs and padding added to Vellum's prose.

The rules are the crisp-writing checker's, vendored unchanged as
crisp_writing_check.py so Vellum and the skill share one rule set. This wrapper
checks only lines added since the base, reads comment text only in code files
(the checker's --comments-only), and skips paths that hold history or another
project's bytes.

Usage: prose_lint.py [--base <ref>]     default base: origin/main
Add "crisp-writing: ok" and a reason to a line to accept it.
Exit 0 when clean, 1 on findings, 2 when the diff cannot be read.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
CHECKER = HERE / "crisp_writing_check.py"
# SHA-256 of the vendored checker. Update it only together with a fresh copy of
# the skill's scripts/check.py, so the two rule sets cannot drift apart quietly.
CHECKER_SHA256 = "e649f8a05377de6035943e6201e310c074582ad97eea17f80314eca78b3d3200"

# History-bearing records, the byte-identical Pulp export, generated data, and
# this lint's own files, which quote the patterns they test. The Three.js
# payload files are another project's unmodified bytes.
EXEMPT_PREFIXES = ("mirror/", "framework/", "provenance/", "decisions/",
                   "assets/threejs/build/", "assets/threejs/examples/")
EXEMPT_FILES = {
    "NOTICE.md",
    "scripts/crisp_writing_check.py",
    "scripts/prose_lint.py",
    "scripts/test_prose_lint.py",
}
EXEMPT_SUFFIXES = (".json", ".patch")
# Files moved from Pulp verbatim: each is listed in a manifest here, and
# apps/design-tool/verify_verbatim_move.py holds it to Pulp's bytes, so its
# prose is Pulp's and changes only as a later edit that also drops it from the
# manifest.
VERBATIM_MOVE_MANIFESTS = "apps/design-tool/moves"


def load_checker():
    spec = importlib.util.spec_from_file_location("crisp_writing_check", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CHECKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COMMENTS_ONLY = True
    return module


def verbatim_moved_files(root: Path = Path(".")) -> set[str]:
    moved: set[str] = set()
    for manifest in sorted((root / VERBATIM_MOVE_MANIFESTS).glob("*.json")):
        try:
            files = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        moved.update(item["path"] for item in files if isinstance(item, dict) and "path" in item)
    return moved


def exempt(path: str, moved: frozenset[str] | set[str] = frozenset()) -> bool:
    return (path.startswith(EXEMPT_PREFIXES) or path in EXEMPT_FILES
            or path.endswith(EXEMPT_SUFFIXES) or path in moved)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)

    checker = load_checker()
    try:
        findings = checker.scan_diff(args.base)
    except subprocess.CalledProcessError as error:
        print(f"prose_lint: cannot diff against {args.base}: {error.stderr.strip()}", file=sys.stderr)
        return 2
    moved = verbatim_moved_files()
    findings = [line for line in findings if not exempt(line.split(":", 1)[0], moved)]
    if findings:
        print("\n".join(findings))
        print(f"prose_lint: {len(findings)} finding(s) in lines added since {args.base}. "
              "Reword them, or add \"crisp-writing: ok\" and a reason to the line.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
