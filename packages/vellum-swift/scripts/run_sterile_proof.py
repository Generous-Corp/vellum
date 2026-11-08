#!/usr/bin/env python3
"""Build the Swift host proof against an installed Vellum SDK prefix and run it."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKIP = 77
PREFIX_VARIABLE = "VELLUM_SWIFT_PROOF_SDK_PREFIX"
PKG_CONFIG_FILE = Path("lib/pkgconfig/vellum-host-c.pc")
PACKAGE_FILES = re.compile(
    r"\A(?:Package\.swift|README\.md|Fixtures/[\w.-]+\.js|"
    r"Sources/CVellumHostC/(?:module\.modulemap|shim\.h)|Sources/[\w]+/[\w]+\.swift|"
    r"scripts/[\w]+\.py)\Z"
)
SHIM_INCLUDE = "#include <vellum/c/vellum_host.h>"
ISOLATED_VARIABLES = (
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
    "LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH", "PKG_CONFIG_LIBDIR", "SDKROOT_OVERRIDE",
)


PINNED_XCODE = ("27.2", "27B52")
PINNED_SWIFT = "6.4.2"


class ProofError(Exception):
    pass


class Skip(Exception):
    pass


def locate_sdk(prefix_text: str) -> Path:
    if not prefix_text:
        raise Skip(
            f"no installed Vellum SDK prefix; set -D{PREFIX_VARIABLE}=<prefix> or "
            f"{PREFIX_VARIABLE} to a prefix made by scripts/build_sdk_artifact.py and "
            "scripts/install.sh"
        )
    prefix = Path(prefix_text).expanduser()
    if not prefix.is_dir():
        raise Skip(f"installed Vellum SDK prefix is not available: {prefix}")
    for candidate in (prefix, prefix / "sdk", prefix / "lib" / "vellum" / "sdk"):
        if (candidate / PKG_CONFIG_FILE).is_file():
            return candidate.resolve()
    raise ProofError(f"installed SDK at {prefix} does not ship {PKG_CONFIG_FILE}")


def check_package(package: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package).as_posix()
        if not path.is_file() or relative.startswith(".build/") or "/__pycache__/" in f"/{relative}":
            continue
        if not PACKAGE_FILES.match(relative):
            problems.append(f"unexpected file in the Swift package: {relative}")
    shim = package / "Sources/CVellumHostC/shim.h"
    if not shim.is_file() or shim.read_text(encoding="utf-8").strip() != SHIM_INCLUDE:
        problems.append("the C module shim must include only the installed vellum_host.h")
    return problems


def tool_version(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return " ".join((result.stdout or result.stderr).split())


def pinned_toolchain_problems(xcode: str, swift: str) -> list[str]:
    problems: list[str] = []
    match = re.search(r"Xcode (\S+) Build version (\S+)", xcode)
    if match is None or (match.group(1), match.group(2)) != PINNED_XCODE:
        problems.append(f"Xcode is not the pinned {PINNED_XCODE[0]} ({PINNED_XCODE[1]}): {xcode}")
    match = re.search(r"Apple Swift version (\S+)", swift)
    if match is None or match.group(1) != PINNED_SWIFT:
        problems.append(f"Swift is not the pinned {PINNED_SWIFT}: {swift}")
    return problems


def sterile_environment(sdk: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in ISOLATED_VARIABLES}
    environment["PKG_CONFIG_PATH"] = str(sdk / PKG_CONFIG_FILE.parent)
    environment["MTL_DEBUG_LAYER"] = "0"
    return environment


def foreign_source_reference(text: str, source_root: Path, allowed: list[Path]) -> bool:
    for path in sorted(allowed, key=lambda item: len(str(item)), reverse=True):
        text = text.replace(str(path), "<allowed>")
    return str(source_root) in text


def run(command: list[str], environment: dict[str, str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command, env=environment, cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ProofError(
            f"{' '.join(command)} failed ({result.returncode})\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout + result.stderr


def prove(sdk: Path, package: Path, work: Path, bundle: Path) -> dict[str, object]:
    swift = shutil.which("swift")
    if swift is None:
        raise Skip("the swift toolchain is not on PATH")
    swift_version = tool_version([swift, "--version"])
    xcode_version = tool_version(["xcodebuild", "-version"])
    problems = pinned_toolchain_problems(xcode_version, swift_version) + check_package(package)
    if problems:
        raise ProofError("\n".join(problems))
    source_root = package.resolve().parents[1]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    staged = work / "package"
    shutil.copytree(
        package, staged,
        ignore=shutil.ignore_patterns(".build", "__pycache__", "scripts", "Fixtures"),
    )
    scratch = work / "scratch"
    environment = sterile_environment(sdk)
    rpath = ["-Xlinker", "-rpath", "-Xlinker", str(sdk / "lib")]
    build = [swift, "build", "-c", "release", "--package-path", str(staged),
             "--scratch-path", str(scratch), "-v", *rpath]
    started = time.monotonic()
    log = run(build, environment)
    build_seconds = time.monotonic() - started
    started = time.monotonic()
    run(build, environment)
    incremental_seconds = time.monotonic() - started
    (work / "swift-build.log").write_text(log, encoding="utf-8")
    allowed = [work, sdk]
    if foreign_source_reference(log, source_root, allowed):
        raise ProofError(f"the Swift build referenced the Vellum source tree {source_root}")
    if str(sdk) not in log:
        raise ProofError("the Swift build did not use the installed SDK prefix")

    binary_dir = Path(run(
        [swift, "build", "-c", "release", "--package-path", str(staged),
         "--scratch-path", str(scratch), "--show-bin-path"], environment).strip().splitlines()[-1])
    binary = binary_dir / "vellum-swift-proof"
    linked = run(["otool", "-L", str(binary)], environment)
    vellum_libraries = sorted(set(re.findall(r"(\S*libvellum[\w.-]*\.dylib)", linked)))
    if vellum_libraries != ["@rpath/libvellum-host-c.dylib"]:
        raise ProofError(f"the proof links Vellum libraries other than the C ABI: {vellum_libraries}")
    if foreign_source_reference(linked, source_root, allowed):
        raise ProofError("the proof binary links from the Vellum source tree")

    staged_bundle = work / bundle.name
    shutil.copy2(bundle, staged_bundle)
    started = time.monotonic()
    output = run([str(binary), "--bundle", str(staged_bundle)], environment, cwd=work)
    run_seconds = time.monotonic() - started
    report = json.loads(output.strip().splitlines()[-1])
    if report.get("ok") is not True:
        raise ProofError(f"the proof did not report success: {report}")
    return {
        "schema": "vellum.swift-host-proof.v1",
        "sdk_root": str(sdk),
        "swift_version": swift_version,
        "xcode_version": xcode_version,
        "pinned_toolchain": {"xcode": " ".join(PINNED_XCODE), "swift": PINNED_SWIFT},
        "extra_build_flags": rpath,
        "pkg_config_path": environment["PKG_CONFIG_PATH"],
        "clean_build_seconds": round(build_seconds, 3),
        "noop_build_seconds": round(incremental_seconds, 3),
        "binary_bytes": binary.stat().st_size,
        "linked_vellum_libraries": vellum_libraries,
        "process_wall_seconds": round(run_seconds, 3),
        "proof": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-prefix", default="")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args(argv)
    package = args.package.resolve()
    bundle = (args.bundle or package / "Fixtures" / "host-proof-app.js").resolve()
    try:
        sdk = locate_sdk(args.sdk_prefix or os.environ.get(PREFIX_VARIABLE, ""))
        evidence = prove(sdk, package, args.work.resolve(), bundle)
    except Skip as reason:
        print(f"SKIP vellum.swift-host-proof: {reason}")
        return SKIP
    except (ProofError, OSError, json.JSONDecodeError) as error:
        print(f"vellum.swift-host-proof: {error}", file=sys.stderr)
        return 1
    text = json.dumps(evidence, indent=2, sort_keys=True)
    (args.work.resolve() / "swift-host-proof-evidence.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
