#!/usr/bin/env python3
"""Validate every installed project-template layer without scaffolding."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
import vellum_cli  # noqa: E402


def validate(root: Path) -> dict[str, list[str]]:
    rendered: dict[str, list[str]] = {}
    for name in vellum_cli.PUBLIC_TEMPLATES:
        files = vellum_cli.template_files(root, name)
        rendered[name] = sorted(
            relative.removesuffix(".template") for relative in files
        )
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", type=Path, default=ROOT / "templates")
    args = parser.parse_args(argv)
    try:
        variants = validate(args.templates.resolve())
    except vellum_cli.CliFailure as error:
        print(f"template-guard: FAIL: {error}", file=sys.stderr)
        return error.exit_code
    print(
        "template-guard: OK ("
        + ", ".join(f"{name}={len(files)}" for name, files in variants.items())
        + ")"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
