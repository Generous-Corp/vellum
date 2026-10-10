"""Host-compatibility lanes: built plug-in bundles inside a real host process.

Registered with the parity harness through ``--lane-module
tools/parity/lanes/host.py``. This row is separate from screenshot parity.
Each side's implementation runs ``host_runner.py`` (its host probe, its
validators, its standalone apps) and leaves observation files. These lanes
compare those files:

  host-load        plug-ins load and prepare; format, name, editor presence,
                   latency, parameter count agree; every bundle was built from
                   the side's checkout HEAD, from a clean tree
  host-editor      every editor opens attached at the case's size, covers its
                   whole window (no host-backdrop pixel), clears a colour floor,
                   takes the GPU path when expected, resizes, closes, reopens;
                   lifecycle and frames agree across sides (frames pixel for
                   pixel unless the plug-in is declared animated)
  host-audio       editor closed / open / closed renders are byte identical,
                   audible and finite; renders agree across sides byte for byte
  host-coexist     two or more distinct plug-ins in one process render and draw
                   what each does alone; every plug-in is bound to its own
                   bundled bytes of each shared runtime; the loaded-image census
                   agrees across sides
  host-validator   the external validator exits 0 with its success marker
  host-standalone  the standalone app exits 0 with a first frame that is not
                   blank; frames agree across sides
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any

import parity_harness as ph

sys.path.insert(0, str(Path(ph.__file__).resolve().parents[2] / "cli"))
from vellum_png import RgbaImage, decode_png_bytes, encode_png  # noqa: E402

OBSERVATION_SCHEMA = "pulp.host-lane.observation.v1"
BACKDROP = (255, 0, 255)
MIN_COLORS = 8
MIN_RMS = 1e-4
ADAPTER_LINE = re.compile(r"\[plugin-gpu-host\] adapter mode=(\S+) use_gpu=(true|false)")
OBS = "together/host-observation.json"
lane_result = ph.lane_result


# ── observation helpers ─────────────────────────────────────────────────────


def observation(directory: Path) -> dict[str, Any]:
    doc = ph.load_json(directory / "host-observation.json")
    if doc.get("schema") != OBSERVATION_SCHEMA:
        raise ph.HarnessError(f"{directory}: observation schema {doc.get('schema')!r}")
    return doc


def frames_of(obs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(f"plugin{e['plugin']}/{e['event']}/{e.get('open', '-')}", e["frame"])
            for e in obs["events"] if "frame" in e]


def plugin_index(key: str) -> int:
    return int(key.split("/", 1)[0][len("plugin"):])


def frame_image(frame: dict[str, Any], directory: Path) -> tuple[RgbaImage | None, str | None]:
    if not frame.get("read"):
        return None, "read-back unavailable"
    path = directory / Path(frame.get("png", "")).name
    if not path.is_file():
        return None, f"missing frame {path.name}"
    return decode_png_bytes(path.read_bytes()), None


def frame_stats(image: RgbaImage) -> dict[str, int]:
    px = image.pixels
    colours, backdrop = set(), 0
    for i in range(0, len(px), 4):
        if (px[i], px[i + 1], px[i + 2]) == BACKDROP:
            backdrop += 1
        if len(colours) < 4096:
            colours.add(px[i:i + 3])
    return {"width": image.width, "height": image.height, "backdrop_pixels": backdrop,
            "unique_colors": len(colours)}


def lifecycle(obs: dict[str, Any]) -> list[tuple]:
    sig = []
    for e in obs["events"]:
        if e["event"] == "create":
            sig.append(("create", e["plugin"], e.get("open"), e["attached"], e.get("width"), e.get("height")))
        elif e["event"] == "resize":
            sig.append(("resize", e["plugin"], tuple(e["requested"]), e["accepted"], tuple(e["size"])))
        elif e["event"] == "close":
            sig.append(("close", e["plugin"], e.get("open"), e["had_editor"]))
    return sig


def audio_digests(directory: Path, side: str) -> tuple[list[str], dict[str, dict[str, str | None]]]:
    obs = observation(directory)
    reasons, digests = [], {}
    for p in obs["plugins"]:
        mine = {}
        for tag in ("initial", "closed", "open", "reclosed"):
            render = p["audio"].get(tag)
            if not render:
                reasons.append(f"{side}: plugin{p['index']}: no {tag} render")
                continue
            path = directory / Path(render.get("file", "")).name
            digest = ph.sha256_file(path) if path.is_file() else None
            mine[tag] = digest
            if digest is None:
                reasons.append(f"{side}: plugin{p['index']}: missing {tag} audio file")
            elif digest != render.get("sha256"):
                reasons.append(f"{side}: plugin{p['index']}: {tag} file hash differs from the reported hash")
            if not render.get("finite", False):
                reasons.append(f"{side}: plugin{p['index']}: {tag} has non-finite samples")
        if (p["audio"].get("closed") or {}).get("rms", 0) < MIN_RMS:
            reasons.append(f"{side}: plugin{p['index']}: closed render is silent (rms below {MIN_RMS})")
        if len({mine.get("closed"), mine.get("open"), mine.get("reclosed")}) != 1:
            reasons.append(f"{side}: plugin{p['index']}: audio differs with the editor open")
        digests[f"plugin{p['index']}"] = mine
    return reasons, digests


# ── comparators (ref_dir, cand_dir, case) ───────────────────────────────────


def compare_load(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    keys = ("format", "name", "has_editor", "latency_samples", "parameters")
    reasons, rows = [], {}
    expected = len([a for a in case["args"]["plugins"] if a != "--plugin"])
    for side, d in (("reference", ref), ("candidate", cand)):
        obs = observation(d / "together")
        rows[side] = [{k: p[k] for k in keys} for p in obs["plugins"]]
        if len(obs["plugins"]) != expected:
            reasons.append(f"{side}: loaded {len(obs['plugins'])} of {expected} plug-ins")
        if case.get("expect", {}).get("editor") and not all(p["has_editor"] for p in obs["plugins"]):
            reasons.append(f"{side}: a plug-in reports no editor")
        prov = ph.load_json(d / "provenance.json")
        head = prov["checkout"].get("head")
        for b in prov["bundles"]:
            build = b.get("build") or {}
            if not build:
                reasons.append(f"{side}: {b['bundle']} carries no build record")
            elif build.get("source_git_sha") != head:
                reasons.append(f"{side}: {b['bundle']} was built from {build.get('source_git_sha')}, "
                               f"checkout HEAD is {head}")
            if build.get("source_git_dirty"):
                reasons.append(f"{side}: {b['bundle']} was built from a dirty tree")
    if rows["reference"] != rows["candidate"]:
        reasons += ph.json_differences(rows["reference"], rows["candidate"], "/plugins")
    return lane_result("fail" if reasons else "pass", plugins=rows["reference"], reasons=reasons)


def editor_side(case: dict[str, Any], directory: Path, side: str) -> tuple[list[str], dict[str, Any]]:
    obs = observation(directory / "together")
    expect = case.get("expect", {})
    opens = int(obs["settings"]["opens"])
    reasons: list[str] = []
    detail: dict[str, Any] = {"frames": {}}
    creates = [e for e in obs["events"] if e["event"] == "create"]
    editors = [p["index"] for p in obs["plugins"] if p["has_editor"]]
    if not editors:
        reasons.append(f"{side}: no plug-in has an editor (vacuous)")
    for idx in editors:
        mine = [e for e in creates if e["plugin"] == idx]
        if len(mine) != opens:
            reasons.append(f"{side}: plugin{idx} opened {len(mine)} of {opens} times")
        for e in mine:
            if not e["attached"]:
                reasons.append(f"{side}: plugin{idx} open {e.get('open')} did not attach")
            elif [e.get("width"), e.get("height")] != [case["width"], case["height"]]:
                reasons.append(f"{side}: plugin{idx} editor is {e.get('width')}x{e.get('height')}, "
                               f"case expects {case['width']}x{case['height']}")
        closes = [e for e in obs["events"] if e["event"] == "close" and e["plugin"] == idx]
        if len(closes) != opens or not all(c["had_editor"] for c in closes):
            reasons.append(f"{side}: plugin{idx} closed {sum(c['had_editor'] for c in closes)} of {opens} editors")
    blind = 0
    for key, frame in frames_of(obs):
        image, why = frame_image(frame, directory / "together")
        if image is None:
            reasons.append(f"{side}: {key}: {why}")
            blind += why == "read-back unavailable"
            continue
        stats = frame_stats(image)
        detail["frames"][key] = stats
        if stats["backdrop_pixels"]:
            reasons.append(f"{side}: {key}: {stats['backdrop_pixels']} host-backdrop pixels (editor did not cover)")
        if stats["unique_colors"] < MIN_COLORS:
            reasons.append(f"{side}: {key}: {stats['unique_colors']} colours, below floor {MIN_COLORS}")
    for e in obs["events"]:
        if e["event"] == "resize" and e["accepted"]:
            stats = detail["frames"].get(f"plugin{e['plugin']}/resized-frame/-")
            if stats and [stats["width"], stats["height"]] != e["size"]:
                reasons.append(f"{side}: plugin{e['plugin']} accepted {e['size']} but drew "
                               f"{stats['width']}x{stats['height']}")
    stderr = (directory / "probe.stderr").read_text(encoding="utf-8")
    decisions = [{"mode": m.group(1), "use_gpu": m.group(2) == "true"} for m in ADAPTER_LINE.finditer(stderr)]
    detail["adapter_decisions"] = decisions
    attached = sum(1 for c in creates if c["attached"])
    if expect.get("gpu"):
        if not decisions or len(decisions) != attached:
            reasons.append(f"{side}: {len(decisions)} GPU-host decisions logged for {attached} attached opens")
        if not all(d["use_gpu"] for d in decisions):
            reasons.append(f"{side}: an editor took the CPU path (use_gpu=false) where the case expects GPU")
    detail["blind_frames"] = blind
    detail["lifecycle"] = lifecycle(obs)
    return reasons, detail


def compare_editor(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    reasons, details, images = [], {}, {}
    for side, d in (("reference", ref), ("candidate", cand)):
        r, detail = editor_side(case, d, side)
        reasons += r
        details[side] = detail
        obs = observation(d / "together")
        images[side] = {k: frame_image(f, d / "together")[0] for k, f in frames_of(obs)}
    if details["reference"]["lifecycle"] != details["candidate"]["lifecycle"]:
        reasons += ["lifecycle differs: " + x for x in ph.json_differences(
            details["reference"]["lifecycle"], details["candidate"]["lifecycle"], "/events")]
    animated = set(case.get("expect", {}).get("animated", []))
    animated_diffs = {}
    for key in sorted(set(images["reference"]) | set(images["candidate"])):
        a, b = images["reference"].get(key), images["candidate"].get(key)
        if a is None or b is None:
            continue
        if (a.width, a.height) != (b.width, b.height):
            reasons.append(f"{key}: frame {a.width}x{a.height} != {b.width}x{b.height}")
            continue
        differ = sum(1 for i in range(0, len(a.pixels), 4) if a.pixels[i:i + 3] != b.pixels[i:i + 3])
        if plugin_index(key) in animated:
            # Wall-clock animation: kept to size, coverage, colour floor and
            # GPU path; the difference is recorded, never called equality.
            animated_diffs[key] = differ
        elif differ:
            reasons.append(f"{key}: {differ} pixels differ")
    if details["reference"]["blind_frames"] and details["reference"]["blind_frames"] == len(images["reference"]):
        return lane_result("blocked", reasons=["reference read-back is blind (instrument, not result)"] + reasons)
    return lane_result("fail" if reasons else "pass", reasons=reasons, animated_frame_diffs=animated_diffs,
                       details=details)


def compare_audio(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    r1, d1 = audio_digests(ref / "together", "reference")
    r2, d2 = audio_digests(cand / "together", "candidate")
    reasons = r1 + r2
    s1, s2 = observation(ref / "together")["settings"], observation(cand / "together")["settings"]
    if s1 != s2:
        reasons += ["settings differ: " + x for x in ph.json_differences(s1, s2, "/settings")]
    # A plug-in whose first render races its own background initialization
    # (declared per case) keeps that render recorded, not compared.
    racing = {f"plugin{i}" for i in case.get("expect", {}).get("readiness_dependent_initial", [])}
    advisory = {}
    for plugin in sorted(set(d1) | set(d2)):
        for tag in ("initial", "closed"):
            a, b = d1.get(plugin, {}).get(tag), d2.get(plugin, {}).get(tag)
            if tag == "initial" and plugin in racing:
                advisory[plugin] = {"initial_identical": a == b}
            elif a != b:
                reasons.append(f"{plugin}/{tag}: {str(a)[:12]} != {str(b)[:12]} (not byte identical)")
    return lane_result("fail" if reasons else "pass", reasons=reasons, advisory=advisory,
                       digests={"reference": d1, "candidate": d2})


def normalized_census(images: list[str], bundles: list[str]) -> list[str]:
    out = []
    for image in images:
        owner = next((i for i, b in enumerate(bundles) if f"/{b}/" in image), None)
        out.append(f"<plugin{owner}>/{image.split(f'/{bundles[owner]}/', 1)[1]}" if owner is not None
                   else f"<external>/{Path(image).name}")
    return sorted(out)


def coexist_side(case: dict[str, Any], directory: Path, side: str) -> tuple[list[str], dict[str, Any]]:
    reasons = []
    together = observation(directory / "together")
    if len({p["path"] for p in together["plugins"]}) < 2 or len({p["name"] for p in together["plugins"]}) < 2:
        reasons.append(f"{side}: fewer than two distinct plug-ins in one process (vacuous)")
    _, tdig = audio_digests(directory / "together", side)
    tframes = dict(frames_of(together))
    animated = set(case.get("expect", {}).get("animated", []))
    for i in range(len(together["plugins"])):
        solo_dir = directory / f"solo-{i}"
        if not (solo_dir / "host-observation.json").is_file():
            reasons.append(f"{side}: no solo run for plugin{i}")
            continue
        _, sdig = audio_digests(solo_dir, side)
        if sdig.get("plugin0", {}).get("closed") != tdig.get(f"plugin{i}", {}).get("closed"):
            reasons.append(f"{side}: plugin{i} renders differently next to another plug-in than alone")
        compared = 0
        for key, frame in frames_of(observation(solo_dir)):
            if not key.startswith("plugin0/frame/"):
                continue
            tkey = f"plugin{i}/" + key.split("/", 1)[1]
            a, _ = frame_image(frame, solo_dir)
            b, _ = frame_image(tframes.get(tkey, {}), directory / "together")
            compared += 1
            if a is None or b is None or (a.width, a.height) != (b.width, b.height):
                reasons.append(f"{side}: plugin{i} {tkey} has no frame of its solo size next to another plug-in")
            elif i not in animated and a.pixels != b.pixels:
                reasons.append(f"{side}: plugin{i} {tkey} draws differently next to another plug-in")
        if not compared:
            reasons.append(f"{side}: plugin{i}: no editor frame to compare with its solo run")
    binding = ph.load_json(directory / "runtime-binding.json")
    for row in binding:
        if not row.get("bound_to_own_bytes"):
            reasons.append(f"{side}: plugin{row['plugin']} ({row['bundle']}) links {row['install_name']} but runs "
                           f"on a copy with different bytes (owner plugin{row.get('loaded_copy_owner')}, "
                           f"{row.get('imported_symbols')} symbols imported from it)")
    bundles = [Path(p["path"]).name for p in together["plugins"]]
    census = normalized_census(together.get("dyld_images", []), bundles)
    shape = sorted((r["plugin"], r["install_name"], r["loaded_copy_owner"], r["loaded_copies"],
                    r["imported_symbols"], r["bound_to_own_bytes"]) for r in binding)
    return reasons, {"census": census, "binding": shape}


def compare_coexist(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    r1, d1 = coexist_side(case, ref, "reference")
    r2, d2 = coexist_side(case, cand, "candidate")
    reasons = r1 + r2
    if d1["census"] != d2["census"]:
        reasons += ["loaded-image census differs: " + x for x in ph.json_differences(d1["census"], d2["census"], "/images")]
    if d1["binding"] != d2["binding"]:
        reasons.append("runtime binding differs across sides")
    return lane_result("fail" if reasons else "pass", reasons=reasons, reference=d1, candidate=d2)


def compare_validator(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    marker = case.get("expect", {}).get("validator_success", "SUCCESS")
    for side, d in (("reference", ref), ("candidate", cand)):
        record = ph.load_json(d / "validator.json")
        if record.get("exit") != 0:
            reasons.append(f"{side}: validator exited {record.get('exit')}")
        if marker not in record.get("stdout_tail", ""):
            reasons.append(f"{side}: validator output lacks success marker {marker!r}")
    return lane_result("fail" if reasons else "pass", reasons=reasons)


def compare_standalone(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    reasons, images = [], {}
    for side, d in (("reference", ref), ("candidate", cand)):
        image = decode_png_bytes((d / "standalone.png").read_bytes())
        images[side] = image
        stats = frame_stats(image)
        if stats["unique_colors"] < MIN_COLORS:
            reasons.append(f"{side}: standalone frame has {stats['unique_colors']} colours (blank)")
        if [image.width, image.height] != [case["width"], case["height"]]:
            reasons.append(f"{side}: standalone frame {image.width}x{image.height}, "
                           f"case expects {case['width']}x{case['height']}")
    a, b = images["reference"], images["candidate"]
    if (a.width, a.height) != (b.width, b.height):
        reasons.append(f"standalone frame {a.width}x{a.height} != {b.width}x{b.height}")
    elif a.pixels != b.pixels:
        reasons.append("standalone first frame differs")
    return lane_result("fail" if reasons else "pass", reasons=reasons)


ph.register_lane("host-load", (OBS, "provenance.json"), compare_load)
ph.register_lane("host-editor", (OBS, "probe.stderr"), compare_editor)
ph.register_lane("host-audio", (OBS,), compare_audio)
ph.register_lane("host-coexist", (OBS, "solo-0/host-observation.json", "solo-1/host-observation.json",
                                  "runtime-binding.json"), compare_coexist)
ph.register_lane("host-validator", ("validator.json",), compare_validator)
ph.register_lane("host-standalone", ("standalone.png", "standalone.json"), compare_standalone)


# ── seeded controls ─────────────────────────────────────────────────────────


def flip_byte(path: Path, fraction: float = 0.5) -> None:
    data = bytearray(path.read_bytes())
    data[int(len(data) * fraction) & ~3] ^= 0x01
    path.write_bytes(bytes(data))


def rehash_render(directory: Path, plugin: int, tag: str) -> None:
    obs = ph.load_json(directory / "host-observation.json")
    obs["plugins"][plugin]["audio"][tag]["sha256"] = ph.sha256_file(directory / f"audio-{plugin}-{tag}.f32")
    ph.write_json(directory / "host-observation.json", obs)


def animated(d: Path) -> set[int]:
    path = d / "runner.json"
    return set(ph.load_json(path).get("animated", [])) if path.is_file() else set()


def png_edit(path: Path, edit) -> None:
    image = decode_png_bytes(path.read_bytes())
    path.write_bytes(encode_png(edit(image)))


def one_pixel(image: RgbaImage) -> RgbaImage:
    px = bytearray(image.pixels)
    i = (image.height // 2 * image.width + image.width // 2) * 4
    px[i] = (px[i] + 1) % 256
    return RgbaImage(image.width, image.height, bytes(px))


def uniform(image: RgbaImage) -> RgbaImage:
    return RgbaImage(image.width, image.height, bytes([24, 24, 24, 255]) * (image.width * image.height))


def backdrop_pixel(image: RgbaImage) -> RgbaImage:
    px = bytearray(image.pixels)
    px[0:4] = bytes([*BACKDROP, 255])
    return RgbaImage(image.width, image.height, bytes(px))


def m_audio_one_byte(d: Path) -> bool:
    f = d / "together" / "audio-0-closed.f32"
    return f.is_file() and (flip_byte(f), True)[1]


def m_editor_perturbs_dsp(d: Path) -> bool:
    f = d / "together" / "audio-0-open.f32"
    if not f.is_file():
        return False
    flip_byte(f, 0.75)
    rehash_render(d / "together", 0, "open")
    return True


def m_frame_one_pixel(d: Path) -> bool:
    f = d / "together" / "editor-0-open0.png"
    if not f.is_file() or 0 in animated(d):
        return False
    png_edit(f, one_pixel)
    return True


def m_uniform(d: Path) -> bool:
    files = list((d / "together").glob("editor-*.png"))
    for f in files:
        png_edit(f, uniform)
    return bool(files)


def m_backdrop(d: Path) -> bool:
    f = d / "together" / "editor-0-open0.png"
    return f.is_file() and (png_edit(f, backdrop_pixel), True)[1]


def m_census_drift(d: Path) -> bool:
    path = d / OBS
    obs = ph.load_json(path)
    before = len(obs["dyld_images"])
    obs["dyld_images"] = [p for p in obs["dyld_images"] if not p.endswith(".dylib")]
    ph.write_json(path, obs)
    return len(obs["dyld_images"]) != before


def m_solo_differs(d: Path) -> bool:
    f = d / "solo-1" / "audio-0-closed.f32"
    if not f.is_file():
        return False
    flip_byte(f)
    rehash_render(d / "solo-1", 0, "closed")
    return True


def m_provenance(field: str):
    def mutate(d: Path) -> bool:
        path = d / "provenance.json"
        prov = ph.load_json(path)
        build = prov["bundles"][0].get("build")
        if not build:
            return False
        if field == "dirty":
            build["source_git_dirty"] = True
        else:
            build["source_git_sha"] = "0" * 40 if build.get("source_git_sha") != "0" * 40 else "1" * 40
        ph.write_json(path, prov)
        return True
    return mutate


def m_validator_marker(d: Path) -> bool:
    path = d / "validator.json"
    record = ph.load_json(path)
    record["stdout_tail"] = record.get("stdout_tail", "").replace("SUCCESS", "S")
    ph.write_json(path, record)
    return True


def m_standalone_blank(d: Path) -> bool:
    png_edit(d / "standalone.png", uniform)
    return True


def reasons_contain(text: str):
    return lambda result: any(text in r for r in result.get("reasons", []))


ph.register_controls(artifact={
    "host-audio-one-byte": {"lane": "host-audio", "candidate": m_audio_one_byte},
    "host-audio-editor-perturbs-dsp": {"lane": "host-audio", "candidate": m_editor_perturbs_dsp,
                                       "bites": reasons_contain("audio differs with the editor open")},
    "host-editor-frame-one-pixel": {"lane": "host-editor", "candidate": m_frame_one_pixel,
                                    "bites": reasons_contain("pixels differ")},
    "host-editor-uniform-both": {"lane": "host-editor", "both": m_uniform,
                                 "bites": reasons_contain("below floor")},
    "host-editor-backdrop-both": {"lane": "host-editor", "both": m_backdrop,
                                  "bites": reasons_contain("host-backdrop pixels")},
    "host-coexist-census-drift": {"lane": "host-coexist", "candidate": m_census_drift},
    "host-coexist-render-differs-from-solo": {"lane": "host-coexist", "candidate": m_solo_differs,
                                              "bites": reasons_contain("renders differently")},
    "host-load-dirty-build": {"lane": "host-load", "candidate": m_provenance("dirty"),
                              "bites": reasons_contain("dirty tree")},
    "host-load-built-from-other-commit": {"lane": "host-load", "candidate": m_provenance("sha"),
                                          "bites": reasons_contain("was built from")},
    "host-validator-marker-absent": {"lane": "host-validator", "candidate": m_validator_marker,
                                     "bites": reasons_contain("success marker")},
    "host-standalone-blank-both": {"lane": "host-standalone", "both": m_standalone_blank,
                                   "bites": reasons_contain("blank")},
})


def with_args(case: dict[str, Any], **changes: list[str]) -> dict[str, Any]:
    return dict(case, args={**case.get("args", {}), **changes})


def fault(case: dict[str, Any], *extra: str) -> dict[str, Any]:
    return with_args(case, fault=[*case.get("args", {}).get("fault", []), *extra])


def kind(case: dict[str, Any], *kinds: str) -> bool:
    return case["source_kind"] in kinds


def prep_env(var: str, lane: str, need_gpu: bool = False):
    def prepare(case, source_root, work_dir=None):
        if lane not in case["lanes"] or (need_gpu and not case.get("expect", {}).get("gpu")):
            return None
        return fault(case, "--env", f"{var}=1"), {}
    return prepare


def prep_null_parent(case, source_root, work_dir=None):
    return (fault(case, "--probe-arg=--fault-null-parent"), {}) if "host-editor" in case["lanes"] else None


def prep_stimulus(case, source_root, work_dir=None):
    if "host-audio" not in case["lanes"]:
        return None
    settings = json.loads((ph.case_root(case, source_root) / case["source"]).read_text(encoding="utf-8"))
    settings["blocks"] -= 1
    return case, {"source_override": json.dumps(settings, indent=2).encode()}


def prep_swap(case, source_root, work_dir=None):
    other = case.get("controls", {}).get("swap")
    if not other or "host-load" not in case["lanes"] or kind(case, "host-pair"):
        return None
    plugins = list(case["args"]["plugins"])
    plugins[1] = "{root:" + other + "}"
    return with_args(case, plugins=plugins), {}


def prep_corrupt(lane: str):
    def prepare(case, source_root, work_dir=None):
        if lane not in case["lanes"] or work_dir is None or kind(case, "host-pair"):
            return None
        return fault(case, "--corrupt-first", str(Path(work_dir) / "corrupt")), {}
    return prepare


def prep_same_twice(case, source_root, work_dir=None):
    if "host-coexist" not in case["lanes"]:
        return None
    first = case["args"]["plugins"][:2]
    return with_args(case, plugins=first + first, solo1=first), {}


def prep_runtime_skew(case, source_root, work_dir=None):
    runtime = case.get("controls", {}).get("skew_runtime_root")
    if "host-coexist" not in case["lanes"] or not runtime or work_dir is None:
        return None
    return fault(case, "--skew-second", "{root:" + runtime + "}", "--work", str(work_dir)), {}


def prep_validator_missing(case, source_root, work_dir=None):
    return (fault(case, "--validator-missing"), {}) if "host-validator" in case["lanes"] else None


def prep_other_app(case, source_root, work_dir=None):
    other = case.get("controls", {}).get("swap")
    if "host-standalone" not in case["lanes"] or not other:
        return None
    return with_args(case, app=["--app", "{root:" + other + "}"]), {}


ph.register_controls(rerun={
    "host-editor-disabled-by-plugin-env": {"lane": "host-editor",
                                           "prepare": prep_env("PULP_DISABLE_PLUGIN_EDITOR", "host-editor")},
    "host-editor-attach-null-parent": {"lane": "host-editor", "prepare": prep_null_parent,
                                       "bites": reasons_contain("did not attach")},
    "host-gpu-host-silently-disabled": {"lane": "host-editor",
                                        "prepare": prep_env("PULP_DISABLE_PLUGIN_GPU", "host-editor", True),
                                        "bites": reasons_contain("CPU path")},
    "host-stimulus-changed": {"lane": "host-audio", "prepare": prep_stimulus, "identical_inputs": False},
    "host-bundle-swapped": {"lane": "host-load", "prepare": prep_swap},
    "host-bundle-corrupt": {"lane": "host-load", "prepare": prep_corrupt("host-load"), "expect": "blocked"},
    "host-coexist-same-plugin-twice": {"lane": "host-coexist", "prepare": prep_same_twice,
                                       "bites": reasons_contain("vacuous")},
    "host-coexist-runtime-version-skew": {"lane": "host-coexist", "prepare": prep_runtime_skew,
                                          "bites": reasons_contain("runs on a copy with different bytes")},
    "host-validator-on-corrupt-bundle": {"lane": "host-validator", "prepare": prep_corrupt("host-validator"),
                                         "bites": reasons_contain("validator exited")},
    "host-validator-missing": {"lane": "host-validator", "prepare": prep_validator_missing, "expect": "blocked"},
    "host-standalone-other-app": {"lane": "host-standalone", "prepare": prep_other_app},
})
