#!/usr/bin/env python3
"""The path filter that decides whether a pull request builds the SDK twice."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sdk_repro_inputs as inputs  # noqa: E402

WORKFLOW = ROOT / ".github/workflows/gpu-macos.yml"


def verdict(*paths: str) -> bool:
    return inputs.required(list(paths))[0]


class SdkReproInputsTests(unittest.TestCase):
    def test_link_and_toolchain_inputs_require_both_builds(self) -> None:
        for path in ("cmake/VellumSkiaDawn.cmake", "cmake/skia_dawn_provider.exports",
                     "CMakeLists.txt", "cmake/toolchains/arm64.cmake", "gpu-runtime/renderer-lock.json",
                     "gpu-runtime/CMakeLists.txt", "scripts/build_sdk_artifact.py",
                     "scripts/compare_sdk_archives.py", "tools/mirror/mirror_package.py",
                     "tools/ci/Apple-Toolchain.json", ".github/workflows/gpu-macos.yml",
                     "scripts/sdk_repro_inputs.py"):
            with self.subTest(path=path):
                self.assertTrue(verdict(path))

    def test_a_change_that_cannot_reach_the_link_builds_once(self) -> None:
        self.assertFalse(verdict("docs/guide.md"))
        self.assertFalse(verdict("scripts/test_docs_sync.py", "cli/vellum_cli.py"))

    def test_one_matching_path_among_many_requires_both_builds(self) -> None:
        self.assertTrue(verdict("docs/a.md", "cli/b.py", "cmake/VellumSkiaDawn.cmake"))

    def test_anything_unknown_requires_both_builds(self) -> None:
        for paths in ([], [""], ["/etc/passwd"], ["../x"], ["a/../cmake/x.txt"]):
            with self.subTest(paths=paths):
                self.assertTrue(verdict(*paths))

    def test_cli_fails_closed(self) -> None:
        script = str(ROOT / "scripts/sdk_repro_inputs.py")
        with tempfile.TemporaryDirectory() as temporary:
            listing = Path(temporary) / "paths"
            listing.write_bytes(b"docs/a.md\0cli/b.py\0")
            quiet = subprocess.run([sys.executable, "-I", script, "--files0-from", str(listing)],
                                   capture_output=True, text=True, check=True)
            self.assertEqual(quiet.stdout.strip(), "not-required")
            listing.write_bytes(b"docs/a.md\0cmake/VellumSkiaDawn.cmake\0")
            loud = subprocess.run([sys.executable, "-I", script, "--files0-from", str(listing)],
                                  capture_output=True, text=True, check=True)
            self.assertEqual(loud.stdout.strip(), "required")  # negative control
            listing.write_bytes(b"")
            self.assertEqual(subprocess.run([sys.executable, "-I", script, "--files0-from", str(listing)],
                                            capture_output=True, text=True, check=True).stdout.strip(),
                             "required")
            missing = subprocess.run([sys.executable, "-I", script, "--files0-from", str(listing) + ".none"],
                                     capture_output=True, text=True)
            self.assertEqual((missing.returncode, missing.stdout), (2, ""))

    def test_the_workflow_builds_twice_off_pull_requests_and_runs_nightly(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^  schedule:\n    - cron: ")
        step = text.split("      - name: Prove the installed SDK artifact is reproducible\n", 1)[1]
        step = step.split("\n      - name: ", 1)[0]
        # The only code that can turn single_build on is the pull_request and
        # merge_group arm, and it starts false: pushes, tags, the nightly run and
        # dispatches always build twice.
        self.assertEqual(step.count("single_build=true"), 1)
        self.assertLess(step.index("single_build=false"), step.index("case \"$GITHUB_EVENT_NAME\" in"))
        arm = step.split('case "$GITHUB_EVENT_NAME" in', 1)[1].split("esac", 1)[0]
        self.assertTrue(arm.lstrip().startswith("pull_request|merge_group)"), arm[:60])
        self.assertEqual(arm.count(")\n"), arm.count("pull_request|merge_group)\n"))  # no other arm
        # The judge is the base's copy and any doubt leaves single_build false.
        self.assertIn('git show "$base:scripts/sdk_repro_inputs.py"', arm)
        self.assertIn('[ "$repro_verdict" = not-required ]', arm)
        # The comparison runs whenever the SDK was built twice.
        self.assertIn('if [ "$single_build" != true ]; then\n            python3 scripts/compare_sdk_archives.py', step)
        self.assertIn('cmp "$first/SHA256SUMS" "$second/SHA256SUMS"', step)


    def decide(self, event: str, changed: str, *, base_has_decider: bool = True,
               queue_base: str = "") -> str:
        """Run the workflow's own decision block against a throwaway repository."""
        text = WORKFLOW.read_text(encoding="utf-8")
        step = text.split("      - name: Prove the installed SDK artifact is reproducible\n", 1)[1]
        block = step.split("          single_build=false\n", 1)[1].split("          esac\n", 1)[0]
        block = "single_build=false\n" + block + "esac\n"
        block = "\n".join(line[10:] if line.startswith(" " * 10) else line for line in block.splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            (repo / "scripts").mkdir(parents=True)
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run([*git[:3], "init", "-q"], check=True)
            (repo / "README.md").write_text("x\n", encoding="utf-8")
            if base_has_decider:
                shutil.copy(ROOT / "scripts/sdk_repro_inputs.py", repo / "scripts/sdk_repro_inputs.py")
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
            target = repo / changed
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("changed\n", encoding="utf-8")
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "change"], check=True)
            head = subprocess.run([*git[:3], "rev-parse", "HEAD"], check=True, capture_output=True,
                                  text=True).stdout.strip()
            environment = {**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_SHA": head,
                           "RUNNER_TEMP": temporary, "QUEUE_BASE": queue_base}
            result = subprocess.run(["bash", "-c", f"set -euo pipefail\n{block}\necho single=$single_build"],
                                    cwd=repo, env=environment, capture_output=True, text=True, check=True)
            return result.stdout.strip().splitlines()[-1]

    def test_decision_block_builds_once_only_for_unrelated_pull_requests(self) -> None:
        self.assertEqual(self.decide("pull_request", "docs/a.md"), "single=true")
        self.assertEqual(self.decide("pull_request", "cmake/VellumSkiaDawn.cmake"), "single=false")

    def test_decision_block_fails_closed(self) -> None:
        # Every one of these must build twice even though the diff is harmless.
        self.assertEqual(self.decide("pull_request", "docs/a.md", base_has_decider=False), "single=false")
        self.assertEqual(self.decide("merge_group", "docs/a.md"), "single=false")  # no queue base
        for event in ("push", "schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                self.assertEqual(self.decide(event, "docs/a.md"), "single=false")
        # Control: the same harmless diff with a base builds once on a queue run.
        self.assertEqual(self.decide("merge_group", "docs/a.md", queue_base="HEAD^"), "single=true")


if __name__ == "__main__":
    unittest.main()
