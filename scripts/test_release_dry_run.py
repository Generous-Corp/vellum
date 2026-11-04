#!/usr/bin/env python3
"""A release dry run can never publish, never leave a tag or draft behind, and never touch a v* tag."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_dry_run as dry  # noqa: E402
import verify_release_tag as vrt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
REPOSITORY = "Generous-Corp/vellum"
DRY_TAG = "dry-run/v0.1.10/41-1"
TAG_OBJECT = "c" * 40


def step_script(workflow: str, title: str) -> str:
    """The `run: |` text of the named step, dedented, read without a YAML library."""
    lines = (WORKFLOWS / workflow).read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == f"- name: {title}")
    run = next(index for index in range(start, len(lines)) if lines[index].strip() == "run: |")
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < 10:
            break
        body.append(line[10:] if line.strip() else "")
    return "\n".join(body) + "\n"


def job_text(workflow: str, job: str) -> str:
    text = (WORKFLOWS / workflow).read_text()
    start = text.index(f"\n  {job}:\n")
    following = [text.find(f"\n  {name}:\n", start + 1) for name in
                 ("validate-sdk-artifacts", "finalize-sdk-release", "clean-dry-run", "select-release-commit",
                  "sign-release-tag", "sign-dry-run-tag")]
    ends = [index for index in following if index > start]
    return text[start:min(ends) if ends else None]


# A stateful stand-in for `gh`: releases, assets and tags live in a JSON file,
# and every call is logged. It answers exactly the calls the workflow scripts
# make and fails on anything else.
FAKE_GH = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
from pathlib import Path
state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
repo = os.environ["GITHUB_REPOSITORY"]
def save():
    state_path.write_text(json.dumps(state))
def option(name):
    return args[args.index(name) + 1] if name in args else None
def release_by_id(rid):
    return next(r for r in state["releases"] if r["id"] == rid)
if args[:2] == ["release", "create"]:
    tag = args[2]
    if tag not in state["tags"]:
        sys.exit("tag missing")
    state["next"] += 1
    state["releases"].append({"id": state["next"], "tag_name": tag, "name": option("--title"),
                              "body": Path(option("--notes-file")).read_text(), "draft": "--draft" in args,
                              "immutable": "--draft" not in args, "prerelease": False,
                              "published_at": None if "--draft" in args else "2026-11-04T00:00:00Z",
                              "assets": []})
    save(); sys.exit(0)
if args[:2] == ["release", "upload"]:
    tag, path = args[2], Path(args[3])
    release = next(r for r in state["releases"] if r["tag_name"] == tag)
    data = path.read_bytes()
    state["next"] += 1
    release["assets"].append({"id": state["next"], "name": path.name, "size": len(data), "state": "uploaded",
                              "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
    save(); sys.exit(0)
if args[:1] == ["release"]:
    sys.exit("fake gh: a release command a dry run must never reach: " + " ".join(args))
if args[:1] != ["api"]:
    sys.exit("fake gh: unexpected " + " ".join(args))
method = option("--method") or "GET"
path = next(a for a in args[1:] if a.startswith("repos/"))
jq = option("--jq")
if path == f"repos/{repo}/immutable-releases":
    print("true" if jq == ".enabled" else json.dumps({"enabled": True, "enforced_by_owner": False})); sys.exit(0)
if path.startswith(f"repos/{repo}/rulesets"):
    print(json.dumps([[]])); sys.exit(0)
if path.startswith(f"repos/{repo}/git/ref/tags/"):
    tag = path[len(f"repos/{repo}/git/ref/tags/"):]
    if tag not in state["tags"]:
        sys.exit(1)
    print(f"tag\t{state['tags'][tag]}" if jq else json.dumps({"object": {"type": "tag", "sha": state["tags"][tag]}}))
    sys.exit(0)
if method == "DELETE" and path.startswith(f"repos/{repo}/git/refs/tags/"):
    state["tags"].pop(path[len(f"repos/{repo}/git/refs/tags/"):]); save(); sys.exit(0)
if path.startswith(f"repos/{repo}/releases?"):
    print(json.dumps([state["releases"]])); sys.exit(0)
if method == "DELETE" and path.startswith(f"repos/{repo}/releases/assets/"):
    aid = int(path.rsplit("/", 1)[1])
    for release in state["releases"]:
        release["assets"] = [a for a in release["assets"] if a["id"] != aid]
    save(); sys.exit(0)
if path.startswith(f"repos/{repo}/releases/") and path.rsplit("/", 1)[1].isdigit():
    rid = int(path.rsplit("/", 1)[1])
    if method == "DELETE":
        state["releases"] = [r for r in state["releases"] if r["id"] != rid]; save(); sys.exit(0)
    release = release_by_id(rid)
    if method == "PATCH":
        if "draft=false" in args:
            state["published"].append(rid)
            release.update(draft=False, immutable=True, published_at="2026-11-04T00:00:00Z")
        save()
    print(json.dumps(release)); sys.exit(0)
sys.exit("fake gh: unexpected api " + " ".join(args))
'''


MKTEMP = r'''#!/usr/bin/env python3
import os, re, sys, tempfile
args = sys.argv[1:]
directory = "-d" in args
template = next((a for a in args if a != "-d"), None)
if template is None:
    path = tempfile.mkdtemp() if directory else tempfile.mkstemp()[1]
else:
    match = re.fullmatch(r"(.*?)(X{3,})(.*)", template)
    folder, name = os.path.split(match[1])
    if directory:
        path = tempfile.mkdtemp(prefix=name, suffix=match[3], dir=folder or None)
    else:
        handle, path = tempfile.mkstemp(prefix=name, suffix=match[3], dir=folder or None)
        os.close(handle)
print(path)
'''


class FakeGitHub:
    def __init__(self, case: unittest.TestCase, tags: dict[str, str], releases: list | None = None) -> None:
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "bin").mkdir()
        gh = self.root / "bin/gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        # GNU mktemp (the runners') accepts a suffix after the X's; BSD's does not.
        shim = self.root / "bin/mktemp"
        shim.write_text(MKTEMP)
        shim.chmod(0o755)
        # The attestation wait after a publication sleeps; the stand-in never needs it.
        nap = self.root / "bin/sleep"
        nap.write_text("#!/bin/sh\nexit 0\n")
        nap.chmod(0o755)
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({"tags": tags, "releases": releases or [], "published": [], "next": 100}))
        self.log = self.root / "calls.log"
        self.log.write_text("")

    def env(self, **extra: str) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state),
                "FAKE_GH_LOG": str(self.log), "GITHUB_REPOSITORY": REPOSITORY, "GH_TOKEN": "fake",
                "GITHUB_STEP_SUMMARY": str(self.root / "summary.md"), **extra}

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def current(self) -> dict:
        return json.loads(self.state.read_text())


class PublishStep(unittest.TestCase):
    """sdk-release's publish step, run for real against a stand-in GitHub."""

    def run_publish(self, dry_run: str | None, tag: str = DRY_TAG, script: str | None = None):
        github = FakeGitHub(self, {tag: TAG_OBJECT})
        temp = github.root / "runner-temp"
        for directory in ("gpu", "trusted"):
            (temp / directory).mkdir(parents=True)
        archive = "vellum-sdk-0.1.10-darwin-arm64.tar.gz"
        files = {f"gpu/{archive}": b"archive", "trusted/install.sh": b"#!/bin/sh\n",
                 "trusted/install_core.py": b"print(1)\n", "trusted/SHA256SUMS": b"sums\n",
                 "trusted/trusted-sdk-verification.json": b"{}\n",
                 "trusted/trusted-sdk-installed-validation.json": b"{}\n", "trusted/web-payload.json": b"{}\n",
                 "release-trust.json": b"{}\n", "immutable-release-preflight.json": b"{}\n"}
        for relative, data in files.items():
            (temp / relative).write_bytes(data)
        env = github.env(RUNNER_TEMP=str(temp), RELEASE_TAG=tag, RELEASE_ARCHIVE=archive,
                         RELEASE_NAME="Vellum 0.1.10 experimental SDK (release dry run)",
                         TAG_OBJECT_SHA=TAG_OBJECT)
        if dry_run is not None:
            env["DRY_RUN"] = dry_run
        else:
            env.pop("DRY_RUN", None)
        text = script if script is not None else step_script("sdk-release.yml",
                                                              "Publish only through live no-bypass controls")
        done = subprocess.run(["bash", "-eo", "pipefail", "-c", text], cwd=ROOT, env=env, capture_output=True,
                              text=True, check=False)
        return done, github

    def test_a_dry_run_builds_and_verifies_the_draft_and_never_publishes(self) -> None:
        done, github = self.run_publish("true")
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        calls = github.calls()
        self.assertIn("--draft", next(call for call in calls if call[:2] == ["release", "create"]))
        uploaded = sorted(asset["name"] for asset in github.current()["releases"][0]["assets"])
        self.assertEqual(uploaded, sorted([
            "vellum-sdk-0.1.10-darwin-arm64.tar.gz", "install.sh", "install_core.py", "SHA256SUMS",
            "sdk-verification.json", "sdk-installed-validation.json", "web-payload.json", "release-trust.json",
            "immutable-release-preflight.json", "release-tag-protection.json", "release-assets-verification.json"]))
        self.assertEqual(github.current()["published"], [])
        self.assertFalse(any(call[:2] == ["release", "verify"] for call in calls))
        self.assertFalse(any("draft=false" in call for call in calls))
        self.assertTrue(github.current()["releases"][0]["draft"])

    def test_an_unset_or_unexpected_flag_never_publishes(self) -> None:
        for value in (None, "", "yes", "TRUE"):
            with self.subTest(value=value):
                _, github = self.run_publish(value)
                self.assertEqual(github.current()["published"], [])

    def test_removing_the_guard_would_publish(self) -> None:
        # The control: with the guard removed, the same harness records the
        # publication, so its silence above means the dry run did not publish.
        text = step_script("sdk-release.yml", "Publish only through live no-bypass controls")
        guard = text[text.index('  if [ "$DRY_RUN" != false ]; then'):text.index(
            "  # GitHub exposes no revision/ETag precondition")]
        _, github = self.run_publish("true", script=text.replace(guard, ""))
        self.assertEqual(github.current()["published"], [101])

    def test_a_published_release_on_a_dry_run_tag_is_refused(self) -> None:
        github = FakeGitHub(self, {DRY_TAG: TAG_OBJECT})
        text = step_script("sdk-release.yml", "Publish only through live no-bypass controls")
        self.assertIn('if [ "$DRY_RUN" = true ] && [ "$draft" != true ]; then', text)
        self.assertLess(text.index('if [ "$DRY_RUN" = true ] && [ "$draft" != true ]; then'),
                        text.index('if [ "$draft" = true ]; then'))
        del github

    def test_the_dry_run_flag_comes_only_from_the_validated_tag(self) -> None:
        load = step_script("sdk-release.yml", "Load validated release coordinates")
        self.assertIn('"DRY_RUN": "true" if dry_run else "false"', load)
        derive = step_script("sdk-release.yml", "Derive the release name and archive from the tag")
        self.assertIn('dry-run/*) test "$DRY_RUN" = true', derive)
        self.assertIn('*) test "$DRY_RUN" = false', derive)


class Cleanup(unittest.TestCase):
    """clean-dry-run deletes the dry run's draft and tag, and nothing else."""

    def run_cleanup(self, tag: str, tags: dict[str, str], releases: list):
        github = FakeGitHub(self, tags, releases)
        temp = github.root / "runner-temp"
        temp.mkdir()
        done = subprocess.run(["bash", "-eo", "pipefail", "-c",
                               step_script("sdk-release.yml", "Delete the dry run's draft and tag")],
                              cwd=ROOT, env=github.env(RUNNER_TEMP=str(temp), DRY_RUN_TAG=tag),
                              capture_output=True, text=True, check=False)
        return done, github

    @staticmethod
    def release(rid: int, tag: str, draft: bool) -> dict:
        return {"id": rid, "tag_name": tag, "draft": draft, "assets": []}

    def test_the_draft_and_the_tag_are_deleted(self) -> None:
        done, github = self.run_cleanup(DRY_TAG, {DRY_TAG: TAG_OBJECT, "v0.1.10": "d" * 40},
                                        [self.release(7, DRY_TAG, True), self.release(8, "v0.1.10", False)])
        self.assertEqual(done.returncode, 0, done.stderr)
        state = github.current()
        self.assertEqual([r["id"] for r in state["releases"]], [8])
        self.assertEqual(set(state["tags"]), {"v0.1.10"})

    def test_a_dry_run_that_made_nothing_is_cleaned_without_error(self) -> None:
        done, github = self.run_cleanup(DRY_TAG, {"v0.1.10": "d" * 40}, [self.release(8, "v0.1.10", False)])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(github.current()["releases"]), 1)

    def test_a_release_tag_is_never_touched(self) -> None:
        for tag in ("v0.1.10", "dry-run/v0.1.10", "dry-run/../v0.1.10/1-1", "authority/x", "dry-run/v0.1.10/0-1"):
            with self.subTest(tag=tag):
                done, github = self.run_cleanup(tag, {"v0.1.10": "d" * 40, tag: TAG_OBJECT},
                                                [self.release(8, "v0.1.10", False), self.release(9, tag, True)])
                self.assertNotEqual(done.returncode, 0)
                self.assertFalse(any(option == "DELETE" for call in github.calls() for option in call))
                self.assertIn("v0.1.10", github.current()["tags"])

    def test_a_published_release_on_a_dry_run_tag_is_not_deleted(self) -> None:
        done, github = self.run_cleanup(DRY_TAG, {DRY_TAG: TAG_OBJECT}, [self.release(7, DRY_TAG, False)])
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(len(github.current()["releases"]), 1)

    def test_cleanup_runs_whatever_happened_before(self) -> None:
        job = job_text("sdk-release.yml", "clean-dry-run")
        self.assertIn("needs: [validate-sdk-artifacts, finalize-sdk-release]", job)
        self.assertIn("always() &&", job)
        self.assertIn("startsWith(github.event.workflow_run.head_branch, 'dry-run/v')", job)
        self.assertNotIn("python", job)


class Names(unittest.TestCase):
    def test_only_well_formed_dry_run_names_are_dry_runs(self) -> None:
        self.assertEqual(dry.identity("v0.1.10"), ("release", "0.1.10"))
        self.assertEqual(dry.identity("dry-run/v0.1.10/41-1"), ("dry-run", "0.1.10"))
        for name in ("dry-run/v0.1.10", "dry-run/v0.1.10/0-1", "dry-run/v0.1.10/41-0", "dry-run/v0.1/41-1",
                     "dry-run/v0.1.10/41-1/x", "v0.1.10/41-1", "dry-run/0.1.10/41-1", "authority/x", "v01.1.10"):
            with self.subTest(name=name), self.assertRaises(dry.DryRunError):
                dry.identity(name)
        self.assertEqual(dry.tag_name("0.1.11", "7", "2"), "dry-run/v0.1.11/7-2")

    def test_the_tag_verifier_reads_dry_runs_against_the_release_key_only(self) -> None:
        self.assertEqual(vrt.required_signers_file(DRY_TAG), "release-signers.allowed_signers")
        self.assertEqual(vrt.required_signers_file("v0.1.10"), "release-signers.allowed_signers")
        self.assertEqual(vrt.required_signers_file("authority/x"), "authority-signers.allowed_signers")
        for name in ("dry-run/v0.1.10", "dry-run/anything", "dry-run/v0.1.10/41-1 "):
            with self.subTest(name=name), self.assertRaises(vrt.ReleaseTagVerificationError):
                vrt.required_signers_file(name)


class Target(unittest.TestCase):
    def test_the_newest_reconciled_commit_is_tagged(self) -> None:
        import test_release_request as trr  # noqa: PLC0415
        history = trr.History(self)
        reconciled = history.land(history.reconcile(history.change()))
        later = history.change("three\n")
        self.assertEqual(dry.reconciled_target(history.root, later), reconciled)
        again = history.land(history.reconcile(later))
        self.assertEqual(dry.reconciled_target(history.root, again), again)


class Wiring(unittest.TestCase):
    def test_the_dry_run_tag_is_signed_only_by_hand_on_main_and_never_frozen(self) -> None:
        text = (WORKFLOWS / "release-tag.yml").read_text()
        self.assertIn("  workflow_dispatch:\n", text)
        select = job_text("release-tag.yml", "select-release-commit")
        self.assertIn("if: github.event_name == 'push'", select)
        job = job_text("release-tag.yml", "sign-dry-run-tag")
        self.assertIn("if: github.event_name == 'workflow_dispatch'", job)
        self.assertIn("environment: release", job)
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', job)
        self.assertNotIn("rulesets", job)
        self.assertNotIn("freeze", job)
        push = step_script("release-tag.yml", "Push the dry-run tag")
        self.assertIn('case "$TAG" in dry-run/v*) ;; *)', push)
        self.assertIn('"refs/tags/$TAG:refs/tags/$TAG"', push)
        self.assertNotIn("python", push)
        sign = step_script("release-tag.yml", "Sign the dry-run tag")
        self.assertIn('case "$TAG" in dry-run/v*) ;; *) exit 1 ;; esac', sign)

    def test_the_tag_pipeline_accepts_the_dry_run_namespace(self) -> None:
        for workflow in ("gpu-macos.yml", "provenance.yml"):
            with self.subTest(workflow=workflow):
                text = (WORKFLOWS / workflow).read_text()
                self.assertIn("tags: ['v[0-9]*', 'authority/**', 'dry-run/**']", text)
        gpu = (WORKFLOWS / "gpu-macos.yml").read_text()
        self.assertEqual(gpu.count("if: startsWith(github.ref, 'refs/tags/v') || "
                                   "startsWith(github.ref, 'refs/tags/dry-run/')"), 2)
        provenance = (WORKFLOWS / "provenance.yml").read_text()
        self.assertIn('release_readiness+=(--release-tag "${BASH_REMATCH[1]}")', provenance)


if __name__ == "__main__":
    unittest.main()
