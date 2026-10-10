#!/usr/bin/env python3
"""Paired parity harness: one corpus, two implementations, one receipt.

The harness runs the same versioned corpus through a REFERENCE and a CANDIDATE
implementation and compares what each produced, lane by lane:

  identity     exported tree equals the origin closure, blob for blob
  structure    canonical DesignIR JSON
  raster       native render pixels, plus an ink-coverage floor
  geometry     laid-out view tree: kind, id, bounds, clipping, hit regions
  semantic     text/selection observation
  interaction  replayed pointer/keyboard log and the post-interaction render

An implementation is data, not code: a JSON description of the commands that
turn one corpus case into a fixed set of observation files (see
``implementations/``). Nothing here knows how either implementation works.

A receipt is accepting only when every selected lane of every case passed, the
denominator is non-empty, and both implementation identities are present.
``parity`` mode additionally refuses identical identities, so two runs of the
same implementation cannot be reported as parity; ``calibration`` mode requires
them identical and is reported as harness calibration, never parity.

``calibrate`` runs the positive comparison and then every seeded negative
control, and fails unless the positive accepts and every control is rejected
by the lane it targets. A harness whose controls do not fail proves nothing.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from vellum_image_compare import compare_images  # noqa: E402
from vellum_png import PngError, RgbaImage, decode_png_bytes, encode_png  # noqa: E402

CORPUS_SCHEMA = "vellum.parity.corpus.v1"
IMPLEMENTATION_SCHEMA = "vellum.parity.implementation.v1"
RECEIPT_SCHEMA = "vellum.parity.receipt.v1"
IDENTITY_SCHEMA = "vellum.parity.identity-receipt.v1"
CALIBRATION_SCHEMA = "vellum.parity.calibration.v1"

LANES = ("structure", "raster", "geometry", "semantic", "interaction")
LANE_ARTIFACTS = {
    "structure": ("design.ir.json",),
    "raster": ("render.png",),
    "geometry": ("layout.json",),
    "semantic": ("text.json",),
    "interaction": ("interaction.json", "interaction.png"),
}
SKIP_EXIT = 77
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEXT_BOX_TOLERANCE_PX = 0.5


class HarnessError(ValueError):
    """A malformed corpus, implementation, or manifest. Never a lane result."""


# ── small utilities ────────────────────────────────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def delete_pointer(document: Any, pointer: str) -> None:
    """Remove one RFC 6901 member if present. Only object members are removable."""
    if not pointer.startswith("/"):
        raise HarnessError(f"volatile field must be a JSON pointer: {pointer!r}")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]
    node = document
    for part in parts[:-1]:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def canonical(document: Any, volatile: list[str]) -> Any:
    clone = copy.deepcopy(document)
    for pointer in volatile:
        delete_pointer(clone, pointer)
    return clone


def json_differences(left: Any, right: Any, path: str = "", limit: int = 25) -> list[str]:
    out: list[str] = []

    def walk(a: Any, b: Any, where: str) -> None:
        if len(out) >= limit:
            return
        if type(a) is not type(b):
            out.append(f"{where or '/'}: type {type(a).__name__} != {type(b).__name__}")
        elif isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                child = f"{where}/{key}"
                if key not in a:
                    out.append(f"{child}: only in candidate")
                elif key not in b:
                    out.append(f"{child}: only in reference")
                else:
                    walk(a[key], b[key], child)
        elif isinstance(a, list):
            if len(a) != len(b):
                out.append(f"{where or '/'}: length {len(a)} != {len(b)}")
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{where}/{index}")
        elif a != b:
            out.append(f"{where or '/'}: {a!r} != {b!r}")

    walk(left, right, path)
    return out


# ── corpus and implementation descriptions ─────────────────────────────────


def validate_corpus(corpus: Any, corpus_dir: Path | None = None) -> list[dict[str, Any]]:
    if not isinstance(corpus, dict) or corpus.get("schema") != CORPUS_SCHEMA:
        raise HarnessError(f"corpus schema must be {CORPUS_SCHEMA}")
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise HarnessError("corpus.cases must be a list")
    if corpus_dir is not None:
        cases = [dict(case, _corpus_dir=str(corpus_dir)) for case in cases]
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", case_id):
            raise HarnessError(f"invalid case_id: {case_id!r}")
        if case_id in seen:
            raise HarnessError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        lanes = case.get("lanes")
        if not isinstance(lanes, list) or any(lane not in LANES for lane in lanes):
            raise HarnessError(f"{case_id}: lanes must be a subset of {LANES}")
        for key in ("source", "source_kind"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise HarnessError(f"{case_id}: missing {key}")
        for key in ("width", "height"):
            if not isinstance(case.get(key), int) or case[key] <= 0:
                raise HarnessError(f"{case_id}: {key} must be a positive integer")
        if case.get("source_base", "source-root") not in ("source-root", "corpus"):
            raise HarnessError(f"{case_id}: source_base must be source-root or corpus")
        if "interaction" in lanes and not case.get("interactions"):
            raise HarnessError(f"{case_id}: interaction lane selected without interactions")
    return cases


def validate_implementation(impl: Any) -> dict[str, Any]:
    if not isinstance(impl, dict) or impl.get("schema") != IMPLEMENTATION_SCHEMA:
        raise HarnessError(f"implementation schema must be {IMPLEMENTATION_SCHEMA}")
    for key in ("name", "repository", "steps", "source_kinds"):
        if key not in impl:
            raise HarnessError(f"implementation missing {key}")
    return impl


def implementation_identity(impl: dict[str, Any], tools: dict[str, Path]) -> dict[str, Any]:
    """Who produced the observations. Absent or malformed fields are kept as-is
    so that identity validation, not this function, decides acceptability."""
    return {
        "name": impl.get("name"),
        "repository": impl.get("repository"),
        "source_sha": impl.get("source_sha"),
        "tools": {
            name: {"path": str(path), "sha256": sha256_file(path) if path.is_file() else None}
            for name, path in sorted(tools.items())
        },
    }


def identity_errors(identity: dict[str, Any], side: str) -> list[str]:
    errors = []
    if not identity.get("repository"):
        errors.append(f"{side}: repository identity is absent")
    if not isinstance(identity.get("source_sha"), str) or not SHA1_RE.match(identity["source_sha"]):
        errors.append(f"{side}: source_sha is absent or not a 40-hex commit")
    tools = identity.get("tools") or {}
    if not tools:
        errors.append(f"{side}: no tool identities recorded")
    for name, tool in tools.items():
        if not isinstance(tool.get("sha256"), str) or not SHA256_RE.match(tool["sha256"]):
            errors.append(f"{side}: tool {name} has no content hash (missing binary?)")
    return errors


def identity_key(identity: dict[str, Any]) -> tuple:
    return (
        identity.get("repository"),
        identity.get("source_sha"),
        tuple(sorted((n, t.get("sha256")) for n, t in (identity.get("tools") or {}).items())),
    )


def encode_interactions(encoding: str, plan: list[dict[str, Any]]) -> list[str]:
    if encoding != "argv-click-drag-key":
        raise HarnessError(f"unsupported interaction encoding: {encoding}")
    argv: list[str] = []
    for step in plan:
        if "click" in step:
            x, y = step["click"]
            argv += ["--click", f"{x},{y}"]
        elif "drag" in step:
            (x1, y1), (x2, y2) = step["drag"]
            argv += ["--drag", f"{x1},{y1}:{x2},{y2}"]
        elif "key" in step:
            argv += ["--key", str(step["key"])]
        else:
            raise HarnessError(f"unknown interaction step: {step}")
    return argv


# ── running one side ───────────────────────────────────────────────────────


def case_root(case: dict[str, Any], source_root: Path) -> Path:
    """Sources resolve against --source-root, or against the corpus file's own
    directory for implementation-neutral fixtures the corpus carries itself."""
    if case.get("source_base") == "corpus":
        return Path(case["_corpus_dir"])
    return source_root


def stage_case(case: dict[str, Any], source_root: Path, destination: Path,
               source_override: bytes | None = None) -> dict[str, str]:
    """Copy the case source (and declared companions) into a side directory so
    both sides read byte-identical inputs at identical relative paths."""
    destination.mkdir(parents=True, exist_ok=True)
    source_root = case_root(case, source_root)
    source = source_root / case["source"]
    if not source.is_file():
        raise HarnessError(f"{case['case_id']}: source not found: {source}")
    staged = destination / source.name
    staged.write_bytes(source_override if source_override is not None else source.read_bytes())
    hashes = {source.name: sha256_file(staged)}
    for companion in case.get("stage", []):
        origin = source_root / companion
        target = destination / origin.name
        if origin.is_dir():
            shutil.copytree(origin, target, dirs_exist_ok=True)
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    hashes[path.relative_to(destination).as_posix()] = sha256_file(path)
        elif origin.is_file():
            shutil.copy2(origin, target)
            hashes[origin.name] = sha256_file(target)
        else:
            raise HarnessError(f"{case['case_id']}: staged companion not found: {origin}")
    return hashes


def expand(arg: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise HarnessError(f"unknown placeholder {{{key}}}")
        return values[key]

    return re.sub(r"\{([a-z_:]+)\}", replace, arg)


def run_side(case: dict[str, Any], impl: dict[str, Any], tools: dict[str, Path],
             source_root: Path, out: Path, *, interactions: list[dict[str, Any]] | None = None,
             source_override: bytes | None = None, skip_steps: tuple[str, ...] = (),
             timeout: int = 300) -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    inputs = stage_case(case, source_root, out, source_override)
    source_name = Path(case["source"]).name
    kind_args = impl["source_kinds"].get(case["source_kind"])
    if kind_args is None:
        raise HarnessError(f"{impl['name']} does not declare source kind {case['source_kind']}")
    values = {f"tool:{name}": str(path) for name, path in tools.items()}
    values.update({"source": source_name, "width": str(case["width"]), "height": str(case["height"])})
    plan = interactions if interactions is not None else case.get("interactions", [])
    steps_out = []
    for step in impl["steps"]:
        if step.get("when") == "interactions" and "interaction" not in case["lanes"]:
            continue
        if "source_kinds" in step and case["source_kind"] not in step["source_kinds"]:
            continue
        argv: list[str] = []
        for arg in step["argv"]:
            if arg == "@source_kind_args":
                argv += [expand(a, values) for a in kind_args]
            elif arg == "@interactions":
                argv += encode_interactions(impl.get("interaction_encoding", ""), plan)
            else:
                argv.append(expand(arg, values))
        record: dict[str, Any] = {"id": step["id"], "argv": argv}
        if step["id"] in skip_steps:
            # A planted adapter skip: the step reports "not run" exactly as a
            # tool that cannot run here would. It must never read as a pass.
            argv = [sys.executable, "-c", f"import sys; sys.exit({SKIP_EXIT})"]
            record["planted_skip"] = True
        started = time.monotonic()
        try:
            result = subprocess.run(argv, cwd=out, capture_output=True, text=True, timeout=timeout)
            record["exit"] = result.returncode
            record["stderr_tail"] = result.stderr[-2000:]
        except (OSError, subprocess.TimeoutExpired) as error:
            record["exit"] = None
            record["error"] = str(error)
        record["seconds"] = round(time.monotonic() - started, 3)
        record["status"] = ("ok" if record["exit"] == 0 else
                            "skipped" if record["exit"] == SKIP_EXIT else "failed")
        steps_out.append(record)
        if record["status"] != "ok":
            break
    artifacts = {
        path.name: sha256_file(path)
        for path in sorted(out.iterdir())
        if path.is_file() and path.name in {a for names in LANE_ARTIFACTS.values() for a in names}
    }
    return {"inputs": inputs, "steps": steps_out, "artifacts": artifacts,
            "interaction_plan": plan}


# ── lane comparators ───────────────────────────────────────────────────────


def lane_result(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, **details}


def missing(lane: str, ref: Path, cand: Path) -> dict[str, Any] | None:
    absent = [f"{side}/{name}" for side, root in (("reference", ref), ("candidate", cand))
              for name in LANE_ARTIFACTS[lane] if not (root / name).is_file()]
    if absent:
        return lane_result("blocked", reasons=[f"missing artifact {a}" for a in absent])
    return None


def node_count(ir: Any) -> int:
    root = ir.get("root") if isinstance(ir, dict) else None
    if not isinstance(root, dict):
        return 0
    stack, count = [root], 0
    while stack:
        node = stack.pop()
        count += 1
        stack.extend(c for c in node.get("children", []) if isinstance(c, dict))
    return count


def compare_structure(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    volatile = case.get("volatile", {}).get("structure", [])
    try:
        a = canonical(load_json(ref / "design.ir.json"), volatile)
        b = canonical(load_json(cand / "design.ir.json"), volatile)
    except (OSError, json.JSONDecodeError) as error:
        return lane_result("fail", reasons=[f"unreadable DesignIR: {error}"])
    counts = (node_count(a), node_count(b))
    reasons = []
    if min(counts) < 2:
        reasons.append(f"vacuous DesignIR: node counts {counts} (need a root and at least one child)")
    diffs = json_differences(a, b)
    reasons += diffs
    return lane_result("fail" if reasons else "pass", reasons=reasons,
                       node_counts=list(counts), volatile_ignored=volatile)


def ink_pixels(image: RgbaImage) -> int:
    background = image.pixels[0:4]
    return sum(1 for offset in range(0, len(image.pixels), 4)
               if image.pixels[offset:offset + 4] != background)


def compare_png_pair(ref_png: Path, cand_png: Path, threshold: int, min_ink: int) -> dict[str, Any]:
    try:
        a = decode_png_bytes(ref_png.read_bytes())
        b = decode_png_bytes(cand_png.read_bytes())
    except (OSError, PngError) as error:
        return lane_result("fail", reasons=[f"undecodable PNG: {error}"])
    reasons = []
    ink = (ink_pixels(a), ink_pixels(b))
    if min(ink) < min_ink:
        reasons.append(f"ink coverage {ink} below floor {min_ink}: a blank or uniform render is not evidence")
    if (a.width, a.height) != (b.width, b.height):
        reasons.append(f"dimensions {(a.width, a.height)} != {(b.width, b.height)}")
        return lane_result("fail", reasons=reasons, ink_pixels=list(ink))
    report, _diff = compare_images(a, b, threshold=threshold)
    if report["differing_pixels"]:
        reasons.append(f"{report['differing_pixels']} pixels differ above threshold {threshold} "
                       f"(max channel error {report['max_channel_error']})")
    return lane_result("fail" if reasons else "pass", reasons=reasons, ink_pixels=list(ink),
                       dimensions=[a.width, a.height], differing_pixels=report["differing_pixels"],
                       threshold=threshold)


def compare_raster(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    return compare_png_pair(ref / "render.png", cand / "render.png",
                            int(case.get("raster_threshold", 0)), int(case.get("min_ink_pixels", 1)))


def rect_delta(a: dict[str, Any], b: dict[str, Any]) -> float:
    return max(abs(float(a.get(k, 0)) - float(b.get(k, 0))) for k in ("x", "y", "w", "h"))


def compare_geometry(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    try:
        a = load_json(ref / "layout.json").get("nodes", [])
        b = load_json(cand / "layout.json").get("nodes", [])
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        return lane_result("fail", reasons=[f"unreadable layout: {error}"])
    reasons = []
    sized = [sum(1 for n in nodes[1:] if n.get("rect", {}).get("w", 0) > 0 and n.get("rect", {}).get("h", 0) > 0)
             for nodes in (a, b)]
    if min(sized) < 1:
        reasons.append(f"vacuous layout: sized non-root nodes {sized}")
    if len(a) != len(b):
        reasons.append(f"node count {len(a)} != {len(b)}")
    exact = ("id", "kind", "visible", "overflow", "z_order", "clipping", "hit_regions")
    for index, (x, y) in enumerate(zip(a, b)):
        label = f"node {index} ({x.get('kind')} {x.get('id')!r})"
        for key in exact:
            if x.get(key) != y.get(key):
                reasons.append(f"{label}: {key} {x.get(key)!r} != {y.get(key)!r}")
        if x.get("rect") != y.get("rect"):
            reasons.append(f"{label}: rect {x.get('rect')} != {y.get('rect')}")
        boxes = (x.get("measured_text_boxes") or [], y.get("measured_text_boxes") or [])
        if len(boxes[0]) != len(boxes[1]):
            reasons.append(f"{label}: text box count {len(boxes[0])} != {len(boxes[1])}")
        for p, q in zip(*boxes):
            if p.get("text") != q.get("text") or rect_delta(p.get("rect", {}), q.get("rect", {})) > TEXT_BOX_TOLERANCE_PX:
                reasons.append(f"{label}: text box {p} != {q}")
        if len(reasons) > 25:
            break
    return lane_result("fail" if reasons else "pass", reasons=reasons[:25], node_counts=[len(a), len(b)])


def compare_semantic(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    volatile = case.get("volatile", {}).get("semantic", [])
    records = case.get("semantic_records", "/runs")
    try:
        a = canonical(load_json(ref / "text.json"), volatile)
        b = canonical(load_json(cand / "text.json"), volatile)
    except (OSError, json.JSONDecodeError) as error:
        return lane_result("fail", reasons=[f"unreadable semantic observation: {error}"])
    reasons = []
    key = records.lstrip("/")
    counts = [len(doc.get(key) or []) if isinstance(doc, dict) else 0 for doc in (a, b)]
    if min(counts) < 1:
        reasons.append(f"vacuous semantic observation: {records} counts {counts}")
    reasons += json_differences(a, b)
    return lane_result("fail" if reasons else "pass", reasons=reasons, record_counts=counts,
                       volatile_ignored=volatile)


def compare_interaction(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    volatile = case.get("volatile", {}).get("interaction", [])
    try:
        a = canonical(load_json(ref / "interaction.json"), volatile)
        b = canonical(load_json(cand / "interaction.json"), volatile)
    except (OSError, json.JSONDecodeError) as error:
        return lane_result("fail", reasons=[f"unreadable interaction log: {error}"])
    reasons = []
    for side, log in (("reference", a), ("candidate", b)):
        steps = log.get("steps") or []
        if len(steps) != len(case["interactions"]):
            reasons.append(f"{side}: {len(steps)} logged steps for {len(case['interactions'])} planned")
        if not any(step.get("handled") for step in steps):
            reasons.append(f"{side}: no step reached a control; the plan exercised nothing")
    reasons += json_differences(a, b)
    pixels = compare_png_pair(ref / "interaction.png", cand / "interaction.png",
                              int(case.get("raster_threshold", 0)), int(case.get("min_ink_pixels", 1)))
    reasons += [f"post-interaction render: {r}" for r in pixels.get("reasons", [])]
    return lane_result("fail" if reasons else "pass", reasons=reasons[:25])


COMPARATORS: dict[str, Callable[[Path, Path, dict[str, Any]], dict[str, Any]]] = {
    "structure": compare_structure,
    "raster": compare_raster,
    "geometry": compare_geometry,
    "semantic": compare_semantic,
    "interaction": compare_interaction,
}


def side_blocked(run: dict[str, Any]) -> str | None:
    for step in run["steps"]:
        if step["status"] != "ok":
            return f"step {step['id']} {step['status']} (exit {step.get('exit')})"
    return None


def evaluate_case(case: dict[str, Any], ref_dir: Path, cand_dir: Path,
                  ref_run: dict[str, Any], cand_run: dict[str, Any]) -> dict[str, Any]:
    lanes: dict[str, Any] = {}
    blocked = [f"{side}: {why}" for side, run in (("reference", ref_run), ("candidate", cand_run))
               if (why := side_blocked(run))]
    for lane in LANES:
        if lane not in case["lanes"]:
            lanes[lane] = lane_result("not_selected")
            continue
        absent = missing(lane, ref_dir, cand_dir)
        if absent:
            absent["reasons"] = blocked + absent["reasons"]
            lanes[lane] = absent
        elif blocked:
            lanes[lane] = lane_result("blocked", reasons=blocked)
        else:
            lanes[lane] = COMPARATORS[lane](ref_dir, cand_dir, case)
    inputs_match = ref_run["inputs"] == cand_run["inputs"]
    return {"case_id": case["case_id"], "inputs_identical": inputs_match,
            "reference": ref_run, "candidate": cand_run, "lanes": lanes}


def acceptance(mode: str, cases: list[dict[str, Any]], reference: dict[str, Any],
               candidate: dict[str, Any], *, require_identical_inputs: bool = True) -> dict[str, Any]:
    reasons = identity_errors(reference, "reference") + identity_errors(candidate, "candidate")
    same = identity_key(reference) == identity_key(candidate)
    if mode == "parity" and same:
        reasons.append("parity mode: reference and candidate identities are identical; "
                       "this compares an implementation with itself")
    if mode == "calibration" and not same:
        reasons.append("calibration mode: reference and candidate identities differ")
    if not cases:
        reasons.append("empty corpus: no case was compared")
    selected = 0
    for result in cases:
        statuses = {lane: r["status"] for lane, r in result["lanes"].items()}
        chosen = {lane: s for lane, s in statuses.items() if s != "not_selected"}
        selected += len(chosen)
        if not chosen:
            reasons.append(f"{result['case_id']}: no lane selected")
        for lane, status in chosen.items():
            if status != "pass":
                reasons.append(f"{result['case_id']}/{lane}: {status}")
        if require_identical_inputs and not result["inputs_identical"]:
            reasons.append(f"{result['case_id']}: reference and candidate inputs differ")
    return {"accepting": not reasons, "scope": "harness-calibration" if mode == "calibration" else "parity",
            "selected_lane_results": selected, "reasons": reasons}


# ── whole-corpus run ───────────────────────────────────────────────────────


def resolve_tools(impl: dict[str, Any], overrides: dict[str, str]) -> dict[str, Path]:
    """Tool paths resolve against the invoking directory, never against a
    case's working directory, so a relative default cannot silently point at
    a different binary per case."""
    tools = {name: Path(path) for name, path in (impl.get("tools") or {}).items()}
    tools.update({name: Path(path) for name, path in overrides.items()})
    return {name: path.resolve() for name, path in tools.items()}


def run_corpus(corpus_path: Path, source_root: Path, reference_path: Path, candidate_path: Path,
               out: Path, *, mode: str, tool_overrides: dict[str, dict[str, str]] | None = None,
               source_shas: dict[str, str | None] | None = None) -> dict[str, Any]:
    corpus = load_json(corpus_path)
    cases = validate_corpus(corpus, corpus_path.resolve().parent)
    impls = {}
    for side, path in (("reference", reference_path), ("candidate", candidate_path)):
        impl = validate_implementation(load_json(path))
        if (source_shas or {}).get(side):
            impl = dict(impl, source_sha=source_shas[side])
        impls[side] = (impl, resolve_tools(impl, (tool_overrides or {}).get(side, {})))
    ref_impl, ref_tools = impls["reference"]
    cand_impl, cand_tools = impls["candidate"]
    results = []
    for case in cases:
        ref_dir = out / case["case_id"] / "reference"
        cand_dir = out / case["case_id"] / "candidate"
        ref_run = run_side(case, ref_impl, ref_tools, source_root, ref_dir)
        cand_run = run_side(case, cand_impl, cand_tools, source_root, cand_dir)
        results.append(evaluate_case(case, ref_dir, cand_dir, ref_run, cand_run))
    return assemble_receipt(mode, corpus_path, corpus, ref_impl, ref_tools, cand_impl, cand_tools, results)


def assemble_receipt(mode: str, corpus_path: Path, corpus: dict[str, Any],
                     ref_impl: dict[str, Any], ref_tools: dict[str, Path],
                     cand_impl: dict[str, Any], cand_tools: dict[str, Path],
                     results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    reference = implementation_identity(ref_impl, ref_tools)
    candidate = implementation_identity(cand_impl, cand_tools)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "mode": mode,
        "corpus": {"path": str(corpus_path), "sha256": sha256_bytes(
            json.dumps(corpus, sort_keys=True).encode()), "cases": len(results)},
        "reference": reference,
        "candidate": candidate,
        "cases": results,
        **extra,
    }
    receipt["verdict"] = acceptance(mode, results, reference, candidate)
    return receipt


# ── identity lane ──────────────────────────────────────────────────────────


def tree_blobs(root: Path, prefixes: list[str]) -> dict[str, str]:
    blobs: dict[str, str] = {}
    for prefix in prefixes:
        base = root / prefix
        if base.is_file():
            blobs[prefix] = git_blob_sha(base.read_bytes())
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.is_symlink():
                blobs[path.relative_to(root).as_posix()] = git_blob_sha(path.read_bytes())
    return blobs


def git_tree_blobs(repo: Path, commit: str, prefixes: list[str]) -> dict[str, str]:
    if not SHA1_RE.match(commit):
        raise HarnessError("identity origin commit must be a 40-hex SHA")
    output = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", commit, "--", *prefixes],
                                     text=True)
    blobs: dict[str, str] = {}
    for line in output.splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, object_id = meta.split()
        if kind == "blob" and mode != "120000":
            blobs[path] = object_id
    return blobs


def compare_identity(manifest: dict[str, Any], origin: dict[str, str], exported: dict[str, str]) -> dict[str, Any]:
    reasons = []
    if not manifest.get("roots"):
        reasons.append("empty closure manifest: no roots declared")
    if not origin:
        reasons.append("origin closure is empty")
    only_origin = sorted(set(origin) - set(exported))
    only_export = sorted(set(exported) - set(origin))
    changed = sorted(p for p in set(origin) & set(exported) if origin[p] != exported[p])
    reasons += [f"missing from export: {p}" for p in only_origin[:25]]
    reasons += [f"not in origin closure: {p}" for p in only_export[:25]]
    reasons += [f"blob differs: {p} {origin[p]} != {exported[p]}" for p in changed[:25]]
    return {
        "schema": IDENTITY_SCHEMA,
        "unit": manifest.get("unit"),
        "roots": manifest.get("roots", []),
        "origin_files": len(origin),
        "exported_files": len(exported),
        "origin_tree_sha256": sha256_bytes(json.dumps(origin, sort_keys=True).encode()),
        "exported_tree_sha256": sha256_bytes(json.dumps(exported, sort_keys=True).encode()),
        "status": "fail" if reasons else "pass",
        "reasons": reasons,
    }


def export_closure(repo: Path, commit: str, roots: list[str], destination: Path) -> None:
    """A stand-in export for calibration: the closure roots at commit, extracted
    with git archive. The real export comes from the protected mirror ref."""
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", commit, "--", *roots],
                             check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive, check=True)


def calibrate_identity(manifest: dict[str, Any], repo: Path, commit: str, out: Path) -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    roots = manifest.get("roots") or []
    origin = git_tree_blobs(repo, commit, roots)
    export = out / "export"
    export_closure(repo, commit, roots, export)
    positive = compare_identity(manifest, origin, tree_blobs(export, roots))

    def control(name: str, mutate: Callable[[Path], None], use_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        tree = out / "controls" / name
        shutil.copytree(export, tree, symlinks=True)
        mutate(tree)
        chosen = use_manifest if use_manifest is not None else manifest
        chosen_roots = chosen.get("roots") or []
        report = compare_identity(chosen, git_tree_blobs(repo, commit, chosen_roots) if chosen_roots else {},
                                  tree_blobs(tree, chosen_roots))
        return {"control": name, "status": report["status"], "reasons": report["reasons"][:3],
                "outcome": "REJECTED as required" if report["status"] == "fail" else "CONTROL DID NOT FAIL"}

    files = sorted(p for p in export.rglob("*") if p.is_file() and not p.is_symlink())
    if not files:
        raise HarnessError("identity calibration export is empty")
    victim = files[len(files) // 2]

    def flip_byte(tree: Path) -> None:
        target = tree / victim.relative_to(export)
        data = bytearray(target.read_bytes())
        data[len(data) // 2] ^= 0x01
        target.write_bytes(bytes(data))

    controls = [
        control("one-byte-mutation", flip_byte),
        control("file-removed", lambda tree: (tree / victim.relative_to(export)).unlink()),
        control("extra-file", lambda tree: (tree / roots[0] / "untracked_extra.txt").write_text("extra\n")),
        control("empty-manifest", lambda tree: None, use_manifest={"unit": manifest.get("unit"), "roots": []}),
    ]
    failures = [c["control"] for c in controls if c["outcome"] != "REJECTED as required"]
    return {
        "schema": IDENTITY_SCHEMA,
        "calibrated": positive["status"] == "pass" and not failures,
        "origin": {"repo": str(repo), "commit": commit},
        "victim_file": victim.relative_to(export).as_posix(),
        "positive": positive,
        "controls": controls,
        "control_failures": failures,
    }


# ── calibration: positive plus seeded negative controls ────────────────────


def mutate_json_string(path: Path, predicate: Callable[[str, Any], bool]) -> bool:
    """Change one character of the first string member matching predicate."""
    document = load_json(path)
    done = False

    def walk(node: Any) -> None:
        nonlocal done
        if done:
            return
        if isinstance(node, dict):
            for key in sorted(node):
                value = node[key]
                if isinstance(value, str) and value and predicate(key, value):
                    node[key] = value[:-1] + ("X" if value[-1] != "X" else "Y")
                    done = True
                    return
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    if done:
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return done


def mutate_png(path: Path, transform: Callable[[RgbaImage], RgbaImage]) -> None:
    path.write_bytes(encode_png(transform(decode_png_bytes(path.read_bytes()))))


def flip_center_pixel(image: RgbaImage) -> RgbaImage:
    pixels = bytearray(image.pixels)
    offset = ((image.height // 2) * image.width + image.width // 2) * 4
    pixels[offset] = (pixels[offset] + 1) % 256
    return RgbaImage(image.width, image.height, bytes(pixels))


def uniform_image(image: RgbaImage) -> RgbaImage:
    return RgbaImage(image.width, image.height, image.pixels[0:4] * (image.width * image.height))


def shift_last_node(path: Path) -> bool:
    layout = load_json(path)
    nodes = layout.get("nodes") or []
    if len(nodes) < 2:
        return False
    nodes[-1]["rect"]["x"] = nodes[-1]["rect"]["x"] + 1
    write_json(path, layout)
    return True


def empty_layout(path: Path) -> bool:
    layout = load_json(path)
    layout["nodes"] = []
    write_json(path, layout)
    return True


def perturb_interaction_state(path: Path) -> bool:
    log = load_json(path)
    for step in reversed(log.get("steps") or []):
        for entry in step.get("state") or []:
            if isinstance(entry.get("value"), (int, float)):
                entry["value"] = entry["value"] + 0.125
                write_json(path, log)
                return True
            if isinstance(entry.get("on"), bool):
                entry["on"] = not entry["on"]
                write_json(path, log)
                return True
    return False


def moved_pointer_plan(case: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Move the first pointer step to the case's bottom-right corner, which the
    corpus reserves as bare root background: the step must stop reaching the
    control it reached before."""
    x, y = case["width"] - 2, case["height"] - 2
    plan = case["interactions"]
    for index, step in enumerate(plan):
        if "click" in step or "drag" in step:
            moved = copy.deepcopy(plan)
            moved[index] = {"click": [x, y]} if "click" in step else {"drag": [[x, y], [x, y - 40]]}
            return moved
    return None


def changed_key_plan(case: dict[str, Any]) -> list[dict[str, Any]] | None:
    plan = case["interactions"]
    for index, step in enumerate(plan):
        if "key" in step:
            changed = copy.deepcopy(plan)
            changed[index] = {"key": "escape" if step["key"] != "escape" else "tab"}
            return changed
    return None


def one_byte_source(data: bytes) -> bytes:
    """Change one byte inside the first quoted string value of the source."""
    match = re.search(rb'"name"\s*:\s*"([^"\\]+)"', data) or re.search(rb">([A-Za-z][^<]{2,})<", data)
    if not match:
        raise HarnessError("no mutable text found in source")
    offset = match.start(1)
    return data[:offset] + bytes([data[offset] ^ 0x01]) + data[offset + 1:]


ARTIFACT_CONTROLS: dict[str, dict[str, Any]] = {
    "structure-one-byte": {"lane": "structure", "candidate": lambda d: mutate_json_string(
        d / "design.ir.json", lambda k, v: k in ("name", "label", "text"))},
    "raster-one-pixel": {"lane": "raster", "candidate": lambda d: (mutate_png(d / "render.png", flip_center_pixel), True)[1]},
    "raster-blank-both": {"lane": "raster", "both": lambda d: (mutate_png(d / "render.png", uniform_image), True)[1]},
    "missing-render": {"lane": "raster", "expect": "blocked", "candidate": lambda d: ((d / "render.png").unlink(), True)[1]},
    "geometry-shift-node": {"lane": "geometry", "candidate": lambda d: shift_last_node(d / "layout.json")},
    "geometry-zero-nodes-both": {"lane": "geometry", "both": lambda d: empty_layout(d / "layout.json")},
    "semantic-one-byte": {"lane": "semantic", "candidate": lambda d: mutate_json_string(
        d / "text.json", lambda k, v: k == "text")},
    "interaction-state": {"lane": "interaction", "candidate": lambda d: perturb_interaction_state(d / "interaction.json")},
}
RERUN_CONTROLS: dict[str, dict[str, Any]] = {
    "input-one-byte": {"lane": "structure"},
    "viewport-changed": {"lane": "raster"},
    "asset-missing": {"lane": "raster"},
    "interaction-pointer-moved": {"lane": "interaction"},
    "interaction-key-changed": {"lane": "interaction"},
    "planted-skip": {"lane": "*", "expect": "blocked"},
}
RECEIPT_CONTROLS = ("identity-absent", "identity-same-in-parity-mode", "empty-corpus", "no-lane-selected")


def calibrate(corpus_path: Path, source_root: Path, impl_path: Path, out: Path,
              tool_overrides: dict[str, str] | None = None,
              source_sha: str | None = None) -> dict[str, Any]:
    corpus = load_json(corpus_path)
    cases = validate_corpus(corpus, corpus_path.resolve().parent)
    impl = validate_implementation(load_json(impl_path))
    if source_sha is not None:
        impl = dict(impl, source_sha=source_sha)
    tools = resolve_tools(impl, tool_overrides or {})
    if out.exists():
        shutil.rmtree(out)

    def side(case: dict[str, Any], label: str, **kwargs: Any) -> tuple[Path, dict[str, Any]]:
        directory = out / "runs" / case["case_id"] / label
        return directory, run_side(case, impl, tools, source_root, directory, **kwargs)

    baseline: dict[str, tuple[Path, dict[str, Any], Path, dict[str, Any]]] = {}
    positive_results = []
    for case in cases:
        ref_dir, ref_run = side(case, "reference")
        cand_dir, cand_run = side(case, "candidate")
        baseline[case["case_id"]] = (ref_dir, ref_run, cand_dir, cand_run)
        positive_results.append(evaluate_case(case, ref_dir, cand_dir, ref_run, cand_run))

    def receipt(results: list[dict[str, Any]], mode: str = "calibration",
                cand: dict[str, Any] | None = None) -> dict[str, Any]:
        return assemble_receipt(mode, corpus_path, corpus, impl, tools, cand or impl, tools, results)

    positive = receipt(positive_results)
    controls: list[dict[str, Any]] = []

    def record(name: str, lane: str, expect: str, results: list[dict[str, Any]] | None,
               verdict: dict[str, Any], applied_cases: list[str]) -> None:
        if not applied_cases:
            controls.append({"control": name, "lane": lane, "applied_cases": [],
                             "rejected": False, "lane_hit": False,
                             "outcome": "NOT APPLICABLE (no case exercises it)"})
            return
        hit = True
        if results is not None and lane != "*":
            hit = all(r["lanes"][lane]["status"] in (("blocked",) if expect == "blocked" else ("fail",))
                      for r in results if r["case_id"] in applied_cases)
        elif results is not None:
            hit = all(any(l["status"] == "blocked" for l in r["lanes"].values())
                      for r in results if r["case_id"] in applied_cases)
        rejected = not verdict["accepting"]
        evidence = {}
        for r in results or []:
            lanes = [lane] if lane != "*" else [l for l, v in r["lanes"].items() if v["status"] != "not_selected"][:1]
            for l in lanes:
                evidence[f"{r['case_id']}/{l}"] = {"status": r["lanes"][l]["status"],
                                                    "reasons": (r["lanes"][l].get("reasons") or [])[:3]}
        controls.append({
            "control": name, "lane": lane, "expected_status": expect,
            "applied_cases": applied_cases, "rejected": rejected, "lane_hit": hit,
            "lane_evidence": evidence,
            "outcome": "REJECTED as required" if rejected and hit else "CONTROL DID NOT FAIL",
            "verdict_reasons": verdict["reasons"][:12],
        })

    for name, spec in ARTIFACT_CONTROLS.items():
        results, applied = [], []
        for case in cases:
            ref_dir, ref_run, cand_dir, cand_run = baseline[case["case_id"]]
            if spec["lane"] not in case["lanes"]:
                continue
            work = out / "controls" / name / case["case_id"]
            mref, mcand = work / "reference", work / "candidate"
            shutil.copytree(ref_dir, mref)
            shutil.copytree(cand_dir, mcand)
            changed = spec["candidate"](mcand) if "candidate" in spec else (
                spec["both"](mref) and spec["both"](mcand))
            if not changed:
                continue
            applied.append(case["case_id"])
            results.append(evaluate_case(case, mref, mcand, ref_run, cand_run))
        record(name, spec["lane"], spec.get("expect", "fail"), results,
               receipt(results)["verdict"] if results else {"accepting": True, "reasons": []}, applied)

    for name, spec in RERUN_CONTROLS.items():
        results, applied = [], []
        for case in cases:
            ref_dir, ref_run, _cand_dir, _cand_run = baseline[case["case_id"]]
            kwargs: dict[str, Any] = {}
            run_case = case
            if name == "input-one-byte":
                if "structure" not in case["lanes"]:
                    continue
                kwargs["source_override"] = one_byte_source(
                    (case_root(case, source_root) / case["source"]).read_bytes())
            elif name == "viewport-changed":
                if "raster" not in case["lanes"]:
                    continue
                run_case = dict(case, width=case["width"] + 8)
            elif name == "asset-missing":
                if "raster" not in case["lanes"] or not case.get("stage"):
                    continue
                run_case = dict(case, stage=[])
            elif name == "planted-skip":
                kwargs["skip_steps"] = ("observe",)
            else:
                if "interaction" not in case["lanes"]:
                    continue
                plan = (moved_pointer_plan if name == "interaction-pointer-moved" else changed_key_plan)(case)
                if plan is None:
                    continue
                kwargs["interactions"] = plan
            cand_dir = out / "controls" / name / case["case_id"] / "candidate"
            cand_run = run_side(run_case, impl, tools, source_root, cand_dir, **kwargs)
            applied.append(case["case_id"])
            results.append(evaluate_case(case, ref_dir, cand_dir, ref_run, cand_run))
        # input-one-byte deliberately feeds different inputs, so the identical-
        # inputs rule would reject it on its own; the lane, not that rule, must
        # do the rejecting.
        verdict = (acceptance("calibration", results, positive["reference"], positive["candidate"],
                              require_identical_inputs=name not in ("input-one-byte", "asset-missing"))
                   if results else {"accepting": True, "reasons": []})
        record(name, spec["lane"], spec.get("expect", "fail"), results, verdict, applied)

    absent = dict(impl, source_sha=None)
    record("identity-absent", "*", "fail", None, receipt(positive_results, cand=absent)["verdict"], ["*"])
    record("identity-same-in-parity-mode", "*", "fail", None,
           receipt(positive_results, mode="parity")["verdict"], ["*"])
    record("empty-corpus", "*", "fail", None, receipt([])["verdict"], ["*"])
    unselected = [dict(r, lanes={lane: lane_result("not_selected") for lane in LANES})
                  for r in positive_results[:1]]
    record("no-lane-selected", "*", "fail", None, receipt(unselected)["verdict"], ["*"])

    failures = [c for c in controls if c["outcome"] != "REJECTED as required"]
    calibrated = positive["verdict"]["accepting"] and not failures
    return {
        "schema": CALIBRATION_SCHEMA,
        "calibrated": calibrated,
        "positive": positive,
        "controls": controls,
        "control_failures": [c["control"] for c in failures],
    }


# ── command line ───────────────────────────────────────────────────────────


def parse_tool_overrides(values: list[str]) -> dict[str, str]:
    overrides = {}
    for value in values or []:
        name, sep, path = value.partition("=")
        if not sep:
            raise HarnessError(f"--tool expects name=path, got {value!r}")
        overrides[name] = path
    return overrides


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="compare reference and candidate over a corpus")
    run.add_argument("--corpus", required=True, type=Path)
    run.add_argument("--source-root", required=True, type=Path)
    run.add_argument("--reference", required=True, type=Path)
    run.add_argument("--candidate", required=True, type=Path)
    run.add_argument("--mode", choices=("parity", "calibration"), default="parity")
    run.add_argument("--reference-tool", action="append", default=[])
    run.add_argument("--candidate-tool", action="append", default=[])
    run.add_argument("--reference-sha")
    run.add_argument("--candidate-sha")
    run.add_argument("--out", required=True, type=Path)

    cal = sub.add_parser("calibrate", help="positive comparison plus every negative control")
    cal.add_argument("--corpus", required=True, type=Path)
    cal.add_argument("--source-root", required=True, type=Path)
    cal.add_argument("--implementation", required=True, type=Path)
    cal.add_argument("--tool", action="append", default=[])
    cal.add_argument("--source-sha")
    cal.add_argument("--out", required=True, type=Path)

    ident = sub.add_parser("identity", help="compare an exported tree against its origin closure")
    ident.add_argument("--manifest", required=True, type=Path)
    ident.add_argument("--origin-repo", required=True, type=Path)
    ident.add_argument("--origin-commit", required=True)
    ident.add_argument("--exported-root", required=True, type=Path)
    ident.add_argument("--out", required=True, type=Path)

    ical = sub.add_parser("identity-calibrate", help="identity lane positive plus negative controls")
    ical.add_argument("--manifest", required=True, type=Path)
    ical.add_argument("--origin-repo", required=True, type=Path)
    ical.add_argument("--origin-commit", required=True)
    ical.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            receipt = run_corpus(args.corpus, args.source_root, args.reference, args.candidate,
                                 args.out, mode=args.mode,
                                 tool_overrides={"reference": parse_tool_overrides(args.reference_tool),
                                                 "candidate": parse_tool_overrides(args.candidate_tool)},
                                 source_shas={"reference": args.reference_sha,
                                              "candidate": args.candidate_sha})
            write_json(args.out / "receipt.json", receipt)
            print(json.dumps(receipt["verdict"], indent=2))
            return 0 if receipt["verdict"]["accepting"] else 1
        if args.command == "calibrate":
            report = calibrate(args.corpus, args.source_root, args.implementation, args.out,
                               parse_tool_overrides(args.tool), args.source_sha)
            write_json(args.out / "calibration.json", report)
            for control in report["controls"]:
                print(f"{control['control']:32} {control['outcome']}")
            print(f"positive accepting: {report['positive']['verdict']['accepting']}")
            if not report["positive"]["verdict"]["accepting"]:
                for reason in report["positive"]["verdict"]["reasons"]:
                    print(f"  {reason}")
            print("CALIBRATED" if report["calibrated"] else "NOT CALIBRATED")
            return 0 if report["calibrated"] else 1
        if args.command == "identity-calibrate":
            report = calibrate_identity(load_json(args.manifest), args.origin_repo, args.origin_commit, args.out)
            write_json(args.out / "identity-calibration.json", report)
            print(f"positive: {report['positive']['status']} ({report['positive']['origin_files']} files)")
            for control in report["controls"]:
                print(f"{control['control']:24} {control['outcome']}")
            print("CALIBRATED" if report["calibrated"] else "NOT CALIBRATED")
            return 0 if report["calibrated"] else 1
        manifest = load_json(args.manifest)
        roots = manifest.get("roots") or []
        origin = git_tree_blobs(args.origin_repo, args.origin_commit, roots) if roots else {}
        exported = tree_blobs(args.exported_root, roots)
        report = compare_identity(manifest, origin, exported)
        report["origin"] = {"repo": str(args.origin_repo), "commit": args.origin_commit}
        report["exported_root"] = str(args.exported_root)
        write_json(args.out, report)
        print(json.dumps({k: report[k] for k in ("unit", "status", "origin_files", "exported_files")}))
        for reason in report["reasons"][:10]:
            print(f"  {reason}")
        return 0 if report["status"] == "pass" else 1
    except HarnessError as error:
        print(f"parity-harness: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
