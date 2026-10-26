"""Artifact lanes: the static shape of a built plug-in or app artifact.

Registered with the parity harness through ``--lane-module
tools/parity/lanes/artifact.py``. Each side's implementation runs
``artifact_runner.py`` over its artifact, which writes
artifact-observation.json without executing the artifact. These lanes compare
those files against the case's expectations and across sides:

  artifact-slices     every Mach-O in the artifact carries exactly the case's
                      architectures, each slice is built for the case's
                      platform; every ELF image has the case's machine; every
                      WebAssembly module has a valid header
  artifact-runtimes   each runtime the case names is bundled, is a Mach-O with
                      exactly the case's architectures, and every slice of the
                      main image loads it
  artifact-bundle     the bundle's Info.plist reads, its main image exists,
                      every path the case requires is present; the file list
                      and bytes agree across sides
  artifact-signature  the signature the case expects verifies: the sealed
                      bundle, or each named slice of the main image alone (a
                      development build carries only the linker's signature)
  artifact-linkfloor  per architecture of the main image: no symbol of a
                      forbidden family and no forbidden load command; at least
                      one symbol of each required family; each load command
                      the case requires exactly once appears exactly once

A case selects only the lanes that apply to its artifact (a lone ELF library
has no bundle, a development build may carry no seal), so every control of a
selected lane has a case to bite on.

Platforms are LC_BUILD_VERSION numbers: 1 macOS, 2 iOS, 7 iOS simulator.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import parity_harness as ph

SCHEMA = "pulp.artifact-lane.observation.v1"
OBS = "artifact-observation.json"
lane_result = ph.lane_result


def observation(directory: Path) -> dict[str, Any]:
    doc = ph.load_json(directory / OBS)
    if doc.get("schema") != SCHEMA:
        raise ph.HarnessError(f"{directory}: observation schema {doc.get('schema')!r}")
    return doc


def binaries(obs: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [f for f in obs["files"] if f.get("kind") == kind]


def slices_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> tuple[list[str], dict[str, Any]]:
    expect = case.get("expect", {})
    reasons, shape = [], {}
    want = sorted(expect.get("archs", []))
    platform = expect.get("platform")
    exempt = [re.compile(p) for p in expect.get("slice_exempt", [])]
    machos = binaries(obs, "macho")
    elves = binaries(obs, "elf")
    wasms = binaries(obs, "wasm")
    if not (machos or elves or wasms):
        reasons.append(f"{side}: no binary image in the artifact (vacuous)")
    if obs.get("main_image") is None and expect.get("main_image", True):
        reasons.append(f"{side}: no main image")
    for f in machos:
        if any(p.search(f["path"]) for p in exempt):
            continue
        shape[f["path"]] = f["archs"]
        if f["archs"] != want:
            reasons.append(f"{side}: {f['path']} carries {f['archs'] or 'no readable'} slices, case expects {want}")
        for arch, plats in (f.get("platforms") or {}).items():
            if platform is not None and plats != [platform]:
                reasons.append(f"{side}: {f['path']} [{arch}] is built for platform {plats}, "
                               f"case expects {platform}")
    machine = expect.get("elf_machine")
    for f in elves:
        shape[f["path"]] = [f["machine"], f["class"]]
        if machine and f["machine"] != machine:
            reasons.append(f"{side}: {f['path']} is an ELF image for {f['machine']}, case expects {machine}")
    for f in wasms:
        shape[f["path"]] = ["wasm"]
    main = obs.get("main_image")
    main_kind = next((f.get("kind") for f in obs["files"] if f["path"] == main), None)
    if main is not None and main_kind not in ("macho", "elf", "wasm"):
        reasons.append(f"{side}: main image {main} is not a readable binary ({main_kind})")
    return reasons, shape


def compare_slices(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    r1, s1 = slices_side(case, observation(ref), "reference")
    r2, s2 = slices_side(case, observation(cand), "candidate")
    reasons = r1 + r2
    if s1 != s2:
        reasons += ["slice map differs: " + x for x in ph.json_differences(s1, s2, "/images")]
    return lane_result("fail" if reasons else "pass", reasons=reasons, slices=s1)


def bundle_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    paths = {f["path"] for f in obs["files"]}
    dirs = {p.rsplit("/", i)[0] for p in paths for i in range(1, p.count("/") + 1)}
    if expect.get("bundle", True):
        plist = obs.get("plist")
        if plist is None:
            reasons.append(f"{side}: no Info.plist")
        elif "unreadable" in plist:
            reasons.append(f"{side}: Info.plist does not parse: {plist['unreadable']}")
        elif obs.get("main_image") is None:
            reasons.append(f"{side}: CFBundleExecutable {plist.get('CFBundleExecutable')!r} does not exist")
        for key, value in expect.get("plist", {}).items():
            if (plist or {}).get(key) != value:
                reasons.append(f"{side}: Info.plist {key} is {(plist or {}).get(key)!r}, case expects {value!r}")
    for rel in expect.get("required", []):
        if rel not in paths and rel not in dirs:
            reasons.append(f"{side}: required bundle path {rel} is missing")
    return reasons


def compare_bundle(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    o1, o2 = observation(ref), observation(cand)
    reasons = bundle_side(case, o1, "reference") + bundle_side(case, o2, "candidate")
    f1 = {f["path"]: f.get("sha256", f.get("symlink")) for f in o1["files"]}
    f2 = {f["path"]: f.get("sha256", f.get("symlink")) for f in o2["files"]}
    if sorted(f1) != sorted(f2):
        reasons.append(f"file list differs: only reference {sorted(set(f1) - set(f2))[:5]}, "
                       f"only candidate {sorted(set(f2) - set(f1))[:5]}")
    else:
        changed = sorted(p for p in f1 if f1[p] != f2[p])
        if changed:
            reasons.append(f"{len(changed)} files differ in bytes: {changed[:5]}")
    return lane_result("fail" if reasons else "pass", reasons=reasons, files=len(f1), plist=o1.get("plist"))


def signature_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    # "bundle": a shipped, sealed bundle (codesign --verify --deep --strict);
    # "image": a development build, whose main image carries only the linker's
    # ad-hoc signature (the linker signs the arm64 slice of a universal image
    # and leaves x86_64 unsigned, so signed_archs names the slices that must
    # verify) and whose bundle resources are not sealed.
    signed = expect.get("signed")
    if not signed:
        reasons.append(f"{side}: the case selects the signature lane but expects no signature (vacuous)")
    if signed == "bundle":
        cs = obs.get("codesign") or {}
        if cs.get("exit") != 0:
            reasons.append(f"{side}: codesign (bundle) verification exited {cs.get('exit')}: "
                           f"{(cs.get('stderr_tail') or '').strip()[:160]}")
    elif signed:
        slices = obs.get("codesign_image") or {}
        for arch in expect.get("signed_archs", expect.get("archs", [])):
            cs = slices.get(arch) or {}
            if cs.get("exit") != 0:
                reasons.append(f"{side}: codesign (image, {arch}) verification exited {cs.get('exit')}: "
                               f"{(cs.get('stderr_tail') or '').strip()[:160]}")
    return reasons


def compare_signature(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    reasons = (signature_side(case, observation(ref), "reference")
               + signature_side(case, observation(cand), "candidate"))
    return lane_result("fail" if reasons else "pass", reasons=reasons)


def runtimes_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> list[str]:
    expect = case.get("expect", {})
    reasons = []
    names = expect.get("runtimes", [])
    if not names:
        reasons.append(f"{side}: the case selects the runtimes lane but names no runtime (vacuous)")
    files = {f["path"]: f for f in obs["files"]}
    for rel in names:
        f = files.get(rel)
        if f is None:
            reasons.append(f"{side}: bundled runtime {rel} is missing")
            continue
        if f.get("kind") != "macho":
            reasons.append(f"{side}: bundled runtime {rel} is not a Mach-O image")
        elif f["archs"] != sorted(expect.get("archs", [])):
            reasons.append(f"{side}: bundled runtime {rel} carries {f['archs']} slices, "
                           f"case expects {sorted(expect.get('archs', []))}")
        leaf = Path(rel).name
        for arch, data in sorted((obs.get("slices") or {}).items()):
            if not any(Path(x).name == leaf for x in data["load_commands"]):
                reasons.append(f"{side}: [{arch}] the main image does not load bundled runtime {leaf}")
    return reasons


def compare_runtimes(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    reasons = (runtimes_side(case, observation(ref), "reference")
               + runtimes_side(case, observation(cand), "candidate"))
    return lane_result("fail" if reasons else "pass", reasons=reasons)


def linkfloor_side(case: dict[str, Any], obs: dict[str, Any], side: str) -> tuple[list[str], dict[str, Any]]:
    expect = case.get("expect", {})
    reasons, counts = [], {}
    slices = obs.get("slices") or {}
    if not slices:
        reasons.append(f"{side}: no Mach-O slice of the main image to scan (vacuous)")
    for arch, data in sorted(slices.items()):
        fam = data["families"]
        counts[arch] = fam
        for family in expect.get("forbid", []):
            if fam.get(family, 0):
                reasons.append(f"{side}: [{arch}] {fam[family]} symbols of forbidden family {family}")
        for family in expect.get("require", []):
            if not fam.get(family, 0):
                reasons.append(f"{side}: [{arch}] no symbol of required family {family}")
        loads = data["load_commands"]
        for needle in expect.get("forbid_load", []):
            hits = [x for x in loads if needle in x]
            if hits:
                reasons.append(f"{side}: [{arch}] forbidden load command {hits[0]}")
        for needle in expect.get("require_load_once", []):
            n = sum(1 for x in loads if needle in x)
            if n != 1:
                reasons.append(f"{side}: [{arch}] {n} load commands match {needle}, case requires exactly one")
    for family in set(expect.get("forbid", [])) | set(expect.get("require", [])):
        if not any(family in fam for fam in counts.values()) and counts:
            reasons.append(f"{side}: scan fixture names no family {family}")
    return reasons, counts


def compare_linkfloor(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    r1, c1 = linkfloor_side(case, observation(ref), "reference")
    r2, c2 = linkfloor_side(case, observation(cand), "candidate")
    reasons = r1 + r2
    if c1 != c2:
        reasons += ["family counts differ: " + x for x in ph.json_differences(c1, c2, "/slices")]
    return lane_result("fail" if reasons else "pass", reasons=reasons, families=c1)


ph.register_lane("artifact-slices", (OBS,), compare_slices)
ph.register_lane("artifact-runtimes", (OBS,), compare_runtimes)
ph.register_lane("artifact-bundle", (OBS,), compare_bundle)
ph.register_lane("artifact-signature", (OBS,), compare_signature)
ph.register_lane("artifact-linkfloor", (OBS,), compare_linkfloor)


# ── seeded controls ─────────────────────────────────────────────────────────


def reasons_contain(text: str):
    return lambda result: any(text in r for r in result.get("reasons", []))


def m_drop_file(d: Path) -> bool:
    path = d / OBS
    obs = ph.load_json(path)
    if len(obs["files"]) < 2:
        return False
    obs["files"] = obs["files"][:-1]
    ph.write_json(path, obs)
    return True


def m_family_appears(d: Path, case: dict[str, Any]) -> bool:
    forbid = case.get("expect", {}).get("forbid", [])
    path = d / OBS
    obs = ph.load_json(path)
    if not forbid or not obs.get("slices"):
        return False
    arch = sorted(obs["slices"])[-1]
    obs["slices"][arch]["families"][forbid[0]] = 1
    ph.write_json(path, obs)
    return True


ph.register_controls(artifact={
    "artifact-bundle-file-dropped": {"lane": "artifact-bundle", "candidate": m_drop_file,
                                     "bites": reasons_contain("file list differs")},
    "artifact-linkfloor-forbidden-family-in-one-slice": {"lane": "artifact-linkfloor", "candidate": m_family_appears,
                                                         "bites": reasons_contain("forbidden family")},
})


def fault(case: dict[str, Any], work_dir, *names: str) -> dict[str, Any]:
    args = case.get("args", {})
    extra = [x for n in names for x in ("--fault", n)]
    return dict(case, args={**args, "fault": [*args.get("fault", []), *extra, "--work", str(work_dir)]})


def prep_fault(lane: str, name: str, need=lambda case: True):
    def prepare(case, source_root, work_dir=None):
        if lane not in case["lanes"] or work_dir is None or not need(case):
            return None
        return fault(case, work_dir, name(case) if callable(name) else name), {}
    return prepare


def prep_swap_floor(case, source_root, work_dir=None):
    other = case.get("controls", {}).get("swap_floor")
    if "artifact-linkfloor" not in case["lanes"] or not other:
        return None
    return dict(case, args={**case["args"], "artifact": ["--artifact", "{root:" + other + "}"]}), {}


expects = lambda key: (lambda case: bool(case.get("expect", {}).get(key)))  # noqa: E731

ph.register_controls(rerun={
    "artifact-wrong-arch-slice": {"lane": "artifact-slices", "prepare": prep_fault("artifact-slices", "wrong-arch"),
                                  "bites": reasons_contain("case expects")},
    "artifact-runtime-wrong-arch-slice": {
        "lane": "artifact-runtimes",
        "prepare": prep_fault("artifact-runtimes", lambda c: "nested-wrong-arch:" + c["expect"]["runtimes"][0]),
        "bites": reasons_contain("bundled runtime")},
    "artifact-runtime-missing": {
        "lane": "artifact-runtimes",
        "prepare": prep_fault("artifact-runtimes", lambda c: "drop:" + c["expect"]["runtimes"][0]),
        "bites": reasons_contain("is missing")},
    "artifact-missing-bundle-resource": {
        "lane": "artifact-bundle",
        "prepare": prep_fault("artifact-bundle", lambda c: "drop:" + c["expect"]["required"][0], expects("required")),
        "bites": reasons_contain("is missing")},
    "artifact-signature-broken": {
        "lane": "artifact-signature", "prepare": prep_fault("artifact-signature", "unsigned-edit"),
        "bites": reasons_contain("codesign")},
    # Bites only on the case's own floor, not on the cross-side difference
    # the swap also causes: the floor must reject the other link by itself.
    "artifact-linkfloor-swapped-for-other-link": {
        "lane": "artifact-linkfloor", "prepare": prep_swap_floor,
        "bites": lambda r: any("of forbidden family" in x or "of required family" in x for x in r.get("reasons", []))},
})
