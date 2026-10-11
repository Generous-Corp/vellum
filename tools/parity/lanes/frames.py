"""Frames lane: frame-by-frame comparison under a fixed host clock.

A final-frame comparison cannot see timing. An animation integrated at the
wrong rate, a document that mounts one frame late, a parameter that reaches a
second editor a frame after the first, or a host that skips a vsync it should
have dispatched all converge on the same last frame. This lane compares every
frame instead.

Both implementations replay the same schedule: host presentation timestamps,
parameter declarations, and timed events (hover, click, drag, keys, context
press, host parameter writes, resize, editor attach and detach). Each writes
``frames.json`` (one record per frame: the dispatch decision, dt, readiness,
focus, control and animation state, open menus, parameter values, and layout
and pixel digests) plus PNG captures of the frames the schedule names.

The comparison is exact. The first divergent frame is reported, and so is
whether the final frames agree, so a receipt shows when timing was the only
difference. Vacuity guards: every frame of the schedule is present, every
record carries the full schema, every frame has pixels, every scheduled event
was applied in its frame, and the observation moved at least once.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import parity_harness as ph

LANE = "frames"
ARTIFACT = "frames.json"
SCHEMA = "pulp-ui-frames-v1"
SCHEDULE_SCHEMA = "pulp-ui-frame-schedule-v1"
CAPTURE_RE = re.compile(r"^frame-v\d+-f\d{4}\.png$")

TOP_KEYS = ("schema", "environment", "inputs", "frames", "views")
ENVIRONMENT_KEYS = ("platform", "arch", "backend", "scale", "width", "height", "timer_clock")
INPUT_KEYS = ("mode", "input_sha256", "schedule_sha256")
FRAME_KEYS = ("frame", "timestamp", "events", "views", "params")
VIEW_KEYS = ("view", "dispatched", "clock_time", "readiness", "focused", "state",
             "layout_sha256", "pixels_sha256", "pixel_size")
DISPATCHED_KEYS = ("dt", "resumed", "rendered", "continuous", "continuous_after")
FRAME_REASON = "frame "


def _schedule_name(case: dict[str, Any]) -> str:
    return Path(str((case.get("params") or {}).get("schedule", ""))).name


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_errors(side: str, log: Any) -> list[str]:
    if not isinstance(log, dict):
        return [f"{side}: frames record is not an object"]
    errors = [f"{side}: missing {k}" for k in TOP_KEYS if k not in log]
    if log.get("schema") != SCHEMA:
        errors.append(f"{side}: schema {log.get('schema')!r} != {SCHEMA!r}")
    errors += [f"{side}: environment missing {k}" for k in ENVIRONMENT_KEYS
               if k not in (log.get("environment") or {})]
    errors += [f"{side}: inputs missing {k}" for k in INPUT_KEYS if k not in (log.get("inputs") or {})]
    for frame in log.get("frames") or []:
        index = frame.get("frame", "?")
        errors += [f"{side}: frame {index} missing {k}" for k in FRAME_KEYS if k not in frame]
        for view in frame.get("views") or []:
            keys = VIEW_KEYS + (DISPATCHED_KEYS if view.get("dispatched") else ())
            errors += [f"{side}: frame {index} view {view.get('view', '?')} missing {k}"
                       for k in keys if k not in view]
        if len(errors) > 25:
            break
    return errors


def _observable(view: dict[str, Any]) -> Any:
    """What a viewer of that editor sees at that frame: state and pixels."""
    return {k: view.get(k) for k in ("view", "readiness", "focused", "state",
                                     "layout_sha256", "pixels_sha256", "pixel_size")}


def _changed_frames(frames: list[dict[str, Any]]) -> int:
    changed = 0
    for previous, current in zip(frames, frames[1:]):
        if ([_observable(v) for v in previous.get("views") or []] !=
                [_observable(v) for v in current.get("views") or []] or
                previous.get("params") != current.get("params")):
            changed += 1
    return changed


def _scheduled_events(schedule: dict[str, Any]) -> dict[int, list[str]]:
    by_frame: dict[int, list[str]] = {}
    for event in schedule.get("events") or []:
        by_frame.setdefault(int(event["frame"]), []).append(event["kind"])
    return by_frame


def compare_frames(ref: Path, cand: Path, case: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        a, b = _load(ref / ARTIFACT), _load(cand / ARTIFACT)
    except (OSError, json.JSONDecodeError) as error:
        return ph.lane_result("fail", reasons=[f"unreadable frames record: {error}"])
    reasons += _schema_errors("reference", a) + _schema_errors("candidate", b)
    if reasons:
        return ph.lane_result("fail", reasons=reasons[:25])

    identity = {"reference": {"environment": a["environment"], "inputs": a["inputs"]},
                "candidate": {"environment": b["environment"], "inputs": b["inputs"]}}
    # Each side must have consumed the schedule it was staged with; the two
    # environments must agree, or a difference cannot be attributed.
    for side, log, root in (("reference", a, ref), ("candidate", b, cand)):
        staged = root / _schedule_name(case)
        if not staged.is_file():
            reasons.append(f"{side}: staged schedule {staged.name} is missing")
            continue
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        if log["inputs"]["schedule_sha256"] != digest:
            reasons.append(f"{side}: observer did not read the staged schedule "
                           f"({log['inputs']['schedule_sha256'][:12]} != {digest[:12]})")
        schedule = _load(staged)
        if schedule.get("schema") != SCHEDULE_SCHEMA:
            reasons.append(f"{side}: schedule schema {schedule.get('schema')!r}")
            continue
        expected_frames = len(schedule.get("timestamps") or [])
        if len(log["frames"]) != expected_frames:
            reasons.append(f"{side}: {len(log['frames'])} frames recorded for {expected_frames} scheduled")
        events = _scheduled_events(schedule)
        for frame in log["frames"]:
            applied = [e.get("kind") for e in frame["events"]]
            if applied != events.get(frame["frame"], []):
                reasons.append(f"{side}: frame {frame['frame']} applied {applied}, "
                               f"scheduled {events.get(frame['frame'], [])}")
        blank = [f"{f['frame']}/{v['view']}" for f in log["frames"] for v in f["views"]
                 if not v["pixels_sha256"]]
        if blank:
            reasons.append(f"{side}: no pixels for frame/view {blank[:5]}")
    for key in ("environment", "inputs"):
        reasons += [f"identity {key}{d}" for d in ph.json_differences(a[key], b[key])]

    minimum = int((case.get("params") or {}).get("min_changed_frames", 1))
    changed = [_changed_frames(a["frames"]), _changed_frames(b["frames"])]
    if min(changed) < minimum:
        reasons.append(f"vacuous frames: frames that changed {changed}, need {minimum}: "
                       "the schedule exercised nothing")

    first_divergent = None
    for fa, fb in zip(a["frames"], b["frames"]):
        # Timestamps are schedule bookkeeping; everything else in a frame is
        # something the implementation computed.
        if fa["timestamp"] != fb["timestamp"]:
            reasons.append(f"schedule frame {fa['frame']}: timestamp {fa['timestamp']} != {fb['timestamp']}")
        diffs = ph.json_differences({k: v for k, v in fa.items() if k != "timestamp"},
                                    {k: v for k, v in fb.items() if k != "timestamp"})
        if diffs:
            if first_divergent is None:
                first_divergent = fa["frame"]
            reasons += [f"{FRAME_REASON}{fa['frame']}{d}" for d in diffs[:4]]
        if len(reasons) > 40:
            break
    final_identical = bool(a["frames"] and b["frames"]) and (
        [_observable(v) for v in a["frames"][-1]["views"]] ==
        [_observable(v) for v in b["frames"][-1]["views"]] and
        a["frames"][-1]["params"] == b["frames"][-1]["params"])
    if not ph.json_differences(a["views"], b["views"]) == []:
        reasons += [f"view lifetimes{d}" for d in ph.json_differences(a["views"], b["views"])]

    captures = sorted(p.name for p in ref.iterdir() if CAPTURE_RE.match(p.name))
    candidate_captures = sorted(p.name for p in cand.iterdir() if CAPTURE_RE.match(p.name))
    if captures != candidate_captures:
        reasons.append(f"captures {captures} != {candidate_captures}")
    for name in captures:
        if (cand / name).is_file():
            pair = ph.compare_png_pair(ref / name, cand / name, 0, 1)
            reasons += [f"capture {name}: {r}" for r in pair.get("reasons", [])]

    return ph.lane_result("fail" if reasons else "pass", reasons=reasons[:40],
                          frames=[len(a["frames"]), len(b["frames"])],
                          changed_frames=changed, first_divergent_frame=first_divergent,
                          final_frame_identical=final_identical, captures=captures,
                          identity=identity)


# ── seeded controls ────────────────────────────────────────────────────────


def _edit(path: Path, mutate) -> bool:
    log = _load(path)
    if not mutate(log):
        return False
    path.write_text(json.dumps(log, indent=1), encoding="utf-8")
    return True


def _one_frame_late(log: dict[str, Any]) -> bool:
    """The first change in view 0 lands one frame later; the end is untouched."""
    frames = log["frames"]
    for k in range(1, len(frames) - 1):
        before = frames[k - 1]["views"][0] if frames[k - 1]["views"] else None
        now = frames[k]["views"][0] if frames[k]["views"] else None
        if before and now and _observable(before) != _observable(now):
            for key in ("state", "pixels_sha256", "layout_sha256", "readiness", "focused"):
                now[key] = copy.deepcopy(before[key])
            return True
    return False


def _perturb_dt(log: dict[str, Any]) -> bool:
    for frame in log["frames"][1:]:
        for view in frame["views"]:
            if view.get("dispatched") and view.get("dt"):
                view["dt"] = view["dt"] * 2
                return True
    return False


def _drop_pixels_key(log: dict[str, Any]) -> bool:
    log["frames"][0]["views"][0].pop("pixels_sha256", None)
    return True


def _freeze(log: dict[str, Any]) -> bool:
    first = log["frames"][0]
    for frame in log["frames"][1:]:
        frame["views"] = copy.deepcopy(first["views"])
        frame["params"] = copy.deepcopy(first["params"])
    return True


def _truncate(log: dict[str, Any]) -> bool:
    log["frames"].pop()
    return True


def _blank_pixels(log: dict[str, Any]) -> bool:
    for frame in log["frames"]:
        for view in frame["views"]:
            view["pixels_sha256"] = ""
    return True


def _drop_event(log: dict[str, Any]) -> bool:
    for frame in log["frames"]:
        if frame["events"]:
            frame["events"].pop(0)
            return True
    return False


def _flip_capture(directory: Path) -> bool:
    captures = sorted(p for p in directory.iterdir() if CAPTURE_RE.match(p.name))
    if not captures:
        return False
    ph.mutate_png(captures[-1], ph.flip_center_pixel)
    return True


def _reasons(result: dict[str, Any]) -> list[str]:
    return result.get("reasons") or []


OBSERVABLE_RE = re.compile(
    r"^frame \d+/(views/\d+/(state|pixels_sha256|layout_sha256|readiness|focused|pixel_size)|views: length|params)")


def _observable_reason(result: dict[str, Any]) -> bool:
    """Rejected because what a viewer would see differed at some frame, not
    merely because the schedule's own bookkeeping (timestamps, input hash)
    differed."""
    return any(OBSERVABLE_RE.match(r) for r in _reasons(result))


def _contains(text: str):
    return lambda result: any(text in r for r in _reasons(result))


ARTIFACT_CONTROLS = {
    "frames-one-frame-late": {
        "lane": LANE,
        "candidate": lambda d: _edit(d / ARTIFACT, _one_frame_late),
        # The defect a final-frame comparison misses: rejected by a frame
        # record while the final frames still agree.
        "bites": lambda r: _observable_reason(r) and r.get("final_frame_identical") is True,
    },
    "frames-dt-perturbed": {"lane": LANE, "candidate": lambda d: _edit(d / ARTIFACT, _perturb_dt),
                              "bites": _contains("/dt:")},
    "frames-schema-key-dropped-both": {"lane": LANE, "both": lambda d: _edit(d / ARTIFACT, _drop_pixels_key),
                                         "bites": _contains("missing pixels_sha256")},
    "frames-frozen-both": {"lane": LANE, "both": lambda d: _edit(d / ARTIFACT, _freeze),
                             "bites": _contains("vacuous frames")},
    "frames-truncated": {"lane": LANE, "candidate": lambda d: _edit(d / ARTIFACT, _truncate),
                           "bites": _contains("frames recorded")},
    "frames-blank-pixels-both": {"lane": LANE, "both": lambda d: _edit(d / ARTIFACT, _blank_pixels),
                                   "bites": _contains("no pixels")},
    "frames-event-dropped-both": {"lane": LANE, "both": lambda d: _edit(d / ARTIFACT, _drop_event),
                                    "bites": _contains("scheduled")},
    "frames-capture-pixel": {"lane": LANE, "candidate": _flip_capture,
                               "bites": _contains("capture frame-")},
}


def _rerun_with_schedule(mutate):
    """A rerun control: the candidate replays a modified copy of the case's
    schedule, staged under the original file name so the command line is
    unchanged. Returns None when the mutation does not apply to the case."""

    def prepare(case: dict[str, Any], source_root: Path, work: Path):
        if LANE not in case["lanes"]:
            return None
        root = ph.case_root(case, source_root)
        name = (case.get("params") or {}).get("schedule")
        original = next((root / s for s in case.get("stage", []) if Path(s).name == name), None)
        if original is None or not original.is_file():
            return None
        schedule = _load(original)
        if not mutate(schedule, case):
            return None
        staged = work / "schedule" / original.name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(json.dumps(schedule, indent=1), encoding="utf-8")
        # A file a control writes itself travels as extra_stage; corpus `stage`
        # entries stay relative to the case root.
        stage = [s for s in case.get("stage", []) if Path(s).name != name]
        return dict(case, stage=stage), {"extra_stage": [staged]}

    return prepare


def _retime(hz: float):
    def mutate(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
        start = schedule["timestamps"][0]
        schedule["timestamps"] = [round(start + i / hz, 9) for i in range(len(schedule["timestamps"]))]
        schedule["nominal_dt"] = round(1 / hz, 9)
        return True
    return mutate


def _dropped_vsync(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
    stamps = schedule["timestamps"]
    if len(stamps) < 5:
        return False
    step = stamps[1] - stamps[0]
    at = int((case.get("params") or {}).get("drop_at", 4))
    schedule["timestamps"] = stamps[:at] + [round(t + step, 9) for t in stamps[at:]]
    return True


def _event_late(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
    events = schedule.get("events") or []
    if not events or events[0]["frame"] + 1 >= len(schedule["timestamps"]):
        return False
    events[0]["frame"] += 1
    return True


def _readiness_immediate(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
    if case.get("source_kind") != "frames-script" or schedule.get("readiness", "deferred") != "deferred":
        return False
    schedule["readiness"] = "immediate"
    return True


def _param_write_changed(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
    for event in schedule.get("events") or []:
        if event["kind"] == "set_param":
            event["value"] = round(float(event["value"]) - 0.0625, 6)
            return True
    return False


def _no_second_view(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
    events = schedule.get("events") or []
    kept = [e for e in events if e["kind"] != "attach_view" and int(e.get("view", 0)) == 0]
    if len(kept) == len(events):
        return False
    schedule["events"] = kept
    return True


def _resize_changed(schedule: dict[str, Any], case: dict[str, Any]) -> bool:
    for event in schedule.get("events") or []:
        if event["kind"] == "resize":
            event["width"] = int(event["width"]) + 8
            return True
    return False


def _rerun(mutate) -> dict[str, Any]:
    return {"lane": LANE, "prepare": _rerun_with_schedule(mutate), "identical_inputs": False,
            "bites": _observable_reason}


def _timer_clock_ignored(case: dict[str, Any], source_root: Path, work: Path):
    """The candidate keeps script timers on the wall clock while the schedule
    advances host time: the timer case must then diverge frame by frame. Only
    cases that declare timers apply, since a timer-free script cannot tell the
    clocks apart."""
    params = case.get("params") or {}
    if LANE not in case["lanes"] or not params.get("timers") or params.get("timer_clock") != "schedule":
        return None
    return dict(case, params=dict(params, timer_clock="wall")), {}


RERUN_CONTROLS = {
    "schedule-rate-120hz": _rerun(_retime(120.0)),
    "schedule-dropped-vsync": _rerun(_dropped_vsync),
    "event-one-frame-late": _rerun(_event_late),
    "readiness-immediate": _rerun(_readiness_immediate),
    "param-write-changed": _rerun(_param_write_changed),
    "second-view-not-attached": _rerun(_no_second_view),
    "resize-changed": _rerun(_resize_changed),
    "timer-clock-ignored": {"lane": LANE, "prepare": _timer_clock_ignored, "bites": _observable_reason},
}


ph.register_lane(LANE, (ARTIFACT,), compare_frames)
ph.register_controls(artifact=ARTIFACT_CONTROLS, rerun=RERUN_CONTROLS)
