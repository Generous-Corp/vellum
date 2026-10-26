"""Platform lanes: plug-in and app artifacts of the platforms a Mac cannot host
as a plug-in (Linux, Windows, web, iOS simulator, Android).

Registered with the parity harness through ``--lane-module
tools/parity/lanes/platforms.py``. Each side's implementation runs
``platform_runner.py`` over its artifact, which writes
platform-observation.json. These lanes compare those files against the case's
expectations and across sides. The static lanes are named for what they read,
not for a platform: none of them hosts a plug-in, so none of them stands in
for a platform's host checks (validators, two plug-ins in one process, editor
lifecycle, offline render).

  linux-elf-static     every ELF image has the case's machine and type; each
                       image the case names exports what its format's host
                       looks up (clap_entry, GetPluginFactory, ...) and, when
                       the case says so, nothing matching a forbidden export
                       pattern (an exported internal symbol is interposed
                       between two plug-ins in one host process), needs every
                       library the case requires and none it forbids, and
                       carries no symbol of a forbidden family; the files the
                       case requires exist; images and bytes agree across sides
  windows-sdk-pe       the same over PE images: machine, DLL or executable,
                       subsystem, exported names, imported DLLs (no debug CRT
                       when the case forbids it), symbol families of the
                       exports
  web-wam-wclap        every WebAssembly module the case names has the exports
                       its ABI needs (clap_entry for WebCLAP, the emscripten
                       entry points for WAM) and imports only the modules the
                       case allows; Pulp's own Node host checks for the module
                       exit 0 and print their PASS marker
  ios-install-registration
                       the host app and every embedded extension verify as
                       sealed bundles; the app installs on a booted simulator;
                       the installed app carries each extension; each extension
                       has the case's extension point and is listed by
                       pluginkit
  android-host-checks  the APK carries the native libraries the case requires,
                       each exports the JNI symbol of every native method the
                       app declares (a missing one is an UnsatisfiedLinkError
                       the first time Java calls it), the APK installs, its activity starts, the process is alive
                       after the settle time, no fatal exception is logged, and
                       every log marker the case requires appears

Every check is made on each side, then the observations that must not differ
between two runs of one implementation are compared.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import parity_harness as ph

SCHEMA = "pulp.platform-lane.observation.v1"
OBS = "platform-observation.json"
lane_result = ph.lane_result


def observation(directory: Path) -> dict[str, Any]:
    doc = ph.load_json(directory / OBS)
    if doc.get("schema") != SCHEMA:
        raise ph.HarnessError(f"{directory}: observation schema {doc.get('schema')!r}")
    return doc


def both(side_fn, shape_fn, ref: Path, cand: Path, case: dict[str, Any], label: str) -> dict[str, Any]:
    o1, o2 = observation(ref), observation(cand)
    reasons = side_fn(case, o1, "reference") + side_fn(case, o2, "candidate")
    s1, s2 = shape_fn(o1), shape_fn(o2)
    if s1 != s2:
        reasons += [f"{label} differs: " + x for x in ph.json_differences(s1, s2, "")]
    return lane_result("fail" if reasons else "pass", reasons=reasons, shape=s1)


# ── static images (linux, windows) ──────────────────────────────────────────


def image_side(case: dict[str, Any], obs: dict[str, Any], side: str, kind: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    images = {rel: img for rel, img in (obs.get("images") or {}).items() if img.get("kind") == kind}
    if not images:
        reasons.append(f"{side}: no {kind.upper()} image in the artifact (vacuous)")
    paths = {f["path"] for f in obs.get("files", [])}
    dirs = {p.rsplit("/", i)[0] for p in paths for i in range(1, p.count("/") + 1)}
    for rel in expect.get("required", []):
        if rel not in paths and rel not in dirs:
            reasons.append(f"{side}: required path {rel} is missing")
    machine = expect.get("machine")
    for rel, img in sorted(images.items()):
        if "unreadable" in img:
            reasons.append(f"{side}: {rel} is not a readable {kind.upper()} image: {img['unreadable']}")
            continue
        if machine and img.get("machine") != machine:
            reasons.append(f"{side}: {rel} is a {kind.upper()} image for {img.get('machine')}, case expects {machine}")
    for rel, want in sorted(expect.get("images", {}).items()):
        img = images.get(rel)
        if img is None:
            reasons.append(f"{side}: named image {rel} is missing")
            continue
        if "unreadable" in img:
            continue
        for key in ("type", "dll", "subsystem"):
            if key in want and img.get(key) != want[key]:
                reasons.append(f"{side}: {rel} {key} is {img.get(key)!r}, case expects {want[key]!r}")
        exports = set(img.get("exports", []))
        for name in want.get("require_exports", []):
            if name not in exports:
                reasons.append(f"{side}: {rel} is missing export {name}")
        for pattern in want.get("forbid_exports", []):
            hits = [x for x in sorted(exports) if re.search(pattern, x)]
            if hits:
                reasons.append(f"{side}: {rel} exports {len(hits)} symbols matching forbidden pattern {pattern!r} "
                               f"(of {len(exports)} exports), e.g. {hits[0]}")
        linked = img.get("needed", []) + img.get("imports", []) + img.get("delay_imports", [])
        lowered = [x.lower() for x in linked]
        for name in want.get("require_linked", []):
            if name.lower() not in lowered:
                reasons.append(f"{side}: {rel} does not link required library {name}")
        for pattern in want.get("forbid_linked", []):
            hits = [x for x in linked if re.search(pattern, x, re.IGNORECASE)]
            if hits:
                reasons.append(f"{side}: {rel} links forbidden library {hits[0]}")
        fam = img.get("families", {})
        for family in want.get("forbid_families", []):
            if family not in fam:
                reasons.append(f"{side}: scan fixture names no family {family}")
            elif fam[family]:
                reasons.append(f"{side}: {rel} carries {fam[family]} symbols of forbidden family {family}")
        for family in want.get("require_families", []):
            if not fam.get(family):
                reasons.append(f"{side}: {rel} carries no symbol of required family {family}")
    return reasons


def image_shape(obs: dict[str, Any]) -> dict[str, Any]:
    keep = ("kind", "machine", "type", "class", "dll", "subsystem", "needed", "imports", "delay_imports", "exports",
            "families", "import_modules", "unreadable")
    return {"images": {rel: {k: img[k] for k in keep if k in img} for rel, img in (obs.get("images") or {}).items()},
            "files": {f["path"]: f.get("sha256", f.get("symlink")) for f in obs.get("files", [])}}


def compare_linux(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    return both(lambda c, o, s: image_side(c, o, s, "elf"), image_shape, ref, cand, case, "ELF artifact")


def compare_windows(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    return both(lambda c, o, s: image_side(c, o, s, "pe"), image_shape, ref, cand, case, "PE artifact")


# ── web ─────────────────────────────────────────────────────────────────────


def web_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    images = {rel: img for rel, img in (obs.get("images") or {}).items() if img.get("kind") == "wasm"}
    if not images:
        reasons.append(f"{side}: no WebAssembly module in the artifact (vacuous)")
    for rel, want in sorted(expect.get("images", {}).items()):
        img = images.get(rel)
        if img is None:
            reasons.append(f"{side}: named module {rel} is missing")
            continue
        if "unreadable" in img:
            reasons.append(f"{side}: {rel} is not a readable WebAssembly module: {img['unreadable']}")
            continue
        exports = set(img.get("exports", []))
        for name in want.get("require_exports", []):
            if name not in exports:
                reasons.append(f"{side}: {rel} is missing export {name}")
        allowed = want.get("allowed_import_modules")
        if allowed is not None:
            extra = sorted(set(img.get("import_modules", [])) - set(allowed))
            if extra:
                reasons.append(f"{side}: {rel} imports from modules the case does not allow: {extra}")
    checks = (obs.get("node") or {}).get("checks", {})
    wanted = expect.get("node_checks", [])
    if not wanted:
        reasons.append(f"{side}: the case names no Node host check (vacuous)")
    for name in wanted:
        check = checks.get(name)
        if check is None:
            reasons.append(f"{side}: node check {name} did not run")
        elif check["exit"] != 0 or not check["pass_lines"]:
            reasons.append(f"{side}: node check {name} exited {check['exit']} with "
                           f"{len(check['pass_lines'])} PASS lines {check['fail_lines'][:2]}")
    return reasons


def web_shape(obs: dict[str, Any]) -> dict[str, Any]:
    shape = image_shape(obs)
    shape["node"] = {n: {"exit": c["exit"], "pass_lines": c["pass_lines"]}
                     for n, c in ((obs.get("node") or {}).get("checks") or {}).items()}
    return shape


def compare_web(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    return both(web_side, web_shape, ref, cand, case, "web artifact")


# ── iOS simulator ───────────────────────────────────────────────────────────


def ios_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    cs = (obs.get("app") or {}).get("codesign") or {}
    if cs.get("exit") != 0:
        reasons.append(f"{side}: codesign (host app) verification exited {cs.get('exit')}: "
                       f"{(cs.get('stderr_tail') or '').strip()[:160]}")
    extensions = obs.get("extensions") or {}
    for name in expect.get("extensions", []):
        ext = extensions.get(name)
        if ext is None:
            reasons.append(f"{side}: extension {name} is not embedded in the host app")
            continue
        if (ext.get("codesign") or {}).get("exit") != 0:
            reasons.append(f"{side}: codesign ({name}) verification exited {ext['codesign'].get('exit')}: "
                           f"{(ext['codesign'].get('stderr_tail') or '').strip()[:160]}")
        point = expect.get("extension_point")
        if point and ext.get("extension_point") != point:
            reasons.append(f"{side}: {name} extension point is {ext.get('extension_point')!r}, case expects {point!r}")
        if expect.get("audio_component") and not ext.get("audio_components"):
            reasons.append(f"{side}: {name} declares no AudioComponents")
    install = obs.get("install") or {}
    if install.get("exit") != 0:
        reasons.append(f"{side}: simctl install exited {install.get('exit')}: "
                       f"{(install.get('stderr_tail') or '').strip()[:200]}")
    else:
        for name in expect.get("extensions", []):
            if name not in obs.get("installed_app_plugins", []):
                reasons.append(f"{side}: the installed app does not carry {name}")
            if not (obs.get("pluginkit") or {}).get(name, {}).get("listed"):
                reasons.append(f"{side}: pluginkit does not list {name} as registered")
    if not expect.get("extensions"):
        reasons.append(f"{side}: the case names no extension (vacuous)")
    return reasons


def ios_shape(obs: dict[str, Any]) -> dict[str, Any]:
    return {"app": (obs.get("app") or {}).get("bundle_id"),
            "codesign": ((obs.get("app") or {}).get("codesign") or {}).get("exit"),
            "install": (obs.get("install") or {}).get("exit"),
            "installed_app_plugins": obs.get("installed_app_plugins"),
            "extensions": {n: {"bundle_id": e.get("bundle_id"), "extension_point": e.get("extension_point"),
                               "audio_components": e.get("audio_components"),
                               "codesign": (e.get("codesign") or {}).get("exit"),
                               "listed": (obs.get("pluginkit") or {}).get(n, {}).get("listed")}
                           for n, e in (obs.get("extensions") or {}).items()}}


def compare_ios(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    return both(ios_side, ios_shape, ref, cand, case, "iOS install")


# ── Android ─────────────────────────────────────────────────────────────────


def android_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    for lib in expect.get("native_libs", []):
        if lib not in obs.get("apk_native_libs", []):
            reasons.append(f"{side}: the APK does not carry {lib}")
    exported = obs.get("jni_exports") or {}
    for lib, names in sorted(expect.get("jni_exports", {}).items()):
        have = set(exported.get(lib, []))
        missing = [n for n in names if n not in have]
        if missing:
            reasons.append(f"{side}: {lib} does not export JNI symbol {missing[0]}"
                           + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else "")
                           + f": {missing[:6]}")
    abi = expect.get("abi")
    if abi and obs.get("abi") != abi:
        reasons.append(f"{side}: the device ABI is {obs.get('abi')!r}, case expects {abi!r}")
    install = obs.get("install") or {}
    if install.get("exit") != 0:
        reasons.append(f"{side}: adb install exited {install.get('exit')}: {install.get('output_tail', '')[:160]}")
        return reasons
    if not (obs.get("start") or {}).get("ok"):
        reasons.append(f"{side}: the activity did not start")
    if not obs.get("alive_after_settle"):
        reasons.append(f"{side}: the app process is not alive after the settle time")
    if obs.get("fatal"):
        reasons.append(f"{side}: fatal exception logged: {' | '.join(obs['fatal'][:3])[:300]}")
    lines = obs.get("tag_lines", [])
    markers = expect.get("markers", [])
    if not markers:
        reasons.append(f"{side}: the case names no log marker (vacuous)")
    for marker in markers:
        if not any(re.search(marker, ln) for ln in lines):
            reasons.append(f"{side}: log marker {marker!r} is missing")
    return reasons


def android_shape(obs: dict[str, Any], markers: list[str] | None = None) -> dict[str, Any]:
    lines = obs.get("tag_lines", [])
    return {"apk_native_libs": obs.get("apk_native_libs"), "jni_exports": obs.get("jni_exports"),
            "install": (obs.get("install") or {}).get("exit"),
            "started": (obs.get("start") or {}).get("ok"), "alive": obs.get("alive_after_settle"),
            "fatal": bool(obs.get("fatal")),
            "markers": {m: any(re.search(m, ln) for ln in lines) for m in (markers or [])}}


def compare_android(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    markers = case.get("expect", {}).get("markers", [])
    return both(android_side, lambda o: android_shape(o, markers), ref, cand, case, "Android run")


ph.register_lane("linux-elf-static", (OBS,), compare_linux)
ph.register_lane("windows-sdk-pe", (OBS,), compare_windows)
ph.register_lane("web-wam-wclap", (OBS,), compare_web)
ph.register_lane("ios-install-registration", (OBS,), compare_ios)
ph.register_lane("android-host-checks", (OBS,), compare_android)


# ── seeded controls ─────────────────────────────────────────────────────────
#
# Every control is a fault the candidate's runner applies to a copy of its
# artifact (case["controls"][<key>] names the fault string for that case), so
# the lane must reject a real defect in real bytes, for the stated reason.


def reasons_contain(*texts: str):
    return lambda result: any(t in r for r in result.get("reasons", []) for t in texts)


def prep(lane: str, key: str):
    def prepare(case, source_root, work_dir=None):
        faults = (case.get("controls") or {}).get(key)
        if lane not in case["lanes"] or work_dir is None or not faults:
            return None
        faults = faults if isinstance(faults, list) else [faults]
        args = case.get("args", {})
        extra = [x for f in faults for x in ("--fault", f)]
        return dict(case, args={**args, "fault": [*args.get("fault", []), *extra, "--work", str(work_dir)]}), {}
    return prepare


CONTROLS = {
    "linux-elf-wrong-machine": ("linux-elf-static", "machine", ("case expects",)),
    "linux-elf-entry-export-missing": ("linux-elf-static", "rename_export", ("is missing export",)),
    "linux-elf-forbidden-library-linked": ("linux-elf-static", "rename_needed", ("links forbidden library",)),
    "linux-elf-required-path-missing": ("linux-elf-static", "drop", ("is missing",)),
    "windows-pe-wrong-machine": ("windows-sdk-pe", "machine", ("case expects",)),
    "windows-pe-export-missing": ("windows-sdk-pe", "rename_export", ("is missing export",)),
    "windows-pe-required-import-missing": ("windows-sdk-pe", "rename_import", ("does not link required library",)),
    "web-export-missing": ("web-wam-wclap", "rename_export", ("is missing export",)),
    "web-node-check-rejects-broken-module": ("web-wam-wclap", "rename_export", ("node check",)),
    "ios-extension-seal-broken": ("ios-install-registration", "unsigned_edit", ("codesign",)),
    "ios-extension-point-wrong": ("ios-install-registration", "extension_point", ("extension point is",)),
    "ios-extension-not-embedded": ("ios-install-registration", "drop_extension", ("not embedded",)),
    "android-native-library-dropped": ("android-host-checks", "drop_native_lib", ("does not carry",)),
    "android-jni-export-missing": ("android-host-checks", "rename_jni_export", ("does not export JNI symbol",)),
    "android-app-does-not-start-cleanly": ("android-host-checks", "drop_native_lib",
                                           ("log marker", "fatal exception", "not alive")),
}

ph.register_controls(rerun={
    name: {"lane": lane, "prepare": prep(lane, key), "bites": reasons_contain(*bites)}
    for name, (lane, key, bites) in CONTROLS.items()})
