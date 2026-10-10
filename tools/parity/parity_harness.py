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
from collections import Counter
import copy
import hashlib
import importlib.util
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from vellum_image_compare import compare_images  # noqa: E402
from vellum_png import PngError, RgbaImage, decode_png_bytes, encode_png  # noqa: E402

CORPUS_SCHEMA = "vellum.parity.corpus.v1"
IMPLEMENTATION_SCHEMA = "vellum.parity.implementation.v1"
RECEIPT_SCHEMA = "vellum.parity.receipt.v1"
IDENTITY_SCHEMA = "vellum.parity.identity-receipt.v1"
CALIBRATION_SCHEMA = "vellum.parity.calibration.v1"

LANES = ["structure", "raster", "geometry", "semantic", "interaction"]
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
# A raster threshold above 0 must carry a justification and stay under this
# ceiling; anything looser hides real rendering differences.
MAX_RASTER_THRESHOLD = 16
DEFAULT_SCALE = 2.0
# Floors that keep a stub from passing: a root plus two children, and a render
# whose luminance actually varies (one stray pixel on a flat field does not).
DEFAULT_MIN_STRUCTURE_NODES = 3
MIN_LUMINANCE_STDDEV = 1.0
# Volatile members the harness removes before comparing. A corpus may extend the
# list only through `volatile_allowlist` entries that carry a justification, and
# never with a pointer that would erase a whole observation.
DEFAULT_VOLATILE_ALLOWLIST = ("/imported_at",)
FORBIDDEN_VOLATILE_POINTERS = ("", "/", "/root", "/runs", "/steps", "/nodes", "/initial_state")
# Every laid-out node must carry these; a thinner schema on both sides would
# otherwise compare None == None and pass.
REQUIRED_LAYOUT_KEYS = ("id", "kind", "rect", "visible", "overflow", "z_order", "clipping",
                        "hit_regions", "measured_text_boxes")
# Facts an implementation must report about itself, beyond its binaries, so a
# raster difference can be attributed to build, provider or fonts.
REQUIRED_PROBES = ("build", "provider", "fonts")
LANE_MODULES: list[Path] = []


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
        validate_case_knobs(case, volatile_allowlist(corpus))
    return cases


def volatile_allowlist(corpus: dict[str, Any]) -> set[str]:
    allowed = set(DEFAULT_VOLATILE_ALLOWLIST)
    for entry in corpus.get("volatile_allowlist", []):
        if (not isinstance(entry, dict) or not isinstance(entry.get("pointer"), str)
                or not str(entry.get("justification", "")).strip()):
            raise HarnessError("volatile_allowlist entries need a pointer and a justification")
        allowed.add(entry["pointer"])
    for pointer in allowed:
        if pointer in FORBIDDEN_VOLATILE_POINTERS:
            raise HarnessError(f"volatile pointer {pointer!r} would erase a whole observation")
    return allowed


def validate_case_knobs(case: dict[str, Any], allowed_volatile: set[str]) -> None:
    """The per-case knobs that could turn a real difference into a pass are
    bounded here, before anything runs."""
    case_id = case["case_id"]
    scale = case.setdefault("scale", DEFAULT_SCALE)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 8:
        raise HarnessError(f"{case_id}: scale must be a number in (0, 8]")
    threshold = case.get("raster_threshold", 0)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise HarnessError(f"{case_id}: raster_threshold must be a non-negative integer")
    if threshold > MAX_RASTER_THRESHOLD:
        raise HarnessError(f"{case_id}: raster_threshold {threshold} exceeds the ceiling {MAX_RASTER_THRESHOLD}")
    if threshold and not str(case.get("raster_threshold_justification", "")).strip():
        raise HarnessError(f"{case_id}: a non-zero raster_threshold needs raster_threshold_justification")
    nodes = case.get("min_structure_nodes", DEFAULT_MIN_STRUCTURE_NODES)
    if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < DEFAULT_MIN_STRUCTURE_NODES:
        raise HarnessError(f"{case_id}: min_structure_nodes must be an integer >= {DEFAULT_MIN_STRUCTURE_NODES}")
    floor = case.get("min_ink_pixels", 1)
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
        raise HarnessError(f"{case_id}: min_ink_pixels must be an integer >= 1")
    volatile = case.get("volatile", {})
    if not isinstance(volatile, dict):
        raise HarnessError(f"{case_id}: volatile must map lanes to pointer lists")
    for lane, pointers in volatile.items():
        for pointer in pointers:
            if pointer not in allowed_volatile:
                raise HarnessError(f"{case_id}: volatile pointer {pointer!r} for {lane} is not allowlisted")
    params = case.get("params", {})
    if not isinstance(params, dict) or not all(isinstance(k, str) and isinstance(v, (str, int, float))
                                               for k, v in params.items()):
        raise HarnessError(f"{case_id}: params must map names to scalar values")


def validate_implementation(impl: Any) -> dict[str, Any]:
    if not isinstance(impl, dict) or impl.get("schema") != IMPLEMENTATION_SCHEMA:
        raise HarnessError(f"implementation schema must be {IMPLEMENTATION_SCHEMA}")
    for key in ("name", "repository", "steps", "source_kinds"):
        if key not in impl:
            raise HarnessError(f"implementation missing {key}")
    return impl


def git_head(repo: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
                                       encoding="utf-8", stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_repository(repo: Path) -> str | None:
    """OWNER/REPO of the checkout's origin remote, from the checkout itself."""
    try:
        url = subprocess.check_output(["git", "-C", str(repo), "remote", "get-url", "origin"], text=True,
                                      encoding="utf-8", stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else None


def export_origin(roots: dict[str, Path]) -> dict[str, Any] | None:
    """For a mirrored implementation (D1), the export receipt names the origin
    repository and commit the mirrored source came from."""
    receipt_path = roots.get("export_receipt")
    if receipt_path is None:
        return None
    try:
        receipt = load_json(receipt_path)
    except (OSError, json.JSONDecodeError):
        return {"error": f"unreadable export receipt {receipt_path}"}
    return {"repository": receipt.get("pulp_repository"), "commit": receipt.get("pulp_commit"),
            "mirror_commit": receipt.get("mirror_commit"),
            "receipt_sha256": sha256_file(Path(receipt_path))}


def run_probes(impl: dict[str, Any], tools: dict[str, Path], roots: dict[str, Path]) -> dict[str, Any]:
    values = placeholder_values(tools, roots)
    probes = {}
    for probe in impl.get("probes", []):
        try:
            argv = [expand(arg, values) for arg in probe["argv"]]
            result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=120)
            output = result.stdout
            probes[probe["name"]] = {"exit": result.returncode, "output": output[:4000],
                                     "sha256": sha256_bytes(output.encode("utf-8"))}
        except (OSError, subprocess.TimeoutExpired, HarnessError) as error:
            probes[probe["name"]] = {"exit": None, "error": str(error)}
    return probes


def implementation_identity(impl: dict[str, Any], tools: dict[str, Path],
                            roots: dict[str, Path] | None = None) -> dict[str, Any]:
    """Who produced the observations. The commit is read from the declared
    checkout, never taken from the operator; an operator-supplied source_sha
    must agree with it. Absent or malformed fields are kept so identity
    validation, not this function, decides acceptability."""
    roots = roots or {}
    derived = git_head(roots["checkout"]) if "checkout" in roots else None
    declared = impl.get("source_sha")
    remote = git_repository(roots["checkout"]) if "checkout" in roots else None
    return {
        "name": impl.get("name"),
        "repository": impl.get("repository"),
        "repository_remote": remote,
        "origin": export_origin(roots),
        "source_sha": derived or declared,
        "source_sha_derived": derived is not None,
        "source_sha_declared": declared,
        "roots": {name: str(path) for name, path in sorted(roots.items())},
        "tools": {
            name: {"path": str(path), "sha256": sha256_file(path) if path.is_file() else None}
            for name, path in sorted(tools.items())
        },
        "probes": run_probes(impl, tools, roots),
    }


def environment_identity() -> dict[str, Any]:
    """Where the comparison ran and with which harness code."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "mac_ver": platform.mac_ver()[0],
        "python": platform.python_version(),
        "harness": harness_fingerprint(),
    }


def harness_fingerprint() -> dict[str, Any]:
    files = [Path(__file__).resolve(), *sorted(p.resolve() for p in LANE_MODULES)]
    hashes = {p.name: sha256_file(p) for p in files}
    return {"files": hashes, "sha256": sha256_bytes(json.dumps(hashes, sort_keys=True).encode())}


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
    remote = identity.get("repository_remote")
    if remote and identity.get("repository") and remote.lower() != str(identity["repository"]).lower():
        errors.append(f"{side}: declared repository {identity['repository']} != checkout remote {remote}")
    origin = identity.get("origin")
    if origin is not None and (origin.get("error") or not SHA1_RE.match(str(origin.get("commit", "")))):
        errors.append(f"{side}: export receipt origin is unreadable or has no 40-hex origin commit")
    if not identity.get("source_sha_derived"):
        errors.append(f"{side}: source_sha was not read from a checkout (operator-typed commits are not identity)")
    declared = identity.get("source_sha_declared")
    if declared and identity.get("source_sha_derived") and declared != identity.get("source_sha"):
        errors.append(f"{side}: declared source_sha {declared} != checkout HEAD {identity.get('source_sha')}")
    probes = identity.get("probes") or {}
    for name in REQUIRED_PROBES:
        probe = probes.get(name)
        if not probe or probe.get("exit") != 0 or not probe.get("output", "").strip():
            errors.append(f"{side}: required identity probe {name!r} is missing, failed or empty")
    return errors


def content_key(identity: dict[str, Any]) -> tuple:
    """The binaries actually run. Two sides with the same multiset of tool
    hashes are the same implementation, whatever their labels say."""
    return tuple(sorted(t.get("sha256") or "" for t in (identity.get("tools") or {}).values()))


def identity_key(identity: dict[str, Any]) -> tuple:
    return (
        identity.get("repository"),
        identity.get("source_sha"),
        tuple(sorted((n, t.get("sha256")) for n, t in (identity.get("tools") or {}).items())),
        tuple(sorted((n, p.get("sha256")) for n, p in (identity.get("probes") or {}).items())),
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

    return re.sub(r"\{([a-z_]+(?::[A-Za-z0-9_.-]+)?)\}", replace, arg)


def placeholder_values(tools: dict[str, Path], roots: dict[str, Path]) -> dict[str, str]:
    values = {f"tool:{name}": str(path) for name, path in tools.items()}
    values.update({f"root:{name}": str(path) for name, path in roots.items()})
    return values


def run_side(case: dict[str, Any], impl: dict[str, Any], tools: dict[str, Path],
             source_root: Path, out: Path, *, interactions: list[dict[str, Any]] | None = None,
             source_override: bytes | None = None, skip_steps: tuple[str, ...] = (),
             timeout: int = 300, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    inputs = stage_case(case, source_root, out, source_override)
    source_name = Path(case["source"]).name
    kind_args = impl["source_kinds"].get(case["source_kind"])
    if kind_args is None:
        raise HarnessError(f"{impl['name']} does not declare source kind {case['source_kind']}")
    values = placeholder_values(tools, roots or {})
    values.update({"source": source_name, "width": str(case["width"]), "height": str(case["height"]),
                   "scale": str(case.get("scale", DEFAULT_SCALE))})
    values.update({f"param:{k}": str(v) for k, v in case.get("params", {}).items()})
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
            elif arg.startswith("@case_args:"):
                # Per-case argument lists a lane declares (seeded faults, schedules).
                argv += [expand(str(a), values) for a in case.get("args", {}).get(arg.split(":", 1)[1], [])]
            else:
                argv.append(expand(arg, values))
        record: dict[str, Any] = {"id": step["id"], "argv": argv}
        if step["id"] in skip_steps:
            # A planted adapter skip: the step reports "not run" exactly as a
            # tool that cannot run here would. It must never read as a pass.
            argv = [sys.executable, "-c", f"import sys; sys.exit({SKIP_EXIT})"]
            record["planted_skip"] = True
        env = None
        if step.get("env"):
            env = dict(os.environ, **{k: expand(str(v), values) for k, v in step["env"].items()})
            record["env"] = {k: env[k] for k in step["env"]}
        started = time.monotonic()
        try:
            result = subprocess.run(argv, cwd=out, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout, env=env)
            record["exit"] = result.returncode
            record["stderr_tail"] = result.stderr[-2000:]
            if step.get("stderr_artifact"):
                # Full stderr kept as an observation (e.g. a loader census).
                (out / step["stderr_artifact"]).write_text(result.stderr, encoding="utf-8")
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
            "interaction_plan": plan, "scale": case.get("scale", DEFAULT_SCALE)}


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
    floor = int(case.get("min_structure_nodes", DEFAULT_MIN_STRUCTURE_NODES))
    if min(counts) < floor:
        reasons.append(f"vacuous DesignIR: node counts {counts} below the floor of {floor}")
    diffs = json_differences(a, b)
    reasons += diffs
    return lane_result("fail" if reasons else "pass", reasons=reasons,
                       node_counts=list(counts), volatile_ignored=volatile)


def ink_pixels(image: RgbaImage) -> int:
    """Pixels that differ from the most common colour. The most common colour,
    not the top-left pixel, is the background: a stray corner pixel must not
    turn a whole render into ink."""
    counts = Counter(image.pixels[offset:offset + 4] for offset in range(0, len(image.pixels), 4))
    if not counts:
        return 0
    return sum(counts.values()) - counts.most_common(1)[0][1]


def luminance_stddev(image: RgbaImage) -> float:
    total = len(image.pixels) // 4
    if not total:
        return 0.0
    values = [0.2126 * image.pixels[o] + 0.7152 * image.pixels[o + 1] + 0.0722 * image.pixels[o + 2]
              for o in range(0, len(image.pixels), 4)]
    mean = sum(values) / total
    return (sum((v - mean) ** 2 for v in values) / total) ** 0.5


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
    spread = (round(luminance_stddev(a), 3), round(luminance_stddev(b), 3))
    if min(spread) < MIN_LUMINANCE_STDDEV:
        reasons.append(f"luminance spread {spread} below {MIN_LUMINANCE_STDDEV}: a near-uniform render is not evidence")
    if (a.width, a.height) != (b.width, b.height):
        reasons.append(f"dimensions {(a.width, a.height)} != {(b.width, b.height)}")
        return lane_result("fail", reasons=reasons, ink_pixels=list(ink))
    report, _diff = compare_images(a, b, threshold=threshold)
    if report["differing_pixels"]:
        reasons.append(f"{report['differing_pixels']} pixels differ above threshold {threshold} "
                       f"(max channel error {report['max_channel_error']})")
    return lane_result("fail" if reasons else "pass", reasons=reasons, ink_pixels=list(ink),
                       luminance_stddev=list(spread),
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
    for side, nodes in (("reference", a), ("candidate", b)):
        for index, node in enumerate(nodes):
            absent = [key for key in REQUIRED_LAYOUT_KEYS if key not in node]
            if absent:
                reasons.append(f"{side} node {index}: missing required keys {absent}")
                break
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
    for side, doc in (("reference", a), ("candidate", b)):
        for index, record in enumerate((doc.get(key) or []) if isinstance(doc, dict) else []):
            if not isinstance(record, dict) or not isinstance(record.get("text"), str):
                reasons.append(f"{side} {records}/{index}: missing required text")
                break
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


def register_lane(name: str, artifacts: tuple[str, ...],
                  comparator: Callable[[Path, Path, dict[str, Any]], dict[str, Any]]) -> None:
    """Add a lane from its own module (tools/parity/lanes/<name>.py). The
    module is loaded with --lane-module and enters the harness fingerprint, so a
    calibration only vouches for the lanes it actually ran with."""
    if name in LANES:
        raise HarnessError(f"lane {name!r} is already registered")
    LANES.append(name)
    LANE_ARTIFACTS[name] = tuple(artifacts)
    COMPARATORS[name] = comparator


def register_controls(artifact: dict[str, dict[str, Any]] | None = None,
                      rerun: dict[str, dict[str, Any]] | None = None) -> None:
    """Add seeded negative controls. An artifact control mutates copied
    observations ("candidate" or "both" callables). A rerun control re-runs the
    candidate: "prepare(case, source_root, work_dir) -> (run_case, run_side kwargs)
    | None" and "identical_inputs": False when it deliberately changes the inputs.
    Either kind may add "bites": f(lane_result) -> bool, so the control counts only
    when the lane rejected it for the targeted reason."""
    for table, entries in ((ARTIFACT_CONTROLS, artifact or {}), (RERUN_CONTROLS, rerun or {})):
        for name, spec in entries.items():
            if name in ARTIFACT_CONTROLS or name in RERUN_CONTROLS:
                raise HarnessError(f"control {name!r} is already registered")
            table[name] = spec


def load_lane_module(path: Path) -> Any:
    """Load a lane module once and return it; a second call returns the same
    module without re-registering its lanes."""
    path = path.resolve()
    name = f"parity_lane_{path.stem}"
    if path in LANE_MODULES and name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"cannot load lane module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("parity_harness", sys.modules[__name__])
    # Registered before execution: dataclasses and pickling look a module up by name.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    LANE_MODULES.append(path)
    return module


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


def comparison_scope(mode: str, reference: dict[str, Any], candidate: dict[str, Any]) -> str:
    """What a passing receipt may be cited as. Two builds of one repository at
    one commit prove build insensitivity, not parity; parity needs different
    repositories (a Vellum mirror of Pulp source is a different repository
    whose origin commit is recorded)."""
    if mode == "calibration":
        return "harness-calibration"
    same_repo = str(reference.get("repository") or "").lower() == str(candidate.get("repository") or "").lower()
    if same_repo and reference.get("source_sha") == candidate.get("source_sha"):
        return "build-sensitivity"
    if same_repo:
        return "revision-difference"
    return "parity"


def acceptance(mode: str, cases: list[dict[str, Any]], reference: dict[str, Any],
               candidate: dict[str, Any], *, require_identical_inputs: bool = True) -> dict[str, Any]:
    reasons = identity_errors(reference, "reference") + identity_errors(candidate, "candidate")
    if mode == "parity" and content_key(reference) == content_key(candidate):
        reasons.append("parity mode: reference and candidate run byte-identical tools; "
                       "this compares an implementation with itself whatever its labels say")
    if mode == "calibration" and identity_key(reference) != identity_key(candidate):
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
    return {"accepting": not reasons, "scope": comparison_scope(mode, reference, candidate),
            "origins": {"reference": reference.get("origin"), "candidate": candidate.get("origin")},
            "selected_lane_results": selected, "reasons": reasons}


# ── whole-corpus run ───────────────────────────────────────────────────────


def resolve_tools(impl: dict[str, Any], overrides: dict[str, str]) -> dict[str, Path]:
    """Tool paths resolve against the invoking directory, never against a
    case's working directory, so a relative default cannot silently point at
    a different binary per case."""
    tools = {name: Path(path) for name, path in (impl.get("tools") or {}).items()}
    tools.update({name: Path(path) for name, path in overrides.items()})
    return {name: path.resolve() for name, path in tools.items()}


def corpus_sha256(corpus: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps(corpus, sort_keys=True).encode())


def calibration_errors(calibration: Any, corpus: dict[str, Any]) -> list[str]:
    """A parity run is only as good as the controls that proved its corpus and
    harness can fail. Refuse a run whose corpus or harness code was not the
    one calibrated."""
    if not isinstance(calibration, dict) or calibration.get("schema") != CALIBRATION_SCHEMA:
        return [f"calibration record is not {CALIBRATION_SCHEMA}"]
    errors = []
    if not calibration.get("calibrated"):
        errors.append("calibration record is not CALIBRATED")
    positive = calibration.get("positive") or {}
    if (positive.get("corpus") or {}).get("sha256") != corpus_sha256(corpus):
        errors.append("corpus differs from the calibrated corpus (re-run calibrate)")
    recorded = ((positive.get("environment") or {}).get("harness") or {}).get("sha256")
    if recorded != harness_fingerprint()["sha256"]:
        errors.append("harness code or lane modules differ from the calibrated harness (re-run calibrate)")
    return errors


def run_corpus(corpus_path: Path, source_root: Path, reference_path: Path, candidate_path: Path,
               out: Path, *, mode: str, tool_overrides: dict[str, dict[str, str]] | None = None,
               source_shas: dict[str, str | None] | None = None,
               roots: dict[str, dict[str, Path]] | None = None,
               calibration: Any = None) -> dict[str, Any]:
    corpus = load_json(corpus_path)
    cases = validate_corpus(corpus, corpus_path.resolve().parent)
    gate = calibration_errors(calibration, corpus) if mode == "parity" else []
    impls = {}
    for side, path in (("reference", reference_path), ("candidate", candidate_path)):
        impl = validate_implementation(load_json(path))
        if (source_shas or {}).get(side):
            impl = dict(impl, source_sha=source_shas[side])
        side_roots = {k: Path(v).resolve() for k, v in ((roots or {}).get(side) or {}).items()}
        impls[side] = (impl, resolve_tools(impl, (tool_overrides or {}).get(side, {})), side_roots)
    ref_impl, ref_tools, ref_roots = impls["reference"]
    cand_impl, cand_tools, cand_roots = impls["candidate"]
    results = []
    for case in cases:
        ref_dir = out / case["case_id"] / "reference"
        cand_dir = out / case["case_id"] / "candidate"
        ref_run = run_side(case, ref_impl, ref_tools, source_root, ref_dir, roots=ref_roots)
        cand_run = run_side(case, cand_impl, cand_tools, source_root, cand_dir, roots=cand_roots)
        results.append(evaluate_case(case, ref_dir, cand_dir, ref_run, cand_run))
    receipt = assemble_receipt(mode, corpus_path, corpus,
                               implementation_identity(ref_impl, ref_tools, ref_roots),
                               implementation_identity(cand_impl, cand_tools, cand_roots), results)
    if mode == "parity":
        receipt["calibration"] = {"sha256": sha256_bytes(json.dumps(calibration, sort_keys=True).encode())
                                  if calibration is not None else None, "errors": gate}
        if gate:
            receipt["verdict"]["accepting"] = False
            receipt["verdict"]["reasons"] = gate + receipt["verdict"]["reasons"]
    return receipt


def assemble_receipt(mode: str, corpus_path: Path, corpus: dict[str, Any],
                     reference: dict[str, Any], candidate: dict[str, Any],
                     results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "mode": mode,
        "corpus": {"path": str(corpus_path), "sha256": corpus_sha256(corpus), "cases": len(results)},
        "environment": environment_identity(),
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
                                     text=True, encoding="utf-8")
    blobs: dict[str, str] = {}
    for line in output.splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, object_id = meta.split()
        if kind == "blob" and mode != "120000":
            blobs[path] = object_id
    return blobs


def manifest_paths(manifest: dict[str, Any]) -> list[str]:
    """Closure paths: the exact `files` of a machine-generated closure manifest
    (pulp.vellum.closure.v1), or the `roots` of a hand-declared calibration one."""
    files = manifest.get("files")
    if isinstance(files, list):
        return [f if isinstance(f, str) else f.get("path", "") for f in files]
    return list(manifest.get("roots") or [])


def merge_manifests(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """One closure for a multi-unit export. A path claimed by two units is an
    error: each file has exactly one owning unit."""
    if len(manifests) == 1:
        return manifests[0]
    owner: dict[str, int] = {}
    files: list[str] = []
    for index, manifest in enumerate(manifests):
        for path in manifest_paths(manifest):
            if path in owner:
                raise HarnessError(f"closure path {path} is claimed by manifests {owner[path]} and {index}")
            owner[path] = index
            files.append(path)
    return {"schema": "vellum.parity.merged-closure.v1", "files": files,
            "unit": [m.get("unit") or [t.get("name") for t in m.get("targets", [])] for m in manifests]}


def git_exported_blobs(repo: Path, commit: str, prefix: str) -> dict[str, str]:
    """Every blob in an exported commit under prefix, keyed by origin path. The
    whole prefix is listed, not only the manifest's paths, so an extra file in
    the export is seen."""
    blobs = git_tree_blobs(repo, commit, [prefix.rstrip("/")] if prefix else ["."])
    if not prefix:
        return blobs
    return {path[len(prefix):]: sha for path, sha in blobs.items() if path.startswith(prefix)}


def compare_identity(manifest: dict[str, Any], origin: dict[str, str], exported: dict[str, str]) -> dict[str, Any]:
    reasons = []
    if not manifest_paths(manifest):
        reasons.append("empty closure manifest: no roots or files declared")
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
        "unit": manifest.get("unit") or [t.get("name") for t in manifest.get("targets", [])],
        "manifest_schema": manifest.get("schema"),
        "closure_paths": len(manifest_paths(manifest)),
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


def stray_pixel_image(image: RgbaImage) -> RgbaImage:
    """A flat field with one off-colour pixel: correctly sized, nominally inked."""
    return flip_center_pixel(uniform_image(image))


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


def stub_design_ir(path: Path) -> bool:
    """Both sides emit a root with one empty child."""
    document = load_json(path)
    document["root"] = {"type": "frame", "children": [{"type": "frame", "children": []}]}
    write_json(path, document)
    return True


def strip_layout_to_rects(path: Path) -> bool:
    """Both sides emit a thinner layout schema: bounds only."""
    layout = load_json(path)
    layout["nodes"] = [{"rect": node.get("rect")} for node in layout.get("nodes") or []]
    write_json(path, layout)
    return bool(layout["nodes"])


def builtin_rerun_prepare(name: str) -> Callable[..., tuple[dict[str, Any], dict[str, Any]] | None]:
    def prepare(case: dict[str, Any], source_root: Path,
                work_dir: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if name == "input-one-byte":
            if "structure" not in case["lanes"]:
                return None
            return case, {"source_override": one_byte_source(
                (case_root(case, source_root) / case["source"]).read_bytes())}
        if name == "viewport-changed":
            return (dict(case, width=case["width"] + 8), {}) if "raster" in case["lanes"] else None
        if name == "scale-changed":
            return (dict(case, scale=case.get("scale", DEFAULT_SCALE) * 1.5), {}) if "raster" in case["lanes"] else None
        if name == "asset-missing":
            return (dict(case, stage=[]), {}) if "raster" in case["lanes"] and case.get("stage") else None
        if name == "planted-skip":
            return case, {"skip_steps": ("observe",)}
        if "interaction" not in case["lanes"]:
            return None
        plan = (moved_pointer_plan if name == "interaction-pointer-moved" else changed_key_plan)(case)
        return (case, {"interactions": plan}) if plan is not None else None

    return prepare


ARTIFACT_CONTROLS: dict[str, dict[str, Any]] = {
    "structure-one-byte": {"lane": "structure", "candidate": lambda d: mutate_json_string(
        d / "design.ir.json", lambda k, v: k in ("name", "label", "text"))},
    "raster-one-pixel": {"lane": "raster", "candidate": lambda d: (mutate_png(d / "render.png", flip_center_pixel), True)[1]},
    "raster-blank-both": {"lane": "raster", "both": lambda d: (mutate_png(d / "render.png", uniform_image), True)[1]},
    "raster-stray-pixel-both": {"lane": "raster", "both": lambda d: (mutate_png(d / "render.png", stray_pixel_image), True)[1],
                                "bites": lambda r: any("luminance spread" in x for x in r.get("reasons", []))},
    "structure-stub-both": {"lane": "structure", "both": lambda d: stub_design_ir(d / "design.ir.json")},
    "missing-render": {"lane": "raster", "expect": "blocked", "candidate": lambda d: ((d / "render.png").unlink(), True)[1]},
    "geometry-shift-node": {"lane": "geometry", "candidate": lambda d: shift_last_node(d / "layout.json")},
    "geometry-zero-nodes-both": {"lane": "geometry", "both": lambda d: empty_layout(d / "layout.json")},
    "geometry-thin-schema-both": {"lane": "geometry", "both": lambda d: strip_layout_to_rects(d / "layout.json")},
    "semantic-one-byte": {"lane": "semantic", "candidate": lambda d: mutate_json_string(
        d / "text.json", lambda k, v: k == "text")},
    "interaction-state": {"lane": "interaction", "candidate": lambda d: perturb_interaction_state(d / "interaction.json")},
}
RERUN_CONTROLS: dict[str, dict[str, Any]] = {
    name: {"lane": lane, "prepare": builtin_rerun_prepare(name), **extra}
    for name, lane, extra in (
        ("input-one-byte", "structure", {"identical_inputs": False}),
        ("viewport-changed", "raster", {}),
        ("scale-changed", "raster", {}),
        ("asset-missing", "raster", {"identical_inputs": False}),
        ("interaction-pointer-moved", "interaction", {}),
        ("interaction-key-changed", "interaction", {}),
        ("planted-skip", "*", {"expect": "blocked"}),
    )
}


def calibrate(corpus_path: Path, source_root: Path, impl_path: Path, out: Path,
              tool_overrides: dict[str, str] | None = None,
              source_sha: str | None = None,
              roots: dict[str, Path] | None = None) -> dict[str, Any]:
    corpus = load_json(corpus_path)
    cases = validate_corpus(corpus, corpus_path.resolve().parent)
    impl = validate_implementation(load_json(impl_path))
    if source_sha is not None:
        impl = dict(impl, source_sha=source_sha)
    tools = resolve_tools(impl, tool_overrides or {})
    roots = {k: Path(v).resolve() for k, v in (roots or {"checkout": source_root}).items()}
    if out.exists():
        shutil.rmtree(out)
    identity = implementation_identity(impl, tools, roots)

    def side(case: dict[str, Any], label: str, **kwargs: Any) -> tuple[Path, dict[str, Any]]:
        directory = out / "runs" / case["case_id"] / label
        return directory, run_side(case, impl, tools, source_root, directory, roots=roots, **kwargs)

    baseline: dict[str, tuple[Path, dict[str, Any], Path, dict[str, Any]]] = {}
    positive_results = []
    for case in cases:
        ref_dir, ref_run = side(case, "reference")
        cand_dir, cand_run = side(case, "candidate")
        baseline[case["case_id"]] = (ref_dir, ref_run, cand_dir, cand_run)
        positive_results.append(evaluate_case(case, ref_dir, cand_dir, ref_run, cand_run))

    def receipt(results: list[dict[str, Any]], mode: str = "calibration",
                cand: dict[str, Any] | None = None) -> dict[str, Any]:
        return assemble_receipt(mode, corpus_path, corpus, identity, cand or identity, results)

    positive = receipt(positive_results)
    controls: list[dict[str, Any]] = []

    lanes_in_scope = sorted({lane for case in cases for lane in case["lanes"]})

    def record(name: str, lane: str, expect: str, results: list[dict[str, Any]] | None,
               verdict: dict[str, Any], applied_cases: list[str],
               bites: Callable[[dict[str, Any]], bool] | None = None) -> None:
        if lane != "*" and lane not in lanes_in_scope:
            # The corpus selects this lane nowhere, so the lane makes no claim
            # here; a control for a selected lane that applies to no case still fails.
            controls.append({"control": name, "lane": lane, "applied_cases": [],
                             "rejected": None, "lane_hit": None,
                             "outcome": "OUT OF SCOPE (no case selects this lane)"})
            return
        if not applied_cases:
            controls.append({"control": name, "lane": lane, "applied_cases": [],
                             "rejected": False, "lane_hit": False,
                             "outcome": "NOT APPLICABLE (no case exercises it)"})
            return
        hit = True
        if results is not None and lane != "*":
            hit = all(r["lanes"][lane]["status"] in (("blocked",) if expect == "blocked" else ("fail",))
                      and (bites is None or bites(r["lanes"][lane]))
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
            if any(not (d / name_).is_file() for d in (ref_dir, cand_dir)
                   for name_ in LANE_ARTIFACTS.get(spec["lane"], ())):
                # The baseline itself lacks the lane's observations (a blocked
                # positive); there is nothing to mutate. The positive already
                # fails calibration, and this control counts as not applied.
                continue
            work = out / "controls" / name / case["case_id"]
            mref, mcand = work / "reference", work / "candidate"
            shutil.copytree(ref_dir, mref)
            shutil.copytree(cand_dir, mcand)
            try:
                changed = spec["candidate"](mcand) if "candidate" in spec else (
                    spec["both"](mref) and spec["both"](mcand))
            except (OSError, ValueError, KeyError, PngError) as error:
                raise HarnessError(f"control {name} could not mutate {case['case_id']}: {error}") from error
            if not changed:
                continue
            applied.append(case["case_id"])
            results.append(evaluate_case(case, mref, mcand, ref_run, cand_run))
        record(name, spec["lane"], spec.get("expect", "fail"), results,
               receipt(results)["verdict"] if results else {"accepting": True, "reasons": []}, applied,
               spec.get("bites"))

    for name, spec in RERUN_CONTROLS.items():
        results, applied = [], []
        for case in cases:
            ref_dir, ref_run, _cand_dir, _cand_run = baseline[case["case_id"]]
            work_dir = out / "controls" / name / case["case_id"]
            prepared = spec["prepare"](case, source_root, work_dir)
            if prepared is None:
                continue
            run_case, kwargs = prepared
            cand_dir = out / "controls" / name / case["case_id"] / "candidate"
            cand_run = run_side(run_case, impl, tools, source_root, cand_dir, roots=roots, **kwargs)
            applied.append(case["case_id"])
            results.append(evaluate_case(case, ref_dir, cand_dir, ref_run, cand_run))
        # A control that deliberately changes the inputs would be rejected by
        # the identical-inputs rule alone; the lane, not that rule, must reject.
        verdict = (acceptance("calibration", results, identity, identity,
                              require_identical_inputs=spec.get("identical_inputs", True))
                   if results else {"accepting": True, "reasons": []})
        record(name, spec["lane"], spec.get("expect", "fail"), results, verdict, applied, spec.get("bites"))

    def relabelled(**changes: Any) -> dict[str, Any]:
        return dict(copy.deepcopy(identity), **changes)

    record("identity-absent", "*", "fail", None,
           receipt(positive_results, cand=relabelled(source_sha=None, source_sha_derived=False))["verdict"], ["*"])
    record("identity-same-in-parity-mode", "*", "fail", None,
           receipt(positive_results, mode="parity")["verdict"], ["*"])
    record("identity-relabelled-in-parity-mode", "*", "fail", None,
           receipt(positive_results, mode="parity",
                   cand=relabelled(repository="example/other", source_sha="f" * 40))["verdict"], ["*"])
    record("identity-typed-sha", "*", "fail", None,
           receipt(positive_results, cand=relabelled(source_sha_derived=False))["verdict"], ["*"])
    probes_missing = relabelled()
    probes_missing["probes"] = {k: v for k, v in (identity.get("probes") or {}).items() if k != "fonts"}
    record("identity-probe-missing", "*", "fail", None,
           receipt(positive_results, cand=probes_missing)["verdict"], ["*"])
    record("empty-corpus", "*", "fail", None, receipt([])["verdict"], ["*"])
    unselected = [dict(r, lanes={lane: lane_result("not_selected") for lane in LANES})
                  for r in positive_results[:1]]
    record("no-lane-selected", "*", "fail", None, receipt(unselected)["verdict"], ["*"])

    failures = [c for c in controls
                if c["outcome"] not in ("REJECTED as required", "OUT OF SCOPE (no case selects this lane)")]
    calibrated = positive["verdict"]["accepting"] and not failures
    return {
        "schema": CALIBRATION_SCHEMA,
        "calibrated": calibrated,
        "lanes_in_scope": lanes_in_scope,
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
            raise HarnessError(f"expected name=path, got {value!r}")
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
    run.add_argument("--reference-root", action="append", default=[],
                     help="name=path; 'checkout' is the git checkout the reference was built from")
    run.add_argument("--candidate-root", action="append", default=[])
    run.add_argument("--calibration", type=Path,
                     help="calibration.json for this corpus and harness (required in parity mode)")
    run.add_argument("--lane-module", action="append", default=[], type=Path)
    run.add_argument("--out", required=True, type=Path)

    cal = sub.add_parser("calibrate", help="positive comparison plus every negative control")
    cal.add_argument("--corpus", required=True, type=Path)
    cal.add_argument("--source-root", required=True, type=Path)
    cal.add_argument("--implementation", required=True, type=Path)
    cal.add_argument("--tool", action="append", default=[])
    cal.add_argument("--source-sha")
    cal.add_argument("--root", action="append", default=[],
                     help="name=path; defaults to checkout=<source-root>")
    cal.add_argument("--lane-module", action="append", default=[], type=Path)
    cal.add_argument("--out", required=True, type=Path)

    ident = sub.add_parser("identity", help="compare an exported tree against its origin closure")
    ident.add_argument("--manifest", required=True, type=Path, action="append",
                       help="repeat for a multi-unit receipt (e.g. foundation + canvas)")
    ident.add_argument("--origin-repo", required=True, type=Path)
    ident.add_argument("--origin-commit", required=True)
    ident.add_argument("--exported-root", type=Path)
    ident.add_argument("--exported-repo", type=Path)
    ident.add_argument("--exported-commit")
    ident.add_argument("--exported-prefix", default="")
    ident.add_argument("--out", required=True, type=Path)

    ical = sub.add_parser("identity-calibrate", help="identity lane positive plus negative controls")
    ical.add_argument("--manifest", required=True, type=Path)
    ical.add_argument("--origin-repo", required=True, type=Path)
    ical.add_argument("--origin-commit", required=True)
    ical.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        for module in getattr(args, "lane_module", []) or []:
            load_lane_module(module)
        if args.command == "run":
            if args.mode == "parity" and args.calibration is None:
                raise HarnessError("parity mode requires --calibration <calibration.json>")
            receipt = run_corpus(args.corpus, args.source_root, args.reference, args.candidate,
                                 args.out, mode=args.mode,
                                 tool_overrides={"reference": parse_tool_overrides(args.reference_tool),
                                                 "candidate": parse_tool_overrides(args.candidate_tool)},
                                 source_shas={"reference": args.reference_sha,
                                              "candidate": args.candidate_sha},
                                 roots={"reference": parse_tool_overrides(args.reference_root),
                                        "candidate": parse_tool_overrides(args.candidate_root)},
                                 calibration=load_json(args.calibration) if args.calibration else None)
            write_json(args.out / "receipt.json", receipt)
            print(json.dumps(receipt["verdict"], indent=2))
            return 0 if receipt["verdict"]["accepting"] else 1
        if args.command == "calibrate":
            roots = parse_tool_overrides(args.root) or {"checkout": str(args.source_root)}
            report = calibrate(args.corpus, args.source_root, args.implementation, args.out,
                               parse_tool_overrides(args.tool), args.source_sha,
                               {k: Path(v) for k, v in roots.items()})
            write_json(args.out / "calibration.json", report)
            for control in report["controls"]:
                print(f"{control['control']:36} {control['outcome']}")
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
        manifest = merge_manifests([load_json(path) for path in args.manifest])
        paths = manifest_paths(manifest)
        origin = git_tree_blobs(args.origin_repo, args.origin_commit, paths) if paths else {}
        if args.exported_repo is not None:
            if not args.exported_commit:
                raise HarnessError("--exported-repo requires --exported-commit")
            exported = git_exported_blobs(args.exported_repo, args.exported_commit, args.exported_prefix)
            where = {"repo": str(args.exported_repo), "commit": args.exported_commit,
                     "prefix": args.exported_prefix}
        elif args.exported_root is not None:
            exported = tree_blobs(args.exported_root, paths)
            where = {"root": str(args.exported_root)}
        else:
            raise HarnessError("identity needs --exported-root or --exported-repo/--exported-commit")
        report = compare_identity(manifest, origin, exported)
        report["origin"] = {"repo": str(args.origin_repo), "commit": args.origin_commit}
        report["exported"] = where
        write_json(args.out, report)
        print(json.dumps({k: report[k] for k in ("status", "closure_paths", "origin_files", "exported_files")}))
        for reason in report["reasons"][:10]:
            print(f"  {reason}")
        return 0 if report["status"] == "pass" else 1
    except HarnessError as error:
        print(f"parity-harness: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
