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


if __name__ == "__main__":
    unittest.main()
