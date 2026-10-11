#!/usr/bin/env python3
"""Unit tests for the paired parity harness.

The harness is exercised end to end against a small fake implementation, so the
tests need no external toolchain. The fake is deliberately honest: every
observation is a function of its input, so the seeded negative controls must
fail against it. A second fake ignores its input; calibration must notice that
the controls no longer bite and refuse to report the harness as calibrated.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parity_harness as ph  # noqa: E402

CLI_DIR = Path(__file__).resolve().parents[2] / "cli"

FAKE_TOOL = textwrap.dedent(
    """
    import json, sys, zlib
    sys.path.insert(0, {cli_dir!r})
    from vellum_png import RgbaImage, encode_png

    IGNORE_INPUT = {ignore_input!r}
    args = sys.argv[1:]
    mode = args.pop(0)

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    if mode == "import":
        source = json.load(open(opt("--file")))
        if IGNORE_INPUT:
            source = {{"nodes": [{{"name": "fixed", "x": 4, "y": 4, "w": 10, "h": 10}},
                                 {{"name": "also-fixed", "x": 20, "y": 4, "w": 6, "h": 20}}]}}
        children = [dict(type="frame", name=n["name"], x=n["x"], y=n["y"], w=n["w"], h=n["h"])
                    for n in source["nodes"]]
        json.dump({{"version": 1, "imported_at": "volatile-" + str(id(children)),
                   "root": {{"type": "frame", "name": "root", "children": children}}}},
                  open(opt("--output"), "w"))
        sys.exit(0)

    ir = json.load(open(opt("--input")))
    scale = float(opt("--scale", "1"))
    width, height = int(int(opt("--width")) * scale), int(int(opt("--height")) * scale)
    nodes = ir["root"]["children"]
    values = {{n["name"]: 0.5 for n in nodes}}
    focus = None
    steps = []
    plan = []
    i = 0
    while i < len(args):
        if args[i] in ("--click", "--drag", "--key"):
            plan.append((args[i], args[i + 1]))
            i += 2
        else:
            i += 1

    def hit(x, y):
        for n in nodes:
            if n["x"] <= x < n["x"] + n["w"] and n["y"] <= y < n["y"] + n["h"]:
                return n["name"]
        return ""

    for kind, value in plan:
        record = {{"kind": kind[2:]}}
        if kind == "--key":
            if value == "tab" and nodes:
                names = [n["name"] for n in nodes]
                focus = names[(names.index(focus) + 1) % len(names)] if focus in names else names[0]
                target = focus
            else:
                target = ""
            record.update(key=value, target=target, handled=bool(target))
        else:
            first = value.split(":")[0]
            x, y = (float(v) for v in first.split(","))
            target = hit(x, y)
            if kind == "--drag" and target:
                values[target] = min(1.0, values[target] + 0.25)
            record.update(target=target, handled=bool(target))
        record["focused"] = focus or ""
        record["state"] = [{{"anchor": k, "value": v}} for k, v in sorted(values.items())]
        steps.append(record)

    import os
    tint = open("assets/tint.txt").read() if os.path.exists("assets/tint.txt") else ""
    pixels = bytearray(b"\\x10\\x10\\x10\\xff" * (width * height))
    for n in nodes:
        shade = 64 + int(values[n["name"]] * 128)
        colour = bytes((zlib.crc32((n["name"] + tint).encode()) & 0xFF, shade, 200, 255))
        for y in range(n["y"], min(height, n["y"] + n["h"])):
            for x in range(n["x"], min(width, n["x"] + n["w"])):
                o = (y * width + x) * 4
                pixels[o:o + 4] = colour
    open(opt("--render"), "wb").write(encode_png(RgbaImage(width, height, bytes(pixels))))
    common = {{"visible": True, "overflow": "visible", "z_order": {{"paint": 0}}, "clipping": {{}},
              "hit_regions": [], "measured_text_boxes": []}}
    layout = [dict(common, id="root", kind="View", rect={{"x": 0, "y": 0, "w": width, "h": height}})]
    layout += [dict(common, id=n["name"], kind="View",
                    rect={{"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"]}}) for n in nodes]
    json.dump({{"nodes": layout}}, open(opt("--layout"), "w"))
    if opt("--text-diagnostics"):
        json.dump({{"runs": [{{"text": n["name"]}} for n in nodes]}}, open(opt("--text-diagnostics"), "w"))
    if opt("--interaction-log"):
        json.dump({{"steps": steps}}, open(opt("--interaction-log"), "w"))
    """
)


def git_commit(repo: Path) -> str:
    """The harness reads each side's commit from its checkout, so test sources live in a repository."""
    run = lambda *a: subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=t",
                                              "-c", "user.email=t@example.invalid", *a], text=True).strip()
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    run("add", "-A")
    run("commit", "-qm", "fixture")
    return run("rev-parse", "HEAD")


def implementation(tool: Path) -> dict:
    return {
        "schema": ph.IMPLEMENTATION_SCHEMA,
        "name": "fake",
        "repository": "example/fake",
        "source_sha": None,
        "tools": {"fake": str(tool)},
        "source_kinds": {"fake-json": []},
        "authority": "native-render",
        "probes": [{"name": name, "argv": [sys.executable, "-c", f"print('{name} of the fake')"]}
                   for name in ("build", "provider", "fonts")],
        "interaction_encoding": "argv-click-drag-key",
        "steps": [
            {"id": "import", "argv": [sys.executable, "{tool:fake}", "import", "--file", "{source}",
                                      "--output", "design.ir.json"]},
            {"id": "observe", "argv": [sys.executable, "{tool:fake}", "observe", "--input", "design.ir.json",
                                       "--render", "render.png", "--layout", "layout.json",
                                       "--text-diagnostics", "text.json", "--scale", "{scale}",
                                       "--width", "{width}", "--height", "{height}"]},
            {"id": "interact", "when": "interactions",
             "argv": [sys.executable, "{tool:fake}", "observe", "--input", "design.ir.json",
                      "--render", "interaction.png", "--layout", "interaction-layout.json",
                      "--width", "{width}", "--height", "{height}", "--scale", "{scale}",
                      "--interaction-log", "interaction.json", "@interactions"]},
        ],
    }


SOURCE = {"nodes": [{"name": "knob", "x": 4, "y": 4, "w": 12, "h": 12},
                    {"name": "fader", "x": 20, "y": 4, "w": 6, "h": 20}]}


def corpus(cases: list[dict] | None = None) -> dict:
    return {
        "schema": ph.CORPUS_SCHEMA,
        "cases": cases if cases is not None else [{
            "case_id": "fake-controls",
            "source": "source.json",
            "source_kind": "fake-json",
            "stage": ["assets"],
            "width": 40,
            "height": 32,
            "scale": 1,
            "lanes": ["structure", "raster", "geometry", "semantic", "interaction"],
            "volatile": {"structure": ["/imported_at"]},
            "regions": [{"name": "knob", "x": 4, "y": 4, "w": 12, "h": 12}],
            "interactions": [{"key": "tab"}, {"drag": [[8, 8], [8, 2]]}, {"click": [22, 10]}],
        }],
    }


class HarnessFixture(unittest.TestCase):
    ignore_input = False

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tool = self.root / "fake_tool.py"
        self.tool.write_text(FAKE_TOOL.format(cli_dir=str(CLI_DIR), ignore_input=self.ignore_input))
        (self.root / "src").mkdir()
        (self.root / "src/source.json").write_text(json.dumps(SOURCE))
        (self.root / "src/assets").mkdir()
        (self.root / "src/assets/tint.txt").write_text("warm")
        git_commit(self.root / "src")
        self.impl_path = self.root / "impl.json"
        ph.write_json(self.impl_path, implementation(self.tool))
        self.corpus_path = self.root / "corpus.json"
        ph.write_json(self.corpus_path, corpus())

    def tearDown(self) -> None:
        self._tmp.cleanup()


class CalibrationTests(HarnessFixture):
    def test_honest_implementation_calibrates_and_every_control_bites(self) -> None:
        report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
        self.assertTrue(report["positive"]["verdict"]["accepting"], report["positive"]["verdict"])
        failed = {c["control"]: c for c in report["controls"]
                  if c["outcome"] != "REJECTED as required" and not c["outcome"].startswith("OUT OF SCOPE")}
        self.assertEqual(failed, {})
        self.assertTrue(report["calibrated"])
        names = {c["control"] for c in report["controls"]}
        for required in ("raster-blank-both", "geometry-zero-nodes-both", "planted-skip",
                         "identity-same-in-parity-mode", "empty-corpus", "input-one-byte",
                         "interaction-pointer-moved", "interaction-key-changed"):
            self.assertIn(required, names)

    def test_positive_receipt_is_scoped_as_calibration_not_parity(self) -> None:
        report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
        self.assertEqual(report["positive"]["verdict"]["scope"], "harness-calibration")
        self.assertEqual(report["positive"]["mode"], "calibration")

    def test_volatile_field_must_be_declared(self) -> None:
        undeclared = corpus()
        undeclared["cases"][0].pop("volatile")
        ph.write_json(self.corpus_path, undeclared)
        receipt = ph.run_corpus(self.corpus_path, self.root / "src", self.impl_path, self.impl_path,
                                self.root / "out", mode="calibration")
        lane = receipt["cases"][0]["lanes"]["structure"]
        self.assertEqual(lane["status"], "fail")
        self.assertTrue(any("/imported_at" in r for r in lane["reasons"]))


class BlockedBaselineTests(HarnessFixture):
    def test_blocked_positive_reports_not_calibrated_instead_of_crashing(self) -> None:
        impl = implementation(self.tool)
        impl["steps"][2]["argv"] = [sys.executable, "-c", "import sys; sys.exit(3)"]  # interact fails
        ph.write_json(self.impl_path, impl)
        report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
        self.assertFalse(report["calibrated"])
        self.assertFalse(report["positive"]["verdict"]["accepting"])
        outcomes = {c["control"]: c["outcome"] for c in report["controls"]}
        self.assertEqual(outcomes["interaction-state"], "NOT APPLICABLE (no case exercises it)")


class InsensitiveImplementationTests(HarnessFixture):
    ignore_input = True

    def test_calibration_refuses_when_a_control_cannot_bite(self) -> None:
        report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
        self.assertTrue(report["positive"]["verdict"]["accepting"])
        self.assertIn("input-one-byte", report["control_failures"])
        self.assertFalse(report["calibrated"])


class AcceptanceTests(unittest.TestCase):
    def identity(self, sha: str = "b" * 40, tool_hash: str | None = "c" * 64) -> dict:
        return {"name": "x", "repository": "example/x", "source_sha": sha, "source_sha_derived": True,
                "tools": {"t": {"path": "/t", "sha256": tool_hash}},
                "probes": {name: {"exit": 0, "output": name, "sha256": name} for name in ph.REQUIRED_PROBES}}

    def passing_case(self) -> dict:
        lanes = {lane: {"status": "not_selected"} for lane in ph.LANES}
        lanes["raster"] = {"status": "pass"}
        return {"case_id": "c", "inputs_identical": True, "lanes": lanes}

    def test_parity_mode_refuses_self_comparison(self) -> None:
        verdict = ph.acceptance("parity", [self.passing_case()], self.identity(), self.identity())
        self.assertFalse(verdict["accepting"])

    def test_parity_mode_refuses_relabelled_identical_binaries(self) -> None:
        relabelled = dict(self.identity(sha="d" * 40), repository="example/other")
        verdict = ph.acceptance("parity", [self.passing_case()], self.identity(), relabelled)
        self.assertFalse(verdict["accepting"])
        self.assertTrue(any("byte-identical tools" in r for r in verdict["reasons"]))

    def test_parity_mode_accepts_distinct_binaries(self) -> None:
        verdict = ph.acceptance("parity", [self.passing_case()], self.identity(),
                                self.identity(sha="d" * 40, tool_hash="e" * 64))
        self.assertTrue(verdict["accepting"], verdict["reasons"])

    def test_operator_typed_commit_is_not_identity(self) -> None:
        typed = dict(self.identity(tool_hash="e" * 64), source_sha_derived=False)
        self.assertFalse(ph.acceptance("parity", [self.passing_case()], self.identity(), typed)["accepting"])

    def test_missing_identity_probe_is_refused(self) -> None:
        thin = self.identity(tool_hash="e" * 64)
        del thin["probes"]["fonts"]
        self.assertFalse(ph.acceptance("parity", [self.passing_case()], self.identity(), thin)["accepting"])

    def test_calibration_mode_refuses_distinct_identities(self) -> None:
        verdict = ph.acceptance("calibration", [self.passing_case()], self.identity(),
                                self.identity(tool_hash="e" * 64))
        self.assertFalse(verdict["accepting"])

    def test_missing_tool_hash_is_not_an_identity(self) -> None:
        verdict = ph.acceptance("parity", [self.passing_case()], self.identity(),
                                self.identity(sha="d" * 40, tool_hash=None))
        self.assertFalse(verdict["accepting"])

    def test_blocked_and_unselected_are_never_accepting(self) -> None:
        blocked = self.passing_case()
        blocked["lanes"]["raster"] = {"status": "blocked"}
        unselected = self.passing_case()
        unselected["lanes"]["raster"] = {"status": "not_selected"}
        for case in (blocked, unselected):
            verdict = ph.acceptance("parity", [case], self.identity(),
                                    self.identity(sha="d" * 40, tool_hash="e" * 64))
            self.assertFalse(verdict["accepting"])

    def test_empty_corpus_is_not_accepting(self) -> None:
        self.assertFalse(ph.acceptance("parity", [], self.identity(),
                                       self.identity(sha="d" * 40, tool_hash="e" * 64))["accepting"])


class IdentityTests(unittest.TestCase):
    def test_identity_comparator(self) -> None:
        origin = {"a/x.cpp": "1" * 40, "a/y.hpp": "2" * 40}
        manifest = {"unit": "u", "roots": ["a"]}
        self.assertEqual(ph.compare_identity(manifest, origin, dict(origin))["status"], "pass")
        for exported in ({"a/x.cpp": "1" * 40},
                         dict(origin, **{"a/z.cpp": "3" * 40}),
                         dict(origin, **{"a/y.hpp": "4" * 40})):
            self.assertEqual(ph.compare_identity(manifest, origin, exported)["status"], "fail")
        self.assertEqual(ph.compare_identity({"roots": []}, {}, {})["status"], "fail")

    def test_identity_calibration_against_a_real_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "unit/src").mkdir(parents=True)
            (repo / "unit/src/a.cpp").write_text("int a() { return 1; }\n")
            (repo / "unit/b.hpp").write_text("#pragma once\n")
            (repo / "other.txt").write_text("outside the closure\n")
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(git + ["add", "."], check=True)
            subprocess.run(git + ["commit", "-qm", "seed"], check=True)
            commit = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
            report = ph.calibrate_identity({"unit": "unit", "roots": ["unit"]}, repo, commit, Path(tmp) / "out")
            self.assertEqual(report["positive"]["origin_files"], 2)
            self.assertTrue(report["calibrated"], report)


class ClosureManifestIdentityTests(unittest.TestCase):
    """The identity lane against a machine-generated closure manifest (exact
    files) and an export committed under a mirror prefix."""

    def git(self, repo: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=t",
                                        "-c", "user.email=t@example.invalid", *args], text=True).strip()

    def commit(self, repo: Path, files: dict[str, str]) -> str:
        for path, text in files.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "c")
        return self.git(repo, "rev-parse", "HEAD")

    def test_exact_files_and_mirror_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            origin, mirror = Path(tmp) / "origin", Path(tmp) / "mirror"
            for repo in (origin, mirror):
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
            files = {"unit/a.cpp": "a\n", "unit/b.hpp": "b\n"}
            origin_sha = self.commit(origin, dict(files, **{"unit/not_in_closure.cpp": "x\n"}))
            manifest = {"schema": "pulp.vellum.closure.v1", "files": sorted(files)}
            good = self.commit(mirror, {f"mirror/{p}": t for p, t in files.items()})
            origin_blobs = ph.git_tree_blobs(origin, origin_sha, ph.manifest_paths(manifest))
            self.assertEqual(sorted(origin_blobs), sorted(files))

            def check(commit: str) -> dict:
                return ph.compare_identity(manifest, origin_blobs,
                                           ph.git_exported_blobs(mirror, commit, "mirror/"))

            self.assertEqual(check(good)["status"], "pass")
            mutated = self.commit(mirror, {"mirror/unit/a.cpp": "A\n"})
            self.assertEqual(check(mutated)["status"], "fail")
            build_copy = dict(manifest, files=manifest["files"] + ["build/_deps/vendored.h"])
            self.assertIn("closure path absent from origin commit: build/_deps/vendored.h",
                          ph.compare_identity(build_copy, origin_blobs,
                                              ph.git_exported_blobs(mirror, good, "mirror/"))["reasons"])
            extra = self.commit(mirror, {"mirror/unit/a.cpp": "a\n", "mirror/unit/extra.cpp": "e\n"})
            self.assertIn("not in origin closure: unit/extra.cpp", check(extra)["reasons"])


class CorpusValidationTests(unittest.TestCase):
    def test_rejects_malformed_corpora(self) -> None:
        good = corpus()["cases"][0]
        for broken in (
            {"schema": "wrong", "cases": []},
            corpus([dict(good, lanes=["pixels"])]),
            corpus([dict(good, width=0)]),
            corpus([good, dict(good)]),
            corpus([dict(good, interactions=[])]),
            corpus([dict(good, case_id="../escape")]),
            corpus([dict(good, raster_threshold=255)]),
            corpus([dict(good, raster_threshold=4)]),
            corpus([dict(good, min_ink_pixels=0)]),
            corpus([dict(good, scale=0)]),
            corpus([dict(good, min_structure_nodes=2)]),
            corpus([dict(good, volatile={"structure": ["/root"]})]),
            corpus([dict(good, volatile={"semantic": ["/runs/0/text"]})]),
            dict(corpus(), volatile_allowlist=[{"pointer": "/runs"}]),
            dict(corpus(), volatile_allowlist=[{"pointer": "/runs", "justification": "x"}]),
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(ph.HarnessError):
                    ph.validate_corpus(broken)


class ScopeTests(unittest.TestCase):
    def ident(self, repo: str, sha: str, origin: dict | None = None) -> dict:
        return {"repository": repo, "source_sha": sha, "origin": origin}

    def test_scope_classification(self) -> None:
        pulp_a = self.ident("Generous-Corp/pulp", "a" * 40)
        self.assertEqual(ph.comparison_scope("parity", pulp_a, dict(pulp_a)), "build-sensitivity")
        self.assertEqual(ph.comparison_scope("parity", pulp_a, self.ident("generous-corp/PULP", "a" * 40)),
                         "build-sensitivity")
        self.assertEqual(ph.comparison_scope("parity", pulp_a, self.ident("Generous-Corp/pulp", "b" * 40)),
                         "revision-difference")
        mirror = self.ident("Generous-Corp/vellum", "c" * 40,
                            {"repository": "Generous-Corp/pulp", "commit": "a" * 40})
        self.assertEqual(ph.comparison_scope("parity", pulp_a, mirror), "parity")
        self.assertEqual(ph.comparison_scope("calibration", pulp_a, pulp_a), "harness-calibration")

    def test_repository_must_match_the_checkout_remote_and_origin_must_be_readable(self) -> None:
        base = {"repository": "Generous-Corp/vellum", "repository_remote": "Generous-Corp/pulp",
                "source_sha": "a" * 40, "source_sha_derived": True, "tools": {"t": {"sha256": "c" * 64}},
                "probes": {n: {"exit": 0, "output": n} for n in ph.REQUIRED_PROBES}, "origin": None}
        self.assertTrue(any("checkout remote" in e for e in ph.identity_errors(base, "x")))
        broken_origin = dict(base, repository_remote="Generous-Corp/vellum", origin={"commit": "nope"})
        self.assertTrue(any("export receipt origin" in e for e in ph.identity_errors(broken_origin, "x")))
        good = dict(base, repository_remote="Generous-Corp/vellum",
                    origin={"repository": "Generous-Corp/pulp", "commit": "b" * 40})
        self.assertEqual(ph.identity_errors(good, "x"), [])

    def test_export_origin_reads_the_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "export-receipt.json"
            ph.write_json(receipt, {"pulp_repository": "Generous-Corp/pulp", "pulp_commit": "d" * 40,
                                    "mirror_commit": "e" * 40})
            origin = ph.export_origin({"export_receipt": receipt})
            self.assertEqual((origin["repository"], origin["commit"]), ("Generous-Corp/pulp", "d" * 40))
            self.assertIsNone(ph.export_origin({}))
            ph.write_json(receipt, {"pulp_repository": "Generous-Corp/pulp", "pulp_sha": "f" * 40, "mirror_sha": "e" * 40})
            landed = ph.export_origin({"export_receipt": receipt})
            self.assertEqual((landed["commit"], landed["mirror_commit"]), ("f" * 40, "e" * 40))


class KnobBoundsTests(unittest.TestCase):
    def test_justified_threshold_and_allowlisted_volatile_are_accepted(self) -> None:
        good = corpus()["cases"][0]
        allowed = dict(corpus([dict(good, raster_threshold=4, raster_threshold_justification="AA on a hairline",
                                     volatile={"semantic": ["/input"]})]),
                       volatile_allowlist=[{"pointer": "/input", "justification": "observer input path"}])
        (case,) = ph.validate_corpus(allowed)
        self.assertEqual(case["raster_threshold"], 4)


class CalibrationGateTests(HarnessFixture):
    def run_parity(self, calibration) -> dict:
        other = self.root / "other_tool.py"
        other.write_text(self.tool.read_text() + "\n# a different binary\n")
        candidate = implementation(other)
        cand_path = self.root / "cand.json"
        ph.write_json(cand_path, candidate)
        roots = {"reference": {"checkout": self.root / "src"}, "candidate": {"checkout": self.root / "src"}}
        return ph.run_corpus(self.corpus_path, self.root / "src", self.impl_path, cand_path,
                             self.root / "parity", mode="parity", roots=roots, calibration=calibration)

    def test_parity_run_needs_a_matching_calibration(self) -> None:
        report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "cal")
        self.assertTrue(report["calibrated"])
        same_source = self.run_parity(report)["verdict"]
        self.assertTrue(same_source["accepting"])
        self.assertEqual(same_source["scope"], "build-sensitivity")
        refused = self.run_parity(None)
        self.assertFalse(refused["verdict"]["accepting"])
        changed = json.loads(json.dumps(report))
        changed["positive"]["corpus"]["sha256"] = "0" * 64
        self.assertIn("corpus differs from the calibrated corpus (re-run calibrate)",
                      self.run_parity(changed)["verdict"]["reasons"])
        uncalibrated = dict(report, calibrated=False)
        self.assertFalse(self.run_parity(uncalibrated)["verdict"]["accepting"])


class ControlScopeTests(HarnessFixture):
    def test_unselected_lane_controls_are_out_of_scope_and_bites_is_enforced(self) -> None:
        only_raster = corpus()
        only_raster["cases"][0]["lanes"] = ["raster"]
        ph.write_json(self.corpus_path, only_raster)
        report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
        outcomes = {c["control"]: c["outcome"] for c in report["controls"]}
        self.assertEqual(outcomes["geometry-shift-node"], "OUT OF SCOPE (no case selects this lane)")
        self.assertEqual(outcomes["raster-one-pixel"], "REJECTED as required")
        self.assertEqual(report["lanes_in_scope"], ["raster"])
        self.assertTrue(report["calibrated"], report["control_failures"])
        ph.ARTIFACT_CONTROLS["raster-wrong-reason"] = {
            "lane": "raster", "candidate": lambda d: (ph.mutate_png(d / "render.png", ph.flip_center_pixel), True)[1],
            "bites": lambda result: any("ink coverage" in r for r in result.get("reasons", []))}
        try:
            report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out2")
        finally:
            del ph.ARTIFACT_CONTROLS["raster-wrong-reason"]
        self.assertIn("raster-wrong-reason", report["control_failures"])


class RelativeOutTests(HarnessFixture):
    def test_relative_out_and_corpus_resolve_against_the_invoking_directory(self) -> None:
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            status = ph.main(["calibrate", "--corpus", "corpus.json", "--source-root", "src",
                              "--implementation", "impl.json", "--out", "../" + self.root.name + "/rel-out"])
        finally:
            os.chdir(cwd)
        self.assertEqual(status, 0)
        self.assertTrue((self.root / "rel-out" / "calibration.json").is_file())

    def test_paths_reach_calibrate_absolute(self) -> None:
        # A rerun control may stage a companion it wrote under the out dir; the
        # stager joins it onto the corpus dir, which is only safe when out is absolute.
        seen = {}
        real = ph.calibrate

        def capture(corpus_path, source_root, impl_path, out, *args, **kwargs):
            seen.update(corpus=corpus_path, source=source_root, out=out)
            return real(corpus_path, source_root, impl_path, out, *args, **kwargs)

        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            ph.calibrate = capture
            ph.main(["calibrate", "--corpus", "corpus.json", "--source-root", "src",
                     "--implementation", "impl.json", "--out", "rel-out-2"])
        finally:
            ph.calibrate = real
            os.chdir(cwd)
        self.assertTrue(all(Path(v).is_absolute() for v in seen.values()), seen)


class AuthorityAndProvenanceTests(HarnessFixture):
    def test_authority_is_declared_and_cross_authority_is_never_parity(self) -> None:
        impl = implementation(self.tool)
        del impl["authority"]
        with self.assertRaises(ph.HarnessError):
            ph.validate_implementation(impl)
        a = {"repository": "Generous-Corp/pulp", "source_sha": "a" * 40, "authority": "native-render"}
        b = {"repository": "Generous-Corp/vellum", "source_sha": "b" * 40, "authority": "browser-source"}
        self.assertEqual(ph.comparison_scope("parity", a, b), "cross-authority")
        self.assertEqual(ph.comparison_scope("parity", a, dict(b, authority="native-render")), "parity")

    def test_observations_must_come_from_declared_binaries(self) -> None:
        run = ph.run_side(corpus()["cases"][0] | {"_corpus_dir": str(self.root)}, implementation(self.tool),
                          {"fake": self.tool}, self.root / "src", self.root / "side")
        identity = {"tools": {"fake": {"sha256": ph.sha256_file(self.tool)}}, "passthrough_binaries": []}
        self.assertEqual(ph.provenance_errors(run, identity, "x", ["raster", "structure"]), [])
        stranger = {"tools": {"fake": {"sha256": "1" * 64}}, "passthrough_binaries": []}
        self.assertTrue(ph.provenance_errors(run, stranger, "x", ["raster"]))
        self.assertIn("binary_sha256", run["produced_by"]["render.png"])

    def test_nested_artifacts_are_hashed_and_their_producer_checked(self) -> None:
        nested = "deep/nested-observation.json"
        ph.LANE_ARTIFACTS["nested-probe"] = (nested,)
        try:
            impl = implementation(self.tool)
            impl["steps"].append({"id": "mkdir", "argv": ["/bin/mkdir", "-p", "deep"]})
            impl["steps"].append({"id": "nested", "argv": ["/usr/bin/touch", "deep/nested-observation.json"]})
            case = corpus()["cases"][0] | {"_corpus_dir": str(self.root)}
            run = ph.run_side(case, impl, {"fake": self.tool}, self.root / "src", self.root / "nested-side")
            self.assertIn(nested, run["artifacts"])
            self.assertEqual(run["produced_by"][nested]["binary"], "/usr/bin/touch")
            identity = {"tools": {"fake": {"sha256": ph.sha256_file(self.tool)}}, "passthrough_binaries": []}
            errors = ph.provenance_errors(run, identity, "x", ["nested-probe"])
            self.assertTrue(any(nested in e and "undeclared binary" in e for e in errors), errors)
        finally:
            del ph.LANE_ARTIFACTS["nested-probe"]

    def test_nested_artifact_calibration_control_bites(self) -> None:
        def nested_lane(ref, cand, case):
            same = (ref / "deep/obs.json").read_bytes() == (cand / "deep/obs.json").read_bytes()
            return {"status": "pass" if same else "fail", "reasons": [] if same else ["differs"]}

        ph.register_lane("nested-lane", ("deep/obs.json",), nested_lane)
        try:
            impl = implementation(self.tool)
            impl["steps"].append({"id": "nested", "argv": [
                sys.executable, str(self.tool), "nested", "--write", "deep/obs.json"]})
            ph.write_json(self.impl_path, impl)
            self.tool.write_text(self.tool.read_text().replace(
                'mode = args.pop(0)',
                'mode = args.pop(0)\nif mode == "nested":\n    import os\n'
                '    os.makedirs("deep", exist_ok=True)\n    open("deep/obs.json", "w").write("{}")\n'
                '    sys.exit(0)'))
            ph.write_json(self.impl_path, implementation(self.tool) | {"steps": impl["steps"]})
            nested_corpus = corpus()
            nested_corpus["cases"][0]["lanes"].append("nested-lane")
            ph.write_json(self.corpus_path, nested_corpus)
            report = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
            outcomes = {c["control"]: c["outcome"] for c in report["controls"]}
            self.assertEqual(outcomes["provenance-undeclared-nested-binary"], "REJECTED as required")
            self.assertTrue(report["calibrated"], report["control_failures"])
        finally:
            ph.LANES.remove("nested-lane")
            ph.LANE_ARTIFACTS.pop("nested-lane", None)
            ph.COMPARATORS.pop("nested-lane", None)

    def test_regions_are_validated_and_named_when_they_differ(self) -> None:
        good = corpus()["cases"][0]
        for regions in ([{"name": "a", "x": 0, "y": 0, "w": 50, "h": 4}],
                        [{"name": "a", "x": 0, "y": 0, "w": 4, "h": 4}, {"name": "a", "x": 1, "y": 1, "w": 2, "h": 2}],
                        [{"name": "a", "x": 0, "y": 0, "w": 0, "h": 4}]):
            with self.subTest(regions=regions), self.assertRaises(ph.HarnessError):
                ph.validate_corpus(corpus([dict(good, regions=regions)]))
        ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "out")
        ref = self.root / "out/runs/fake-controls/reference"
        cand = self.root / "regional"
        shutil.copytree(self.root / "out/runs/fake-controls/candidate", cand)
        self.assertTrue(ph.flip_region_pixel(cand / "render.png", dict(good, scale=1)))
        result = ph.compare_raster(ref, cand, dict(good, scale=1))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(r.startswith("region knob:") for r in result["reasons"]))
        # A region on bare background drew nothing: it fails even though the
        # whole frame matches exactly.
        empty = dict(good, scale=1, regions=[{"name": "corner", "x": 30, "y": 26, "w": 8, "h": 4}])
        same = ph.compare_raster(ref, self.root / "out/runs/fake-controls/candidate", empty)
        self.assertEqual(same["differing_pixels"], 0)
        self.assertEqual(same["status"], "fail")

    def test_external_sources_need_a_permissive_pinned_licence(self) -> None:
        good = dict(corpus()["cases"][0], source_base="external:thirdparty")
        prov = {"license": "MIT", "repository": "example/x", "commit": "a" * 40, "sha256": "b" * 64}
        ph.validate_corpus(corpus([dict(good, provenance=prov)]))
        for bad in ({}, dict(prov, license="TBD"), dict(prov, commit="abc"), dict(prov, sha256="x")):
            with self.subTest(bad=bad), self.assertRaises(ph.HarnessError):
                ph.validate_corpus(corpus([dict(good, provenance=bad)]))
        pinned = dict(good, provenance=dict(prov, sha256="0" * 64),
                      _external_roots={"thirdparty": str(self.root / "src")})
        with self.assertRaises(ph.HarnessError):
            ph.stage_case(pinned, self.root / "src", self.root / "staged")


class ContainmentTests(HarnessFixture):
    """R10 must-fix 1: a corpus path may not leave its declared root."""

    def staged(self, **changes) -> None:
        case = dict(corpus()["cases"][0], **changes)
        case["_corpus_dir"] = str(self.root)
        ph.stage_case(case, self.root / "src", self.root / "staged")

    def test_escapes_are_refused_before_staging(self) -> None:
        (self.root / "outside.json").write_text("{}")
        (self.root / "outside-dir").mkdir()
        (self.root / "outside-dir/x.json").write_text("{}")
        os.symlink(self.root / "outside.json", self.root / "src/link.json")
        os.symlink(self.root / "outside-dir", self.root / "src/linkdir")
        (self.root / "src/inner").mkdir()
        os.symlink(self.root / "outside-dir", self.root / "src/inner/hop")
        (self.root / "src/assets/sneaky.txt").symlink_to(self.root / "outside.json")
        attempts = {
            "dotdot": {"source": "../outside.json"},
            "absolute": {"source": str(self.root / "outside.json")},
            "symlinked file": {"source": "link.json"},
            "symlinked dir": {"source": "linkdir/x.json"},
            "intermediate symlink": {"source": "inner/hop/x.json"},
            "companion escape": {"stage": ["../outside-dir"]},
            "symlink inside a staged dir": {"stage": ["assets"]},
        }
        for label, change in attempts.items():
            with self.subTest(label), self.assertRaises(ph.HarnessError):
                self.staged(**change)
        self.assertFalse((self.root / "staged/outside.json").exists())

    def test_contained_paths_still_stage(self) -> None:
        (self.root / "src/assets/sneaky.txt").unlink(missing_ok=True)
        self.staged()
        self.assertTrue((self.root / "staged/source.json").is_file())
        self.assertTrue((self.root / "staged/assets/tint.txt").is_file())


class RegionBoundsTests(HarnessFixture):
    """R10 must-fix 2: a region is never shortened to fit the render."""

    def pngs(self, ref_size, cand_size) -> tuple[Path, Path]:
        dirs = []
        for name, (w, h) in (("ref", ref_size), ("cand", cand_size)):
            d = self.root / name
            d.mkdir()
            pixels = bytearray(b"\x10\x10\x10\xff" * (w * h))
            pixels[0:4] = b"\xff\x00\x00\xff"
            (d / "render.png").write_bytes(ph.encode_png(ph.RgbaImage(w, h, bytes(pixels))))
            dirs.append(d)
        return dirs[0], dirs[1]

    def test_region_outside_both_smaller_renders_fails(self) -> None:
        ref, cand = self.pngs((20, 16), (20, 16))
        case = dict(corpus()["cases"][0], scale=1,
                    regions=[{"name": "wide", "x": 0, "y": 0, "w": 30, "h": 10}])
        result = ph.compare_regions(ref / "render.png", cand / "render.png", case)
        self.assertTrue(any("not fully present" in r for r in result["wide"]["reasons"]), result)

    def test_region_past_one_side_fails(self) -> None:
        ref, cand = self.pngs((40, 32), (20, 16))
        case = dict(corpus()["cases"][0], scale=1,
                    regions=[{"name": "corner", "x": 0, "y": 0, "w": 30, "h": 10}])
        result = ph.compare_regions(ref / "render.png", cand / "render.png", case)
        self.assertTrue(any("candidate render" in r for r in result["corner"]["reasons"]), result)


class DelegationTests(HarnessFixture):
    """R10 guard gap: a declared wrapper delegating through a command string."""

    def test_shell_command_string_is_refused_even_as_passthrough(self) -> None:
        ph.LANE_ARTIFACTS["delegate-probe"] = ("delegated.json",)
        try:
            impl = implementation(self.tool)
            impl["steps"].append({"id": "delegate", "argv": [
                "/bin/sh", "-c", f"{sys.executable} -c 'open(\"delegated.json\", \"w\").write(\"{{}}\")'"]})
            case = corpus()["cases"][0] | {"_corpus_dir": str(self.root)}
            run = ph.run_side(case, impl, {"fake": self.tool}, self.root / "src", self.root / "side")
            self.assertTrue(run["produced_by"]["delegated.json"].get("delegated"))
            identity = {"tools": {"fake": {"sha256": ph.sha256_file(self.tool)}},
                        "passthrough_binaries": ["/bin/sh"]}
            errors = ph.provenance_errors(run, identity, "x", ["delegate-probe"])
            self.assertTrue(any("shell command string" in e for e in errors), errors)
            inline = ph.producing_binary([sys.executable, "-c", "pass"])
            self.assertTrue(inline.get("delegated"))
            self.assertFalse(ph.producing_binary(["/bin/cp", "a", "b"]).get("delegated"))
        finally:
            del ph.LANE_ARTIFACTS["delegate-probe"]


class LaneRegistrationTests(unittest.TestCase):
    def test_duplicate_lane_and_control_are_refused(self) -> None:
        with self.assertRaises(ph.HarnessError):
            ph.register_lane("raster", ("x.png",), lambda a, b, c: {"status": "pass"})
        with self.assertRaises(ph.HarnessError):
            ph.register_controls(artifact={"raster-one-pixel": {"lane": "raster"}})


class StepFeatureTests(unittest.TestCase):
    def test_env_stderr_artifact_and_case_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/source.json").write_text("{}")
            script = "import os,sys; sys.stderr.write(os.environ['PARITY_PROBE'] + ':' + ','.join(sys.argv[1:]))"
            impl = {"name": "f", "repository": "r", "source_kinds": {"k": []},
                    "steps": [{"id": "observe", "argv": [sys.executable, "-c", script, "@case_args:faults"],
                               "env": {"PARITY_PROBE": "w{width}"}, "stderr_artifact": "probe.txt"}]}
            case = {"case_id": "c", "source": "source.json", "source_kind": "k", "width": 7, "height": 3,
                    "lanes": [], "args": {"faults": ["--lose-device", "{width}"]}}
            run = ph.run_side(case, impl, {}, root / "src", root / "out")
            self.assertEqual(run["steps"][0]["status"], "ok")
            self.assertEqual((root / "out/probe.txt").read_text(), "w7:--lose-device,7")
            self.assertEqual(run["steps"][0]["env"], {"PARITY_PROBE": "w7"})


class LaneModuleLoadingTests(unittest.TestCase):
    def test_lane_module_with_a_dataclass_registers_a_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp) / "demo_lane.py"
            module.write_text(textwrap.dedent("""
                from dataclasses import dataclass
                import parity_harness as ph

                @dataclass
                class Verdict:
                    status: str

                import pickle
                # Pickling resolves the class through sys.modules[__module__].
                assert pickle.loads(pickle.dumps(Verdict("pass"))).status == "pass"
                ph.register_lane("demo-lane", ("demo.json",), lambda a, b, c: {"status": Verdict("pass").status})
            """), encoding="utf-8")
            try:
                loaded = ph.load_lane_module(module)
                self.assertTrue(hasattr(loaded, "Verdict"))
                self.assertIs(ph.load_lane_module(module), loaded)
                self.assertIn("demo-lane", ph.LANES)
                self.assertIn(module.resolve(), ph.LANE_MODULES)
                self.assertIn(module.name, ph.harness_fingerprint()["files"])
            finally:
                ph.LANES.remove("demo-lane")
                ph.LANE_ARTIFACTS.pop("demo-lane", None)
                ph.COMPARATORS.pop("demo-lane", None)
                ph.LANE_MODULES.remove(module.resolve())


class MultiUnitManifestTests(unittest.TestCase):
    def test_union_of_units_and_duplicate_ownership(self) -> None:
        foundation = {"schema": "pulp.vellum.closure.v1", "files": ["core/runtime/a.cpp"],
                      "targets": [{"name": "pulp-runtime"}]}
        canvas = {"schema": "pulp.vellum.closure.v1", "files": ["core/canvas/b.cpp"],
                  "targets": [{"name": "pulp-canvas"}]}
        merged = ph.merge_manifests([foundation, canvas])
        self.assertEqual(ph.manifest_paths(merged), ["core/runtime/a.cpp", "core/canvas/b.cpp"])
        with self.assertRaises(ph.HarnessError):
            ph.merge_manifests([foundation, dict(canvas, files=["core/runtime/a.cpp"])])


class HostRequestTests(HarnessFixture):
    def test_content_roots_hash_whole_bundles_into_identity(self) -> None:
        bundle = self.root / "Plug.bundle"
        (bundle / "Contents/Resources").mkdir(parents=True)
        (bundle / "Contents/Resources/skin.png").write_bytes(b"one")
        before = ph.tree_sha256(bundle)
        (bundle / "Contents/Resources/skin.png").write_bytes(b"two")
        self.assertNotEqual(before, ph.tree_sha256(bundle))
        impl = dict(implementation(self.tool), content_roots=["bundle"])
        roots = {"checkout": self.root / "src", "bundle": bundle}
        with_bundle = ph.implementation_identity(impl, {"fake": self.tool}, roots)
        self.assertIn(with_bundle["content_roots"]["bundle"], ph.content_key(with_bundle))
        missing = ph.implementation_identity(impl, {"fake": self.tool}, {"checkout": self.root / "src"})
        self.assertTrue(any("content root bundle" in e for e in ph.identity_errors(missing, "x")))

    def test_mutators_may_take_the_case(self) -> None:
        seen = []
        self.assertTrue(ph.call_mutator(lambda d, case: seen.append(case["case_id"]) or True,
                                        self.root, {"case_id": "c1"}))
        self.assertTrue(ph.call_mutator(lambda d: True, self.root, {"case_id": "c2"}))
        self.assertEqual(seen, ["c1"])

    def test_case_filter_record_is_refused_by_parity(self) -> None:
        with self.assertRaises(ph.HarnessError):
            ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "x", case_filter=["nope"])
        subset = ph.calibrate(self.corpus_path, self.root / "src", self.impl_path, self.root / "sub",
                              case_filter=["fake-controls"])
        self.assertEqual(subset["case_filter"], ["fake-controls"])
        self.assertTrue(any("--case subset" in e for e in ph.calibration_errors(subset, corpus())))


class ComparatorTests(unittest.TestCase):
    def test_geometry_requires_the_layout_schema_on_both_sides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            for d in (a, b):
                d.mkdir()
                ph.write_json(d / "layout.json", {"nodes": [{"rect": {"x": 0, "y": 0, "w": 9, "h": 9}},
                                                            {"rect": {"x": 1, "y": 1, "w": 2, "h": 2}}]})
            result = ph.compare_geometry(a, b, {})
            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("missing required keys" in r for r in result["reasons"]))

    def test_stray_pixel_on_flat_field_fails_the_luminance_floor(self) -> None:
        flat = ph.RgbaImage(64, 64, b"\x10\x10\x10\xff" * 4096)
        stray = ph.stray_pixel_image(flat)
        self.assertEqual(ph.ink_pixels(stray), 1)
        self.assertLess(ph.luminance_stddev(stray), ph.MIN_LUMINANCE_STDDEV)

    def test_uniform_render_has_no_ink(self) -> None:
        image = ph.RgbaImage(4, 4, b"\x01\x02\x03\xff" * 16)
        self.assertEqual(ph.ink_pixels(image), 0)
        self.assertEqual(ph.ink_pixels(ph.flip_center_pixel(image)), 1)

    def test_volatile_pointer_removes_only_that_member(self) -> None:
        doc = {"a": 1, "b": {"c": 2, "d": 3}}
        self.assertEqual(ph.canonical(doc, ["/b/c"]), {"a": 1, "b": {"d": 3}})
        self.assertEqual(doc["b"]["c"], 2)
        with self.assertRaises(ph.HarnessError):
            ph.canonical(doc, ["b/c"])


if __name__ == "__main__":
    unittest.main()
