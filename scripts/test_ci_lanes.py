#!/usr/bin/env python3
"""Every test file runs somewhere, and the workflows run the manifest's lanes."""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_ci_lane as lanes  # noqa: E402

# Each workflow step that runs a lane, by the lane it runs.
LANE_WORKFLOWS = {
    "release-policy": ".github/workflows/release-policy.yml",
    "release-source": ".github/workflows/gpu-macos.yml",
    "product-quality": ".github/workflows/product-quality.yml",
}


# The Linux checks that are required on main and run on pull_request and
# merge_group. This mirrors the repository's branch protection, which a test
# cannot read, so a change to that list is a change to this one.
REQUIRED_LINUX_CHECKS = ("product-quality", "provenance-verify", "forbidden-deps")


def workflow_events(text: str) -> dict[str, str]:
    """The `on:` block of a workflow: each event mapped to its nested lines."""
    match = re.search(r"^on:\n((?:[ \t]+.*\n|\s*\n)+)", text, re.MULTILINE)
    if match is None:
        return {}
    events: dict[str, str] = {}
    current = None
    for line in match.group(1).splitlines():
        header = re.fullmatch(r"  ([a-z_]+):\s*", line)
        if header:
            current = header.group(1)
            events[current] = ""
        elif current is not None and line.strip() and not line.lstrip().startswith("#"):
            events[current] += line + "\n"
    return events


def job_block(text: str, job: str) -> str | None:
    jobs = re.search(r"^jobs:\n", text, re.MULTILINE)
    if jobs is None:
        return None
    body = text[jobs.end():]
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", body, re.MULTILINE | re.DOTALL)
    return match.group(0) if match else None


def unconditional_step(block: str, needle: str) -> str | None:
    """The step of a job whose text holds `needle`, when it has no `if:` of its own."""
    for step in re.split(r"(?m)^      - ", block)[1:]:
        if needle in step:
            return None if re.search(r"(?m)^(?: {8}|)if:", step) else step
    return None


def linux_claim_problems(manifest: dict, root: Path = ROOT) -> list[str]:
    """Every `linux_covered` claim must be backed by a required Linux check.

    An entry the Mac lane skips on an event is only skipped because a required
    Linux check runs the same test on that event, unconditionally. Returns one
    line per claim that is not provably backed; empty means every claim holds.
    """
    problems: list[str] = []
    for lane_name, entries in manifest["lanes"].items():
        for entry in entries:
            claim = entry.get("linux_covered")
            if claim is None:
                continue
            label = f"{lane_name}: {json.dumps(entry, sort_keys=True)}"
            if "lane" in claim:
                backing_lane = claim["lane"]
                workflow = LANE_WORKFLOWS.get(backing_lane)
                if backing_lane == lane_name or workflow is None:
                    problems.append(f"{label}: `{backing_lane}` is not a different workflow lane")
                    continue
                twin = {k: v for k, v in entry.items() if k != "linux_covered"}
                runs = [other for other in manifest["lanes"].get(backing_lane, [])
                        if {k: v for k, v in other.items() if k != "linux_covered"} == twin]
                if not runs:
                    problems.append(f"{label}: lane `{backing_lane}` has no identical entry")
                elif any(skip in other.get("linux_covered", {}).get("events", [])
                         for other in runs for skip in claim["events"]):
                    problems.append(f"{label}: the covering entry is itself skipped on a claimed event")
                job, needle = backing_lane, f"scripts/run_ci_lane.py --lane {backing_lane}"
            else:
                workflow, job = claim["workflow"], claim["job"]
                needle = entry.get("script", "")
                if not needle:
                    problems.append(f"{label}: a workflow claim needs a `script` entry")
                    continue
            if job not in REQUIRED_LINUX_CHECKS:
                problems.append(f"{label}: job `{job}` is not a required Linux check")
            try:
                text = (root / workflow).read_text(encoding="utf-8")
            except OSError:
                problems.append(f"{label}: {workflow} is unreadable")
                continue
            events = workflow_events(text)
            for event in claim["events"]:
                if event not in events:
                    problems.append(f"{label}: {workflow} does not run on {event}")
                elif event == "pull_request" and events[event].strip():
                    problems.append(f"{label}: {workflow} filters pull_request runs")
                elif event == "merge_group" and events[event].split() not in ([], ["branches:", "[main]"]):
                    problems.append(f"{label}: {workflow} filters merge_group runs")
            block = job_block(text, job)
            if block is None:
                problems.append(f"{label}: {workflow} has no job `{job}`")
                continue
            if not re.search(rf"(?m)^    name: {re.escape(job)}$", block):
                problems.append(f"{label}: job `{job}` does not report as `{job}`")
            if "VELLUM_LINUX_RUNS_ON_JSON" not in block:
                problems.append(f"{label}: job `{job}` does not run on a Linux runner")
            condition = re.search(r"(?m)^    if:(.*)$", block)
            if condition and condition.group(1).strip() != "${{ !cancelled() }}":
                problems.append(f"{label}: job `{job}` is conditional: {condition.group(1).strip()}")
            if unconditional_step(block, needle) is None:
                problems.append(f"{label}: job `{job}` has no unconditional step running `{needle}`")
    return problems


def tracked_tests() -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode("utf-8").split("\0")
    return sorted(
        path for path in listed
        if path and not path.startswith("mirror/")
        and fnmatch.fnmatch(PurePosixPath(path).name, "test_*.py")
    )


def covered_by(entry: dict, path: str) -> bool:
    if "script" in entry:
        return entry["script"] == path
    if "module" in entry:
        return entry["module"] == path.removesuffix(".py").replace("/", ".")
    parent = str(PurePosixPath(path).parent)
    return parent == entry["discover"].rstrip("/") and fnmatch.fnmatch(
        PurePosixPath(path).name, entry["pattern"]
    )


def uncovered(manifest: dict, tests: list[str]) -> list[str]:
    entries = [entry for lane in manifest["lanes"].values() for entry in lane]
    return [
        path for path in tests
        if path not in manifest["elsewhere"]
        and not any(covered_by(entry, path) for entry in entries)
    ]


class CiLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = lanes.load()

    def test_every_tracked_test_file_is_run_somewhere(self) -> None:
        tests = tracked_tests()
        self.assertIn("scripts/test_ci_lanes.py", tests)  # control: the listing sees tests
        self.assertEqual(uncovered(self.manifest, tests), [])

    def test_an_unlisted_test_is_reported(self) -> None:
        # Negative control: removing one script entry makes its file uncovered.
        manifest = json.loads(json.dumps(self.manifest))
        policy = manifest["lanes"]["release-policy"]
        policy[:] = [e for e in policy if e.get("script") != "scripts/test_verify_release_tag_ruleset.py"]
        self.assertEqual(uncovered(manifest, tracked_tests()), ["scripts/test_verify_release_tag_ruleset.py"])

    def test_release_path_tests_run_in_release_policy(self) -> None:
        scripts = {e.get("script") for e in self.manifest["lanes"]["release-policy"]}
        for path in ("scripts/test_verify_release_tag_ruleset.py",
                     "scripts/test_collect_release_tag_rulesets.py",
                     "tools/provenance/test_mint_github_app_jwt.py",
                     "scripts/test_retired_release_tags.py"):
            self.assertIn(path, scripts)

    def test_every_entry_names_a_real_target(self) -> None:
        for name, entries in self.manifest["lanes"].items():
            for entry in entries:
                with self.subTest(lane=name, entry=entry):
                    lanes.command(entry)
                    target = entry.get("script") or entry.get("discover")
                    if target is None:
                        target = entry["module"].replace(".", "/") + ".py"
                    self.assertTrue((ROOT / target).exists(), target)

    def test_elsewhere_names_a_runner_that_mentions_the_file(self) -> None:
        for path, where in self.manifest["elsewhere"].items():
            with self.subTest(path=path):
                kind, _, runner = where.partition(":")
                self.assertIn(kind, ("workflow", "cmake"))
                runner_path = ROOT / (f".github/workflows/{runner}" if kind == "workflow" else runner)
                text = runner_path.read_text(encoding="utf-8")
                self.assertTrue(path in text or PurePosixPath(path).name in text, where)

    def test_android_gpu_surface_is_run_by_android_workflow(self) -> None:
        text = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("tools/android/test_android_gpu_surface.cmake", text)

    def test_each_lane_workflow_calls_the_runner_and_lists_no_tests(self) -> None:
        self.assertEqual(set(LANE_WORKFLOWS), set(self.manifest["lanes"]))
        for lane, workflow in LANE_WORKFLOWS.items():
            with self.subTest(lane=lane):
                text = (ROOT / workflow).read_text(encoding="utf-8")
                self.assertIn(f"scripts/run_ci_lane.py --lane {lane}", text)
                # A hand-written test command beside the runner is the drift
                # this manifest replaces.
                for entry in self.manifest["lanes"][lane]:
                    if "script" in entry and PurePosixPath(entry["script"]).name.startswith("test_"):
                        self.assertNotIn(f" {entry['script']}", text, entry["script"])

    def test_every_linux_covered_claim_is_backed_by_a_required_linux_check(self) -> None:
        claims = [(name, entry) for name, lane in self.manifest["lanes"].items()
                  for entry in lane if "linux_covered" in entry]
        self.assertGreaterEqual(len(claims), 30)  # control: the claims are being read
        self.assertEqual(linux_claim_problems(self.manifest), [])

    def test_mac_only_entries_are_never_skipped(self) -> None:
        # The mirror toolchain and iOS tests need macOS; they run on every event.
        for entry in self.manifest["lanes"]["release-source"]:
            if (entry.get("discover") == "tools/mirror"
                    or "metal_ios" in entry.get("script", "")
                    or entry.get("script", "").startswith("scripts/test_select_exact_provenance")):
                self.assertNotIn("linux_covered", entry, entry)

    def test_a_claim_with_its_linux_twin_removed_is_reported(self) -> None:
        # Negative control: drop one covered entry from product-quality only.
        manifest = json.loads(json.dumps(self.manifest))
        quality = manifest["lanes"]["product-quality"]
        quality[:] = [e for e in quality if e.get("script") != "scripts/test_docs_sync.py"]
        problems = linux_claim_problems(manifest)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("scripts/test_docs_sync.py", problems[0])
        self.assertIn("has no identical entry", problems[0])

    def test_an_entry_removed_from_both_lanes_is_reported(self) -> None:
        # Negative control: a test dropped from the Mac lane AND the Linux lane
        # runs nowhere, and the coverage test names the file.
        manifest = json.loads(json.dumps(self.manifest))
        for lane in ("release-source", "product-quality"):
            entries = manifest["lanes"][lane]
            entries[:] = [e for e in entries if e.get("script") != "scripts/test_docs_sync.py"]
        self.assertEqual(linux_claim_problems(manifest), [])
        self.assertEqual(uncovered(manifest, tracked_tests()), ["scripts/test_docs_sync.py"])

    def test_a_workflow_claim_fails_when_the_linux_step_is_gone_or_conditional(self) -> None:
        manifest = json.loads(json.dumps(self.manifest))
        workflow = ".github/workflows/provenance.yml"
        text = (ROOT / workflow).read_text(encoding="utf-8")
        job = job_block(text, "provenance-verify")
        self.assertIsNotNone(job)  # control: the parser finds the job
        needle = "tools/provenance/test_verify_extraction.py"
        self.assertIsNotNone(unconditional_step(job, needle))  # control: and the step
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".github/workflows").mkdir(parents=True)
            for broken in (text.replace(needle, "tools/provenance/other.py"),
                           text.replace("      - name: Test extraction and active-source quarantine\n",
                                        "      - name: Test extraction and active-source quarantine\n"
                                        "        if: false\n")):
                (root / workflow).write_text(broken, encoding="utf-8")
                problems = linux_claim_problems(manifest, root)
                self.assertTrue(any("no unconditional step" in p and needle in p for p in problems), problems)

    def test_a_claim_fails_when_the_linux_workflow_stops_running_on_an_event(self) -> None:
        manifest = json.loads(json.dumps(self.manifest))
        text = (ROOT / LANE_WORKFLOWS["product-quality"]).read_text(encoding="utf-8")
        self.assertEqual(set(workflow_events(text)) & {"pull_request", "merge_group"},
                         {"pull_request", "merge_group"})  # control
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in {w for w in LANE_WORKFLOWS.values()} | {".github/workflows/provenance.yml"}:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
            target = root / LANE_WORKFLOWS["product-quality"]
            target.write_text(text.replace("  merge_group:\n    branches: [main]\n", ""), encoding="utf-8")
            problems = linux_claim_problems(manifest, root)
            self.assertTrue(any("does not run on merge_group" in p for p in problems), problems)
            target.write_text(text.replace("  pull_request:\n", "  pull_request:\n    paths: ['cli/**']\n", 1),
                              encoding="utf-8")
            problems = linux_claim_problems(manifest, root)
            self.assertTrue(any("filters pull_request" in p for p in problems), problems)

    def test_runner_skips_claimed_entries_only_on_the_claimed_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a", "b"):
                (root / f"{name}.py").write_text(f"print('ran {name}')\n", encoding="utf-8")
            claim = {"events": ["pull_request", "merge_group"], "lane": "product-quality"}
            manifest = {"schema": lanes.SCHEMA, "elsewhere": {}, "lanes": {"x": [
                {"script": str(root / "a.py"), "linux_covered": claim}, {"script": str(root / "b.py")}]}}
            path = root / "lanes.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            def ran(*extra: str) -> str:
                return subprocess.run(
                    [sys.executable, "-I", str(ROOT / "scripts/run_ci_lane.py"), "--lane", "x",
                     "--manifest", str(path), *extra],
                    capture_output=True, text=True, encoding="utf-8", check=True).stdout

            for event in ("pull_request", "merge_group"):
                output = ran("--event", event)
                self.assertNotIn("ran a", output)
                self.assertIn("ran b", output)
            for extra in (["--event", "push"], ["--event", "workflow_dispatch"], []):
                output = ran(*extra)
                self.assertIn("ran a", output)
                self.assertIn("ran b", output)

    def test_malformed_claims_fail_closed(self) -> None:
        for claim in ({}, {"events": []}, {"events": ["push"]}, {"events": ["push"], "lane": ""},
                      {"events": ["push"], "lane": "x", "job": "y"}, "pull_request"):
            with self.subTest(claim=claim), self.assertRaises(lanes.LaneError):
                lanes.command({"script": "x.py", "linux_covered": claim})

    def test_runner_stops_at_the_first_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ok.py").write_text("print('ran ok')\n", encoding="utf-8")
            (root / "bad.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
            (root / "never.py").write_text("raise SystemExit('reached')\n", encoding="utf-8")
            manifest = {"schema": lanes.SCHEMA, "elsewhere": {}, "lanes": {"x": [
                {"script": str(root / "ok.py")}, {"script": str(root / "bad.py")},
                {"script": str(root / "never.py")}]}}
            path = root / "lanes.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts/run_ci_lane.py"), "--lane", "x", "--manifest", str(path)],
                capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("ran ok", result.stdout)
            self.assertNotIn("reached", result.stderr)

    def test_runner_annotates_the_first_failing_item_in_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.py").write_text(
                "print('missing field: release.sha256')\nraise SystemExit(7)\n",
                encoding="utf-8",
            )
            manifest = {"schema": lanes.SCHEMA, "elsewhere": {}, "lanes": {"release-source": [
                {"script": str(root / "bad.py")}
            ]}}
            path = root / "lanes.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            environment = dict(os.environ, GITHUB_ACTIONS="true")
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts/run_ci_lane.py"),
                 "--lane", "release-source", "--manifest", str(path)],
                capture_output=True, text=True, encoding="utf-8", check=False,
                env=environment,
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn(
                "::error title=release-source::missing field: release.sha256",
                result.stdout,
            )

    def test_runner_survives_non_utf8_child_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bytes.py").write_text(
                "import sys\nsys.stdout.buffer.write(b'bad \\xff byte\\n')\nsys.stdout.flush()\n",
                encoding="utf-8",
            )
            manifest = {"schema": lanes.SCHEMA, "elsewhere": {}, "lanes": {"x": [
                {"script": str(root / "bytes.py")}
            ]}}
            path = root / "lanes.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts/run_ci_lane.py"), "--lane", "x", "--manifest", str(path)],
                capture_output=True, text=True, encoding="utf-8", check=False,
                env=dict(os.environ, PYTHONUTF8="0", LC_ALL="C"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_unknown_entries_and_lanes_fail_closed(self) -> None:
        with self.assertRaises(lanes.LaneError):
            lanes.command({"script": "x.py", "shell": "rm"})
        with self.assertRaises(lanes.LaneError):
            lanes.run("no-such-lane", self.manifest)


if __name__ == "__main__":
    unittest.main()
