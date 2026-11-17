#!/usr/bin/env python3
"""Checks that native Perfetto tracing is off unless asked for, and wired once when on.

    check_tracing.py source <repo>
    check_tracing.py build <repo> <build-dir> --expect off|on

`source` checks what a default configure would do: the option defaults to OFF,
the module returns before it fetches anything when it is off, and the pinned
Perfetto release, digest and licence agree in the module, DEPENDENCIES.md,
provenance/third-party-lock.json and NOTICE.md.

`build` checks a configured and built tree against the option it was configured
with. Off: no Perfetto source or object was fetched or built, nothing compiles
with PULP_TRACING_ENABLED, and no built library holds a Perfetto symbol. On: one
Perfetto source was fetched and exactly one built library defines the Perfetto
runtime, so the process has one trace session.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MODULE = "cmake/VellumTracing.cmake"
OPTION = "VELLUM_ENABLE_TRACING"
RUNTIME_SYMBOL = "TracingMuxer"
# The package libraries the build tree itself links, at its top level; an SDK a
# test installs under the tree holds copies of them.
LIBRARY_GLOBS = ("libvellum-mirror-*.dylib", "libvellum-skia-dawn-provider*.dylib", "libvellum-gpu*.dylib")


def pins(repo: Path) -> dict[str, str]:
    module = (repo / MODULE).read_text(encoding="utf-8")
    version = re.search(r'set\(VELLUM_PERFETTO_VERSION\s+"([^"]+)"', module)
    digest = re.search(r'set\(VELLUM_PERFETTO_SHA256\s+"([0-9a-f]{64})"', module)
    if not version or not digest:
        raise ValueError(f"{MODULE} does not pin VELLUM_PERFETTO_VERSION and VELLUM_PERFETTO_SHA256")
    return {"version": version.group(1), "sha256": digest.group(1)}


def source_errors(repo: Path) -> list[str]:
    errors: list[str] = []
    root = (repo / "CMakeLists.txt").read_text(encoding="utf-8")
    option = re.search(rf"option\({OPTION}\s+\"[^\"]*\"\s+(\w+)\)", root)
    if not option:
        errors.append(f"CMakeLists.txt does not declare option({OPTION} ...)")
    elif option.group(1) != "OFF":
        errors.append(f"{OPTION} defaults to {option.group(1)}; it must default to OFF")
    if "include(cmake/VellumTracing.cmake)" not in root:
        errors.append("CMakeLists.txt does not include cmake/VellumTracing.cmake")

    module = (repo / MODULE).read_text(encoding="utf-8")
    guard = re.search(rf"if\(NOT {OPTION}\)\s*return\(\)\s*endif\(\)", module)
    fetch = module.find("FetchContent")
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
    row = next((line for line in dependencies.splitlines() if line.startswith("| Perfetto ")), "")
    if pin["sha256"] not in row or pin["version"] not in row:
        errors.append(f"the Perfetto row of DEPENDENCIES.md does not carry {pin['version']} "
                      f"and SHA-256 {pin['sha256']}")
    lock = json.loads((repo / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
    entries = [d for d in lock["dependencies"] if d.get("name") == "Perfetto"]
    if len(entries) != 1:
        errors.append("provenance/third-party-lock.json must hold exactly one Perfetto entry")
    elif entries[0].get("sha256") != pin["sha256"] or entries[0].get("version") != pin["version"]:
        errors.append("the Perfetto entry of provenance/third-party-lock.json differs from the module's pin")
    elif entries[0].get("license") != "Apache-2.0":
        errors.append("the Perfetto lock entry must name Apache-2.0")
    notice = (repo / "NOTICE.md").read_text(encoding="utf-8")
    if not re.search(r"^## .*Perfetto", notice, re.MULTILINE):
        errors.append("NOTICE.md has no Perfetto section")
    return errors


class InspectionError(RuntimeError):
    """nm could not inspect an object; that is a failure, never an empty list."""


def run_nm(flags: str, path: Path) -> list[str]:
    if not path.exists():
        raise InspectionError(f"{path} does not exist, so it cannot be inspected")
    result = subprocess.run(["nm", flags, str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise InspectionError(f"nm {flags} {path} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.splitlines()


def defined_symbols(library: Path) -> list[str]:
    """Every symbol the image defines, local and private ones included.

    `nm -U` lists defined symbols without the external-only filter of `-g`, so
    a runtime hidden inside an image (local or private-extern) still shows.
    Lines are `<value> <type> <name>`; undefined and debug entries are dropped.
    """
    symbols = []
    for line in run_nm("-U", library):
        parts = line.split(None, 2)
        if len(parts) == 3 and len(parts[0]) >= 8 and parts[1] not in ("U", "-"):
            symbols.append(parts[2])
    return symbols


def undefined_symbols(path: Path) -> list[str]:
    return [line.split()[-1] for line in run_nm("-u", path) if line.strip()]


def framework_compiles(ninja_text: str) -> list[tuple[str, str]]:
    """(source, DEFINES) of every compile statement of a framework/core source."""
    found = []
    for block in ninja_text.split("\nbuild ")[1:]:
        head, _, body = block.partition("\n\n")
        first = head.splitlines()[0]
        statement = re.match(r"\S+\.o:\s+\S*COMPILER__\S+\s+(\S*/framework/core/\S+)", first)
        if not statement:
            continue
        defines = re.search(r"^\s*DEFINES = (.*)$", head, re.MULTILINE)
        found.append((statement.group(1), defines.group(1) if defines else ""))
    return found


def adapter_errors(ninja_text: str, build: Path) -> list[str]:
    """The framework's trace adapter must not depend on the renderer."""
    for block in ninja_text.split("\nbuild ")[1:]:
        head = block.partition("\n\n")[0]
        statement = re.match(r"(\S+\.o):\s+\S*COMPILER__\S+\s+\S*/frame_trace_adapter\.cpp", head.splitlines()[0])
        if not statement:
            continue
        includes = re.search(r"^\s*INCLUDES = (.*)$", head, re.MULTILINE)
        errors = []
        if includes and "graphics/include" in includes.group(1):
            errors.append("the framework trace adapter compiles with the renderer's include directory")
        obj = build / statement.group(1)
        try:
            if any("graphics" in symbol for symbol in undefined_symbols(obj)):
                errors.append("the framework trace adapter object references renderer symbols")
        except InspectionError as error:
            errors.append(str(error))
        return errors
    return ["build.ninja has no compile of frame_trace_adapter.cpp"]


def build_errors(repo: Path, build: Path, expect: str) -> list[str]:
    errors: list[str] = []
    cache = (build / "CMakeCache.txt").read_text(encoding="utf-8")
    configured = re.search(rf"^{OPTION}:BOOL=(\w+)", cache, re.MULTILINE)
    value = configured.group(1) if configured else "OFF"
    if (value == "ON") != (expect == "on"):
        errors.append(f"expected a tracing-{expect} build, but {OPTION}={value}")
        return errors

    if expect == "on":
        pin = pins(repo)
        for name, key in (("VELLUM_PERFETTO_VERSION", "version"), ("VELLUM_PERFETTO_SHA256", "sha256")):
            found = re.search(rf"^{name}:[A-Z]+=(.*)$", cache, re.MULTILINE)
            if not found or found.group(1) != pin[key]:
                errors.append(f"the configured {name} is {found.group(1) if found else 'unset'}, "
                              f"not the locked {pin[key]}; a build on another Perfetto is not the pinned one")

    ninja = build / "build.ninja"
    text = ninja.read_text(encoding="utf-8", errors="replace") if ninja.exists() else ""
    sources = sorted(path.name for path in (build / "_deps").glob("vellum_perfetto*"))
    libraries = sorted({p for pattern in LIBRARY_GLOBS for p in build.glob(pattern)
                        if "CMakeFiles" not in p.parts})
    try:
        defined = {library: defined_symbols(library) for library in libraries}
    except InspectionError as error:
        return errors + [str(error)]
    runtimes = [library.name for library, symbols in defined.items()
                if any(RUNTIME_SYMBOL in symbol for symbol in symbols)]
    perfetto_symbols = [library.name for library, symbols in defined.items()
                        if any("perfetto" in symbol.lower() for symbol in symbols)]

    if expect == "off":
        if sources:
            errors.append(f"a tracing-off build fetched Perfetto: {sources} (an earlier tracing-on configure of this directory leaves these behind; check a fresh build directory)")
        if re.search(r"vellum[-_]perfetto|vellum-tracing|/perfetto\.(?:cc|h)\b", text):
            errors.append("build.ninja names the Perfetto library or sources in a tracing-off build")
        leaking = sorted(source for source, defines in framework_compiles(text)
                         if "PULP_TRACING_ENABLED" in defines or "VELLUM_NATIVE_TRACING" in defines)
        if leaking:
            errors.append(f"a tracing-off build compiles {len(leaking)} framework sources with the trace "
                          f"switches, for example {leaking[0]}")
        if perfetto_symbols:
            errors.append(f"tracing-off libraries define Perfetto symbols: {perfetto_symbols}")
        # The frame trace bridge is compiled out without tracing, so no image
        # may reference the framework adapter's emitters.
        try:
            bridged = [library.name for library in libraries
                       if any(symbol.lstrip("_").startswith("vellum_native_trace_")
                              for symbol in undefined_symbols(library))]
        except InspectionError as error:
            return errors + [str(error)]
        if bridged:
            errors.append(f"tracing-off libraries reference vellum_native_trace_* emitters: {bridged}")
    else:
        if not (build / "_deps/vellum_perfetto-src/perfetto.cc").exists():
            errors.append("a tracing-on build has no fetched perfetto.cc")
        compiles = framework_compiles(text)
        if not compiles:
            errors.append("build.ninja holds no compile of a framework source to check")
        missing = sorted(source for source, defines in compiles if "-DPULP_TRACING_ENABLED=1" not in defines)
        if missing:
            errors.append(f"{len(missing)} of {len(compiles)} framework compiles lack "
                          f"PULP_TRACING_ENABLED=1, for example {missing[0]}")
        errors += adapter_errors(text, build)
        if not libraries:
            errors.append("no built mirror library was found to check; build the unit libraries first")
        elif len(runtimes) != 1:
            errors.append(f"exactly one library must define the Perfetto runtime, found {runtimes}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("source").add_argument("repo", type=Path)
    built = sub.add_parser("build")
    built.add_argument("repo", type=Path)
    built.add_argument("build", type=Path)
    built.add_argument("--expect", choices=("off", "on"), required=True)
    args = parser.parse_args()

    errors = (source_errors(args.repo) if args.mode == "source"
              else build_errors(args.repo, args.build, args.expect))
    for error in errors:
        print(f"FAIL {error}", file=sys.stderr)
    if not errors:
        print(f"PASS tracing {args.mode} checks")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
