#!/usr/bin/env python3
"""Checks that native Draco decoding is off unless asked for, and wired once when on.

    check_draco.py source <repo>
    check_draco.py build <repo> <build-dir> --expect off|on

`source` checks what a default configure would do: the option defaults to OFF,
the module returns before it fetches anything when it is off, and the pinned
Draco release, digest and license agree in the module, DEPENDENCIES.md,
provenance/third-party-lock.json and NOTICE.md.

`build` checks a built tree against the option it was configured with. Off: no
built package library defines a Draco symbol. On: the provider defines Draco's
decoder, and no mirror unit library defines a second copy, so the process has
one decoder.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MODULE = "cmake/VellumDraco.cmake"
OPTION = "VELLUM_ENABLE_DRACO"
PROVIDER_GLOB = "libvellum-skia-dawn-provider*.dylib"
UNIT_GLOB = "libvellum-mirror-*.dylib"


def pins(repo: Path) -> dict[str, str]:
    module = (repo / MODULE).read_text(encoding="utf-8")
    version = re.search(r'set\(VELLUM_DRACO_VERSION\s+"([^"]+)"', module)
    digest = re.search(r'set\(VELLUM_DRACO_SHA256\s+"([0-9a-f]{64})"', module)
    if not version or not digest:
        raise ValueError(f"{MODULE} does not pin VELLUM_DRACO_VERSION and VELLUM_DRACO_SHA256")
    return {"version": version.group(1), "sha256": digest.group(1)}


def source_errors(repo: Path) -> list[str]:
    errors: list[str] = []
    root = (repo / "CMakeLists.txt").read_text(encoding="utf-8")
    option = re.search(rf"option\({OPTION}\s+\"[^\"]*\"\s+(\w+)\)", root)
    if not option:
        errors.append(f"CMakeLists.txt does not declare option({OPTION} ...)")
    elif option.group(1) != "OFF":
        errors.append(f"{OPTION} defaults to {option.group(1)}; it must default to OFF")
    if "include(cmake/VellumDraco.cmake)" not in root:
        errors.append("CMakeLists.txt does not include cmake/VellumDraco.cmake")

    module = (repo / MODULE).read_text(encoding="utf-8")
    guard = re.search(rf"if\(NOT {OPTION}\)\s*return\(\)\s*endif\(\)", module)
    fetch = module.find("FetchContent_Declare")
    first_target = module.find("add_library(")
    if not guard:
        errors.append(f"{MODULE} does not return early when {OPTION} is off")
    else:
        for what, at in (("FetchContent", fetch), ("add_library", first_target)):
            if 0 <= at < guard.start():
                errors.append(f"{MODULE} uses {what} before its early return")

    try:
        pin = pins(repo)
    except ValueError as error:
        return errors + [str(error)]
    dependencies = (repo / "DEPENDENCIES.md").read_text(encoding="utf-8")
    row = next((line for line in dependencies.splitlines() if line.startswith("| Draco ")), "")
    if pin["sha256"] not in row or pin["version"] not in row:
        errors.append(f"the Draco row of DEPENDENCIES.md does not carry {pin['version']} "
                      f"and SHA-256 {pin['sha256']}")
    lock = json.loads((repo / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
    entries = [d for d in lock["dependencies"] if d.get("name") == "Draco"]
    if len(entries) != 1:
        errors.append("provenance/third-party-lock.json must hold exactly one Draco entry")
    elif entries[0].get("sha256") != pin["sha256"] or entries[0].get("version") != pin["version"]:
        errors.append("the Draco entry of provenance/third-party-lock.json differs from the module's pin")
    elif entries[0].get("license") != "Apache-2.0":
        errors.append("the Draco lock entry must name Apache-2.0")
    notice = (repo / "NOTICE.md").read_text(encoding="utf-8")
    if not re.search(r"^## .*Draco", notice, re.MULTILINE):
        errors.append("NOTICE.md has no Draco section")
    return errors


class InspectionError(RuntimeError):
    """nm could not inspect an image; that is a failure, never an empty list."""


def defined_symbols(library: Path) -> list[str]:
    if not library.exists():
        raise InspectionError(f"{library} does not exist, so it cannot be inspected")
    result = subprocess.run(["nm", "-U", str(library)], capture_output=True, text=True)
    if result.returncode != 0:
        raise InspectionError(f"nm -U {library} failed ({result.returncode}): {result.stderr.strip()}")
    symbols = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and len(parts[0]) >= 8 and parts[1] not in ("U", "-"):
            symbols.append(parts[2])
    return symbols


def is_draco_symbol(symbol: str) -> bool:
    """A symbol in Draco's own `draco::` namespace (Itanium mangling: N5draco)."""
    return "5draco" in symbol


def build_errors(build: Path, expect: str) -> list[str]:
    providers = sorted(build.glob(PROVIDER_GLOB), key=lambda path: path.stat().st_mtime)
    if not providers:
        return [f"no provider library in {build}"]
    # A reused build tree can hold providers of earlier identities; the newest
    # is the one the build just produced, and its units share its identity.
    provider = providers[-1]
    identity = provider.name[len("libvellum-skia-dawn-provider"):]
    units = sorted(build.glob(UNIT_GLOB.replace("*.dylib", "*" + identity)))
    errors: list[str] = []
    in_provider = sum(is_draco_symbol(s) for s in defined_symbols(provider))
    in_units = {u.name: sum(is_draco_symbol(s) for s in defined_symbols(u)) for u in units}
    if not units:
        errors.append(f"no mirror unit library of identity {identity} in {build}")
    if expect == "off":
        if in_provider:
            errors.append(f"{provider.name} defines {in_provider} Draco symbols with {OPTION} off")
        for name, count in in_units.items():
            if count:
                errors.append(f"{name} defines {count} Draco symbols with {OPTION} off")
    else:
        if not in_provider:
            errors.append(f"{provider.name} defines no Draco symbol with {OPTION} on")
        for name, count in in_units.items():
            if count:
                errors.append(f"{name} defines {count} Draco symbols; the decoder must live only in the provider")
    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    source = sub.add_parser("source")
    source.add_argument("repo", type=Path)
    build = sub.add_parser("build")
    build.add_argument("repo", type=Path)
    build.add_argument("build_dir", type=Path)
    build.add_argument("--expect", choices=("off", "on"), required=True)
    args = parser.parse_args(argv)
    try:
        errors = source_errors(args.repo) if args.mode == "source" else build_errors(args.build_dir, args.expect)
    except (InspectionError, OSError, ValueError, KeyError) as error:
        print(f"check_draco: cannot inspect: {error}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"check_draco: {error}", file=sys.stderr)
    if not errors:
        print(f"check_draco: {args.mode} ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
