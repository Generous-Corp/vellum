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
                                     "--dest", str(Path(out) / "pr"), "--output", str(output)])
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

    def test_an_unrelated_change_is_a_no_op(self):
        self.repo.write("docs/readme.md", b"Vellum, edited\n")
        code, verdict = self.repo.judge()
        self.assertEqual((code, verdict["mode"]), (0, "no-op"))
        with mock.patch.object(trusted, "load_verifier", wraps=trusted.load_verifier) as loaded:
            self.repo.write("docs/other.md", b"x\n")
            code, verdict = self.repo.judge()
        self.assertEqual(verdict["mode"], "no-op")

    def test_the_pull_request_tree_is_written_from_blobs_only(self):
        # A filter or hook the pull request declares never runs: the tree is
        # read from git objects, not checked out.
        self.repo.write(".gitattributes", b"* filter=evil\n*.md export-ignore\n")
        self.repo.write("docs/readme.md", b"Vellum, edited\n")
        os.symlink("readme.md", self.repo.root / "docs/link.md")
        head = commit_all(self.repo.root, "attributes")
        git(self.repo.root, "config", "filter.evil.smudge", "touch /tmp/framework-trusted-verify-smudge-ran && cat")
        with tempfile.TemporaryDirectory() as out:
            dest = Path(out) / "pr"
            trusted.materialize(self.repo.root, head, dest)
            self.assertEqual((dest / "docs/readme.md").read_bytes(), b"Vellum, edited\n")
            self.assertTrue((dest / "docs/link.md").is_symlink())
            self.assertEqual(git(dest, "rev-parse", "HEAD"), head)
            self.assertIn("docs/readme.md", git(dest, "ls-files").splitlines())
        self.assertFalse(Path("/tmp/framework-trusted-verify-smudge-ran").exists())

    def test_malformed_commit_ids_are_refused_before_anything_runs(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = trusted.main(["--repo", str(self.repo.root), "--head", "main; rm -rf /", "--base", self.repo.base,
                                 "--dest", str(Path(self.temporary.name) / "pr")])
        self.assertEqual(code, 2)


class WorkflowIsTrustedOnlyTests(unittest.TestCase):
    """The pull_request_target workflow runs nothing from the pull request."""

    FORBIDDEN_CONTEXT = re.compile(
        r"github\.event\.pull_request\.(title|body|head\.ref|head\.label|labels|user|head\.repo)"
        r"|github\.head_ref|github\.event\.merge_group\.head_ref")

    def setUp(self) -> None:
        # Read as text, so the check needs nothing beyond the standard library.
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.runs = re.findall(r"^( +)run: \|\n((?:\1  .*\n|\n)+)", self.text, re.MULTILINE)
        self.runs = [body for _, body in self.runs]

    def test_it_runs_on_the_base_definition_with_read_only_contents(self):
        trigger = self.text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(re.findall(r"^  (\w+):", trigger, re.MULTILINE), ["pull_request_target", "merge_group"])
        self.assertEqual(re.findall(r"^ *permissions:\n((?: +\w[\w-]*: \w+\n)+)", self.text, re.MULTILINE),
                         ["  contents: read\n", "      contents: read\n"])
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn(": write", self.text)

    def test_the_check_has_a_stable_name(self):
        self.assertEqual(re.findall(r"^jobs:\n  ([\w-]+):\n    name: ([\w-]+)$", self.text, re.MULTILINE),
                         [("framework-trusted-verify", "framework-trusted-verify")])

    def test_only_the_base_is_checked_out(self):
        self.assertEqual(self.text.count("uses: actions/checkout@"), 1)
        self.assertEqual(self.text.count("uses: "), 1)
        checkout = self.text.split("uses: actions/checkout@", 1)[1].split("\n\n", 1)[0]
        self.assertIn("persist-credentials: false", checkout)
        self.assertIn("path: trusted", checkout)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}", checkout)
        self.assertNotIn("head", checkout)

    def test_no_expression_reaches_a_shell(self):
        self.assertEqual(len(self.runs), 2)  # control: both run blocks were read
        for body in self.runs:
            self.assertNotIn("${{", body)

    def test_no_pull_request_controlled_text_is_used_anywhere(self):
        self.assertIsNone(self.FORBIDDEN_CONTEXT.search(self.text), self.FORBIDDEN_CONTEXT.search(self.text))

    def test_every_command_is_trusted_code(self):
        runs = "\n".join(self.runs)
        for forbidden in ("pip ", "npm ", "make ", "cmake ", "source ", "bash ", " sh ", "./", "pr/"):
            self.assertNotIn(forbidden, runs, forbidden)
        interpreters = re.findall(r"python3?\b[^\n]*", runs)
        self.assertTrue(interpreters)  # control: the judge runs
        for line in interpreters:
            self.assertTrue(line.startswith("python3 -I tools/provenance/framework_trusted_verify.py"), line)
        # The pull request's tree is only ever a destination.
        self.assertEqual(re.findall(r"\$RUNNER_TEMP/pr\b[^\n]*", runs), ['$RUNNER_TEMP/pr" \\'])
        self.assertIn('--dest "$RUNNER_TEMP/pr"', runs)
        judge = self.text.split("- name: Judge the change with the base rules", 1)[1].split("run: |", 1)[0]
        self.assertIn("working-directory: trusted", judge)

    def test_the_rule_list_covers_this_workflow_and_its_judge(self):
        for path in (".github/workflows/framework-trusted-verify.yml", ".github/workflows/provenance.yml",
                     "tools/provenance/framework_trusted_verify.py", "tools/provenance/verify_extraction.py",
                     "tools/mirror/"):
            self.assertIn(path, verifier.FRAMEWORK_RULE_PATHS)


if __name__ == "__main__":
    unittest.main()
