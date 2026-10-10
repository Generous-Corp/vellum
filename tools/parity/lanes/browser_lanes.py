"""Browser-oracle, reimport and expected-rejection lanes.

Load with ``--lane-module tools/parity/lanes/browser_lanes.py`` on both
``calibrate`` and ``run``; the file then enters the harness fingerprint.

  oracle     Chromium as a THIRD implementation. Each side runs the pinned
             browser directly on the staged source (no importer code between
             the browser and ``oracle.png``); the two captures must be
             byte-identical (the oracle reproduces itself across processes),
             and each side's NATIVE render is compared to that capture on its
             own. A render that is really a browser screenshot (a DesignIR
             faithful-capture/browser-pixel node, or pixels identical to the
             capture) fails: browser-import success is never native parity.
  reimport   each side imports the unchanged source a second time (``second/``)
             and a declared edit of it (``revision/``). The unchanged import
             must reproduce the canonical DesignIR and its identities, the edit
             must be detected while untouched identities survive, both sides
             must detect the same change, and every output must be bound to the
             exact bytes it was imported from, so stale output is refused.
  rejection  the expected-rejection case kind: a malformed or unsupported input
             that both sides must refuse (or accept with a warning) the same
             way, with the same neutral diagnostic code. The implementation
             wraps its commands in ``outcome_recorder.py``, so a refusal is an
             observation; a crash or a timeout is never a rejection, and a
             skipped step is never a pass.

The three lanes answer separate questions from the core lanes: native parity
(structure/raster/geometry/semantic), browser fidelity (oracle), and importer
contracts (reimport, rejection). None of them implies another.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import parity_harness as ph
from vellum_image_compare import compare_images
from vellum_png import PngError, RgbaImage, decode_png_bytes, encode_png

MAX_ORACLE_MISMATCH = 0.10
MAX_ORACLE_CHANNEL_THRESHOLD = 16
MIN_ORACLE_INK_FRACTION = 0.005
EXPECTED_OUTCOMES = ("warn", "reject")
# DesignIR markers of a node whose pixels are a browser capture rather than a
# native paint. They are schema terms, so the scan holds for every
# implementation that emits DesignIR; an implementation cannot opt out of it.
BROWSER_PIXEL_RENDER_MODES = {"faithful_capture"}
BROWSER_PIXEL_ATTRIBUTE_PREFIXES = {"asset_ref": ("reference:browser", "evidence:browser")}
BROWSER_PIXEL_ATTRIBUTE_VALUES = {"materialized_role": ("captured-paint-authority",)}


# ── oracle ─────────────────────────────────────────────────────────────────


def crop(image: RgbaImage, x: int, y: int, width: int, height: int) -> RgbaImage:
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        raise PngError(f"oracle clip {(x, y, width, height)} exceeds capture {(image.width, image.height)}")
    rows = [image.pixels[((y + r) * image.width + x) * 4:((y + r) * image.width + x + width) * 4]
            for r in range(height)]
    return RgbaImage(width, height, b"".join(rows))


def oracle_settings(case: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The oracle knobs, bounded: a loose ceiling would let any render pass."""
    oracle = case.get("oracle") or {}
    params = case.get("params") or {}
    problems = []
    ceiling = oracle.get("max_mismatch_fraction")
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)) or not 0 <= ceiling <= MAX_ORACLE_MISMATCH:
        problems.append(f"oracle.max_mismatch_fraction must be in [0, {MAX_ORACLE_MISMATCH}]")
    if not str(oracle.get("justification", "")).strip():
        problems.append("oracle.justification is required for the mismatch ceiling")
    threshold = oracle.get("channel_threshold", 8)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= MAX_ORACLE_CHANNEL_THRESHOLD:
        problems.append(f"oracle.channel_threshold must be in [0, {MAX_ORACLE_CHANNEL_THRESHOLD}]")
    for key in ("oracle_window", "oracle_clip_x", "oracle_clip_y"):
        if key not in params:
            problems.append(f"params.{key} is required")
    return {"ceiling": ceiling, "threshold": threshold, "scale": case.get("scale", ph.DEFAULT_SCALE),
            "clip": (params.get("oracle_clip_x"), params.get("oracle_clip_y")),
            "window": params.get("oracle_window")}, problems


def browser_pixel_nodes(ir: Any) -> list[str]:
    found = []
    stack = [ir.get("root")] if isinstance(ir, dict) else []
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        attributes = node.get("attributes") or {}
        label = str(node.get("stable_anchor_id") or node.get("name"))
        if node.get("render_mode") in BROWSER_PIXEL_RENDER_MODES or node.get("capture_asset_id"):
            found.append(label)
        elif any(str(attributes.get(k, "")).startswith(prefix)
                 for k, prefixes in BROWSER_PIXEL_ATTRIBUTE_PREFIXES.items() for prefix in prefixes):
            found.append(label)
        elif any(attributes.get(k) in values for k, values in BROWSER_PIXEL_ATTRIBUTE_VALUES.items()):
            found.append(label)
        stack.extend(node.get("children") or [])
    return sorted(found)


def oracle_clip_image(directory: Path, settings: dict[str, Any], width: int, height: int) -> RgbaImage:
    full = decode_png_bytes((directory / "oracle.png").read_bytes())
    scale = settings["scale"]
    x, y = (int(round(float(v) * scale)) for v in settings["clip"])
    return crop(full, x, y, int(round(width * scale)), int(round(height * scale)))


def compare_oracle(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Each native render against the same Chromium capture, independently."""
    settings, problems = oracle_settings(case)
    if problems:
        return ph.lane_result("fail", reasons=[f"corpus: {p}" for p in problems])
    versions = [(d / "oracle-version.txt").read_text(encoding="utf-8").strip() for d in (ref, cand)]
    if not all(versions):
        return ph.lane_result("blocked", reasons=["oracle browser version is unrecorded: the oracle has no identity"])
    if versions[0] != versions[1]:
        return ph.lane_result("blocked", reasons=[f"the two sides ran different oracle browsers: {versions}"])
    requests = [ph.load_json(d / "oracle-request.json") for d in (ref, cand)]
    expected_request = {"window": settings["window"], "scale": str(settings["scale"]),
                        "clip": [float(v) for v in settings["clip"]]}
    for side, request in zip(("reference", "candidate"), requests):
        normalized = dict(request, clip=[float(v) for v in request.get("clip", [])])
        if normalized != expected_request:
            return ph.lane_result("blocked", reasons=[f"{side}: oracle ran with {request}, case asks {expected_request}"])
    hashes = [ph.sha256_file(d / "oracle.png") for d in (ref, cand)]
    if hashes[0] != hashes[1]:
        return ph.lane_result("blocked", reasons=[f"oracle not reproducible across two browser processes: {hashes}"])
    try:
        reference = oracle_clip_image(ref, settings, case["width"], case["height"])
    except (OSError, PngError) as error:
        return ph.lane_result("fail", reasons=[f"oracle capture unusable: {error}"])
    reasons: list[str] = []
    total = reference.width * reference.height
    ink = ph.ink_pixels(reference)
    if ink < MIN_ORACLE_INK_FRACTION * total:
        reasons.append(f"oracle ink {ink}/{total} below {MIN_ORACLE_INK_FRACTION:.1%}: "
                       "a blank browser capture is not a reference")
    per_side: dict[str, Any] = {}
    for side, directory in (("reference", ref), ("candidate", cand)):
        entry: dict[str, Any] = {}
        per_side[side] = entry
        try:
            ir = ph.load_json(directory / "design.ir.json")
            native = decode_png_bytes((directory / "render.png").read_bytes())
            capture = ph.load_json(directory / "capture.json")
        except (OSError, ValueError, PngError) as error:
            reasons.append(f"{side}: observation unusable: {error}")
            continue
        entry["browser_pixel_nodes"] = browser_pixel_nodes(ir)
        if entry["browser_pixel_nodes"]:
            reasons.append(f"{side}: native render paints browser pixels {entry['browser_pixel_nodes'][:4]}; "
                           "browser-import success is not native parity")
        captured_with = ((capture.get("provenance") or {}).get("browser") or {}).get("version")
        entry["capture_browser_version"] = captured_with
        if not captured_with or captured_with not in versions[0]:
            reasons.append(f"{side}: DesignIR was captured with browser {captured_with!r}, "
                           f"the oracle is {versions[0]!r}")
        if (native.width, native.height) != (reference.width, reference.height):
            reasons.append(f"{side}: native {(native.width, native.height)} != oracle clip "
                           f"{(reference.width, reference.height)}")
            continue
        exact, _ = compare_images(reference, native, threshold=0)
        entry["exact_equal"] = exact["differing_pixels"] == 0
        if entry["exact_equal"] and not (case.get("oracle") or {}).get("exact_match_allowed", False):
            reasons.append(f"{side}: native render is pixel-identical to the browser capture; a native "
                           "rasterizer does not reproduce Chromium exactly, so this is a screenshot")
        report, _ = compare_images(reference, native, threshold=settings["threshold"])
        fraction = report["differing_pixels"] / total
        entry.update(mismatch_pixels=report["differing_pixels"], mismatch_fraction=round(fraction, 6),
                     max_channel_error=report["max_channel_error"])
        if fraction > settings["ceiling"]:
            reasons.append(f"{side}: {report['differing_pixels']}/{total} pixels ({fraction:.2%}) differ from "
                           f"Chromium above channel error {settings['threshold']}; ceiling {settings['ceiling']:.2%}")
    a, b = per_side["reference"], per_side["candidate"]
    budget = int((case.get("oracle") or {}).get("regression_budget_pixels", 0))
    if "mismatch_pixels" in a and "mismatch_pixels" in b and b["mismatch_pixels"] > a["mismatch_pixels"] + budget:
        reasons.append(f"candidate is further from Chromium than reference: {b['mismatch_pixels']} > "
                       f"{a['mismatch_pixels']} + budget {budget}")
    return ph.lane_result("fail" if reasons else "pass", reasons=reasons[:25],
                          scope="browser-fidelity diagnostic; not native parity",
                          oracle={"sha256": hashes[0], "version": versions[0], "ink_pixels": ink,
                                  "clip_device_px": [reference.width, reference.height]},
                          sides=per_side, ceiling=settings["ceiling"], channel_threshold=settings["threshold"])


# ── reimport ───────────────────────────────────────────────────────────────


def anchor_index(ir: Any, volatile: list[str]) -> dict[str, list[str]]:
    """stable identity -> fingerprints of its own content (children excluded),
    in tree order, so a duplicated identity stays visible."""
    index: dict[str, list[str]] = {}
    root = ph.canonical(ir, volatile).get("root") if isinstance(ir, dict) else None
    queue = [root] if isinstance(root, dict) else []
    while queue:
        node = queue.pop(0)
        own = {k: v for k, v in node.items() if k != "children"}
        index.setdefault(str(node.get("stable_anchor_id") or ""), []).append(
            ph.sha256_bytes(json.dumps(own, sort_keys=True).encode()))
        queue.extend(c for c in node.get("children") or [] if isinstance(c, dict))
    return index


def anchor_diff(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, list[str]]:
    common = set(before) & set(after)
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(a for a in common if before[a] != after[a]),
            "preserved": sorted(a for a in common if before[a] == after[a])}


def bound_source(directory: Path) -> str | None:
    capture = ph.load_json(directory / "capture.json")
    return ((capture.get("provenance") or {}).get("source") or {}).get("sha256")


def compare_reimport(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    volatile = (case.get("volatile") or {}).get("structure", [])
    source = Path(case["source"]).name
    reasons: list[str] = []
    diffs: dict[str, dict[str, list[str]]] = {}
    revisions: dict[str, Any] = {}
    revised_inputs = []
    for side, directory in (("reference", ref), ("candidate", cand)):
        original = ph.sha256_file(directory / source)
        revised = ph.sha256_file(directory / "revision" / source)
        revised_inputs.append(revised)
        if revised == original:
            reasons.append(f"{side}: the revision edit did not change the source; nothing to detect")
        loaded = {}
        for label, sub, expected in (("first", directory, original), ("second", directory / "second", original),
                                     ("revision", directory / "revision", revised)):
            try:
                bound = bound_source(sub)
                loaded[label] = ph.load_json(sub / "design.ir.json")
            except (OSError, ValueError) as error:
                reasons.append(f"{side}/{label}: unreadable output: {error}")
                continue
            if bound != expected:
                reasons.append(f"{side}/{label}: stale output: bound to {bound}, imported input is {expected}")
        if len(loaded) != 3:
            continue
        first, second, revision = (ph.canonical(loaded[k], volatile) for k in ("first", "second", "revision"))
        reasons += [f"{side}: unchanged source reimported differently: {d}"
                    for d in ph.json_differences(first, second)[:5]]
        before, again, after = (anchor_index(loaded[k], volatile) for k in ("first", "second", "revision"))
        if before != again:
            reasons.append(f"{side}: unchanged source produced different identities")
        duplicated = sorted(a for a, v in before.items() if len(v) > 1)
        if duplicated:
            reasons.append(f"{side}: duplicate stable identities {duplicated[:4]}")
        diff = anchor_diff(before, after)
        diffs[side], revisions[side] = diff, revision
        if not (diff["added"] or diff["removed"] or diff["changed"]):
            reasons.append(f"{side}: changed revision not detected (no identity added, removed or changed)")
        if not diff["preserved"]:
            reasons.append(f"{side}: the revision kept no identity; untouched nodes must keep theirs")
    if revised_inputs[0] != revised_inputs[1]:
        reasons.append("the two sides revised the source differently")
    if len(diffs) == 2:
        strip = [{k: v for k, v in diffs[s].items() if k != "preserved"} for s in ("reference", "candidate")]
        if strip[0] != strip[1]:
            reasons.append(f"sides detected different changes: {strip[0]} != {strip[1]}")
        reasons += [f"revision DesignIR: {d}"
                    for d in ph.json_differences(revisions["reference"], revisions["candidate"])[:10]]
    return ph.lane_result("fail" if reasons else "pass", reasons=reasons[:25],
                          detected=diffs.get("reference"), volatile_ignored=volatile)


# ── expected rejection ─────────────────────────────────────────────────────


def compare_rejection(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    expect = case.get("expect") or {}
    if expect.get("outcome") not in EXPECTED_OUTCOMES or not expect.get("code"):
        return ph.lane_result("fail", reasons=[f"corpus: expect needs outcome in {EXPECTED_OUTCOMES} and a code"])
    observed = {side: ph.load_json(d / "outcome.json") for side, d in (("reference", ref), ("candidate", cand))}
    reasons = []
    for side, seen in observed.items():
        outcome, codes = seen.get("outcome"), seen.get("codes") or []
        if outcome in ("crash", "timeout"):
            reasons.append(f"{side}: {outcome}" + (f" (signal {seen.get('signal')})" if seen.get("signal") else "")
                           + "; a crash is not a rejection")
        elif outcome != expect["outcome"]:
            reasons.append(f"{side}: expected {expect['outcome']} [{expect['code']}], observed {outcome} {codes}")
        elif expect["code"] not in codes:
            reasons.append(f"{side}: {outcome} without diagnostic {expect['code']} (saw {codes})")
    keys = [(o.get("outcome"), sorted(o.get("codes") or [])) for o in observed.values()]
    if keys[0] != keys[1]:
        reasons.append(f"sides disagree: reference {keys[0]} != candidate {keys[1]}")
    return ph.lane_result("fail" if reasons else "pass", reasons=reasons, expected=expect,
                          observed={s: {"outcome": o.get("outcome"), "codes": o.get("codes")}
                                    for s, o in observed.items()})


# ── seeded negative controls ───────────────────────────────────────────────


def _paint_block(image: RgbaImage) -> RgbaImage:
    pixels = bytearray(image.pixels)
    cx, cy = image.width // 2, image.height // 2
    for y in range(max(0, cy - 12), min(image.height, cy + 12)):
        for x in range(max(0, cx - 12), min(image.width, cx + 12)):
            o = (y * image.width + x) * 4
            pixels[o:o + 3] = bytes(255 - c for c in pixels[o:o + 3])
    return RgbaImage(image.width, image.height, bytes(pixels))


def _render_is_capture(directory: Path) -> bool:
    request = ph.load_json(directory / "oracle-request.json")
    render = decode_png_bytes((directory / "render.png").read_bytes())
    settings = {"scale": float(request["scale"]), "clip": request["clip"]}
    scale = settings["scale"]
    clip = oracle_clip_image(directory, settings, render.width / scale, render.height / scale)
    (directory / "render.png").write_bytes(encode_png(clip))
    return True


def _edit_json(path: Path, edit) -> bool:
    if not path.is_file():
        return False
    document = ph.load_json(path)
    edit(document)
    ph.write_json(path, document)
    return True


def _set_browser_version(document: dict[str, Any]) -> None:
    document["provenance"]["browser"]["version"] = "1.0.0.0"


def _stale(directory: Path) -> bool:
    for name in ("design.ir.json", "capture.json"):
        shutil.copy2(directory / name, directory / "revision" / name)
    return True


def _undetected(directory: Path) -> bool:
    shutil.copy2(directory / "design.ir.json", directory / "revision" / "design.ir.json")
    return True


def _churn(document: dict[str, Any]) -> None:
    stack = [document.get("root")]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("stable_anchor_id"):
                node["stable_anchor_id"] += "~churned"
            stack.extend(node.get("children") or [])


def _outcome(**changes: Any):
    return lambda d: _edit_json(d / "outcome.json", lambda doc: doc.update(changes))


def _reason(*needles: str):
    def bites(result: dict[str, Any]) -> bool:
        text = " ".join(result.get("reasons") or [])
        return any(n in text for n in needles)
    return bites


def _faithful(case: dict[str, Any], source_root: Path, work_dir: Path | None = None):
    if "oracle" not in case["lanes"] or case["source_kind"] != "html-browser":
        return None
    return dict(case, source_kind="html-browser-faithful"), {}


def _one_side_accepts(case: dict[str, Any], source_root: Path, work_dir: Path | None = None):
    wellformed = case.get("wellformed")
    if "rejection" not in case["lanes"] or not wellformed:
        return None
    return dict(case, **wellformed), {}


ph.register_lane("oracle", ("design.ir.json", "render.png", "capture.json", "oracle.png",
                            "oracle-version.txt", "oracle-request.json"), compare_oracle)
ph.register_lane("reimport", ("design.ir.json", "capture.json", "second/design.ir.json", "second/capture.json",
                              "revision/design.ir.json", "revision/capture.json"), compare_reimport)
ph.register_lane("rejection", ("outcome.json",), compare_rejection)

ph.register_controls(
    artifact={
        "oracle-conflation-pixels": {"lane": "oracle", "candidate": _render_is_capture,
                                     "bites": _reason("pixel-identical to the browser capture")},
        "oracle-unstable": {"lane": "oracle", "expect": "blocked",
                            "candidate": lambda d: (ph.mutate_png(d / "oracle.png", ph.flip_center_pixel), True)[1],
                            "bites": _reason("not reproducible")},
        "oracle-blank-both": {"lane": "oracle",
                              "both": lambda d: (ph.mutate_png(d / "oracle.png", ph.uniform_image), True)[1],
                              "bites": _reason("blank browser capture")},
        "oracle-browser-mismatch": {"lane": "oracle",
                                    "candidate": lambda d: _edit_json(d / "capture.json", _set_browser_version),
                                    "bites": _reason("captured with browser")},
        "oracle-native-regression": {"lane": "oracle",
                                     "candidate": lambda d: (ph.mutate_png(d / "render.png", _paint_block), True)[1],
                                     "bites": _reason("further from Chromium")},
        "reimport-stale-output": {"lane": "reimport", "candidate": _stale, "bites": _reason("stale output")},
        "reimport-binding-absent": {"lane": "reimport", "expect": "blocked",
                                    "candidate": lambda d: ((d / "revision" / "capture.json").unlink(), True)[1]},
        "reimport-nondeterministic": {"lane": "reimport",
                                      "candidate": lambda d: ph.mutate_json_string(
                                          d / "second" / "design.ir.json", lambda k, v: k in ("name", "text")),
                                      "bites": _reason("reimported differently")},
        "reimport-revision-undetected-both": {"lane": "reimport", "both": _undetected,
                                              "bites": _reason("not detected")},
        "reimport-identity-churn": {"lane": "reimport",
                                    "candidate": lambda d: _edit_json(d / "revision" / "design.ir.json", _churn),
                                    "bites": _reason("kept no identity", "different changes")},
        "rejection-code-differs": {"lane": "rejection", "candidate": _outcome(codes=["unrelated-failure"]),
                                   "bites": _reason("sides disagree")},
        "rejection-accepted-both": {"lane": "rejection", "both": _outcome(outcome="accept", codes=[]),
                                    "bites": _reason("expected")},
        "rejection-crash": {"lane": "rejection", "candidate": _outcome(outcome="crash", signal=11, codes=[]),
                            "bites": _reason("a crash is not a rejection")},
    },
    rerun={
        "oracle-conflation-faithful": {"lane": "oracle", "prepare": _faithful,
                                       "bites": _reason("paints browser pixels")},
        "rejection-one-side-accepts": {"lane": "rejection", "prepare": _one_side_accepts,
                                       "identical_inputs": False, "bites": _reason("sides disagree")},
    },
)
