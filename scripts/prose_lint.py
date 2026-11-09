#!/usr/bin/env python3
"""Flag em dashes, workflow breadcrumbs and padding added to Vellum's prose.

The rules are the crisp-writing checker's, vendored unchanged as
crisp_writing_check.py so Vellum and the skill share one rule set. This wrapper
checks only lines added since the base, reads comment text only in code files,
and skips paths that hold history or another project's bytes.

Usage: prose_lint.py [--base <ref>]     default base: origin/main
Add "crisp-writing: ok" and a reason to a line to accept it.
Exit 0 when clean, 1 on findings, 2 when the diff cannot be read.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import subprocess
import sys


HERE = Path(__file__).resolve().parent
CHECKER = HERE / "crisp_writing_check.py"
# SHA-256 of the vendored checker. Update it only together with a fresh copy of
# the skill's scripts/check.py, so the two rule sets cannot drift apart quietly.
CHECKER_SHA256 = "157fb14f91f93beda6549c1cdf07d7ce9f4386ddb1f131d9dd5be11bdb34671a"

# History-bearing records, the byte-identical Pulp export, generated data, and
# this lint's own files, which quote the patterns they test.
EXEMPT_PREFIXES = ("mirror/", "framework/", "provenance/", "decisions/")
EXEMPT_FILES = {
    "NOTICE.md",
    "scripts/crisp_writing_check.py",
    "scripts/prose_lint.py",
    "scripts/test_prose_lint.py",
}
EXEMPT_SUFFIXES = (".json", ".patch")

# Vellum's own pattern on top of the shared set: dated rulings that name a role.
EXTRA_CHECKS = [
    ("breadcrumb", re.compile(
        r"\b(?:team[- ]lead|owner) (?:ruling|sign-?off)\b[^.\n]{0,10}\b20\d\d-\d\d-\d\d", re.I)),
]


def load_checker():
    spec = importlib.util.spec_from_file_location("crisp_writing_check", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CHECKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COMMENTS_ONLY = True
    module.CHECKS = [*module.CHECKS, *EXTRA_CHECKS]
    prose_of = module.prose_of

    def code_aware_prose_of(path: str, text: str) -> str:
        # CMakeLists.txt ends in .txt, which the checker reads as prose.
        if Path(path).name == "CMakeLists.txt":
            match = module.COMMENT_RX.search(text)
            return match.group(1) if match else ""
        return prose_of(path, text)

    module.prose_of = code_aware_prose_of
    return module


def exempt(path: str) -> bool:
    return (path.startswith(EXEMPT_PREFIXES) or path in EXEMPT_FILES
            or path.endswith(EXEMPT_SUFFIXES))


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
    findings = [line for line in findings if not exempt(line.split(":", 1)[0])]
    if findings:
        print("\n".join(findings))
        print(f"prose_lint: {len(findings)} finding(s) in lines added since {args.base}. "
              "Reword them, or add \"crisp-writing: ok\" and a reason to the line.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
