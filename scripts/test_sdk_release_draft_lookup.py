#!/usr/bin/env python3
"""The SDK finalizer finds its draft release through the releases list.

GitHub's by-tag release endpoint returns 404 for a draft. These tests run the
finalizer's own release-selection shell against a fake `gh` that behaves that
way, so a lookup that depends on the by-tag endpoint fails here.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/sdk-release.yml"
TAG = "v9.9.9"
NAME = "Vellum 9.9.9 experimental SDK"
NOTES = "notes\n"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
log = open(os.environ["FAKE_GH_LOG"], "a")
log.write(" ".join(args) + "\n")
if args[:2] == ["release", "create"]:
    state["releases"].append({
        "id": 100 + len(state["releases"]), "tag_name": args[2],
        "name": args[args.index("--title") + 1],
        "body": open(args[args.index("--notes-file") + 1]).read(),
        "draft": True, "immutable": False, "prerelease": False,
        "published_at": None, "assets": [],
    })
    json.dump(state, open(state_path, "w"))
    print("https://github.com/o/r/releases/tag/untagged-0000")
    sys.exit(0)
if args and args[0] == "api":
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if "/releases/tags/" in path:
        tag = path.rsplit("/", 1)[1]
        rows = [r for r in state["releases"] if r["tag_name"] == tag and not r["draft"]]
        if not rows:
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(rows[0]))
        sys.exit(0)
    if "/releases?" in path:
        print(json.dumps([state["releases"]]))
        sys.exit(0)
print(f"fake gh: unexpected call {args}", file=sys.stderr)
sys.exit(2)
'''


def selection_shell(workflow_text: str) -> str:
    """The finalizer's shell from the asset list through release selection."""
    lines = workflow_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "allowed=(")
    close = next(i for i in range(start, len(lines)) if lines[i].strip() == ")")
    end = next(
        i for i in range(close, len(lines))
        if lines[i].strip().startswith("release_id=")
    )
    return textwrap.dedent("\n".join(lines[close + 1:end])) + "\n"


class DraftLookupTest(unittest.TestCase):
    def run_selection(self, releases: list[dict], workflow_text: str | None = None):
        text = workflow_text if workflow_text is not None else WORKFLOW.read_text()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(FAKE_GH)
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            state = tmp_path / "state.json"
            state.write_text(json.dumps({"releases": releases}))
            log = tmp_path / "gh.log"
            log.write_text("")
            notes = tmp_path / "notes.md"
            notes.write_text(NOTES)
            script = (
                "set -euo pipefail\n"
                f'notes="{notes}"\n'
                "verify_release_guard() { :; }\n"
                "allowed=(\"SHA256SUMS=/dev/null\")\n"
                + selection_shell(text)
                + 'echo "@@selected@@"; cat "$RUNNER_TEMP/release.json"\n'
            )
            env = dict(
                os.environ,
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                FAKE_GH_STATE=str(state),
                FAKE_GH_LOG=str(log),
                RUNNER_TEMP=str(tmp_path),
                GITHUB_REPOSITORY="o/r",
                RELEASE_TAG=TAG,
                RELEASE_NAME=NAME,
            )
            result = subprocess.run(
                ["bash", "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True,
            )
            final_state = json.loads(state.read_text())
            result.stdout = result.stdout.split("@@selected@@\n")[-1]
            return result, final_state, log.read_text()

    def draft_row(self) -> dict:
        return {
            "id": 7, "tag_name": TAG, "name": NAME, "body": NOTES,
            "draft": True, "immutable": False, "prerelease": False,
            "published_at": None, "assets": [],
        }

    def test_creates_draft_and_selects_it(self) -> None:
        result, state, log = self.run_selection([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.count("release create"), 1)
        self.assertEqual(len(state["releases"]), 1)
        self.assertTrue(json.loads(result.stdout)["draft"])

    def test_resumes_existing_draft_without_creating_another(self) -> None:
        result, state, log = self.run_selection([self.draft_row()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("release create", log)
        self.assertEqual(len(state["releases"]), 1)
        self.assertEqual(json.loads(result.stdout)["id"], 7)

    def test_selection_error_other_than_missing_stops(self) -> None:
        wrong = self.draft_row() | {"name": "someone else's draft"}
        result, state, log = self.run_selection([wrong])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("release create", log)
        self.assertEqual(len(state["releases"]), 1)

    def test_no_by_tag_lookup_before_publication(self) -> None:
        text = WORKFLOW.read_text()
        publish = text.index("-F draft=false")
        self.assertNotIn(
            "releases/tags/$RELEASE_TAG", text[text.index("allowed=("):publish]
        )


UPLOAD_GH = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path))
if args[:2] == ["release", "upload"]:
    # Like the real gh: the asset is named after the uploaded file.
    state["assets"].append({"id": len(state["assets"]) + 1, "name": os.path.basename(args[3])})
    json.dump(state, open(state_path, "w"))
    sys.exit(0)
if args and args[0] == "api" and "/releases/" in args[-1] and "--method" not in args:
    print(json.dumps({"id": 7, "assets": state["assets"]}))
    sys.exit(0)
print(f"fake gh: unexpected call {args}", file=sys.stderr)
sys.exit(2)
'''


def replace_asset_shell(workflow_text: str) -> str:
    """The finalizer's own replace_asset function."""
    lines = workflow_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "replace_asset() {")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].strip() == "}" and len(lines[i]) - len(lines[i].lstrip()) == indent)
    return textwrap.dedent("\n".join(lines[start:end + 1])) + "\n"


class AssetNameTest(unittest.TestCase):
    def test_assets_publish_under_their_listed_names(self) -> None:
        text = WORKFLOW.read_text()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(UPLOAD_GH)
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            state = tmp_path / "state.json"
            state.write_text(json.dumps({"assets": []}))
            trusted = tmp_path / "trusted"
            trusted.mkdir()
            (trusted / "trusted-sdk-verification.json").write_text("{}\n")
            (trusted / "SHA256SUMS").write_text("sums\n")
            script = (
                "set -euo pipefail\n"
                "verify_release_guard() { :; }\n"
                "release_id=7\n"
                + replace_asset_shell(text)
                + f'replace_asset sdk-verification.json "{trusted}/trusted-sdk-verification.json"\n'
                + f'replace_asset SHA256SUMS "{trusted}/SHA256SUMS"\n'
            )
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                       FAKE_GH_STATE=str(state), RUNNER_TEMP=str(tmp_path),
                       GITHUB_REPOSITORY="o/r", RELEASE_TAG=TAG)
            result = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            names = [a["name"] for a in json.loads(state.read_text())["assets"]]
            self.assertEqual(names, ["sdk-verification.json", "SHA256SUMS"])


VERIFY_GH = r'''#!/usr/bin/env python3
import os, sys
counter = os.environ["FAKE_GH_COUNTER"]
n = int(open(counter).read() or 0) + 1
open(counter, "w").write(str(n))
if sys.argv[1:3] == ["release", "verify"]:
    ready = int(os.environ["FAKE_ATTESTATION_AFTER"])
    if ready and n >= ready:
        sys.exit(0)
    print("no attestations for tag", file=sys.stderr)
    sys.exit(1)
sys.exit(2)
'''


def attestation_wait_shell(workflow_text: str) -> str:
    """The finalizer's wait for the release attestation plus its strict verify."""
    lines = workflow_text.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip().startswith("for attempt in $(seq 1 12); do"))
    end = next(i for i in range(start, len(lines))
               if lines[i].strip() == 'gh release verify "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY"')
    return textwrap.dedent("\n".join(lines[start:end + 1])) + "\n"


class AttestationWaitTest(unittest.TestCase):
    def run_wait(self, ready_after: int):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(VERIFY_GH)
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            counter = tmp_path / "count"
            counter.write_text("0")
            script = ("set -euo pipefail\nsleep() { :; }\n"
                      + attestation_wait_shell(WORKFLOW.read_text()))
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                       FAKE_GH_COUNTER=str(counter), FAKE_ATTESTATION_AFTER=str(ready_after),
                       GITHUB_REPOSITORY="o/r", RELEASE_TAG=TAG)
            result = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            return result, int(counter.read_text())

    def test_waits_for_an_attestation_that_appears_after_publication(self) -> None:
        result, calls = self.run_wait(ready_after=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 4)  # three polls, then the strict verify

    def test_missing_attestation_still_fails_after_the_wait(self) -> None:
        result, calls = self.run_wait(ready_after=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, 13)  # twelve bounded polls, then the strict verify



import sys

sys.path.insert(0, str(ROOT / "scripts"))
import extract_workflow_step as ews  # noqa: E402

REHEARSAL = ROOT / ".github/workflows/release-publish-rehearsal.yml"
PUBLISH_STEP = "Publish only through live no-bypass controls"

LOGGING_GH = r"""#!/usr/bin/env python3
import os, sys
open(os.environ["FAKE_GH_LOG"], "a").write(" ".join(sys.argv[1:]) + "\n")
sys.exit(2)
"""


class StepExtractionTest(unittest.TestCase):
    def test_matches_a_yaml_parser_on_every_finalizer_step(self) -> None:
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed; the shape checks below still run")
        text = WORKFLOW.read_text()
        steps = yaml.safe_load(text)["jobs"]["finalize-sdk-release"]["steps"]
        compared = 0
        for step in steps:
            run = step.get("run")
            if not isinstance(run, str) or "${{" in run:
                continue
            with self.subTest(step=step["name"]):
                self.assertEqual(ews.step_script(text, "finalize-sdk-release", step["name"]), run)
                compared += 1
        self.assertGreaterEqual(compared, 5)

    def test_publish_step_is_the_whole_publish_path(self) -> None:
        script = ews.step_script(WORKFLOW.read_text(), "finalize-sdk-release", PUBLISH_STEP)
        self.assertTrue(script.startswith("set -euo pipefail\n"))
        for needle in ("gh release create", "-F draft=false", "for attempt in $(seq 1 12); do",
                       "gh release verify-asset", "--require-published --require-immutable"):
            self.assertIn(needle, script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    def test_refuses_what_it_cannot_reproduce(self) -> None:
        text = WORKFLOW.read_text()
        with self.assertRaises(ews.StepError):
            ews.step_script(text, "finalize-sdk-release", "No such step")
        with self.assertRaises(ews.StepError):
            ews.step_script(text, "no-such-job", PUBLISH_STEP)
        with self.assertRaises(ews.StepError):  # its run block holds an expression
            ews.step_script(text, "validate-sdk-artifacts", "Download the exact source-run GPU artifact")
        twice = text.replace(f"- name: {PUBLISH_STEP}\n", f"- name: {PUBLISH_STEP}\n        run: |\n          :\n"
                             f"      - name: {PUBLISH_STEP}\n", 1)
        self.assertNotEqual(twice, text)
        with self.assertRaises(ews.StepError):
            ews.step_script(twice, "finalize-sdk-release", PUBLISH_STEP)


class PublishModeGateTest(unittest.TestCase):
    """The publish step refuses a mode that does not match its tag before any API call."""

    def run_step(self, dry_run: str, tag: str):
        script = ews.step_script(WORKFLOW.read_text(), "finalize-sdk-release", PUBLISH_STEP)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(LOGGING_GH)
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            log = tmp_path / "gh.log"
            log.write_text("")
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_GH_LOG=str(log),
                       RUNNER_TEMP=str(tmp_path), GITHUB_REPOSITORY="o/r", GITHUB_STEP_SUMMARY=str(tmp_path / "s"),
                       RELEASE_TAG=tag, RELEASE_NAME=NAME, RELEASE_ARCHIVE="a.tar.gz",
                       TAG_OBJECT_SHA="0" * 40, DRY_RUN=dry_run)
            result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            return result, log.read_text()

    def test_mismatched_modes_stop_before_any_api_call(self) -> None:
        for dry_run, tag in (("bogus", TAG), ("", TAG), ("publish-rehearsal", TAG),
                             ("false", "publish-rehearsal/1-1"), ("false", "dry-run/v9.9.9/1-1"),
                             ("true", TAG), ("publish-rehearsal", "dry-run/v9.9.9/1-1")):
            with self.subTest(dry_run=dry_run, tag=tag):
                result, log = self.run_step(dry_run, tag)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(log, "")

    def test_matching_modes_reach_the_live_guard(self) -> None:
        # Control: each accepted pairing gets past the gate to its first API read.
        for dry_run, tag in (("false", TAG), ("true", "dry-run/v9.9.9/1-1"),
                             ("true", "publish-rehearsal/1-1"), ("publish-rehearsal", "publish-rehearsal/1-1")):
            with self.subTest(dry_run=dry_run, tag=tag):
                _, log = self.run_step(dry_run, tag)
                self.assertIn("immutable-releases", log)

    def test_only_a_dry_run_stops_before_publication(self) -> None:
        script = ews.step_script(WORKFLOW.read_text(), "finalize-sdk-release", PUBLISH_STEP)
        before = script[:script.index("-F draft=false")]
        stop = before.rindex("exit 0")
        self.assertIn('if [ "$DRY_RUN" = true ]; then', before[before.rindex("if [", 0, stop) - 2:stop])

    def test_the_finalizer_itself_never_selects_the_rehearsal_mode(self) -> None:
        text = WORKFLOW.read_text()
        self.assertIn('"DRY_RUN": "true" if dry_run else "false",', text)
        self.assertNotIn("DRY_RUN: publish-rehearsal", text)
        self.assertNotIn("DRY_RUN=publish-rehearsal", text)


class PublishRehearsalWiringTest(unittest.TestCase):
    def test_runs_the_finalizer_step_twice_then_cleans_up(self) -> None:
        text = REHEARSAL.read_text()
        triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(triggers.strip(), "workflow_dispatch:")
        self.assertEqual(text.count("if: github.ref == 'refs/heads/main'"), 1)
        self.assertIn("if: always() && github.ref == 'refs/heads/main'", text)
        self.assertIn("--workflow .github/workflows/sdk-release.yml", text)
        self.assertIn("--job finalize-sdk-release", text)
        self.assertIn(f'--step "{PUBLISH_STEP}"', text)
        pass1 = text.index('DRY_RUN: "true"')
        stale = text.index("- name: Leave a stale asset on the draft")
        pass2 = text.index("DRY_RUN: publish-rehearsal")
        clean = text.index("  clean-rehearsal:")
        self.assertLess(pass1, stale)
        self.assertLess(stale, pass2)
        self.assertLess(pass2, clean)
        self.assertEqual(text.count('bash --noprofile --norc -eo pipefail "$RUNNER_TEMP/publish-step.sh"'), 2)
        self.assertEqual(text.count("RELEASE_TAG: publish-rehearsal/${{ github.run_id }}-${{ github.run_attempt }}"), 2)
        self.assertNotIn("refs/tags/v", text)

    def test_no_other_workflow_runs_on_a_rehearsal_tag(self) -> None:
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            if path == REHEARSAL:
                continue
            with self.subTest(workflow=path.name):
                for line in path.read_text().splitlines():
                    if line.strip().startswith("tags:"):
                        self.assertNotIn("publish-rehearsal", line)
                        self.assertNotIn("'**'", line)


if __name__ == "__main__":
    unittest.main()
