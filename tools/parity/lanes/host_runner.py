#!/usr/bin/env python3
"""Side runner for the host lane: one implementation's host probe, validators
and standalone apps, plus its identity probes.

The parity harness runs this as a tool (it is content-hashed into each side's
identity). It turns one case into the observation files the host lane
compares, and applies the seeded faults a calibration control asks for.

  probe       run the host probe over --plugin bundles; write the probe's
              observation directory, provenance.json (bundle hashes and build
              records against the checkout) and runtime-binding.json (which
              copy of each shared runtime every plug-in was bound to)
  validator   run an external validator; write validator.json (exit, output);
              exits 0 whenever the validator ran, so its verdict is a result
  standalone  launch a standalone app with the null audio device and a
              one-shot screenshot; write standalone.json
  identity    print one identity probe (build, provider, fonts) to stdout

Faults (calibration controls only): --env K=V, --probe-arg ARG,
--corrupt-first DIR (truncated copy of the first bundle), --skew-second
RUNTIME --work DIR (copy of the second bundle whose bundled shared runtime is
replaced by RUNTIME), --validator-missing (exit 77, as if absent).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

SKIP_EXIT = 77
RUNTIME_HINTS = ("wgpu", "dawn", "webgpu", "skia")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(path: Path) -> str | None:
    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        return None
    lines = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path).as_posix()
        if item.is_symlink():
            lines.append(f"L {rel} {os.readlink(item)}")
        elif item.is_file():
            lines.append(f"F {rel} {sha256_file(item)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def main_executable(bundle: Path) -> Path | None:
    info = bundle / "Contents" / "Info.plist"
    if info.is_file():
        try:
            name = plistlib.loads(info.read_bytes()).get("CFBundleExecutable")
            if name and (bundle / "Contents" / "MacOS" / name).is_file():
                return bundle / "Contents" / "MacOS" / name
        except (plistlib.InvalidFileException, ValueError):
            pass
    macos = bundle / "Contents" / "MacOS"
    exes = [p for p in sorted(macos.iterdir()) if p.is_file() and p.suffix != ".dylib"] if macos.is_dir() else []
    return exes[0] if exes else None


def build_record(bundle: Path) -> dict | None:
    found = sorted(bundle.rglob("pulp-build-info.json")) if bundle.is_dir() else []
    if not found:
        return None
    info = json.loads(found[0].read_text(encoding="utf-8"))
    pins = info.get("runtime_pins") or {}
    product = info.get("product") or {}
    return {"source_git_sha": product.get("source_git_sha"), "source_git_dirty": product.get("source_git_dirty"),
            "build": info.get("build"), "sdk_version": (info.get("pulp_sdk") or {}).get("version"),
            "skia": {k: (pins.get("skia") or {}).get(k) for k in ("release", "commit", "graphite_backend")}}


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def linked_runtimes(exe: Path) -> list[str]:
    out = run(["otool", "-L", str(exe)]).stdout.splitlines()[1:]
    names = [line.strip().split(" (")[0] for line in out]
    return sorted(n for n in names if any(t in n.lower() for t in RUNTIME_HINTS))


def imported_symbols(exe: Path, install_name: str) -> int:
    """Symbols the executable binds from the dylib with this install name."""
    leaf = Path(install_name).name
    stem = leaf.split(".")[0]
    listing = run(["dyld_info", "-imports", str(exe)]).stdout
    return sum(1 for line in listing.splitlines() if f"(from {stem})" in line or f"(from {leaf})" in line)


def git_state(checkout: Path) -> dict:
    head = run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.strip()
    dirty = bool(run(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"]).stdout.strip())
    return {"head": head or None, "dirty": dirty}


# ── faults ──────────────────────────────────────────────────────────────────


def corrupt_copy(bundle: Path, work: Path) -> Path:
    dest = work / bundle.name
    if dest.exists():
        shutil.rmtree(dest)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, dest, symlinks=True)
    exe = main_executable(dest)
    exe.write_bytes(exe.read_bytes()[:1024])
    return dest


def skew_copy(bundle: Path, runtime: Path, work: Path) -> Path:
    dest = work / "skew" / bundle.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, dest, symlinks=True)
    replaced = 0
    for dylib in (dest / "Contents" / "MacOS").glob("*.dylib"):
        if any(t in dylib.name.lower() for t in RUNTIME_HINTS):
            shutil.copy2(runtime, dylib)
            replaced += 1
    if not replaced:
        raise SystemExit(f"skew: {bundle} carries no bundled runtime to replace")
    run(["codesign", "--force", "--deep", "--sign", "-", str(dest)])
    return dest


# ── subcommands ─────────────────────────────────────────────────────────────


def cmd_probe(a: argparse.Namespace) -> int:
    settings = json.loads(Path(a.settings).read_text(encoding="utf-8"))
    bundles = [Path(p) for p in a.plugin]
    if a.corrupt_first:
        bundles[0] = corrupt_copy(bundles[0], Path(a.corrupt_first))
    if a.skew_second:
        if len(bundles) < 2 or not a.work:
            raise SystemExit("--skew-second needs two plug-ins and --work")
        bundles[1] = skew_copy(bundles[1], Path(a.skew_second), Path(a.work))
    argv = [a.probe]
    for b in bundles:
        argv += ["--plugin", str(b)]
    argv += ["--out", a.out, "--blocks", str(settings["blocks"]), "--block-size", str(settings["block_size"]),
             "--sample-rate", str(settings["sample_rate"]), "--opens", str(settings["opens"]),
             "--watch-ms", str(settings["watch_ms"]), "--resize-by", settings["resize_by"]]
    argv += a.probe_arg
    env = dict(os.environ, **dict(kv.split("=", 1) for kv in a.env))
    result = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=600)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if a.out == "together":
        Path("runner.json").write_text(json.dumps({"animated": [int(i) for i in a.animated], "faults": {
            "env": a.env, "probe_arg": a.probe_arg, "corrupt_first": bool(a.corrupt_first),
            "skew_second": bool(a.skew_second)}}, indent=2), encoding="utf-8")
    if result.returncode != 0:
        return result.returncode
    if a.out == "together":
        write_provenance(bundles, Path(a.checkout) if a.checkout else None)
        write_runtime_binding(Path(a.out) / "host-observation.json", bundles)
    return 0


def write_provenance(bundles: list[Path], checkout: Path | None) -> None:
    state = git_state(checkout) if checkout else {"head": None, "dirty": None}
    records = []
    for b in bundles:
        exe = main_executable(b)
        records.append({"bundle": b.name, "tree_sha256": tree_sha256(b),
                        "executable_sha256": sha256_file(exe) if exe else None, "build": build_record(b)})
    Path("provenance.json").write_text(json.dumps({"checkout": state, "bundles": records}, indent=2),
                                       encoding="utf-8")


def write_runtime_binding(observation: Path, bundles: list[Path]) -> None:
    """For every plug-in, each shared runtime it links: its own bundled copy,
    the copy the process actually loaded under that install name, whether the
    bytes match, and how many symbols the plug-in binds from it."""
    images = json.loads(observation.read_text(encoding="utf-8")).get("dyld_images", [])
    rows = []
    for index, b in enumerate(bundles):
        exe = main_executable(b)
        for install in linked_runtimes(exe) if exe else []:
            leaf = Path(install).name
            own = b / "Contents" / "MacOS" / leaf
            loaded = [p for p in images if Path(p).name == leaf]
            loaded_path = Path(loaded[0]) if loaded else None
            rows.append({
                "plugin": index, "bundle": b.name, "install_name": install,
                "own_copy_sha256": sha256_file(own) if own.is_file() else None,
                "loaded_copy_owner": next((i for i, o in enumerate(bundles)
                                           if loaded_path and str(loaded_path).startswith(str(o) + "/")), None),
                "loaded_copy_sha256": sha256_file(loaded_path) if loaded_path and loaded_path.is_file() else None,
                "loaded_copies": len(loaded),
                "imported_symbols": imported_symbols(exe, install),
            })
    for row in rows:
        row["bound_to_own_bytes"] = row["own_copy_sha256"] == row["loaded_copy_sha256"]
    Path("runtime-binding.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def cmd_validator(a: argparse.Namespace) -> int:
    if a.validator_missing or not Path(a.tool).is_file():
        print(f"validator {a.tool} is not available here", file=sys.stderr)
        return SKIP_EXIT
    bundle = Path(a.bundle)
    if a.corrupt_first:
        bundle = corrupt_copy(bundle, Path(a.corrupt_first))
    argv = [a.tool, *[x.replace("@@BUNDLE@@", str(bundle)) for x in a.tool_args]]
    try:
        result = run(argv, timeout=a.timeout)
        record = {"exit": result.returncode, "stdout_tail": result.stdout[-20000:], "argv": argv}
    except subprocess.TimeoutExpired:
        record = {"exit": None, "error": "timeout", "argv": argv}
    Path(a.out).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return 0


def cmd_standalone(a: argparse.Namespace) -> int:
    app = Path(a.app)
    exe = main_executable(app)
    if exe is None:
        print(f"no executable in {app}", file=sys.stderr)
        return 2
    env = dict(os.environ, PULP_AUDIO_DEVICE="null", PULP_SCREENSHOT=str(Path(a.png).resolve()))
    result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=a.timeout)
    sys.stderr.write(result.stderr[-4000:])
    Path("standalone.json").write_text(json.dumps({"exit": result.returncode, "app": app.name}, indent=2),
                                       encoding="utf-8")
    return result.returncode


def cmd_identity(a: argparse.Namespace) -> int:
    paths = [Path(p) for p in a.paths]
    if a.what == "build":
        checkout, bundles = paths[0], paths[1:]
        print(json.dumps({"checkout": git_state(checkout), "bundles": {
            b.name + "@" + b.parent.name: {"tree_sha256": tree_sha256(b), "build": build_record(b)}
            for b in bundles}}, indent=1, sort_keys=True))
    elif a.what == "provider":
        out = {}
        for b in paths:
            exe = main_executable(b)
            runtimes = linked_runtimes(exe) if exe else []
            out[b.name + "@" + b.parent.name] = {
                "skia": (build_record(b) or {}).get("skia"),
                "linked_runtimes": {r: {"imported_symbols": imported_symbols(exe, r),
                                        "bundled_copy_sha256": sha256_file(b / "Contents/MacOS" / Path(r).name)
                                        if (b / "Contents/MacOS" / Path(r).name).is_file() else None}
                                    for r in runtimes}}
        print(json.dumps(out, indent=1, sort_keys=True))
    elif a.what == "fonts":
        fonts = {}
        for b in paths:
            for f in sorted(b.rglob("*")):
                if f.suffix.lower() in (".ttf", ".otf", ".ttc", ".woff", ".woff2"):
                    fonts[f"{b.name}/{f.relative_to(b).as_posix()}"] = sha256_file(f)
        print(json.dumps({"bundled_fonts": fonts, "system_font_source":
                          f"CoreText system fonts, macOS {platform.mac_ver()[0]} {platform.machine()}"},
                         indent=1, sort_keys=True))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe")
    pr.add_argument("--probe", required=True)
    pr.add_argument("--settings", required=True)
    pr.add_argument("--checkout")
    pr.add_argument("--out", required=True)
    pr.add_argument("--plugin", action="append", default=[])
    pr.add_argument("--animated", action="append", default=[])
    pr.add_argument("--env", action="append", default=[])
    pr.add_argument("--probe-arg", action="append", default=[])
    pr.add_argument("--corrupt-first")
    pr.add_argument("--skew-second")
    pr.add_argument("--work")
    va = sub.add_parser("validator")
    va.add_argument("--tool", required=True)
    va.add_argument("--bundle", required=True)
    va.add_argument("--out", required=True)
    va.add_argument("--timeout", type=int, default=900)
    va.add_argument("--corrupt-first")
    va.add_argument("--validator-missing", action="store_true")
    va.add_argument("tool_args", nargs="*")
    st = sub.add_parser("standalone")
    st.add_argument("--app", required=True)
    st.add_argument("--png", required=True)
    st.add_argument("--timeout", type=int, default=120)
    st.add_argument("--app-override")
    ident = sub.add_parser("identity")
    ident.add_argument("what", choices=("build", "provider", "fonts"))
    ident.add_argument("paths", nargs="+")
    a = p.parse_args()
    if a.cmd == "standalone" and a.app_override:
        a.app = a.app_override
    return {"probe": cmd_probe, "validator": cmd_validator, "standalone": cmd_standalone,
            "identity": cmd_identity}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
