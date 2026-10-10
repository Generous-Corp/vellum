#!/usr/bin/env python3
"""Host-compatibility lane: built plug-in bundles inside a real host process.

This lane is separate from screenshot parity. A case loads one or more built
plug-in bundles (CLAP, VST3, AU v2) into a host probe process, or runs a
validator over a bundle, or launches a standalone app, and records what a host
observes. The same corpus runs through a REFERENCE and a CANDIDATE
implementation; an implementation is a JSON description of the probe, the
validators and the bundles under test (``implementations/*host*.json``), so
the harness links and imports neither side.

Lanes, all required to ``pass`` for an accepting receipt:

  load        every plug-in loads and prepares; format, name, editor presence,
              latency and parameter count agree across sides
  editor      every editor opens attached, draws over its whole window (no host
              backdrop pixel, colour floor), takes the GPU path when the case
              expects it, resizes, closes and reopens; the lifecycle and every
              read-back frame agree across sides, pixel for pixel
  audio       output with editors closed, open and closed again is byte
              identical (the UI cannot perturb DSP), audible and finite; each
              render agrees across sides byte for byte
  coexist     two or more distinct plug-ins in one process render exactly what
              each renders alone, draw exactly what each draws alone, and load
              the same set of runtime images (the dyld census) on both sides
  validator   an external validator exits 0 with its success marker on both sides
  standalone  the standalone app starts with the null audio device, renders a
              first frame that is not blank and exits 0; frames agree across sides

A lane is ``pass``, ``fail``, ``blocked`` (a step failed or skipped, a tool or
artifact is missing, the read-back is blind) or ``not_selected``. Only ``pass``
counts. Things this host cannot run are listed as ``declared_gaps`` in the
corpus and copied into every receipt; they are never reported as passing.

``calibrate`` runs the corpus twice through one implementation (the positive
must accept), then every seeded negative control, and exits non-zero unless the
positive accepts and every control is rejected by the lane it targets.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

import parity_harness as core  # noqa: E402
from parity_harness import HarnessError, lane_result, load_json, sha256_bytes, sha256_file, write_json  # noqa: E402
from vellum_png import RgbaImage, decode_png_bytes, encode_png  # noqa: E402

HOST_CORPUS_SCHEMA = "vellum.parity.host-corpus.v1"
HOST_IMPLEMENTATION_SCHEMA = "vellum.parity.host-implementation.v1"
HOST_RECEIPT_SCHEMA = "vellum.parity.host-receipt.v1"
HOST_CALIBRATION_SCHEMA = "vellum.parity.host-calibration.v1"
OBSERVATION_SCHEMA = "pulp.host-lane.observation.v1"
HOST_LANES = ("load", "editor", "audio", "coexist", "validator", "standalone")
CASE_KINDS = {"probe": {"load", "editor", "audio", "coexist"}, "validator": {"validator"},
              "standalone": {"standalone"}}

# The probe paints its window this colour; any pixel still this colour after
# an editor attached is a place the editor did not draw.
BACKDROP = (255, 0, 255)
MIN_COLORS = 8           # a real editor frame has more than a handful of colours
MIN_RMS = 1e-4           # an audible render, not silence
ADAPTER_LINE = re.compile(r"\[plugin-gpu-host\] adapter mode=(\S+) use_gpu=(true|false)")


# ── documents ───────────────────────────────────────────────────────────────


def validate_corpus(corpus: Any) -> list[dict[str, Any]]:
    if not isinstance(corpus, dict) or corpus.get("schema") != HOST_CORPUS_SCHEMA:
        raise HarnessError(f"host corpus schema must be {HOST_CORPUS_SCHEMA}")
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise HarnessError("host corpus needs a cases list")
    seen = set()
    for case in cases:
        cid = case.get("case_id")
        if not cid or cid in seen:
            raise HarnessError(f"case_id missing or duplicated: {cid!r}")
        seen.add(cid)
        kind = case.get("kind")
        if kind not in CASE_KINDS:
            raise HarnessError(f"{cid}: kind must be one of {sorted(CASE_KINDS)}")
        lanes = case.get("lanes", [])
        bad = [lane for lane in lanes if lane not in CASE_KINDS[kind]]
        if bad:
            raise HarnessError(f"{cid}: lanes {bad} do not apply to a {kind} case")
        if kind == "probe" and not case.get("plugins"):
            raise HarnessError(f"{cid}: a probe case names its plugins")
        if kind in ("validator", "standalone") and not case.get("artifact"):
            raise HarnessError(f"{cid}: a {kind} case names its artifact")
        if kind == "validator" and not case.get("validator"):
            raise HarnessError(f"{cid}: a validator case names its validator")
    return cases


def validate_implementation(impl: Any) -> dict[str, Any]:
    if not isinstance(impl, dict) or impl.get("schema") != HOST_IMPLEMENTATION_SCHEMA:
        raise HarnessError(f"host implementation schema must be {HOST_IMPLEMENTATION_SCHEMA}")
    for key in ("name", "repository", "build_mode", "tools", "artifacts", "probe_argv"):
        if key not in impl:
            raise HarnessError(f"host implementation missing {key}")
    return impl


def resolve(impl: dict[str, Any], tools: dict[str, str], artifacts: dict[str, str]) -> dict[str, Any]:
    """Tool and artifact paths resolve against the invoking directory."""
    out = copy.deepcopy(impl)
    out["tools"] = {n: str(Path(p).resolve()) for n, p in {**impl["tools"], **tools}.items()}
    out["artifacts"] = {n: str(Path(p).resolve()) for n, p in {**impl["artifacts"], **artifacts}.items()}
    return out


def tree_sha256(path: Path) -> str | None:
    """Content hash of a bundle directory (or a file): every regular file's
    relative path and bytes, in sorted order. Symlinks are recorded by target."""
    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        return None
    entries = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path).as_posix()
        if item.is_symlink():
            entries.append(f"L {rel} {os.readlink(item)}")
        elif item.is_file():
            entries.append(f"F {rel} {sha256_file(item)}")
    return sha256_bytes("\n".join(entries).encode())


def build_info(path: Path) -> dict[str, Any] | None:
    found = sorted(path.rglob("pulp-build-info.json")) if path.is_dir() else []
    if not found:
        return None
    info = load_json(found[0])
    pins = info.get("runtime_pins") or {}
    return {
        "source_git_sha": (info.get("product") or {}).get("source_git_sha"),
        "source_git_dirty": (info.get("product") or {}).get("source_git_dirty"),
        "build": info.get("build"),
        "sdk_version": (info.get("pulp_sdk") or {}).get("version"),
        "skia": {k: (pins.get("skia") or {}).get(k) for k in ("release", "commit", "graphite_backend")},
    }


def linked_runtimes(path: Path) -> list[str]:
    """Install names of GPU-runtime dylibs the bundle's executables link."""
    names: set[str] = set()
    macos = path / "Contents" / "MacOS"
    for exe in sorted(macos.iterdir()) if macos.is_dir() else []:
        if not exe.is_file() or exe.suffix == ".dylib":
            continue
        try:
            listing = subprocess.run(["otool", "-L", str(exe)], capture_output=True, text=True,
                                     encoding="utf-8", timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in listing.splitlines()[1:]:
            name = line.strip().split(" (")[0]
            if any(t in name.lower() for t in ("wgpu", "dawn", "webgpu", "skia")):
                names.add(name)
    return sorted(names)


def host_identity(impl: dict[str, Any], source_sha: str | None) -> dict[str, Any]:
    tools = {name: Path(path) for name, path in impl["tools"].items()}
    identity = core.implementation_identity(dict(impl, source_sha=source_sha), tools)
    identity["build_mode"] = impl.get("build_mode")
    identity["artifacts"] = {}
    for name, path in sorted(impl["artifacts"].items()):
        p = Path(path)
        identity["artifacts"][name] = {"path": path, "tree_sha256": tree_sha256(p),
                                       "build_info": build_info(p),
                                       "linked_runtimes": linked_runtimes(p) if p.is_dir() else []}
    identity["host"] = {"system": platform.system(), "release": platform.release(),
                        "mac_ver": platform.mac_ver()[0], "machine": platform.machine()}
    return identity


def identity_reasons(identity: dict[str, Any], side: str) -> list[str]:
    reasons = core.identity_errors(identity, side)
    for name, art in identity.get("artifacts", {}).items():
        if not art.get("tree_sha256"):
            reasons.append(f"{side}: artifact {name} is missing ({art.get('path')})")
            continue
        info = art.get("build_info")
        built = (info or {}).get("source_git_sha")
        if built and identity.get("source_sha") and built != identity["source_sha"]:
            reasons.append(f"{side}: artifact {name} was built from {built}, "
                           f"identity says {identity['source_sha']}")
        if info and info.get("source_git_dirty"):
            reasons.append(f"{side}: artifact {name} was built from a dirty tree")
    return reasons


def artifact_key(identity: dict[str, Any]) -> tuple:
    return tuple(sorted((n, a.get("tree_sha256")) for n, a in identity.get("artifacts", {}).items()))


def acceptance(mode: str, results: list[dict[str, Any]], reference: dict[str, Any],
               candidate: dict[str, Any]) -> dict[str, Any]:
    verdict = core.acceptance(mode, results, reference, candidate, require_identical_inputs=True)
    reasons = list(verdict["reasons"])
    reasons += [r for side, ident in (("reference", reference), ("candidate", candidate))
                for r in identity_reasons(ident, side) if r not in reasons]
    same_bundles = artifact_key(reference) == artifact_key(candidate)
    if mode == "parity" and same_bundles:
        reasons.append("parity mode: reference and candidate test the same bundle bytes")
    if mode == "calibration" and not same_bundles:
        reasons.append("calibration mode: reference and candidate bundles differ")
    return dict(verdict, accepting=not reasons, reasons=reasons)


# ── running one side ────────────────────────────────────────────────────────


def expand(arg: str, values: dict[str, str]) -> str:
    return re.sub(r"\{([a-z_:\-.0-9]+)\}", lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), arg)


def run_step(step_id: str, argv: list[str], cwd: Path, env: dict[str, str] | None = None,
             timeout: int = 600, planted_skip: bool = False) -> dict[str, Any]:
    record: dict[str, Any] = {"id": step_id, "argv": argv, "env": env or {}}
    if planted_skip:
        argv = [sys.executable, "-c", f"import sys; sys.exit({core.SKIP_EXIT})"]
        record["planted_skip"] = True
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, env={**os.environ, **(env or {})})
        record["exit"] = result.returncode
        (cwd / f"{step_id}.stdout").write_text(result.stdout, encoding="utf-8")
        (cwd / f"{step_id}.stderr").write_text(result.stderr, encoding="utf-8")
        record["stderr_tail"] = result.stderr[-1500:]
    except (OSError, subprocess.TimeoutExpired) as error:
        record["exit"] = None
        record["error"] = str(error)
    record["seconds"] = round(time.monotonic() - started, 3)
    record["status"] = ("ok" if record["exit"] == 0 else
                        "skipped" if record["exit"] == core.SKIP_EXIT else "failed")
    return record


def probe_argv(impl: dict[str, Any], plugins: list[str], out: Path, extra: list[str]) -> list[str]:
    values = {f"tool:{n}": p for n, p in impl["tools"].items()}
    values["out"] = str(out)
    argv: list[str] = []
    for arg in impl["probe_argv"]:
        if arg == "@plugins":
            for name in plugins:
                argv += ["--plugin", impl["artifacts"].get(name, f"<undeclared artifact {name}>")]
        else:
            argv.append(expand(arg, values))
    return argv + extra


def run_case(case: dict[str, Any], impl: dict[str, Any], out: Path, *,
             env: dict[str, str] | None = None, extra: list[str] | None = None,
             plugins: list[str] | None = None, skip: bool = False) -> dict[str, Any]:
    """Run one case on one side; returns the run record. Observations stay in `out`."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    kind = case["kind"]
    steps = []
    if kind == "probe":
        names = plugins if plugins is not None else case["plugins"]
        steps.append(run_step("probe", probe_argv(impl, names, out / "together", extra or []), out, env,
                              planted_skip=skip))
        if "coexist" in case["lanes"] and steps[-1]["status"] == "ok":
            for i, name in enumerate(names):
                steps.append(run_step(f"solo-{i}", probe_argv(impl, [name], out / f"solo-{i}", extra or []),
                                      out, env))
                if steps[-1]["status"] != "ok":
                    break
    elif kind == "validator":
        tool = impl["tools"].get(case["validator"])
        template = (impl.get("validators") or {}).get(case["validator"])
        if not tool or not Path(tool).is_file() or template is None:
            steps.append({"id": "validator", "status": "skipped", "exit": None,
                          "error": f"validator {case['validator']} is not available to {impl['name']}"})
        else:
            values = {f"tool:{n}": p for n, p in impl["tools"].items()}
            values.update({"artifact": impl["artifacts"].get(case["artifact"], ""), "out": str(out)})
            steps.append(run_step("validator", [expand(a, values) for a in template], out, env,
                                  timeout=int(case.get("timeout", 900)), planted_skip=skip))
    else:  # standalone
        app = Path(impl["artifacts"].get(case["artifact"], "/nonexistent"))
        exe = app / "Contents" / "MacOS" / app.stem
        run_env = {"PULP_AUDIO_DEVICE": "null", "PULP_SCREENSHOT": str(out / "standalone.png"), **(env or {})}
        steps.append(run_step("standalone", [str(exe)], out, run_env, timeout=int(case.get("timeout", 120)),
                              planted_skip=skip))
    return {"steps": steps, "inputs": {"case": sha256_bytes(json.dumps(case, sort_keys=True).encode()),
                                       "plugins": plugins if plugins is not None else case.get("plugins"),
                                       "artifact": case.get("artifact"),
                                       "extra_args": extra or [], "env": env or {}}}


# ── observations ────────────────────────────────────────────────────────────


def read_observation(directory: Path) -> dict[str, Any]:
    path = directory / "host-observation.json"
    if not path.is_file():
        raise HarnessError(f"missing {path}")
    doc = load_json(path)
    if doc.get("schema") != OBSERVATION_SCHEMA:
        raise HarnessError(f"{path}: schema {doc.get('schema')!r}")
    return doc


def adapter_decisions(stderr: str) -> list[dict[str, Any]]:
    return [{"mode": m.group(1), "use_gpu": m.group(2) == "true"} for m in ADAPTER_LINE.finditer(stderr)]


def audio_digest(render: dict[str, Any], directory: Path) -> tuple[str | None, str | None]:
    """Hash the render file itself; the reported hash is only cross-checked."""
    path = directory / Path(render.get("file", "")).name
    if not path.is_file():
        return None, f"missing audio file {path.name}"
    digest = sha256_file(path)
    if digest != render.get("sha256"):
        return digest, f"{path.name}: file hash {digest[:12]} != reported {str(render.get('sha256'))[:12]}"
    return digest, None


def frame_pixels(frame: dict[str, Any], directory: Path) -> tuple[RgbaImage | None, str | None]:
    if not frame.get("read"):
        return None, "read-back unavailable"
    path = directory / Path(frame.get("png", "")).name
    if not path.is_file():
        return None, f"missing frame {path.name}"
    return decode_png_bytes(path.read_bytes()), None


def frame_stats(image: RgbaImage) -> dict[str, int]:
    px = image.pixels
    colors = set()
    backdrop = 0
    for i in range(0, len(px), 4):
        if (px[i], px[i + 1], px[i + 2]) == BACKDROP:
            backdrop += 1
        if len(colors) < 4096:
            colors.add(px[i:i + 3])
    return {"width": image.width, "height": image.height, "backdrop_pixels": backdrop,
            "unique_colors": len(colors)}


def frames_of(obs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    keyed = []
    for event in obs["events"]:
        if "frame" in event:
            keyed.append((f"plugin{event['plugin']}/{event['event']}/{event.get('open', '-')}", event["frame"]))
    return keyed


def plugin_index(key: str) -> int:
    return int(key.split("/", 1)[0][len("plugin"):])


def lifecycle(obs: dict[str, Any]) -> list[tuple]:
    sig = []
    for e in obs["events"]:
        if e["event"] == "create":
            sig.append(("create", e["plugin"], e.get("open"), e["attached"], e.get("width"), e.get("height"),
                        e.get("resizable")))
        elif e["event"] == "resize":
            sig.append(("resize", e["plugin"], tuple(e["requested"]), e["accepted"], tuple(e["size"])))
        elif e["event"] == "close":
            sig.append(("close", e["plugin"], e.get("open"), e["had_editor"]))
    return sig


def normalize_census(images: list[str], artifacts: dict[str, str]) -> list[str]:
    """Express loaded image paths relative to the declared artifacts, so two
    checkouts that load the same topology compare equal."""
    out = []
    roots = sorted(((str(Path(p)), n) for n, p in artifacts.items()), key=lambda t: -len(t[0]))
    for image in images:
        for root, name in roots:
            if image.startswith(root + "/"):
                out.append(f"<{name}>/{image[len(root) + 1:]}")
                break
        else:
            out.append(f"<external>/{Path(image).name}")
    return sorted(out)


# ── lane comparators ────────────────────────────────────────────────────────


def side_block(run: dict[str, Any]) -> str | None:
    """Why a side produced no comparable observation. A validator that ran and
    reported failure is a result (the lane fails), not a blocked step."""
    for step in run["steps"]:
        if step["id"] == "validator" and step["status"] == "failed" and step.get("exit") is not None:
            continue
        if step["status"] != "ok":
            return f"step {step['id']} {step['status']} (exit {step.get('exit')}) {step.get('error', '')}".strip()
    return None


def compare_load(case, ref, cand, impls) -> dict[str, Any]:
    keys = ("format", "name", "has_editor", "latency_samples", "parameters")
    reasons = []
    rows = {}
    for side, (directory, _) in (("reference", ref), ("candidate", cand)):
        obs = read_observation(directory / "together")
        rows[side] = [{k: p[k] for k in keys} for p in obs["plugins"]]
        if len(obs["plugins"]) != len(case["plugins"]):
            reasons.append(f"{side}: loaded {len(obs['plugins'])} of {len(case['plugins'])} plugins")
        if case.get("expect", {}).get("editor") and not all(p["has_editor"] for p in obs["plugins"]):
            reasons.append(f"{side}: a plugin reports no editor")
    if rows["reference"] != rows["candidate"]:
        reasons += core.json_differences(rows["reference"], rows["candidate"], "/plugins")
    return lane_result("fail" if reasons else "pass", plugins=rows["reference"], reasons=reasons)


def editor_side(case: dict[str, Any], directory: Path, stderr: str, side: str) -> tuple[list[str], dict]:
    obs = read_observation(directory)
    expect = case.get("expect", {})
    opens = int(obs["settings"]["opens"])
    reasons: list[str] = []
    detail: dict[str, Any] = {"frames": {}}
    creates = [e for e in obs["events"] if e["event"] == "create"]
    editors = [p["index"] for p in obs["plugins"] if p["has_editor"]]
    if not editors:
        reasons.append(f"{side}: no plugin has an editor (vacuous)")
    for idx in editors:
        mine = [e for e in creates if e["plugin"] == idx]
        if len(mine) != opens:
            reasons.append(f"{side}: plugin{idx} opened {len(mine)} of {opens} times")
        for e in mine:
            if not e["attached"] or not e.get("width") or not e.get("height"):
                reasons.append(f"{side}: plugin{idx} open {e.get('open')} did not attach")
        closes = [e for e in obs["events"] if e["event"] == "close" and e["plugin"] == idx]
        if len(closes) != opens or not all(c["had_editor"] for c in closes):
            reasons.append(f"{side}: plugin{idx} closed {sum(c['had_editor'] for c in closes)} of {opens} editors")
    blind = 0
    for key, frame in frames_of(obs):
        image, why = frame_pixels(frame, directory)
        if image is None:
            reasons.append(f"{side}: {key}: {why}")
            blind += why == "read-back unavailable"
            continue
        stats = frame_stats(image)
        detail["frames"][key] = stats
        if stats["backdrop_pixels"]:
            reasons.append(f"{side}: {key}: {stats['backdrop_pixels']} host-backdrop pixels (editor did not cover)")
        if stats["unique_colors"] < MIN_COLORS:
            reasons.append(f"{side}: {key}: {stats['unique_colors']} colours below floor {MIN_COLORS}")
    for e in obs["events"]:
        if e["event"] == "resize" and e["accepted"]:
            frame = next((f for k, f in frames_of(obs) if k == f"plugin{e['plugin']}/resized-frame/-"), None)
            if frame and frame.get("read") and [frame["width"], frame["height"]] != e["size"]:
                reasons.append(f"{side}: plugin{e['plugin']} resized to {e['size']} but drew "
                               f"{frame['width']}x{frame['height']}")
    decisions = adapter_decisions(stderr)
    detail["adapter_decisions"] = decisions
    if expect.get("gpu"):
        if len(decisions) != len(creates) - sum(not c["attached"] for c in creates) or len(decisions) == 0:
            reasons.append(f"{side}: {len(decisions)} GPU-host decisions logged for {len(creates)} editor opens")
        if not all(d["use_gpu"] for d in decisions):
            reasons.append(f"{side}: an editor took the CPU path (use_gpu=false) where the case expects GPU")
    detail["blind_frames"] = blind
    return reasons, detail


def compare_editor(case, ref, cand, impls) -> dict[str, Any]:
    reasons = []
    details = {}
    images = {}
    for side, (directory, run) in (("reference", ref), ("candidate", cand)):
        sub = directory / "together"
        stderr = (directory / "probe.stderr").read_text(encoding="utf-8") if (directory / "probe.stderr").is_file() else ""
        r, d = editor_side(case, sub, stderr, side)
        reasons += r
        details[side] = d
        obs = read_observation(sub)
        images[side] = {k: frame_pixels(f, sub)[0] for k, f in frames_of(obs)}
        details[side]["lifecycle"] = lifecycle(obs)
    if details["reference"]["lifecycle"] != details["candidate"]["lifecycle"]:
        reasons += ["lifecycle differs: " + d for d in core.json_differences(
            details["reference"]["lifecycle"], details["candidate"]["lifecycle"], "/events")]
    animated = set(case.get("expect", {}).get("animated", []))
    details["animated_frame_diffs"] = {}
    for key in sorted(set(images["reference"]) | set(images["candidate"])):
        a, b = images["reference"].get(key), images["candidate"].get(key)
        if a is None or b is None:
            continue
        if (a.width, a.height) != (b.width, b.height):
            reasons.append(f"{key}: frame {a.width}x{a.height} != {b.width}x{b.height}")
        elif plugin_index(key) in animated:
            # A wall-clock animation cannot be compared pixel for pixel; its
            # frames keep the size, coverage and colour-floor checks above and
            # the difference is recorded, never accepted as equality.
            details["animated_frame_diffs"][key] = sum(
                1 for i in range(0, len(a.pixels), 4) if a.pixels[i:i + 3] != b.pixels[i:i + 3])
        elif a.pixels != b.pixels:
            differ = sum(1 for i in range(0, len(a.pixels), 4) if a.pixels[i:i + 3] != b.pixels[i:i + 3])
            reasons.append(f"{key}: {differ} pixels differ")
    if details["reference"]["blind_frames"] and details["reference"]["blind_frames"] == len(images["reference"]):
        return lane_result("blocked", reasons=["reference read-back is blind (instrument, not result)"] + reasons,
                           details=details)
    return lane_result("fail" if reasons else "pass", reasons=reasons, details=details)


def audio_side(directory: Path, side: str) -> tuple[list[str], dict[str, Any]]:
    obs = read_observation(directory)
    reasons = []
    digests: dict[str, Any] = {}
    for p in obs["plugins"]:
        renders = p["audio"]
        mine = {}
        for tag in ("initial", "closed", "open", "reclosed"):
            if tag not in renders:
                reasons.append(f"{side}: plugin{p['index']}: no {tag} render")
                continue
            digest, why = audio_digest(renders[tag], directory)
            mine[tag] = digest
            if why:
                reasons.append(f"{side}: plugin{p['index']}: {why}")
            if not renders[tag].get("finite", False):
                reasons.append(f"{side}: plugin{p['index']}: {tag} render has non-finite samples")
        if renders.get("closed", {}).get("rms", 0) < MIN_RMS:
            reasons.append(f"{side}: plugin{p['index']}: closed render rms {renders.get('closed', {}).get('rms')} "
                           f"below floor {MIN_RMS} (silent)")
        if len({mine.get("closed"), mine.get("open"), mine.get("reclosed")}) != 1:
            reasons.append(f"{side}: plugin{p['index']}: audio differs with the editor open "
                           f"(closed {str(mine.get('closed'))[:12]}, open {str(mine.get('open'))[:12]}, "
                           f"reclosed {str(mine.get('reclosed'))[:12]})")
        digests[f"plugin{p['index']}"] = mine
    return reasons, digests


def compare_audio(case, ref, cand, impls) -> dict[str, Any]:
    r1, d1 = audio_side(ref[0] / "together", "reference")
    r2, d2 = audio_side(cand[0] / "together", "candidate")
    reasons = r1 + r2
    s1 = read_observation(ref[0] / "together")["settings"]
    s2 = read_observation(cand[0] / "together")["settings"]
    if s1 != s2:
        reasons += ["settings differ: " + d for d in core.json_differences(s1, s2, "/settings")]
    # A plug-in whose first render races its own background initialization
    # (declared per case) keeps that render recorded but not compared; every
    # render after a release + prepare is still compared byte for byte.
    racing = {f"plugin{i}" for i in case.get("expect", {}).get("readiness_dependent_initial", [])}
    advisory = {}
    for plugin in sorted(set(d1) | set(d2)):
        for tag in ("initial", "closed"):
            a, b = d1.get(plugin, {}).get(tag), d2.get(plugin, {}).get(tag)
            if tag == "initial" and plugin in racing:
                advisory[plugin] = {"initial_identical": a == b}
                continue
            if a != b:
                reasons.append(f"{plugin}/{tag}: {str(a)[:12]} != {str(b)[:12]} (not byte identical)")
    return lane_result("fail" if reasons else "pass", reasons=reasons, advisory=advisory,
                       digests={"reference": d1, "candidate": d2})


def coexist_side(case: dict[str, Any], directory: Path, impl: dict[str, Any], side: str) -> tuple[list[str], dict]:
    reasons = []
    together = read_observation(directory / "together")
    paths = {p["path"] for p in together["plugins"]}
    names = {p["name"] for p in together["plugins"]}
    if len(paths) < 2 or len(names) < 2:
        reasons.append(f"{side}: fewer than two distinct plug-ins in one process (vacuous)")
    _, tdig = audio_side(directory / "together", side)
    tframes = {k: f for k, f in frames_of(together)}
    for i, _ in enumerate(case["plugins"]):
        solo_dir = directory / f"solo-{i}"
        if not solo_dir.is_dir():
            reasons.append(f"{side}: no solo run for plugin{i}")
            continue
        _, sdig = audio_side(solo_dir, side)
        if sdig.get("plugin0", {}).get("closed") != tdig.get(f"plugin{i}", {}).get("closed"):
            reasons.append(f"{side}: plugin{i} renders differently next to another plug-in than alone")
        solo = read_observation(solo_dir)
        compared = 0
        for key, frame in frames_of(solo):
            if not key.startswith("plugin0/frame/"):
                continue
            tkey = f"plugin{i}/" + key.split("/", 1)[1]
            a, _ = frame_pixels(frame, solo_dir)
            b, _ = frame_pixels(tframes.get(tkey, {}), directory / "together")
            compared += 1
            if i in set(case.get("expect", {}).get("animated", [])):
                if a is None or b is None or (a.width, a.height) != (b.width, b.height):
                    reasons.append(f"{side}: plugin{i} {tkey} has no frame of its solo size next to another plug-in")
                continue
            if a is None or b is None or a.pixels != b.pixels:
                reasons.append(f"{side}: plugin{i} {tkey} draws differently next to another plug-in")
        if not compared:
            reasons.append(f"{side}: plugin{i}: no editor frame to compare with its solo run")
    census = normalize_census(together.get("dyld_images", []), impl["artifacts"])
    runtimes = [c for c in census if "wgpu" in c or "dawn" in c.lower() or "webgpu" in c]
    return reasons, {"census": census, "runtime_images": runtimes}


def compare_coexist(case, ref, cand, impls) -> dict[str, Any]:
    r1, d1 = coexist_side(case, ref[0], impls["reference"], "reference")
    r2, d2 = coexist_side(case, cand[0], impls["candidate"], "candidate")
    reasons = r1 + r2
    if d1["census"] != d2["census"]:
        reasons += ["dyld census differs: " + d for d in core.json_differences(d1["census"], d2["census"], "/images")]
    return lane_result("fail" if reasons else "pass", reasons=reasons, reference=d1, candidate=d2)


def compare_validator(case, ref, cand, impls) -> dict[str, Any]:
    reasons = []
    marker = (impls["reference"].get("validator_success") or {}).get(case["validator"])
    for side, (directory, run) in (("reference", ref), ("candidate", cand)):
        step = run["steps"][-1]
        if step.get("exit") != 0:
            reasons.append(f"{side}: validator exited {step.get('exit')}")
        out = (directory / "validator.stdout").read_text(encoding="utf-8") if (directory / "validator.stdout").is_file() else ""
        if marker and marker not in out:
            reasons.append(f"{side}: validator output lacks success marker {marker!r}")
    return lane_result("fail" if reasons else "pass", reasons=reasons, marker=marker)


def compare_standalone(case, ref, cand, impls) -> dict[str, Any]:
    reasons = []
    images = {}
    for side, (directory, _) in (("reference", ref), ("candidate", cand)):
        png = directory / "standalone.png"
        if not png.is_file():
            return lane_result("blocked", reasons=[f"{side}: standalone wrote no screenshot"])
        image = decode_png_bytes(png.read_bytes())
        stats = frame_stats(image)
        images[side] = image
        if stats["unique_colors"] < MIN_COLORS:
            reasons.append(f"{side}: standalone frame has {stats['unique_colors']} colours (blank)")
    a, b = images["reference"], images["candidate"]
    if (a.width, a.height) != (b.width, b.height):
        reasons.append(f"standalone frame {a.width}x{a.height} != {b.width}x{b.height}")
    elif a.pixels != b.pixels:
        reasons.append("standalone first frame differs")
    return lane_result("fail" if reasons else "pass", reasons=reasons)


COMPARATORS: dict[str, Callable[..., dict[str, Any]]] = {
    "load": compare_load, "editor": compare_editor, "audio": compare_audio, "coexist": compare_coexist,
    "validator": compare_validator, "standalone": compare_standalone,
}


def evaluate(case: dict[str, Any], ref: tuple[Path, dict], cand: tuple[Path, dict],
             impls: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lanes = {}
    blocked = [f"{side}: {why}" for side, (_, run) in (("reference", ref), ("candidate", cand))
               if (why := side_block(run))]
    for lane in HOST_LANES:
        if lane not in case["lanes"]:
            lanes[lane] = lane_result("not_selected")
        elif blocked:
            lanes[lane] = lane_result("blocked", reasons=blocked)
        else:
            try:
                lanes[lane] = COMPARATORS[lane](case, ref, cand, impls)
            except (HarnessError, KeyError, ValueError, OSError) as error:
                lanes[lane] = lane_result("blocked", reasons=[f"{type(error).__name__}: {error}"])
    return {"case_id": case["case_id"], "inputs_identical": ref[1]["inputs"] == cand[1]["inputs"],
            "reference": ref[1], "candidate": cand[1], "lanes": lanes}


def receipt(mode: str, corpus_path: Path, corpus: dict[str, Any], ref_impl: dict[str, Any],
            cand_impl: dict[str, Any], ref_sha: str | None, cand_sha: str | None,
            results: list[dict[str, Any]]) -> dict[str, Any]:
    reference = host_identity(ref_impl, ref_sha)
    candidate = host_identity(cand_impl, cand_sha)
    return {
        "schema": HOST_RECEIPT_SCHEMA, "mode": mode,
        "corpus": {"path": str(corpus_path), "sha256": sha256_bytes(json.dumps(corpus, sort_keys=True).encode()),
                   "cases": len(results)},
        "declared_gaps": corpus.get("declared_gaps", []),
        "declared_exceptions": corpus.get("declared_exceptions", []),
        "reference": reference, "candidate": candidate, "cases": results,
        "verdict": acceptance(mode, results, reference, candidate),
    }


def run_corpus(corpus_path: Path, ref_impl: dict[str, Any], cand_impl: dict[str, Any], out: Path, *,
               mode: str, ref_sha: str | None, cand_sha: str | None) -> dict[str, Any]:
    corpus = load_json(corpus_path)
    cases = validate_corpus(corpus)
    results = []
    impls = {"reference": ref_impl, "candidate": cand_impl}
    for case in cases:
        ref_dir = out / case["case_id"] / "reference"
        cand_dir = out / case["case_id"] / "candidate"
        ref = (ref_dir, run_case(case, ref_impl, ref_dir))
        cand = (cand_dir, run_case(case, cand_impl, cand_dir))
        results.append(evaluate(case, ref, cand, impls))
    return receipt(mode, corpus_path, corpus, ref_impl, cand_impl, ref_sha, cand_sha, results)


# ── calibration ─────────────────────────────────────────────────────────────


def corrupt_bundle(bundle: Path, destination: Path) -> Path:
    """A copy of `bundle` whose main executable is truncated to 1 KiB."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(bundle, destination, symlinks=True)
    exe = destination / "Contents" / "MacOS" / bundle.stem
    if not exe.is_file():
        candidates = [p for p in (destination / "Contents" / "MacOS").iterdir() if p.suffix != ".dylib"]
        exe = candidates[0]
    data = exe.read_bytes()
    exe.write_bytes(data[:1024])
    return destination


def flip_byte(path: Path, offset_fraction: float = 0.5) -> None:
    data = bytearray(path.read_bytes())
    i = int(len(data) * offset_fraction) & ~3
    data[i] ^= 0x01
    path.write_bytes(bytes(data))


def flip_pixel(path: Path) -> None:
    image = decode_png_bytes(path.read_bytes())
    px = bytearray(image.pixels)
    i = (image.height // 2 * image.width + image.width // 2) * 4
    px[i] = (px[i] + 1) % 256
    path.write_bytes(encode_png(RgbaImage(image.width, image.height, bytes(px))))


def blank_png(path: Path, colour: tuple[int, int, int]) -> None:
    image = decode_png_bytes(path.read_bytes())
    path.write_bytes(encode_png(RgbaImage(image.width, image.height,
                                          bytes([*colour, 255]) * (image.width * image.height))))


RECEIPT_CONTROLS = ("identity-absent", "identity-same-in-parity-mode", "empty-corpus", "no-lane-selected",
                    "artifact-from-other-commit")


def calibrate(corpus_path: Path, impl: dict[str, Any], source_sha: str | None, out: Path,
              *, cases_filter: set[str] | None = None) -> dict[str, Any]:
    corpus = load_json(corpus_path)
    cases = [c for c in validate_corpus(corpus) if not cases_filter or c["case_id"] in cases_filter]
    if out.exists():
        shutil.rmtree(out)
    impls = {"reference": impl, "candidate": impl}
    baseline: dict[str, tuple[tuple[Path, dict], tuple[Path, dict]]] = {}
    positive = []
    for case in cases:
        ref_dir = out / "runs" / case["case_id"] / "reference"
        cand_dir = out / "runs" / case["case_id"] / "candidate"
        ref = (ref_dir, run_case(case, impl, ref_dir))
        cand = (cand_dir, run_case(case, impl, cand_dir))
        baseline[case["case_id"]] = (ref, cand)
        positive.append(evaluate(case, ref, cand, impls))
    positive_receipt = receipt("calibration", corpus_path, corpus, impl, impl, source_sha, source_sha, positive)
    controls: list[dict[str, Any]] = []

    def record(name: str, lane: str, expect: str, results: list[dict[str, Any]], applied: list[str],
               verdict: dict[str, Any] | None = None) -> None:
        if not applied:
            controls.append({"control": name, "lane": lane, "applied_cases": [], "rejected": False,
                             "lane_hit": False, "outcome": "NOT APPLICABLE (no case exercises it)"})
            return
        wanted = ("blocked",) if expect == "blocked" else ("fail",)
        hit = all(r["lanes"][lane]["status"] in wanted for r in results if r["case_id"] in applied) \
            if lane != "*" else all(any(l["status"] == "blocked" for l in r["lanes"].values())
                                    for r in results if r["case_id"] in applied)
        if verdict is None:
            merged = [r for r in positive if r["case_id"] not in applied] + results
            verdict = acceptance("calibration", merged, positive_receipt["reference"], positive_receipt["candidate"])
        evidence = {}
        for r in results:
            if r["case_id"] in applied and lane != "*":
                evidence[r["case_id"]] = r["lanes"][lane].get("reasons", [])[:3]
        rejected = not verdict["accepting"]
        controls.append({"control": name, "lane": lane, "expected_status": wanted[0], "applied_cases": applied,
                         "lane_hit": hit, "rejected": rejected, "evidence": evidence,
                         "outcome": "REJECTED" if rejected and hit else "NOT REJECTED"})

    def rerun(name: str, lane: str, expect: str, applies: Callable[[dict], bool], **kwargs: Any) -> None:
        results, applied = [], []
        for case in cases:
            if not applies(case):
                continue
            ref, _ = baseline[case["case_id"]]
            cand_dir = out / "controls" / name / case["case_id"] / "candidate"
            make = kwargs.get("make")
            run_kwargs = make(case, cand_dir.parent) if make else kwargs.get("run", {})
            cand_impl = run_kwargs.pop("impl", impl)
            cand = (cand_dir, run_case(case, cand_impl, cand_dir, **run_kwargs))
            results.append(evaluate(case, ref, cand, {"reference": impl, "candidate": cand_impl}))
            applied.append(case["case_id"])
        record(name, lane, expect, results, applied)

    def artifact(name: str, lane: str, applies: Callable[[dict], bool],
                 mutate: Callable[[dict, Path, Path], bool]) -> None:
        results, applied = [], []
        for case in cases:
            if not applies(case):
                continue
            (ref_dir, ref_run), (cand_dir, cand_run) = baseline[case["case_id"]]
            work = out / "controls" / name / case["case_id"]
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(ref_dir, work / "reference")
            shutil.copytree(cand_dir, work / "candidate")
            if not mutate(case, work / "reference", work / "candidate"):
                continue
            results.append(evaluate(case, (work / "reference", ref_run), (work / "candidate", cand_run), impls))
            applied.append(case["case_id"])
        record(name, lane, "fail", results, applied)

    probe = lambda lane: (lambda c: c["kind"] == "probe" and lane in c["lanes"])  # noqa: E731
    gpu = lambda c: probe("editor")(c) and c.get("expect", {}).get("gpu")  # noqa: E731

    # Re-run controls: the candidate is run again with one seeded defect.
    rerun("editor-disabled-by-plugin-env", "editor", "fail", probe("editor"),
          run={"env": {"PULP_DISABLE_PLUGIN_EDITOR": "1"}})
    rerun("editor-attach-null-parent", "editor", "fail", probe("editor"), run={"extra": ["--fault-null-parent"]})
    rerun("gpu-host-silently-disabled", "editor", "fail", gpu, run={"env": {"PULP_DISABLE_PLUGIN_GPU": "1"}})
    rerun("stimulus-changed", "audio", "fail", probe("audio"), run={"extra": ["--blocks", "93"]})

    def swapped(case: dict, where: Path) -> dict:
        names = list(case["plugins"])
        fmt = Path(names[0]).suffix
        other = next((n for n in impl["artifacts"] if n.endswith(fmt) and n not in names), None)
        names[0] = other or names[0]
        return {"plugins": names}
    rerun("bundle-swapped", "load", "fail", lambda c: probe("load")(c) and len(c["plugins"]) == 1, make=swapped)

    def corrupted(case: dict, where: Path) -> dict:
        name = case["plugins"][0]
        bad = corrupt_bundle(Path(impl["artifacts"][name]), where / "corrupt" / Path(impl["artifacts"][name]).name)
        return {"impl": dict(impl, artifacts={**impl["artifacts"], name: str(bad)})}
    rerun("bundle-corrupt", "load", "blocked", lambda c: probe("load")(c) and len(c["plugins"]) == 1, make=corrupted)
    rerun("planted-probe-skip", "*", "blocked", lambda c: c["kind"] == "probe", run={"skip": True})
    rerun("coexist-same-plugin-twice", "coexist", "fail", probe("coexist"),
          make=lambda c, w: {"plugins": [c["plugins"][0], c["plugins"][0]]})

    def corrupted_validated(case: dict, where: Path) -> dict:
        name = case["artifact"]
        bad = corrupt_bundle(Path(impl["artifacts"][name]), where / "corrupt" / Path(impl["artifacts"][name]).name)
        return {"impl": dict(impl, artifacts={**impl["artifacts"], name: str(bad)})}
    rerun("validator-on-corrupt-bundle", "validator", "fail", lambda c: c["kind"] == "validator",
          make=corrupted_validated)
    rerun("validator-missing", "validator", "blocked", lambda c: c["kind"] == "validator",
          make=lambda c, w: {"impl": dict(impl, tools={**impl["tools"], c["validator"]: str(w / "absent")})})
    rerun("standalone-other-app", "standalone", "fail", lambda c: c["kind"] == "standalone",
          make=lambda c, w: {"impl": dict(impl, artifacts={**impl["artifacts"], c["artifact"]: next(
              (p for n, p in impl["artifacts"].items() if n.endswith(".app") and n != c["artifact"]),
              impl["artifacts"][c["artifact"]])})})

    # Artifact controls: one observation file of the candidate is changed.
    def audio_byte(case, ref_dir, cand_dir):
        f = cand_dir / "together" / "audio-0-closed.f32"
        if not f.is_file():
            return False
        flip_byte(f)
        return True
    artifact("audio-one-byte", "audio", probe("audio"), audio_byte)

    def editor_perturbs(case, ref_dir, cand_dir):
        f = cand_dir / "together" / "audio-0-open.f32"
        if not f.is_file():
            return False
        flip_byte(f, 0.75)
        obs = read_observation(cand_dir / "together")
        obs["plugins"][0]["audio"]["open"]["sha256"] = sha256_file(f)
        write_json(cand_dir / "together" / "host-observation.json", obs)
        return True
    artifact("audio-editor-perturbs-dsp", "audio", probe("audio"), editor_perturbs)

    def frame_pixel(case, ref_dir, cand_dir):
        f = cand_dir / "together" / "editor-0-open0.png"
        if not f.is_file() or 0 in case.get("expect", {}).get("animated", []):
            return False
        flip_pixel(f)
        return True
    artifact("editor-frame-one-pixel", "editor", probe("editor"), frame_pixel)

    def blank_both(case, ref_dir, cand_dir):
        done = False
        for d in (ref_dir, cand_dir):
            for f in (d / "together").glob("editor-*.png"):
                blank_png(f, (24, 24, 24))
                done = True
        return done
    artifact("editor-uniform-both-sides", "editor", probe("editor"), blank_both)

    def backdrop_both(case, ref_dir, cand_dir):
        done = False
        for d in (ref_dir, cand_dir):
            f = d / "together" / "editor-0-open0.png"
            if f.is_file():
                image = decode_png_bytes(f.read_bytes())
                px = bytearray(image.pixels)
                px[0:4] = bytes([*BACKDROP, 255])
                f.write_bytes(encode_png(RgbaImage(image.width, image.height, bytes(px))))
                done = True
        return done
    artifact("editor-uncovered-backdrop-both-sides", "editor", probe("editor"), backdrop_both)

    def drop_runtime(case, ref_dir, cand_dir):
        obs = read_observation(cand_dir / "together")
        before = len(obs["dyld_images"])
        obs["dyld_images"] = [p for p in obs["dyld_images"] if not p.endswith(".dylib")]
        write_json(cand_dir / "together" / "host-observation.json", obs)
        return len(obs["dyld_images"]) != before
    artifact("coexist-runtime-census-drift", "coexist", probe("coexist"), drop_runtime)

    def solo_differs(case, ref_dir, cand_dir):
        f = cand_dir / "solo-1" / "audio-0-closed.f32"
        if not f.is_file():
            return False
        flip_byte(f)
        obs = read_observation(cand_dir / "solo-1")
        obs["plugins"][0]["audio"]["closed"]["sha256"] = sha256_file(f)
        write_json(cand_dir / "solo-1" / "host-observation.json", obs)
        return True
    artifact("coexist-render-differs-from-solo", "coexist", probe("coexist"), solo_differs)

    def validator_no_marker(case, ref_dir, cand_dir):
        f = cand_dir / "validator.stdout"
        if not f.is_file():
            return False
        f.write_text(f.read_text(encoding="utf-8").replace("SUCCESS", "S"), encoding="utf-8")
        return True
    artifact("validator-success-marker-absent", "validator", lambda c: c["kind"] == "validator", validator_no_marker)

    def standalone_blank(case, ref_dir, cand_dir):
        done = False
        for d in (ref_dir, cand_dir):
            if (d / "standalone.png").is_file():
                blank_png(d / "standalone.png", (10, 10, 10))
                done = True
        return done
    artifact("standalone-blank-both-sides", "standalone", lambda c: c["kind"] == "standalone", standalone_blank)

    # Receipt controls.
    ref_id, cand_id = positive_receipt["reference"], positive_receipt["candidate"]
    other_sha = ("0" if (source_sha or "1")[0] != "0" else "1") * 40
    receipt_checks = {
        "identity-absent": acceptance("calibration", positive, ref_id, dict(cand_id, source_sha=None)),
        "identity-same-in-parity-mode": acceptance("parity", positive, ref_id, cand_id),
        "empty-corpus": acceptance("calibration", [], ref_id, cand_id),
        "no-lane-selected": acceptance("calibration", [dict(positive[0], lanes={
            k: lane_result("not_selected") for k in HOST_LANES})] if positive else [], ref_id, cand_id),
        "artifact-from-other-commit": acceptance("calibration", positive, dict(ref_id, source_sha=other_sha),
                                                 dict(cand_id, source_sha=other_sha)),
    }
    for name in RECEIPT_CONTROLS:
        verdict = receipt_checks[name]
        controls.append({"control": name, "lane": "verdict", "applied_cases": ["receipt"], "lane_hit": True,
                         "rejected": not verdict["accepting"], "evidence": {"receipt": verdict["reasons"][:3]},
                         "outcome": "REJECTED" if not verdict["accepting"] else "NOT REJECTED"})

    calibrated = positive_receipt["verdict"]["accepting"] and all(c["outcome"] == "REJECTED" for c in controls)
    return {"schema": HOST_CALIBRATION_SCHEMA, "positive": positive_receipt, "controls": controls,
            "calibrated": calibrated}


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_pairs(values: list[str]) -> dict[str, str]:
    out = {}
    for value in values:
        if "=" not in value:
            raise HarnessError(f"expected name=path, got {value!r}")
        name, path = value.split("=", 1)
        out[name] = path
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cal = sub.add_parser("calibrate", help="positive comparison plus every seeded negative control")
    cal.add_argument("--corpus", required=True, type=Path)
    cal.add_argument("--implementation", required=True, type=Path)
    cal.add_argument("--tool", action="append", default=[])
    cal.add_argument("--artifact", action="append", default=[])
    cal.add_argument("--source-sha")
    cal.add_argument("--case", action="append", default=[], help="restrict to these case ids")
    cal.add_argument("--out", required=True, type=Path)
    run = sub.add_parser("run", help="compare reference and candidate over a host corpus")
    run.add_argument("--corpus", required=True, type=Path)
    run.add_argument("--mode", choices=("parity", "calibration"), default="parity")
    for side in ("reference", "candidate"):
        run.add_argument(f"--{side}", required=True, type=Path)
        run.add_argument(f"--{side}-tool", action="append", default=[])
        run.add_argument(f"--{side}-artifact", action="append", default=[])
        run.add_argument(f"--{side}-sha")
    run.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "calibrate":
            impl = resolve(validate_implementation(load_json(args.implementation)),
                           parse_pairs(args.tool), parse_pairs(args.artifact))
            report = calibrate(args.corpus, impl, args.source_sha, args.out, cases_filter=set(args.case) or None)
            write_json(args.out / "host-calibration.json", report)
            for c in report["controls"]:
                print(f"{c['control']:40} {c['outcome']}  {','.join(c['applied_cases'])[:60]}")
            print(f"positive accepting: {report['positive']['verdict']['accepting']}")
            for reason in report["positive"]["verdict"]["reasons"][:20]:
                print(f"  {reason}")
            print("CALIBRATED" if report["calibrated"] else "NOT CALIBRATED")
            return 0 if report["calibrated"] else 1
        ref = resolve(validate_implementation(load_json(args.reference)),
                      parse_pairs(args.reference_tool), parse_pairs(args.reference_artifact))
        cand = resolve(validate_implementation(load_json(args.candidate)),
                       parse_pairs(args.candidate_tool), parse_pairs(args.candidate_artifact))
        result = run_corpus(args.corpus, ref, cand, args.out, mode=args.mode,
                            ref_sha=args.reference_sha, cand_sha=args.candidate_sha)
        write_json(args.out / "host-receipt.json", result)
        print(json.dumps(result["verdict"], indent=2))
        return 0 if result["verdict"]["accepting"] else 1
    except HarnessError as error:
        print(f"host-lane: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
