#!/usr/bin/env python3
"""framework_trusted_verify.py and its pull_request_target workflow."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORKFLOW = ROOT / ".github/workflows/framework-trusted-verify.yml"
spec = importlib.util.spec_from_file_location("framework_trusted_verify", HERE / "framework_trusted_verify.py")
assert spec and spec.loader
trusted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trusted)
verifier = trusted.load_verifier()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


class Repository:
    """A Vellum-shaped repository: a base with a mirror export, and a pull request on top."""

    MIRROR = {
        "mirror/core/a/include/pulp/a/a.hpp": b"#pragma once\nnamespace pulp::a {\nint f();\n}\n",
        "mirror/core/a/src/a.cpp": b"#include <pulp/a/a.hpp>\n#if PULP_HAS_A\n#endif\n"
                                   b"namespace pulp::a {\nint f() { return 1; }\n}\n",
    }

    def __init__(self, root: Path) -> None:
        self.root = root
        git(root, "init", "-q", "-b", "main")
        for relative, data in self.MIRROR.items():
            self.write(relative, data)
        git(root, "add", "mirror")
        mirror_tree = git(root, "write-tree", "--prefix=mirror/")
        receipt = {"pulp_repository": "Generous-Corp/pulp", "pulp_sha": "a" * 40, "mirror_tree": mirror_tree,
                   "files": [{"pulp_path": p.removeprefix("mirror/"), "blob": verifier.git_blob_sha(d)}
                             for p, d in self.MIRROR.items()]}
        self.write("provenance/mirror/receipts/r.json", json.dumps(receipt).encode())
        self.write("provenance/mirror/receipts/index.json",
                   json.dumps({"receipts": ["provenance/mirror/receipts/r.json"]}).encode())
        self.write("docs/readme.md", b"Vellum\n")
        self.write("tools/mirror/rename_literal_exclusions.json", b"[]\n")
        # Native Vellum, so the collision census has a native side to read.
        self.write("runtime/include/vellum/runtime/kernel.hpp",
                   b"#pragma once\nnamespace vellum::runtime {\nclass Kernel;\n}\n")
        self.base = commit_all(root, "base")
        git(root, "checkout", "-q", "-b", "change")

    def write(self, relative: str, data: bytes) -> None:
        (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
        (self.root / relative).write_bytes(data)

    def introduce_transfer(self) -> None:
        """The data change, made the way the landed rename tool makes it."""
        with tempfile.TemporaryDirectory() as scratch:
            derived = verifier.derive_framework_transfer(self.root, self.base, Path(scratch))
            shutil.copytree(derived["framework"], self.root / "framework")
        git(self.root, "rm", "-q", "-r", "mirror")
        git(self.root, "add", "framework")
        tree = git(self.root, "write-tree", "--prefix=framework/")
        record = dict(derived["record"], framework_tree=tree)
        self.write(verifier.FRAMEWORK_TRANSFER_RECORD, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
        counts, _ = verifier.framework_name_debt(self.root)
        debt = {"schema": "vellum.framework-name-debt.v1", "rules": {r: f for r, f in counts.items() if f}}
        self.write(verifier.FRAMEWORK_NAME_DEBT, json.dumps(debt).encode())

    def judge(self) -> tuple[int, dict]:
        head = commit_all(self.root, "the pull request")
        with tempfile.TemporaryDirectory() as out:
            output = Path(out) / "verdict.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = trusted.main(["--repo", str(self.root), "--head", head, "--base", self.base,
                                     "--dest-parent", out, "--output", str(output)])
            return code, json.loads(output.read_text()) if output.exists() else {}


class FrameworkTrustedVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VELLUM_GIT_BASE", None)
        self.repo = Repository(Path(self.temporary.name))

    def test_a_clean_data_change_passes(self):
        self.repo.introduce_transfer()
        code, verdict = self.repo.judge()
        self.assertEqual((code, verdict["mode"], verdict["errors"]), (0, "introduction", []))

    def test_a_data_change_that_also_edits_a_rule_file_is_refused(self):
        for rule in ("tools/mirror/rename_mirror_namespace.py", ".github/workflows/framework-trusted-verify.yml",
                     ".github/workflows/provenance.yml", "tools/provenance/framework_trusted_verify.py"):
            with self.subTest(rule=rule):
                repo = Repository(Path(tempfile.mkdtemp(dir=self.temporary.name)))
                repo.introduce_transfer()
                repo.write(rule, b"# looser\n")
                code, verdict = repo.judge()
                self.assertEqual(code, 1)
                self.assertTrue(any("edits the rules that judge it" in e for e in verdict["errors"]), verdict)

    def test_editing_the_rule_list_itself_does_not_help(self):
        # The PR's own verify_extraction.py (with an empty rule list) is data
        # here: the trusted copy judges, and the edit is a rule-file change.
        self.repo.introduce_transfer()
        self.repo.write("tools/provenance/verify_extraction.py", b"FRAMEWORK_RULE_PATHS = ()\n")
        code, verdict = self.repo.judge()
        self.assertEqual(code, 1)
        self.assertIn("tools/provenance/verify_extraction.py", verdict["rule_files"])

    def test_a_data_change_whose_tree_is_not_the_tool_output_is_refused(self):
        self.repo.introduce_transfer()
        target = self.repo.root / "framework/core/a/src/a.cpp"
        target.write_bytes(target.read_bytes() + b"// smuggled\n")
        code, verdict = self.repo.judge()
        self.assertEqual(code, 1)
        self.assertTrue(any("is not the landed rename tool's output" in e for e in verdict["errors"]), verdict)

    def fresh(self) -> Repository:
        return Repository(Path(tempfile.mkdtemp(dir=self.temporary.name)))

    def test_moving_a_rule_file_in_or_out_is_refused(self):
        for case, move in {"out": ("tools/mirror/rename_literal_exclusions.json", "docs/exclusions.json"),
                           "in": ("docs/readme.md", "tools/mirror/readme.md")}.items():
            with self.subTest(case=case):
                repo = self.fresh()
                repo.introduce_transfer()
                git(repo.root, "mv", *move)
                code, verdict = repo.judge()
                self.assertEqual(code, 1)
                self.assertTrue(any("edits the rules that judge it" in e for e in verdict["errors"]), verdict)

    def test_adding_beside_the_rules_is_refused(self):
        for added in ("tools/provenance/argparse.py", ".github/workflows/new.yml"):
            with self.subTest(added=added):
                repo = self.fresh()
                repo.introduce_transfer()
                repo.write(added, b"x = 1\n")
                code, verdict = repo.judge()
                self.assertEqual(code, 1)
                self.assertTrue(any("edits the rules that judge it" in e for e in verdict["errors"]), verdict)

    def test_transfer_data_without_framework_is_refused(self):
        for path in (verifier.FRAMEWORK_TRANSFER_RECORD, verifier.FRAMEWORK_NAME_DEBT,
                     verifier.FRAMEWORK_EXTERNAL_UPDATES):
            with self.subTest(path=path):
                repo = self.fresh()
                repo.write(path, b"{}\n")
                code, verdict = repo.judge()
                self.assertEqual(code, 1)
                self.assertTrue(any("exist without framework/" in e for e in verdict["errors"]), verdict)

    def test_framework_without_its_record_is_refused(self):
        self.repo.introduce_transfer()
        (self.repo.root / verifier.FRAMEWORK_TRANSFER_RECORD).unlink()
        code, verdict = self.repo.judge()
        self.assertEqual(code, 1)
        self.assertTrue(any("framework/ exists without" in e for e in verdict["errors"]), verdict)

    def test_mirror_beside_framework_is_refused(self):
        self.repo.introduce_transfer()
        git(self.repo.root, "checkout", self.repo.base, "--", "mirror")
        code, verdict = self.repo.judge()
        self.assertEqual(code, 1)
        self.assertTrue(any("mirror/ and framework/ both exist" in e for e in verdict["errors"]), verdict)

    def test_removing_a_landed_framework_is_refused(self):
        self.repo.introduce_transfer()
        landed = commit_all(self.repo.root, "land the transfer")
        self.repo.base = landed
        git(self.repo.root, "rm", "-q", "-r", "framework", *verifier.FRAMEWORK_DATA_FILES[:2])
        code, verdict = self.repo.judge()
        self.assertEqual(code, 1)
        self.assertTrue(any("cannot be removed" in e for e in verdict["errors"]), verdict)

    def test_the_two_step_attack_is_refused_at_each_step(self):
        # Step 1: a record (naming a receipt that does not exist yet) and an
        # inflated baseline, without framework/.
        record = {"schema": verifier.FRAMEWORK_TRANSFER_SCHEMA,
                  "export_receipt": "provenance/mirror/receipts/future.json", "records": []}
        self.repo.write(verifier.FRAMEWORK_TRANSFER_RECORD, json.dumps(record).encode())
        self.repo.write(verifier.FRAMEWORK_NAME_DEBT,
                        json.dumps({"schema": "x", "rules": {"pulp-package-or-target": {"framework/a.cpp": 999}}}).encode())
        code, verdict = self.repo.judge()
        self.assertEqual(code, 1)
        self.assertTrue(any("exist without framework/" in e for e in verdict["errors"]), verdict)
        # Forced through anyway: step 2 adds framework/ with an edit. framework/
        # is new against that base, so it is an introduction and judged in full.
        self.repo.base = git(self.repo.root, "rev-parse", "HEAD")
        (self.repo.root / verifier.FRAMEWORK_TRANSFER_RECORD).unlink()
        (self.repo.root / verifier.FRAMEWORK_NAME_DEBT).unlink()
        self.repo.introduce_transfer()
        target = self.repo.root / "framework/core/a/src/a.cpp"
        target.write_bytes(target.read_bytes() + b"// arbitrary\n")
        code, verdict = self.repo.judge()
        self.assertEqual((code, verdict["mode"]), (1, "introduction"))
        self.assertTrue(any("is not the landed rename tool's output" in e for e in verdict["errors"]), verdict)

    def test_an_unrelated_change_is_a_no_op(self):
        self.repo.write("docs/readme.md", b"Vellum, edited\n")
        code, verdict = self.repo.judge()
        self.assertEqual((code, verdict["mode"]), (0, "no-op"))
        with mock.patch.object(trusted, "load_verifier", wraps=trusted.load_verifier) as loaded:
            self.repo.write("docs/other.md", b"x\n")
            code, verdict = self.repo.judge()
        self.assertEqual(verdict["mode"], "no-op")

    def test_the_no_op_path_is_fast_and_reads_nothing_more(self):
        self.repo.write("docs/readme.md", b"Vellum, edited\n")
        verifier_module = trusted.load_verifier()
        def refuse(*args, **kwargs):
            raise AssertionError("an unrelated change must not run the rename or the regime")
        verifier_module.derive_framework_transfer = refuse
        verifier_module.verify_framework = refuse
        verifier_module.load_rename_tool = refuse
        started = time.monotonic()
        with mock.patch.object(trusted, "load_verifier", lambda: verifier_module):
            code, verdict = self.repo.judge()
        self.assertEqual((code, verdict["mode"], verdict["errors"]), (0, "no-op", []))
        self.assertLess(time.monotonic() - started, 10)

    def test_the_pull_request_tree_is_written_from_blobs_only(self):
        # A filter, attribute or hook the pull request declares never runs:
        # the tree is read from git objects, not checked out.
        marker = Path(self.temporary.name) / "smudge-ran"
        self.repo.write(".gitattributes", b"* filter=evil\n*.md export-ignore\n")
        self.repo.write("docs/readme.md", b"Vellum, edited\n")
        head = commit_all(self.repo.root, "attributes")
        git(self.repo.root, "config", "filter.evil.smudge", f"touch {marker} && cat")
        with tempfile.TemporaryDirectory() as out:
            dest = trusted.materialize(self.repo.root, head, Path(out))
            self.assertEqual((dest / "docs/readme.md").read_bytes(), b"Vellum, edited\n")
            self.assertEqual(oct(dest.stat().st_mode & 0o777), "0o700")
            self.assertEqual(git(dest, "rev-parse", "HEAD"), head)
            self.assertIn("docs/readme.md", git(dest, "ls-files").splitlines())
        self.assertFalse(marker.exists())

    def crafted_commit(self, entries: list[tuple[str, str, str]]) -> str:
        """A commit whose root tree lists exactly `entries` (mode, name, object), duplicates included."""
        raw = b"".join(f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha) for mode, name, sha in entries)
        tree = subprocess.run(["git", "-C", str(self.repo.root), "hash-object", "-t", "tree", "-w", "--literally",
                               "--stdin"], input=raw, check=True, capture_output=True).stdout.decode().strip()
        return git(self.repo.root, "commit-tree", tree, "-m", "crafted")

    def blob(self, data: bytes) -> str:
        return subprocess.run(["git", "-C", str(self.repo.root), "hash-object", "-w", "--stdin"], input=data,
                              check=True, capture_output=True).stdout.decode().strip()

    def assert_refused_and_untouched(self, head: str, outside: Path, before: bytes) -> None:
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(trusted.InputError):
                trusted.materialize(self.repo.root, head, Path(out))
        self.assertEqual(outside.read_bytes(), before)

    def test_a_duplicate_entry_cannot_write_through_a_symlink(self):
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_bytes(b"untouched\n")
        head = self.crafted_commit([("120000", "a", self.blob(str(outside).encode())),
                                    ("100644", "a", self.blob(b"overwritten\n"))])
        self.assert_refused_and_untouched(head, outside, b"untouched\n")

    def test_a_symlinked_parent_directory_is_refused(self):
        outside_dir = Path(self.temporary.name) / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "x").write_bytes(b"untouched\n")
        sub = subprocess.run(["git", "-C", str(self.repo.root), "mktree"], input=f"100644 blob {self.blob(b'evil')}\tx\n",
                             check=True, capture_output=True, text=True).stdout.strip()
        head = self.crafted_commit([("120000", "d", self.blob(str(outside_dir).encode())), ("40000", "d", sub)])
        self.assert_refused_and_untouched(head, outside_dir / "x", b"untouched\n")

    def test_a_symlink_into_the_trusted_checkout_is_refused(self):
        judge = Path(trusted.__file__)
        before = judge.read_bytes()
        head = self.crafted_commit([("120000", "link", self.blob(str(judge).encode())),
                                    ("100644", "link", self.blob(b"print('owned')\n"))])
        self.assert_refused_and_untouched(head, judge, before)
        lone = self.crafted_commit([("120000", "link", self.blob(str(judge).encode()))])
        self.assert_refused_and_untouched(lone, judge, before)

    def test_a_gitlink_is_refused(self):
        head = self.crafted_commit([("100644", "a.txt", self.blob(b"a\n")), ("160000", "sub", "1" * 40)])
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaisesRegex(trusted.InputError, "only regular files are judged"):
                trusted.materialize(self.repo.root, head, Path(out))

    def test_the_change_is_never_materialized_inside_the_trusted_checkout(self):
        with self.assertRaisesRegex(trusted.InputError, "inside, or around, the trusted checkout"):
            trusted.materialize(self.repo.root, self.repo.base, self.repo.root)

    def test_malformed_commit_ids_are_refused_before_anything_runs(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = trusted.main(["--repo", str(self.repo.root), "--head", "main; rm -rf /", "--base", self.repo.base,
                                 "--dest-parent", self.temporary.name])
        self.assertEqual(code, 2)


WORKFLOWS = ROOT / ".github/workflows"
# Each still runs a verifier without -I and is listed so the exception is
# visible and cannot grow: sdk-release.yml is a release-path file, and
# gpu-macos.yml's fetch step must stay identical to it but for --root
# (scripts/test_ci_runner_policy.py). Both go isolated together, in a
# release-path change.
UNISOLATED_EXCEPTIONS = {("sdk-release.yml", "python3 tools/mirror/fetch_view_audio_archive.py"),
                         ("gpu-macos.yml", "python3 tools/mirror/fetch_view_audio_archive.py")}
UNISOLATED = re.compile(r"\bpython3?\s+(?!-I\b)(?:-[A-Za-z]+\s+)*(tools/[\w./-]+\.py)")


def run_blocks(text: str) -> list[str]:
    return [body for _, body in re.findall(r"^( +)(?:- )?run: \|\n((?:\1  .*\n|\n)+)", text, re.MULTILINE)] + \
        re.findall(r"^ +(?:- )?run: ([^|\n][^\n]*)$", text, re.MULTILINE)


def unisolated_invocations(directory: Path) -> set[tuple[str, str]]:
    found = set()
    for path in sorted(directory.glob("*.yml")):
        for body in run_blocks(path.read_text(encoding="utf-8")):
            for match in UNISOLATED.finditer(body):
                found.add((path.name, match.group(0).split("\n")[0].strip()))
    return found


def pull_request_workflows_granting_statuses(directory: Path) -> list[str]:
    found = []
    for path in sorted(directory.glob("*.yml")):
        text = "\n" + path.read_text(encoding="utf-8")
        trigger = text.split("\non:", 1)[1].split("\n\n", 1)[0] if "\non:" in text else ""
        if re.search(r"^\s*(?:-\s*)?pull_request\s*:?\s*$|\[[^\]]*\bpull_request\b(?!_)", trigger, re.MULTILINE) \
                and re.search(r"^\s*statuses:\s*write\b", text, re.MULTILINE):
            found.append(path.name)
    return found


class WorkflowIsTrustedOnlyTests(unittest.TestCase):
    """The pull_request_target workflow runs nothing from the pull request and always reports."""

    FORBIDDEN_CONTEXT = re.compile(
        r"github\.event\.pull_request\.(title|body|head\.ref|head\.label|labels|user|head\.repo)"
        r"|github\.head_ref|github\.event\.merge_group\.head_ref|github\.sha\b")

    def setUp(self) -> None:
        # Read as text, so the check needs nothing beyond the standard library.
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.runs = run_blocks(self.text)

    def test_it_runs_on_the_base_definition_with_exactly_contents_read_and_statuses_write(self):
        trigger = self.text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(re.findall(r"^  (\w+):", trigger, re.MULTILINE), ["pull_request_target", "merge_group"])
        self.assertEqual(re.findall(r"^ *permissions:\n((?: +\w[\w-]*: \w+\n)+)", self.text, re.MULTILINE),
                         ["  contents: read\n  statuses: write\n", "      contents: read\n      statuses: write\n"])
        self.assertNotIn("secrets.", self.text)
        self.assertEqual(re.findall(r"\$\{\{ *github\.token *\}\}", self.text), ["${{ github.token }}"])
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", self.text)

    def test_the_check_reports_on_every_pull_request_and_queue_entry(self):
        # A required status that is never posted blocks a pull request
        # forever, so nothing may narrow when the job runs or skip it.
        trigger = self.text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
        blocks = dict(re.findall(r"^  (\w+):\n((?:    .*\n?)*)", trigger, re.MULTILINE))
        self.assertEqual(set(blocks), {"pull_request_target", "merge_group"})
        self.assertEqual(re.findall(r"^    ([\w-]+):", blocks["pull_request_target"], re.MULTILINE), ["types"])
        self.assertEqual(blocks["merge_group"].strip(), "branches: [main]")
        for block in blocks.values():
            for narrowing in ("paths", "paths-ignore", "branches-ignore", "tags"):
                self.assertNotIn(f"{narrowing}:", block)
        jobs = self.text.split("\njobs:\n", 1)[1]
        self.assertIsNone(re.search(r"^\s*(?:-\s+)?if:", jobs, re.MULTILINE))
        self.assertNotIn("continue-on-error", jobs)
        self.assertEqual(len(re.findall(r"^      - name:", jobs, re.MULTILINE)), 1)  # one step: nothing runs before the trap

    def test_the_job_is_not_named_after_the_status(self):
        self.assertEqual(re.findall(r"^jobs:\n  ([\w-]+):\n    name: ([\w-]+)$", self.text, re.MULTILINE),
                         [("framework-trusted-judge", "framework-trusted-judge")])
        self.assertNotRegex(self.text, r"name: framework-trusted-verify\b")

    def test_the_verdict_is_a_status_on_the_change_s_head(self):
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha || github.event.merge_group.head_sha }}",
                      self.text)
        run = self.runs[0]
        self.assertIn('"${GITHUB_API_URL}/repos/${GITHUB_REPOSITORY}/statuses/${HEAD_SHA}"', run)
        self.assertIn('"context":"framework-trusted-verify"', run)
        self.assertIn('"target_url":"%s"', run)
        self.assertIn('run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"', run)
        self.assertEqual(run.count("/statuses/"), 1)

    def test_the_status_is_never_left_pending_or_missing(self):
        run = self.runs[0]
        self.assertIn("trap on_exit EXIT", run)
        self.assertIn("trap 'exit 130' INT TERM", run)
        self.assertRegex(run, r'on_exit\(\) \{\n\s+if \[ "\$finished" != 1 \]; then\n\s+post_status failure ')
        lines = run.splitlines()
        index = {name: next(i for i, line in enumerate(lines) if needle in line)
                 for name, needle in (("trap", "trap on_exit EXIT"), ("pending", "post_status pending"),
                                      ("clone", " clone "), ("finished", "finished=1"))}
        self.assertLess(index["trap"], index["pending"])
        self.assertLess(index["pending"], index["clone"])
        self.assertRegex(run, r"0\) post_status success ")
        self.assertEqual(len(re.findall(r"post_status success", run)), 1)
        self.assertGreater(index["finished"], max(i for i, line in enumerate(lines) if "post_status" in line
                                                  and "local" not in line and "()" not in line))

    def test_the_status_token_never_reaches_git_or_the_judge(self):
        run = self.runs[0]
        lines = [line.strip() for line in run.splitlines()]
        moved = lines.index('status_token="$GITHUB_TOKEN"')
        self.assertEqual(lines[moved + 1], "unset GITHUB_TOKEN GH_TOKEN")
        # Nothing runs before the token leaves the environment.
        self.assertEqual([line for line in lines[:moved] if line and not line.startswith("#")], ["set -euo pipefail"])
        uses = [i for i, line in enumerate(lines) if "GITHUB_TOKEN" in line or "status_token" in line]
        self.assertEqual([lines[i] for i in uses if i not in (moved, moved + 1)],
                         ["printf 'Authorization: Bearer %s\\n' \"$status_token\" |",
                          "env -u GITHUB_TOKEN -u GH_TOKEN -u ACTIONS_RUNTIME_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_TOKEN \\"])
        self.assertIsNone(re.search(r"(?<![\w-])(export|declare -x)\s", run))
        # The token reaches curl on stdin, never in its argv.
        self.assertIn("--header @- \\", run)
        self.assertNotIn("Bearer ${status_token}\"", run)

    def test_no_expression_reaches_a_shell(self):
        self.assertEqual(len(self.runs), 1)  # control: the run block was read
        for body in self.runs:
            self.assertNotIn("${{", body)

    def test_no_pull_request_controlled_text_is_used_anywhere(self):
        self.assertIsNone(self.FORBIDDEN_CONTEXT.search(self.text), self.FORBIDDEN_CONTEXT.search(self.text))

    def test_every_command_is_trusted_code(self):
        self.assertIsNone(re.search(r"^\s*(?:-\s+)?uses:", self.text, re.MULTILINE))  # the base is cloned in the step
        runs = "\n".join(self.runs)
        for forbidden in ("pip ", "npm ", "make ", "cmake ", "source ", "bash ", " sh ", "./", "pr/", "lfs"):
            self.assertNotIn(forbidden, runs, forbidden)
        interpreters = re.findall(r"python3?\b[^\n]*", runs)
        self.assertEqual(interpreters, ["python3 -I tools/provenance/framework_trusted_verify.py \\"])
        # The judge runs with no token in its environment.
        self.assertIn("env -u GITHUB_TOKEN -u GH_TOKEN -u ACTIONS_RUNTIME_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_TOKEN \\\n"
                      "            python3 -I tools/provenance/framework_trusted_verify.py", runs)
        self.assertIn('--dest-parent "$RUNNER_TEMP"', runs)
        git_lines = [line.strip() for line in runs.splitlines() if '"${safe_git[@]}"' in line]
        self.assertEqual(len(git_lines), 5)
        self.assertIn("-c fetch.fsckObjects=true -c transfer.fsckObjects=true)", runs)
        self.assertIn("-c core.hooksPath=/dev/null", runs)
        self.assertIn('checkout --quiet --detach "$BASE_SHA"', runs)
        self.assertEqual(runs.count(" checkout "), 1)
        self.assertIn("clone --quiet --no-checkout", runs)

    def test_the_rule_list_covers_this_workflow_and_its_judge(self):
        for path in (".github/workflows/framework-trusted-verify.yml", ".github/workflows/provenance.yml",
                     ".github/workflows/new.yml", "tools/provenance/framework_trusted_verify.py",
                     "tools/provenance/verify_extraction.py", "tools/provenance/argparse.py", "tools/mirror/x.py"):
            self.assertTrue(path.startswith(verifier.FRAMEWORK_RULE_PATHS), path)


class NoLinksOnTheDefaultBranchTests(unittest.TestCase):
    def test_the_tree_holds_no_symlink_or_gitlink(self):
        # framework_trusted_verify.materialize() refuses any symlink (120000)
        # or gitlink (160000) anywhere in a pull request's tree, so one
        # landed here would fail every later pull request's required status.
        # Adding one is a deliberate decision: change the materializer first.
        listing = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "HEAD"], check=True,
                                 capture_output=True, text=True).stdout.splitlines()
        self.assertGreater(len(listing), 100)  # control: the tree was read
        links = [line for line in listing if line.split(" ", 1)[0] in ("120000", "160000")]
        self.assertEqual(links, [], "a symlink or gitlink would be refused by framework_trusted_verify.materialize()")


class WorkflowFleetLintTests(unittest.TestCase):
    """Rules over every workflow: isolated verifier runs, and no status-writing PR lane."""

    def test_every_tools_verifier_runs_isolated(self):
        found = unisolated_invocations(WORKFLOWS)
        self.assertEqual(found, UNISOLATED_EXCEPTIONS)

    def test_the_isolation_lint_catches_each_spelling(self):
        for line in ("python3 tools/provenance/verify_extraction.py --x", "python tools/mirror/a.py",
                     "python3 -u tools/provenance/observatory.py verify", "python3 tools/agent_instructions/verify.py"):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as temporary:
                Path(temporary, "w.yml").write_text(f"on:\n  push:\n\njobs:\n  a:\n    steps:\n"
                                                    f"      - run: |\n          {line}\n", encoding="utf-8")
                self.assertEqual(len(unisolated_invocations(Path(temporary))), 1)
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "w.yml").write_text("on:\n  push:\n\njobs:\n  a:\n    steps:\n"
                                                "      - run: python3 -I tools/provenance/a.py\n", encoding="utf-8")
            self.assertEqual(unisolated_invocations(Path(temporary)), set())

    def test_a_planted_standard_module_is_not_imported_under_the_ci_invocation(self):
        # -I drops the script's directory from sys.path, and scripts that need
        # their siblings append it after the standard library, so a planted
        # argparse.py or json.py beside them is never what they import.
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary) / "tools"
            shutil.copytree(ROOT / "tools/provenance", tools / "provenance",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "tools/mirror", tools / "mirror", ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("argparse", "json"):
                (tools / "provenance" / f"{name}.py").write_text("raise SystemExit('SHADOWED')\n", encoding="utf-8")
            scripts = ("verify_extraction.py", "framework_trusted_verify.py", "observatory.py",
                       "collect_release_evidence.py", "receiver.py")
            for script in scripts:
                with self.subTest(script=script):
                    isolated = subprocess.run(["python3", "-I", str(tools / "provenance" / script), "--help"],
                                              capture_output=True, text=True, cwd=temporary)
                    self.assertNotIn("SHADOWED", isolated.stdout + isolated.stderr)
                    self.assertEqual(isolated.returncode, 0, isolated.stderr[-400:])
                    # The directory a script adds comes after the standard library,
                    # so a module it imports later cannot be shadowed either.
                    probe = ("import runpy, sys, sysconfig\n"
                             f"script = {str(tools / 'provenance' / script)!r}\n"
                             "sys.argv = [script, '--help']\n"
                             "try:\n    runpy.run_path(script, run_name='__main__')\nexcept SystemExit:\n    pass\n"
                             "import pathlib\nhere = str(pathlib.Path(script).resolve().parent)\n"
                             "import os\nreal = [os.path.realpath(p) for p in sys.path]\n"
                             "std = real.index(os.path.realpath(sysconfig.get_paths()['stdlib']))\n"
                             "mine = [i for i, p in enumerate(real) if p == os.path.realpath(here)]\n"
                             "print('ORDER-OK' if all(i > std for i in mine) else 'ORDER-BAD')\n")
                    order = subprocess.run(["python3", "-I", "-c", probe], capture_output=True, text=True, cwd=temporary)
                    self.assertIn("ORDER-OK", order.stdout, order.stdout[-300:] + order.stderr[-300:])
            # Control: without -I the plant is what the script imports.
            plain = subprocess.run(["python3", str(tools / "provenance" / "verify_extraction.py"), "--help"],
                                   capture_output=True, text=True, cwd=temporary)
            self.assertIn("SHADOWED", plain.stdout + plain.stderr)

    def test_no_pull_request_workflow_may_write_statuses(self):
        self.assertEqual(pull_request_workflows_granting_statuses(WORKFLOWS), [])
        self.assertTrue(any("statuses: write" in p.read_text() for p in WORKFLOWS.glob("*.yml")))  # control

    def test_the_status_lint_catches_top_level_and_job_grants(self):
        for name, text in {
            "top": "on:\n  pull_request:\n\npermissions:\n  statuses: write\n",
            "job": "on:\n  pull_request:\n    types: [opened]\n\njobs:\n  a:\n    permissions:\n      statuses: write\n",
            "list": "on: [push, pull_request]\n\njobs:\n  a:\n    permissions:\n      statuses: write\n",
        }.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                Path(temporary, "w.yml").write_text(text, encoding="utf-8")
                self.assertEqual(pull_request_workflows_granting_statuses(Path(temporary)), ["w.yml"])
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "w.yml").write_text("on:\n  pull_request_target:\n\npermissions:\n  statuses: write\n",
                                                encoding="utf-8")
            self.assertEqual(pull_request_workflows_granting_statuses(Path(temporary)), [])


if __name__ == "__main__":
    unittest.main()
