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
    r"\A(?:Package\.swift|README\.md|CMakeLists\.txt|InstallSdkPrefix\.cmake|"
    r"Fixtures/[\w.-]+\.js|"
    r"Sources/CVellumHostC/(?:module\.modulemap|shim\.h)|Sources/[\w]+/[\w]+\.swift|"
    r"scripts/[\w]+\.py)\Z"
)
SHIM_INCLUDE = "#include <vellum/c/vellum_host.h>"
ISOLATED_VARIABLES = (
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
    "LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH", "PKG_CONFIG_LIBDIR", "SDKROOT_OVERRIDE",
)


DESK_IDLE_SECONDS = 180.0
DESK_MAX_LOAD = 4.0
NATIVE_SKIP_VARIABLE = "VELLUM_SWIFT_PROOF_SKIP_NATIVE"
REQUIRE_PIN_VARIABLE = "VELLUM_SWIFT_PROOF_REQUIRE_PINNED_TOOLCHAIN"
NO_WINDOW_SERVER = "skipped-no-window-server"
PINNED_XCODE = ("27.2", "27B52")
PINNED_SWIFT = "6.4.2"


class ProofError(Exception):
    pass


class Skip(Exception):
    pass


def locate_sdk(prefix_text: str, required: bool = False) -> Path:
    unavailable = ProofError if required else Skip
    if not prefix_text:
        raise unavailable(
            f"no installed Vellum SDK prefix; set -D{PREFIX_VARIABLE}=<prefix> or "
            f"{PREFIX_VARIABLE} to a prefix made by scripts/build_sdk_artifact.py and "
            "scripts/install.sh"
        )
    prefix = Path(prefix_text).expanduser()
    if not prefix.is_dir():
        raise unavailable(f"installed Vellum SDK prefix is not available: {prefix}")
    for candidate in (prefix, prefix / "sdk", prefix / "lib" / "vellum" / "sdk"):
        if (candidate / PKG_CONFIG_FILE).is_file():
            return candidate.resolve()
    raise ProofError(f"installed SDK at {prefix} does not ship {PKG_CONFIG_FILE}")


def package_files(package: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."],
        cwd=package, capture_output=True, text=True, check=False,
    )
    if listed.returncode == 0:
        tracked = sorted(
            name for name in listed.stdout.split("\0")
            if name and Path(name).name != ".DS_Store"
        )
        if tracked:
            return tracked
    names: list[str] = []
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package).as_posix()
        parts = relative.split("/")
        if not path.is_file() or parts[0] in {".build", ".swiftpm"} or "__pycache__" in parts:
            continue
        if path.name == ".DS_Store":
            continue
        names.append(relative)
    return names


def check_package(package: Path) -> list[str]:
    problems: list[str] = []
    for relative in package_files(package):
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


def native_guard_reason(ci: bool, idle_seconds: float | None, load: float) -> str | None:
    if ci:
        return None
    if idle_seconds is None:
        return "could not read HIDIdleTime, so the machine may be in use"
    if idle_seconds < DESK_IDLE_SECONDS:
        return (f"machine in use: HIDIdleTime {idle_seconds:.0f} s is under "
                f"{DESK_IDLE_SECONDS:.0f} s")
    if load >= DESK_MAX_LOAD:
        return f"machine busy: 1-minute load {load:.2f} is not under {DESK_MAX_LOAD:.0f}"
    return None


def hid_idle_seconds() -> float | None:
    result = subprocess.run(
        ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, check=False
    )
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout)
    return int(match.group(1)) / 1_000_000_000 if match else None


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1"}


def running_in_ci(environment: dict[str, str]) -> bool:
    return truthy(environment.get("CI")) or truthy(environment.get("GITHUB_ACTIONS"))


def desk_guard(environment: dict[str, str]) -> str | None:
    if running_in_ci(environment):
        return None
    return native_guard_reason(False, hid_idle_seconds(), os.getloadavg()[0])


def native_problem(report: dict[str, object], required: bool, guard: str | None,
                   ci: bool = False) -> str | None:
    state = report.get("native_hosts")
    if state == "passed" or not required:
        return None
    if guard is not None and state == f"skipped: {guard}":
        return None
    if ci and state == NO_WINDOW_SERVER:
        return None
    return f"the native AppKit and SwiftUI hosts did not pass: {state}"


def cg_session_present() -> bool | None:
    try:
        import ctypes
        graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except (OSError, ImportError):
        return None
    graphics.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
    session = graphics.CGSessionCopyCurrentDictionary()
    if not session:
        return False
    foundation.CFRelease(ctypes.c_void_p(session))
    return True


def window_server_probe() -> dict[str, object]:
    command = ["launchctl", "print", f"gui/{os.getuid()}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
        lines = (result.stdout or result.stderr).strip().splitlines()
        launchctl = {"ok": result.returncode == 0, "exit": result.returncode,
                     "detail": lines[0][:200] if lines else ""}
    except (OSError, subprocess.TimeoutExpired) as error:
        launchctl = {"ok": False, "exit": None, "detail": str(error)[:200]}
    return {"launchctl_command": " ".join(command), "launchctl_gui": launchctl,
            "cg_session": cg_session_present()}


def describe_probe(probe: dict[str, object]) -> str:
    launchctl = probe["launchctl_gui"]
    cg = {True: "non-NULL", False: "NULL", None: "unavailable"}[probe["cg_session"]]
    status = "ok" if launchctl["ok"] else f"failed (exit {launchctl['exit']})"
    return (f"{probe['launchctl_command']} {status}: {launchctl['detail']}; "
            f"CGSessionCopyCurrentDictionary {cg}")


def native_summary(report: dict[str, object], probe: dict[str, object]) -> str:
    state = report.get("native_hosts")
    if state == "passed":
        return "native AppKit and SwiftUI hosts: passed"
    return f"SKIP native AppKit and SwiftUI hosts: {state} [{describe_probe(probe)}]"


def publish_summary(environment: dict[str, str], lines: list[str]) -> None:
    target = environment.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("### Swift host proof\n\n" + "".join(f"- {line}\n" for line in lines) + "\n")
    except OSError:
        pass


def check_native(evidence_path: Path) -> int:
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"SKIP vellum.swift-host-proof-native: no proof evidence at {evidence_path}")
        return SKIP
    state = evidence.get("proof", {}).get("native_hosts")
    probe = evidence.get("window_server_probe")
    detail = f" [{describe_probe(probe)}]" if probe else ""
    if state == "passed":
        print("vellum.swift-host-proof-native: native AppKit and SwiftUI hosts passed" + detail)
        return 0
    if isinstance(state, str) and state.startswith("skipped"):
        print(f"SKIP vellum.swift-host-proof-native: {state}{detail}")
        return SKIP
    print(f"vellum.swift-host-proof-native: unexpected native state {state!r}", file=sys.stderr)
    return 1


def sterile_environment(sdk: Path) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if key not in ISOLATED_VARIABLES and key != NATIVE_SKIP_VARIABLE
    }
    environment["PKG_CONFIG_PATH"] = str(sdk / PKG_CONFIG_FILE.parent)
    environment["MTL_DEBUG_LAYER"] = "0"
    return environment


def foreign_source_reference(text: str, source_root: Path, allowed: list[Path]) -> bool:
    for path in sorted(allowed, key=lambda item: len(str(item)), reverse=True):
        text = text.replace(str(path), "<allowed>")
    return str(source_root) in text


WORK_MARKER = ".vellum-swift-proof-work"


def identity(path: Path) -> tuple[int, int] | None:
    try:
        details = os.stat(path)
    except OSError:
        return None
    return details.st_dev, details.st_ino


def canonical(path: Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(path)))


def lineage(path: Path) -> list[Path]:
    absolute = Path(os.path.abspath(path))
    return [absolute, *absolute.parents]


def same_location(first: Path, second: Path) -> bool:
    first_id, second_id = identity(first), identity(second)
    if first_id is not None and second_id is not None:
        return first_id == second_id
    return str(canonical(first)).casefold() == str(canonical(second)).casefold()


def within(ancestor: Path, path: Path) -> bool:
    target = identity(ancestor)
    for candidate in lineage(canonical(path)):
        if target is not None and identity(candidate) == target:
            return True
        if target is None and str(candidate).casefold() == str(canonical(ancestor)).casefold():
            return True
    return False


def symlink_between(build_dir: Path, work: Path) -> Path | None:
    stop = os.path.abspath(build_dir)
    for candidate in lineage(work):
        if str(candidate) == stop:
            return None
        if os.path.islink(candidate):
            return candidate
        if same_location(candidate, build_dir):
            return None
    return None


def unsafe_work_reason(work: Path, build_dir: Path, package: Path) -> str | None:
    work = Path(os.path.abspath(work))
    package = Path(os.path.abspath(package))
    source_root = canonical(package).parents[1]
    if within(build_dir, source_root):
        return f"the build directory {build_dir} is or contains the source tree"
    link = symlink_between(build_dir, work)
    if link is not None:
        return f"the work directory path {work} passes through the symlink {link}"
    if same_location(work, build_dir) or not within(build_dir, work):
        return f"the work directory {work} is not a dedicated child of the build directory {build_dir}"
    for protected in (package, source_root):
        if within(work, protected):
            return f"the work directory {work} is or contains {protected}"
    if within(package, work):
        return f"the work directory {work} lies inside the package {package}"
    return None


def prepare_work(work: Path, build_dir: Path, package: Path) -> None:
    if os.path.lexists(work):
        if os.path.islink(work) or not os.path.isdir(work):
            raise ProofError(f"refusing to remove the work directory: {work} is not a real directory")
        if not os.path.isfile(work / WORK_MARKER):
            raise ProofError(
                f"refusing to remove the work directory: {work} was not created by this runner "
                f"(no {WORK_MARKER}); remove it by hand")
        unsafe = unsafe_work_reason(work, build_dir, package)
        if unsafe is not None:
            raise ProofError(f"refusing to remove the work directory: {unsafe}")
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(work, 0o700)
    unsafe = unsafe_work_reason(work, build_dir, package)
    if unsafe is not None or os.path.islink(work):
        raise ProofError(f"the created work directory failed validation: {unsafe or work}")
    descriptor = os.open(work / WORK_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)


def run(command: list[str], environment: dict[str, str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command, env=environment, cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ProofError(
            f"{' '.join(command)} failed ({result.returncode})\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout + result.stderr


def prove(sdk: Path, package: Path, work: Path, bundle: Path, build_dir: Path,
          require_pin: bool = False, require_prefix: bool = False) -> dict[str, object]:
    unsafe = unsafe_work_reason(work, build_dir, package)
    if unsafe is not None:
        raise ProofError(f"refusing to remove the work directory: {unsafe}")
    swift = shutil.which("swift")
    if swift is None:
        if require_prefix:
            raise ProofError("the swift toolchain is not on PATH")
        raise Skip("the swift toolchain is not on PATH")
    swift_version = tool_version([swift, "--version"])
    xcode_version = tool_version(["xcodebuild", "-version"])
    pin_problems = pinned_toolchain_problems(xcode_version, swift_version)
    if pin_problems and not require_pin:
        print("vellum.swift-host-proof: running on an unpinned toolchain: " +
              "; ".join(pin_problems))
    problems = (pin_problems if require_pin else []) + check_package(package)
    if problems:
        raise ProofError("\n".join(problems))
    source_root = package.resolve().parents[1]
    prepare_work(work, build_dir, package)
    staged = work / "package"
    shutil.copytree(
        package, staged,
        ignore=shutil.ignore_patterns(
            ".build", ".swiftpm", ".DS_Store", "__pycache__", "scripts", "Fixtures",
            "CMakeLists.txt", "InstallSdkPrefix.cmake"),
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

    probe = window_server_probe()
    print(f"vellum.swift-host-proof: window-server probe: {describe_probe(probe)}")
    native_guard = desk_guard(environment)
    if native_guard is not None:
        print(f"vellum.swift-host-proof: skipping the window-opening native hosts: {native_guard}")
        environment[NATIVE_SKIP_VARIABLE] = native_guard
    staged_bundle = work / bundle.name
    shutil.copy2(bundle, staged_bundle)
    started = time.monotonic()
    output = run([str(binary), "--bundle", str(staged_bundle)], environment, cwd=work)
    run_seconds = time.monotonic() - started
    report = json.loads(output.strip().splitlines()[-1])
    if report.get("ok") is not True:
        raise ProofError(f"the proof did not report success: {report}")
    ci = running_in_ci(environment)
    problem = native_problem(report, ci or require_prefix, native_guard, ci)
    summary = native_summary(report, probe)
    print(f"vellum.swift-host-proof: {summary}")
    publish_summary(environment, [f"window-server probe: {describe_probe(probe)}", summary])
    if problem is not None:
        raise ProofError(problem)
    return {
        "schema": "vellum.swift-host-proof.v1",
        "sdk_root": str(sdk),
        "swift_version": swift_version,
        "xcode_version": xcode_version,
        "pinned_toolchain": {"xcode": " ".join(PINNED_XCODE), "swift": PINNED_SWIFT},
        "pinned_toolchain_matched": not pin_problems,
        "pinned_toolchain_problems": pin_problems,
        "ci": running_in_ci(environment),
        "native_hosts_guard": native_guard or "clear",
        "native_hosts_summary": summary,
        "window_server_probe": probe,
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
    parser.add_argument("--require-prefix", action="store_true")
    parser.add_argument("--require-pinned-toolchain", action="store_true")
    parser.add_argument("--check-native", type=Path)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args(argv)
    if args.check_native is not None:
        return check_native(args.check_native)
    package = args.package.resolve()
    bundle = (args.bundle or package / "Fixtures" / "host-proof-app.js").resolve()
    try:
        sdk = locate_sdk(args.sdk_prefix or os.environ.get(PREFIX_VARIABLE, ""),
                         required=args.require_prefix)
        if args.build_dir is None:
            raise ProofError("--build-dir is required so the work directory can be checked")
        evidence = prove(sdk, package, Path(os.path.abspath(args.work)), bundle, args.build_dir,
                         require_pin=args.require_pinned_toolchain or
                         truthy(os.environ.get(REQUIRE_PIN_VARIABLE)),
                         require_prefix=args.require_prefix)
    except Skip as reason:
        print(f"SKIP vellum.swift-host-proof: {reason}")
        return SKIP
    except (ProofError, OSError, json.JSONDecodeError) as error:
        print(f"vellum.swift-host-proof: {error}", file=sys.stderr)
        return 1
    text = json.dumps(evidence, indent=2, sort_keys=True)
    (Path(os.path.abspath(args.work)) / "swift-host-proof-evidence.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
