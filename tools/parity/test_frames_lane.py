#!/usr/bin/env python3
"""Unit tests for the frames lane, against fake frame observers.

The honest fake integrates the scheduled host timestamps, applies every event
in its frame, mounts a deferred document a frame late and renders state-derived
pixels, so every seeded control must bite. Two dishonest fakes must make
calibration report NOT CALIBRATED: one that ignores the timestamps (a fixed dt),
and one that applies every event on frame 0.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parity_harness as ph  # noqa: E402

sys.path.insert(0, str(HERE / "lanes"))
import frames as lane  # noqa: E402  (registers the lane and its controls on import)

ph.LANE_MODULES.append((HERE / "lanes" / "frames.py").resolve())

FAKE = textwrap.dedent(
    """
    import hashlib, json, sys
    sys.path.insert(0, {cli_dir!r})
    from vellum_png import RgbaImage, encode_png
    MODE = {mode!r}
    args = sys.argv[1:]
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    kind = "script" if "--script" in args else "design-ir"
    source = opt("--script") if kind == "script" else opt("--design-ir")
    schedule_bytes = open(opt("--schedule"), "rb").read()
    schedule = json.loads(schedule_bytes)
    width, height = int(opt("--width")), int(opt("--height"))
    clock = opt("--timer-clock", "schedule")
    timers_on_schedule = clock == "schedule" or MODE == "clock-flag-ignored"
    params = {{p["name"]: float(p.get("default", 0)) for p in schedule.get("params", [])}}
    views = [dict(index=0, w=width, h=height, glow=0.0, target=0.0, attached=0, detached=None)]
    deferred = kind == "script" and schedule.get("readiness", "deferred") == "deferred"
    frames, last = [], None
    t0 = schedule["timestamps"][0]
    timer_fired = False
    def pixels(v):
        shade = int(v["glow"] * 200) + int(params.get("cutoff", 0) * 50) + (17 if timer_fired else 0)
        return RgbaImage(4, 4, bytes([shade % 256, 10, 20, 255]) * 15 + bytes([255, 255, 255, 255]))
    for f, t in enumerate(schedule["timestamps"]):
        applied = []
        for e in schedule.get("events", []):
            due = 0 if MODE == "events-at-zero" else e["frame"]
            if due != f:
                continue
            applied.append(dict(kind=e["kind"], view=e.get("view", 0)))
            v = views[e.get("view", 0)] if e.get("view", 0) < len(views) else None
            if e["kind"] == "hover":
                v["target"] = 1.0 if e["x"] < 100 else 0.0
            elif e["kind"] == "set_param":
                params[e["name"]] = e["value"]
            elif e["kind"] == "attach_view":
                views.append(dict(index=len(views), w=width, h=height, glow=0.0, target=0.0, attached=f, detached=None))
            elif e["kind"] == "detach_view":
                v["detached"] = f
            elif e["kind"] == "resize":
                v["w"], v["h"] = e["width"], e["height"]
        # A 50 ms script timer: on the schedule clock it fires once scheduled
        # time passes the deadline; on the wall clock this fast fake never
        # lets 50 ms of real time pass.
        if schedule.get("timer_script") and timers_on_schedule and t - t0 >= 0.05:
            timer_fired = True
        dt = (1 / 60) if (last is None or MODE == "fixed-dt") else (t - last)
        last = t
        records = []
        for v in views:
            if v["detached"] is not None:
                continue
            step = 4 * dt
            v["glow"] = min(v["target"], v["glow"] + step) if v["target"] > v["glow"] else max(v["target"], v["glow"] - step)
            ready = not deferred or f > v["attached"]
            state = [dict(id="knob", value=params.get("cutoff", 0), hover_glow=round(v["glow"], 6)),
                     dict(id="status", text="fired" if timer_fired else "waiting")] if ready else []
            image = pixels(v)
            records.append(dict(view=v["index"], dispatched=True, dt=dt, resumed=f == v["attached"],
                                rendered=True, continuous=0 < v["glow"] < 1, continuous_after=0 < v["glow"] < 1,
                                clock_time=t, readiness=dict(ready=ready), focused="", state=state,
                                layout_sha256=hashlib.sha256(str((v["w"], v["h"])).encode()).hexdigest(),
                                pixels_sha256=hashlib.sha256(image.pixels).hexdigest(),
                                pixel_size=f"{{v['w'] * 2}}x{{v['h'] * 2}}"))
            if f in schedule.get("capture", []):
                open(f"frame-v{{v['index']}}-f{{f:04d}}.png", "wb").write(encode_png(image))
        frames.append(dict(frame=f, timestamp=t, events=applied, views=records, params=dict(params)))
    log = dict(schema="pulp-ui-frames-v1",
               environment=dict(platform="fake", arch="fake", backend="fake-raster", scale=2, width=width,
                                height=height, timer_clock=clock),
               inputs=dict(mode=kind, input=source, input_sha256=hashlib.sha256(open(source, "rb").read()).hexdigest(),
                           schedule_sha256=hashlib.sha256(schedule_bytes).hexdigest()),
               frames=frames, bindings=[],
               views=[dict(view=v["index"], attached_frame=v["attached"], detached_frame=v["detached"]) for v in views])
    open("frames.json", "w").write(json.dumps(log, indent=1))
    """
)


def make_fixture(root: Path, mode: str) -> tuple[Path, Path, Path]:
    tool = root / f"fake_frames_{mode}.py"
    tool.write_text(FAKE.format(cli_dir=str(HERE.parents[1] / "cli"), mode=mode), encoding="utf-8")
    tool.chmod(0o755)
    wrapper = root / f"fake_frames_{mode}"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {tool} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    fixtures = root / "fixtures"
    fixtures.mkdir(exist_ok=True)
    (fixtures / "panel.json").write_text('{"panel": true}\n', encoding="utf-8")
    (fixtures / "panel.js").write_text("// scripted panel\n", encoding="utf-8")
    stamps = [round(10.0 + i / 60, 9) for i in range(16)]
    base = {"schema": "pulp-ui-frame-schedule-v1", "timestamps": stamps, "nominal_dt": round(1 / 60, 9),
            "params": [{"name": "cutoff", "default": 0.5}], "capture": [0, 15]}
    ir = dict(base, events=[{"frame": 1, "kind": "hover", "x": 40, "y": 40},
                            {"frame": 7, "kind": "set_param", "name": "cutoff", "value": 0.8},
                            {"frame": 8, "kind": "attach_view"},
                            {"frame": 9, "view": 1, "kind": "resize", "width": 100, "height": 80},
                            {"frame": 11, "kind": "hover", "x": 300, "y": 40},
                            {"frame": 13, "view": 1, "kind": "detach_view"}])
    script = dict(base, readiness="deferred", events=ir["events"])
    (fixtures / "ir.schedule.json").write_text(json.dumps(ir), encoding="utf-8")
    (fixtures / "script.schedule.json").write_text(json.dumps(script), encoding="utf-8")
    (fixtures / "timers.schedule.json").write_text(json.dumps(dict(base, readiness="immediate", events=[], timer_script=True)),
                                                   encoding="utf-8")
    corpus = {"schema": "vellum.parity.corpus.v1", "cases": [
        {"case_id": "ir", "source_base": "corpus", "source": "fixtures/panel.json",
         "stage": ["fixtures/ir.schedule.json"], "source_kind": "frames-design-ir", "width": 80, "height": 60,
         "lanes": ["frames"], "scale": 2, "params": {"schedule": "ir.schedule.json", "drop_at": 2, "min_changed_frames": 4,
                                    "timer_clock": "schedule"}},
        {"case_id": "script", "source_base": "corpus", "source": "fixtures/panel.js",
         "stage": ["fixtures/script.schedule.json"], "source_kind": "frames-script", "width": 80, "height": 60,
         "lanes": ["frames"], "scale": 2, "params": {"schedule": "script.schedule.json", "drop_at": 2, "min_changed_frames": 4,
                                    "timer_clock": "schedule"}},
        {"case_id": "timers", "source_base": "corpus", "source": "fixtures/panel.js",
         "stage": ["fixtures/timers.schedule.json"], "source_kind": "frames-script", "width": 80, "height": 60,
         "lanes": ["frames"], "scale": 2,
         "params": {"schedule": "timers.schedule.json", "drop_at": 2, "min_changed_frames": 1,
                    "timer_clock": "schedule", "timers": 1}}]}
    corpus_path = root / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    impl = json.loads((HERE / "implementations" / "pulp-ui-frames-observer.json").read_text(encoding="utf-8"))
    impl["tools"] = {"frames": str(wrapper)}
    impl["probes"] = [{"name": name, "argv": [sys.executable, "-c", f"print('{name} of the fake')"]}
                      for name in ph.REQUIRED_PROBES]
    impl_path = root / "impl.json"
    impl_path.write_text(json.dumps(impl), encoding="utf-8")
    return corpus_path, impl_path, wrapper


def git_commit(repo: Path) -> str:
    """The harness reads the commit from the checkout, so fixtures live in a repository."""
    run = lambda *a: subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=t",
                                              "-c", "user.email=t@example.invalid", *a], text=True).strip()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    run("add", "-A")
    run("commit", "-qm", "fixture")
    return run("rev-parse", "HEAD")


def calibrate(root: Path, mode: str) -> dict:
    corpus, impl, _ = make_fixture(root, mode)
    git_commit(root)
    return ph.calibrate(corpus, root, impl, root / f"out-{mode}")


class FramesLaneTest(unittest.TestCase):
    def test_honest_fake_calibrates_and_every_control_bites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = calibrate(Path(tmp), "honest")
            self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"])
            outcomes = {c["control"]: c["outcome"] for c in report["controls"]}
            for name in list(lane.ARTIFACT_CONTROLS) + list(lane.RERUN_CONTROLS) + ["planted-skip"]:
                self.assertEqual(outcomes[name], "REJECTED as required", (name, report["controls"]))
            self.assertEqual(outcomes["raster-one-pixel"], "OUT OF SCOPE (no case selects this lane)")
            self.assertEqual(report["lanes_in_scope"], ["frames"])
            self.assertTrue(report["calibrated"], report["control_failures"])

    def test_one_frame_late_is_invisible_to_the_final_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = calibrate(Path(tmp), "honest")
            late = next(c for c in report["controls"] if c["control"] == "frames-one-frame-late")
            self.assertTrue(late["lane_hit"])

    def test_fixed_dt_observer_is_not_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = calibrate(Path(tmp), "fixed-dt")
            self.assertFalse(report["calibrated"])
            self.assertIn("schedule-rate-120hz", report["control_failures"])
            self.assertIn("schedule-dropped-vsync", report["control_failures"])

    def test_observer_ignoring_the_timer_clock_flag_is_not_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = calibrate(Path(tmp), "clock-flag-ignored")
            self.assertFalse(report["calibrated"])
            self.assertIn("timer-clock-ignored", report["control_failures"])

    def test_events_at_zero_observer_is_not_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = calibrate(Path(tmp), "events-at-zero")
            self.assertFalse(report["calibrated"])
            self.assertIn("event-one-frame-late", report["control_failures"])

    def test_schema_hole_fails_rather_than_comparing_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = {"schema": "pulp-ui-frames-v1", "environment": {}, "inputs": {}, "frames": [
                {"frame": 0, "timestamp": 0, "events": [], "params": {}, "views": [{"view": 0, "dispatched": False}]}],
                "views": []}
            for side in ("ref", "cand"):
                (root / side).mkdir()
                (root / side / "frames.json").write_text(json.dumps(log), encoding="utf-8")
            result = lane.compare_frames(root / "ref", root / "cand", {"params": {"schedule": "x.json"}})
            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("missing pixels_sha256" in r for r in result["reasons"]), result["reasons"])

    def test_lane_and_controls_cannot_register_twice(self) -> None:
        with self.assertRaises(ph.HarnessError):
            ph.register_controls(rerun={"schedule-rate-120hz": lane.RERUN_CONTROLS["schedule-rate-120hz"]})
        with self.assertRaises(ph.HarnessError):
            ph.register_lane("frames", ("frames.json",), lane.compare_frames)


if __name__ == "__main__":
    unittest.main()
