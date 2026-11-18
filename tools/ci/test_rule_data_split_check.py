#!/usr/bin/env python3
"""Tests for rule_data_split_check.py, which fails a rule added with its data."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location("rule_data_split_check", HERE / "rule_data_split_check.py")
assert SPEC is not None and SPEC.loader is not None
check = importlib.util.module_from_spec(SPEC)
sys.modules["rule_data_split_check"] = check
SPEC.loader.exec_module(check)

WORKFLOW = ROOT / ".github" / "workflows" / "rule-data-split.yml"


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.write("README.md", "x\n")
        self.write("scripts/existing.py", "print('x')\n")
        self.base = self.commit("base")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, name: str, text: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


class CheckCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_check(self, head: str, base: str | None = None) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(["--repo", str(self.repo.path), "--base", base or self.repo.base, "--head", head])
        return code, out.getvalue() + err.getvalue()


class RuleWithDataTests(CheckCase):
    def test_a_new_cmake_gate_and_its_manifest_together_fail(self) -> None:
        # The shape of the first review round's head: a configure gate that
        # enforces scripts/ctest_classes.json, added in the same change.
        self.repo.write("cmake/VellumTestClasses.cmake",
                        'file(READ "${CMAKE_SOURCE_DIR}/scripts/ctest_classes.json" _manifest)\n')
        self.repo.write("scripts/ctest_classes.json", '{"tests": {}}\n')
        code, out = self.run_check(self.repo.commit("rule and data"))
        self.assertEqual(code, 1, out)
        self.assertIn("::error file=cmake/VellumTestClasses.cmake::", out)
        self.assertIn("reads scripts/ctest_classes.json", out)
        self.assertIn("Split it, rule first", out)
        self.assertIn("Rule-Data: together rule=cmake/VellumTestClasses.cmake reason=", out)

    def test_a_script_that_names_its_data_by_file_name_fails(self) -> None:
        self.repo.write("scripts/check_widgets.py",
                        'MANIFEST = ROOT / "provenance" / "widget-list.json"\n')
        self.repo.write("provenance/widget-list.json", "[]\n")
        code, out = self.run_check(self.repo.commit("rule and data"))
        self.assertEqual(code, 1, out)
        self.assertIn("scripts/check_widgets.py is a new rule and reads provenance/widget-list.json", out)

    def test_each_rule_and_data_pair_is_named(self) -> None:
        self.repo.write("scripts/check_a.py", "open('a.yaml'); open('b.toml')\n")
        self.repo.write("data/a.yaml", "x: 1\n")
        self.repo.write("data/b.toml", "x = 1\n")
        code, out = self.run_check(self.repo.commit("rule and data"))
        self.assertEqual(code, 1, out)
        self.assertEqual(len(re.findall(r"^::error ", out, re.MULTILINE)), 2, out)

    def test_a_longer_name_is_not_a_reference(self) -> None:
        self.repo.write("scripts/check_a.py",
                        "open('old-list.json.bak'); open('my-list.jsonl'); open('data/list.json.bak')\n")
        self.repo.write("data/list.json", "[]\n")
        code, out = self.run_check(self.repo.commit("rule and unrelated data"))
        self.assertEqual(code, 0, out)


class SplitChangesPassTests(CheckCase):
    def test_the_rule_alone_passes(self) -> None:
        self.repo.write("cmake/VellumTestClasses.cmake",
                        'if(EXISTS "${CMAKE_SOURCE_DIR}/scripts/ctest_classes.json")\nendif()\n')
        code, out = self.run_check(self.repo.commit("rule"))
        self.assertEqual(code, 0, out)

    def test_the_data_after_the_rule_passes(self) -> None:
        self.repo.write("cmake/VellumTestClasses.cmake", 'set(_m "scripts/ctest_classes.json")\n')
        rule = self.repo.commit("rule")
        self.repo.write("scripts/ctest_classes.json", '{"tests": {}}\n')
        code, out = self.run_check(self.repo.commit("data"), base=rule)
        self.assertEqual(code, 0, out)

    def test_an_existing_script_reading_new_data_passes(self) -> None:
        self.repo.write("scripts/existing.py", "open('scripts/new-list.json')\n")
        self.repo.write("scripts/new-list.json", "[]\n")
        code, out = self.run_check(self.repo.commit("data and an edit"))
        self.assertEqual(code, 0, out)

    def test_a_test_and_its_fixtures_pass(self) -> None:
        self.repo.write("scripts/test_widgets.py", "open('widgets.json'); open('cases.json')\n")
        self.repo.write("scripts/testdata/widgets.json", "[]\n")
        self.repo.write("tools/x/tests/helper.py", "open('cases.json')\n")
        self.repo.write("tools/x/cases.json", "[]\n")
        self.repo.write("tools/x/widget_fixture.json", "[]\n")
        self.repo.write("tools/x/check_x.py", "open('widget_fixture.json')\n")
        code, out = self.run_check(self.repo.commit("tests and fixtures"))
        self.assertEqual(code, 0, out)

    def test_a_rule_moved_with_its_data_passes(self) -> None:
        self.repo.write("scripts/check_widgets.py", "open('scripts/widget-list.json')\n" + "x = 1\n" * 20)
        self.repo.write("scripts/widget-list.json", "[" + ", ".join(str(i) for i in range(40)) + "]\n")
        self.repo.base = self.repo.commit("rule and data on the base")
        (self.repo.path / "tools").mkdir()
        self.repo.git("mv", "scripts/check_widgets.py", "tools/check_widgets.py")
        self.repo.git("mv", "scripts/widget-list.json", "tools/widget-list.json")
        self.repo.write("tools/check_widgets.py", "open('tools/widget-list.json')\n" + "x = 1\n" * 20)
        code, out = self.run_check(self.repo.commit("move"))
        self.assertEqual(code, 0, out)

    def test_a_move_does_not_hide_a_new_rule_beside_it(self) -> None:
        # The moved file sorts first, so a misread rename entry would shift
        # every entry after it.
        self.repo.write("tools/old_tool.py", "x = 1\n" * 30)
        self.repo.base = self.repo.commit("tool on the base")
        (self.repo.path / "apps").mkdir()
        self.repo.git("mv", "tools/old_tool.py", "apps/old_tool.py")
        self.repo.write("scripts/check_widgets.py", "open('widget-list.json')\n")
        self.repo.write("scripts/widget-list.json", "[]\n")
        code, out = self.run_check(self.repo.commit("move and a rule with its data"))
        self.assertEqual(code, 1, out)
        self.assertIn("::error file=scripts/check_widgets.py::scripts/check_widgets.py is a new rule "
                      "and reads scripts/widget-list.json", out)
        self.assertEqual(len(re.findall(r"^::error ", out, re.MULTILINE)), 1, out)

    def test_a_requirements_file_is_not_data(self) -> None:
        self.repo.write("tools/gen.py", "# pip install -r requirements.txt\n")
        self.repo.write("tools/requirements.txt", "pyyaml==6.0.3\n")
        code, out = self.run_check(self.repo.commit("a tool and its requirements"))
        self.assertEqual(code, 0, out)
        self.repo.write("scripts/check_reqs.py", "open('requirements_gate.json')\n")
        self.repo.write("scripts/requirements_gate.json", "[]\n")
        code, out = self.run_check(self.repo.commit("a rule over a requirements manifest"))
        self.assertEqual(code, 1, out)

    def test_a_rule_that_is_not_utf8_is_still_read(self) -> None:
        (self.repo.path / "scripts").mkdir(exist_ok=True)
        (self.repo.path / "scripts" / "check_latin.sh").write_bytes(b"# caf\xe9\ncat scripts/list.json\n")
        self.repo.write("scripts/list.json", "[]\n")
        code, out = self.run_check(self.repo.commit("latin-1 rule and data"))
        self.assertEqual(code, 1, out)
        self.assertIn("scripts/check_latin.sh is a new rule", out)

    def test_workflow_files_are_not_data(self) -> None:
        self.repo.write("scripts/check_flows.py", "open('.github/workflows/new.yml')\n")
        self.repo.write(".github/workflows/new.yml", "name: x\n")
        code, out = self.run_check(self.repo.commit("a check and a workflow"))
        self.assertEqual(code, 0, out)

    def test_a_change_with_no_added_data_passes(self) -> None:
        self.repo.write("scripts/check_new.py", "open('scripts/ctest_classes.json')\n")
        self.repo.write("README.md", "y\n")
        code, out = self.run_check(self.repo.commit("rule"))
        self.assertEqual(code, 0, out)
        self.assertIn("no added rule reads data", out)

    def test_main_moving_ahead_does_not_count(self) -> None:
        # base...head: data main added after the branch point is not this change's.
        self.repo.git("checkout", "-q", "-b", "feature")
        self.repo.write("scripts/check_new.py", "open('scripts/list.json')\n")
        head = self.repo.commit("rule")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("scripts/list.json", "[]\n")
        main = self.repo.commit("data on main")
        code, out = self.run_check(head, base=main)
        self.assertEqual(code, 0, out)


class TrailerTests(CheckCase):
    def add_pair(self) -> None:
        self.repo.write("tools/gen/generate.py", "open('tools/gen/template.json')\n")
        self.repo.write("tools/gen/template.json", "{}\n")

    def test_a_reasoned_trailer_in_any_commit_excuses_it(self) -> None:
        self.add_pair()
        self.repo.commit('tool\n\nRule-Data: together rule=tools/gen/generate.py reason="a generator, no rule"')
        self.repo.write("README.md", "later\n")
        code, out = self.run_check(self.repo.commit("later"))
        self.assertEqual(code, 0, out)
        self.assertIn("excused by a Rule-Data trailer", out)

    def test_a_trailer_excuses_only_the_rule_it_names(self) -> None:
        self.add_pair()
        self.repo.write("scripts/check_widgets.py", "open('widget-list.json')\n")
        self.repo.write("provenance/widget-list.json", "[]\n")
        code, out = self.run_check(self.repo.commit(
            'tool\n\nRule-Data: together rule=tools/gen/generate.py reason="a generator, no rule"'))
        self.assertEqual(code, 1, out)
        self.assertIn("excused by a Rule-Data trailer: tools/gen/generate.py", out)
        self.assertIn("::error file=scripts/check_widgets.py::", out)
        self.assertNotIn("::error file=tools/gen/generate.py::", out)

    def test_a_trailer_without_a_rule_does_not(self) -> None:
        self.add_pair()
        code, out = self.run_check(self.repo.commit('tool\n\nRule-Data: together reason="no rule named"'))
        self.assertEqual(code, 1, out)

    def test_a_trailer_without_a_reason_does_not(self) -> None:
        self.add_pair()
        code, out = self.run_check(self.repo.commit('tool\n\nRule-Data: together rule=tools/gen/generate.py reason=""'))
        self.assertEqual(code, 1, out)

    def test_a_trailer_already_on_the_base_does_not(self) -> None:
        self.repo.commit('old\n\nRule-Data: together rule=tools/gen/generate.py reason="an older change"')
        self.repo.base = self.repo.git("rev-parse", "HEAD")
        self.add_pair()
        code, out = self.run_check(self.repo.commit("tool"))
        self.assertEqual(code, 1, out)


class FailureTests(CheckCase):
    def test_an_unreadable_range_is_an_error(self) -> None:
        code, out = self.run_check("f" * 40)
        self.assertEqual(code, 2, out)
        self.assertIn("rule-data split: error:", out)

    def test_a_crafted_file_name_cannot_start_a_workflow_command(self) -> None:
        finding = check.Finding("scripts/x\n::set-output name=a::b.py", "d\r\n::error::y.json")
        line = f"::error file={check.escape(finding.rule, prop=True)}::{check.escape(finding.message())}"
        self.assertEqual(len(line.splitlines()), 1)
        self.assertNotIn("\n::", line)


class WorkflowTests(unittest.TestCase):
    def test_it_runs_on_pull_requests_only_with_the_base_copy(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        triggers = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+?)^\S", text, re.MULTILINE)
        self.assertIsNotNone(triggers)
        self.assertEqual(set(re.findall(r"^  ([a-z_]+):", triggers.group(1), re.MULTILINE)), {"pull_request"})
        self.assertIn('git show "$BASE_SHA:tools/ci/rule_data_split_check.py" > "$check"', text)
        self.assertIn('git cat-file -e "$BASE_SHA^{commit}"', text)
        self.assertIn('git show "$HEAD_SHA:tools/ci/rule_data_split_check.py" > "$check"', text)
        self.assertNotIn("cp tools/ci/", text)
        self.assertIn('python3 -I "$check" --repo "$GITHUB_WORKSPACE" --base "$BASE_SHA" --head "$HEAD_SHA"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("github.token", text)

    def run_step(self, repo: Repo, base: str, head: str) -> subprocess.CompletedProcess[str]:
        text = WORKFLOW.read_text(encoding="utf-8")
        body = text.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in body.splitlines())
        env = {"PATH": os.environ["PATH"], "BASE_SHA": base, "HEAD_SHA": head,
               "RUNNER_TEMP": str(repo.path / ".tmp"), "GITHUB_WORKSPACE": str(repo.path)}
        (repo.path / ".tmp").mkdir(exist_ok=True)
        return subprocess.run(["bash", "-c", script], cwd=repo.path, env=env,
                              capture_output=True, text=True)

    def test_a_branch_that_predates_the_check_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            head = repo.commit("a change with no check anywhere")
            result = self.run_step(repo, repo.base, head)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("::notice::rule-data-split is skipped", result.stdout)
            self.assertIn("merge main", result.stdout)

    def test_a_missing_base_object_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            head = repo.commit("a change")
            result = self.run_step(repo, "0" * 40, head)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("is skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
