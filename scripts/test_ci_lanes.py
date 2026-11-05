#!/usr/bin/env python3
"""Every test file runs somewhere, and the workflows run the manifest's lanes."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path, PurePosixPath
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

    def test_unknown_entries_and_lanes_fail_closed(self) -> None:
        with self.assertRaises(lanes.LaneError):
            lanes.command({"script": "x.py", "shell": "rm"})
        with self.assertRaises(lanes.LaneError):
            lanes.run("no-such-lane", self.manifest)


if __name__ == "__main__":
    unittest.main()
