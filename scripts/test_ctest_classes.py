#!/usr/bin/env python3
"""Every registered test carries exactly one class: pr-fast, gpu or slow.

scripts/ctest_classes.json lists the tests of each class and
cmake/VellumTestClasses.cmake turns it into ctest labels at configure time,
failing the configure for a test it does not list. This checks the file and the
test registrations without a build: every literal add_test name and every
framework test helper call must appear under exactly one class. Names a CMake
loop builds are caught by the configure step. With VELLUM_CTEST_BUILD_DIR set
it also reads a configured build tree and checks the labels ctest reports.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "ctest_classes.json"
CLASSES = ("pr-fast", "gpu", "slow")

ADD_TEST = re.compile(r"add_test\(\s*NAME\s+([^\s)]+)")
# Sterile consumer projects configure on their own, outside the root ctest.
STANDALONE = re.compile(r"consumer|minimal-scene|link_boundary_control|provider-conflict")
FRAMEWORK_TEST = re.compile(r"^\s*vellum_add_framework_test\(\s*([^\s)]+)", re.MULTILINE)


def load_manifest(path: Path = MANIFEST) -> dict[str, list[str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "vellum.ctest-classes.v1":
        raise AssertionError(f"{path}: unexpected schema {document.get('schema')!r}")
    classes = document["classes"]
    if set(classes) != set(CLASSES):
        raise AssertionError(f"{path}: classes must be exactly {CLASSES}, found {sorted(classes)}")
    return classes


def classes_by_test(classes: dict[str, list[str]]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for name, names in classes.items():
        for test in names:
            owners.setdefault(test, []).append(name)
    return owners


def unclassified(registered: set[str], classes: dict[str, list[str]],
                 policy: str | None = None) -> list[str]:
    """Registered tests that are not under exactly one class.

    With policy "gpu" (VELLUM_UNLISTED_TEST_POLICY, set for merge groups and
    main) an unlisted test is not an error: it runs as gpu, and is reported on
    stderr. A test listed under two classes is always an error.
    """
    policy = policy if policy is not None else os.environ.get("VELLUM_UNLISTED_TEST_POLICY", "fail")
    owners = classes_by_test(classes)
    bad = []
    for test in sorted(registered):
        count = len(owners.get(test, []))
        if count == 1:
            continue
        if count == 0 and policy == "gpu":
            print(f"UNLISTED TEST {test}: running as gpu; add it to {MANIFEST.name}",
                  file=sys.stderr)
            continue
        bad.append(test)
    return bad


def registered_tests(root: Path = ROOT) -> set[str]:
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "*CMakeLists.txt", "*.cmake"],
        check=True, capture_output=True, text=True).stdout
    names: set[str] = set()
    for relative in filter(None, listing.split("\0")):
        if STANDALONE.search(relative):
            continue
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"(?m)^\s*#.*$", "", text)
        names.update(ADD_TEST.findall(text))
        names.update(f"vellum.framework.{name}" for name in FRAMEWORK_TEST.findall(text))
    return {name for name in names if "$" not in name}


def fixture_classes(registered: set[str]) -> dict[str, list[str]]:
    """A manifest over the registered tests, for the mechanism tests, so they
    hold whether or not the repository carries the real manifest."""
    names = sorted(registered)
    return {"pr-fast": names[0::3], "gpu": names[1::3], "slow": names[2::3]}


HAS_MANIFEST = unittest.skipUnless(
    MANIFEST.is_file(), "scripts/ctest_classes.json is not in this tree")


class CtestClassesTest(unittest.TestCase):
    def test_design_tool_verbatim_move_is_registered(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertRegex(
            cmake,
            r'add_test\(NAME\s+vellum\.design-tool-verbatim-move\s+'
            r'COMMAND\s+"\$\{Python3_EXECUTABLE\}"\s+'
            r'apps/design-tool/test_verify_verbatim_move\.py\s+'
            r'WORKING_DIRECTORY\s+"\$\{CMAKE_CURRENT_SOURCE_DIR\}"\)',
        )

    @HAS_MANIFEST
    def test_manifest_lists_each_test_once(self) -> None:
        classes = load_manifest()
        for name in CLASSES:
            self.assertEqual(classes[name], sorted(set(classes[name])), f"{name} is sorted, unique")
        repeated = {test: owners for test, owners in classes_by_test(classes).items()
                    if len(owners) != 1}
        self.assertEqual(repeated, {})

    @HAS_MANIFEST
    def test_every_registered_test_has_one_class(self) -> None:
        registered = registered_tests()
        self.assertGreater(len(registered), 40, "the scan found the registrations")
        # Reads VELLUM_UNLISTED_TEST_POLICY like the configure step: strict in
        # pull requests and locally, tolerant in merge groups and on main.
        self.assertEqual(unclassified(registered, load_manifest()), [])

    def test_a_missing_label_is_reported(self) -> None:
        registered = registered_tests()
        classes = fixture_classes(registered)
        victim = classes["pr-fast"][0]
        self.assertIn(victim, registered)
        removed = {**classes, "pr-fast": [t for t in classes["pr-fast"] if t != victim]}
        self.assertEqual(unclassified(registered, removed, "fail"), [victim])
        doubled = {**classes, "gpu": sorted({*classes["gpu"], victim})}
        self.assertEqual(unclassified(registered, doubled, "fail"), [victim])

    def test_unlisted_policy(self) -> None:
        registered = registered_tests()
        classes = fixture_classes(registered)
        victim = classes["pr-fast"][0]
        removed = {**classes, "pr-fast": [t for t in classes["pr-fast"] if t != victim]}
        self.assertEqual(unclassified(registered, removed, "fail"), [victim])
        self.assertEqual(unclassified(registered, removed, "gpu"), [])
        # A listed test keeps its class, and a doubly listed one still fails.
        self.assertEqual(unclassified(registered, classes, "gpu"), [])
        doubled = {**classes, "gpu": sorted({*classes["gpu"], victim})}
        self.assertEqual(unclassified(registered, doubled, "gpu"), [victim])

    def test_unset_or_unknown_policy_fails_closed(self) -> None:
        registered = registered_tests()
        classes = fixture_classes(registered)
        victim = classes["pr-fast"][0]
        removed = {**classes, "pr-fast": [t for t in classes["pr-fast"] if t != victim]}
        # patch.dict restores the environment, including an unset variable,
        # so the strict check in another test never inherits "gpu" from here.
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("VELLUM_UNLISTED_TEST_POLICY", None)
        self.assertEqual(unclassified(registered, removed), [victim], "unset fails")
        for value in ("", "GPU", "pr-fast", "warn", "gpu "):
            os.environ["VELLUM_UNLISTED_TEST_POLICY"] = value
            self.assertEqual(unclassified(registered, removed), [victim], f"{value!r} fails")
        os.environ["VELLUM_UNLISTED_TEST_POLICY"] = "gpu"
        self.assertEqual(unclassified(registered, removed), [])

    @unittest.skipUnless(os.environ.get("VELLUM_CTEST_BUILD_DIR"),
                         "set VELLUM_CTEST_BUILD_DIR to a configured build tree")
    def test_configured_tree_carries_one_class_label(self) -> None:
        build = os.environ["VELLUM_CTEST_BUILD_DIR"]
        document = json.loads(subprocess.run(
            ["ctest", "--test-dir", build, "--show-only=json-v1"],
            check=True, capture_output=True, text=True).stdout)
        self.assertTrue(document["tests"], "ctest listed tests")
        wrong = {}
        for test in document["tests"]:
            labels = next((p["value"] for p in test["properties"] if p["name"] == "LABELS"), [])
            present = [label for label in labels if label in CLASSES]
            if len(present) != 1:
                wrong[test["name"]] = labels
        self.assertEqual(wrong, {})


CMAKE = shutil.which("cmake")
MODULE = ROOT / "cmake" / "VellumTestClasses.cmake"


@unittest.skipUnless(CMAKE, "cmake is required")
class ClassifierConfigureTest(unittest.TestCase):
    """Configures tiny projects that include the real classifier module."""

    def configure(self, *, parent_test: bool, vellum_tests: str = "ON",
                  cmake_version: str = "", listed: bool = True,
                  policy: str = "fail", manifest: str = "present") -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp(prefix="vellum-classes-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        vellum = tmp / "vellum"
        (vellum / "cmake").mkdir(parents=True)
        (vellum / "scripts").mkdir()
        shutil.copy(MODULE, vellum / "cmake" / "VellumTestClasses.cmake")
        if manifest != "absent":
            # A manifest must list something to be live; an unlisted `vellum.own`
            # is then an unclassified test, not an inert manifest.
            names = [] if manifest == "empty" else (
                ["vellum.own"] if listed else ["vellum.other"])
            (vellum / "scripts" / "ctest_classes.json").write_text(json.dumps(
                {"schema": "vellum.ctest-classes.v1",
                 "classes": {"pr-fast": names, "gpu": [], "slow": []}}))
        own = "add_test(NAME vellum.own COMMAND ${CMAKE_COMMAND} -E true)\n" 
        (vellum / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.24)\nproject(vellum NONE)\n"
            f"set(VELLUM_BUILD_TESTS {vellum_tests})\n"
            + ("enable_testing()\n" + own if vellum_tests == "ON" else "")
            + "include(cmake/VellumTestClasses.cmake)\n"
            'vellum_apply_test_classes("${CMAKE_CURRENT_SOURCE_DIR}/scripts/ctest_classes.json")\n')
        parent = tmp / "parent"
        parent.mkdir()
        (parent / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.24)\nproject(parent NONE)\nenable_testing()\n"
            + ("add_test(NAME parent.unrelated COMMAND ${CMAKE_COMMAND} -E true)\n" if parent_test else "")
            + f"add_subdirectory({vellum.as_posix()} vellum-build)\n")
        self.build = tmp / "b"
        args = [CMAKE, "-S", str(parent), "-B", str(self.build)]
        if cmake_version:
            args.append(f"-DVELLUM_TEST_CLASSES_CMAKE_VERSION={cmake_version}")
        environment = {**os.environ, "VELLUM_UNLISTED_TEST_POLICY": policy}
        return subprocess.run(args, capture_output=True, text=True, env=environment)

    def labelled(self, label: str) -> str:
        return subprocess.run(["ctest", "--test-dir", str(self.build), "-N", "-L", label],
                              capture_output=True, text=True).stdout

    def test_without_a_manifest_configure_succeeds_and_labels_nothing(self) -> None:
        # The rule ships before its data: no manifest means no enforcement,
        # even for a test no manifest lists, under the strict policy.
        result = self.configure(parent_test=False, manifest="absent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stdout)
        self.assertIn("Total Tests: 0", self.labelled("pr-fast"))

    def test_an_empty_manifest_labels_nothing_and_enforces_nothing(self) -> None:
        result = self.configure(parent_test=False, manifest="empty")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lists no test", result.stdout)
        self.assertIn("Total Tests: 0", self.labelled("pr-fast"))

    def test_a_manifest_applies_its_classes(self) -> None:
        result = self.configure(parent_test=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("vellum.own", self.labelled("pr-fast"))
        self.assertIn("Total Tests: 0", self.labelled("gpu"))

    def test_embedding_ignores_unrelated_parent_tests(self) -> None:
        result = self.configure(parent_test=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tests_disabled_configures_without_labelling(self) -> None:
        result = self.configure(parent_test=True, vellum_tests="OFF")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_supported_minimum_cmake_skips_labels(self) -> None:
        result = self.configure(parent_test=False, cmake_version="3.24.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stdout)

    def test_own_unclassified_test_still_fails(self) -> None:
        # Guard: scoping must not hide Vellum's own unlisted tests.
        result = self.configure(parent_test=False, listed=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vellum.own", result.stderr)

    def test_unlisted_test_runs_as_gpu_under_the_merge_group_policy(self) -> None:
        result = self.configure(parent_test=False, listed=False, policy="gpu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UNLISTED TEST vellum.own", result.stderr)


WORKFLOW = ROOT / ".github/workflows/gpu-macos.yml"
FULL_STEP = "      - name: Run native, capture, and sterile-consumer tests\n"
CHEAP_STEP = "      - name: Run the pr-fast tests\n"


def workflow_step(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text.split(name, 1)[1].split("\n      - name: ", 1)[0]


def split_labels() -> tuple[str, str]:
    """The two ctest -L expressions the parallel full proof splits the tests by."""
    step = workflow_step(FULL_STEP)
    gpu = re.search(r"gpu_label='([^']+)'", step)
    other = re.search(r"other_label='([^']+)'", step)
    assert gpu and other, "the workflow no longer declares gpu_label and other_label"
    return gpu.group(1), other.group(1)


class ParallelSplitTest(unittest.TestCase):
    """The parallel ctest split drops no test and runs none twice."""

    @HAS_MANIFEST
    def test_the_two_label_sets_are_disjoint_and_cover_every_class(self) -> None:
        gpu, other = split_labels()
        classes = load_manifest()
        by_gpu = [name for name in CLASSES if re.search(gpu, name)]
        by_other = [name for name in CLASSES if re.search(other, name)]
        self.assertEqual(by_gpu, ["gpu"])
        self.assertEqual(sorted(by_other), ["pr-fast", "slow"])
        self.assertEqual(set(by_gpu) & set(by_other), set())
        self.assertEqual(set(by_gpu) | set(by_other), set(CLASSES))
        # So the union of the two runs is exactly the manifest's test set.
        union = {test for name in by_gpu + by_other for test in classes[name]}
        self.assertEqual(union, set(classes_by_test(classes)))
        self.assertEqual(sum(len(classes[name]) for name in CLASSES), len(union))

    @HAS_MANIFEST
    def test_fixture_setups_share_the_class_of_the_tests_that_require_them(self) -> None:
        # CTest adds a required fixture's setup test to any -L selection, so a
        # setup in another class lands in both halves of the split.
        owners = {test: name for test, names in classes_by_test(load_manifest()).items() for name in names}
        setups: dict[str, set[str]] = {}
        required: dict[str, set[str]] = {}
        listing = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "*CMakeLists.txt", "*.cmake"],
                                 check=True, capture_output=True, text=True).stdout
        for relative in filter(None, listing.split("\0")):
            text = re.sub(r"(?m)^\s*#.*$", "", (ROOT / relative).read_text(encoding="utf-8", errors="replace"))
            for test, body in re.findall(r"set_tests_properties\(\s*(\S+)\s+PROPERTIES([^)]*)\)", text):
                for kind, table in (("FIXTURES_SETUP", setups), ("FIXTURES_REQUIRED", required)):
                    for value in re.findall(kind + r"\s+([^\s)]+)", body):
                        for fixture in value.split(";"):
                            table.setdefault(fixture, set()).add(test)
        self.assertIn("vellum-swift-host-sdk", setups)  # control: the scan sees fixtures
        crossing = {}
        for fixture, users in required.items():
            classes = {owners.get(t) for t in users | setups.get(fixture, set()) if t in owners}
            if len(classes) > 1:
                crossing[fixture] = sorted(classes)
        self.assertEqual(crossing, {})

    def test_a_label_expression_that_misses_or_doubles_a_class_is_caught(self) -> None:
        # Negative control: the same property check, on a split that leaves
        # slow out, and on one that counts pr-fast twice.
        for gpu, other, problem in (("^gpu$", "^pr-fast$", "dropped"), ("^gpu$|^pr-fast$", "^(pr-fast|slow)$", "doubled")):
            with self.subTest(problem=problem):
                a = {name for name in CLASSES if re.search(gpu, name)}
                b = {name for name in CLASSES if re.search(other, name)}
                self.assertTrue(a & b or (a | b) != set(CLASSES))

    @unittest.skipUnless(CMAKE, "cmake is required")
    def test_workflow_runs_every_test_once_and_falls_back_when_labels_gap(self) -> None:
        gpu, other = split_labels()
        step = workflow_step(FULL_STEP)
        body = step.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in body.splitlines())
        script = script.split("./build-gpu/apps/gpu-native", 1)[0]
        tmp = Path(tempfile.mkdtemp(prefix="vellum-split-"))
        self.addCleanup(shutil.rmtree, tmp, True)

        counter = iter(range(1000))

        def run(setting: str, classes: dict[str, list[str]], tests: list[str],
                failing: str = "", fixture: tuple[str, str] | None = None) -> tuple[int, str, str]:
            source = tmp / f"src-{next(counter)}"
            (source / "scripts").mkdir(parents=True)
            (source / "cmake").mkdir()
            shutil.copy(MODULE, source / "cmake" / "VellumTestClasses.cmake")
            (source / "scripts" / "ctest_classes.json").write_text(json.dumps(
                {"schema": "vellum.ctest-classes.v1", "classes": classes}))
            (source / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.24)\nproject(vellum NONE)\nenable_testing()\n"
                + "".join(
                    f"add_test(NAME {t} COMMAND ${{CMAKE_COMMAND}} -E "
                    + ("false" if t == failing else f"echo ran-{t}") + ")\n"
                    for t in tests)
                + (f"set_tests_properties({fixture[0]} PROPERTIES FIXTURES_SETUP fx)\n"
                   f"set_tests_properties({fixture[1]} PROPERTIES FIXTURES_REQUIRED fx)\n" if fixture else "")
                + "include(cmake/VellumTestClasses.cmake)\n"
                'vellum_apply_test_classes("${CMAKE_CURRENT_SOURCE_DIR}/scripts/ctest_classes.json")\n')
            work = tmp / f"work-{source.name}"
            work.mkdir()
            subprocess.run([CMAKE, "-S", str(source), "-B", str(work / "build-gpu")], check=True,
                           capture_output=True, env={**os.environ, "VELLUM_UNLISTED_TEST_POLICY": "fail"})
            result = subprocess.run(
                ["bash", "-e", "-c", script], cwd=work, capture_output=True, text=True,
                env={**os.environ, "CTEST_PARALLEL_SETTING": setting, "RUNNER_TEMP": str(work)})
            return result.returncode, result.stdout, result.stderr

        classes = {"pr-fast": ["t.a", "t.b"], "gpu": ["t.c", "t.d"], "slow": ["t.e"]}
        tests = ["t.a", "t.b", "t.c", "t.d", "t.e"]
        self.assertEqual((gpu, other), ("^gpu$", "^(pr-fast|slow)$"))
        passed = lambda out: re.search(r"100% tests passed.* out of 5", out)  # noqa: E731
        for setting in ("split", "on"):
            code, out, err = run(setting, classes, tests)
            self.assertEqual(code, 0, out + err)
            self.assertNotIn("running them serially", out + err)
            self.assertIn("ctest gpu tests", out)  # control: the split path ran
            # Every test ran exactly once, across the two processes.
            for name in tests:
                self.assertEqual(len(re.findall(rf"\b{re.escape(name)}\b \.+\s+Passed", out)), 1, name)
        for setting in ("off", "", "bogus"):
            code, out, err = run(setting, classes, tests)
            self.assertEqual(code, 0, out + err)
            self.assertTrue(passed(out), out)
            self.assertNotIn("ctest gpu tests", out)
        # A test the labels do not reach (no `gpu` class left) falls back to the serial run.
        gap = {"pr-fast": tests, "gpu": [], "slow": []}
        code, out, err = run("split", gap, tests)
        self.assertEqual(code, 0, out + err)
        self.assertIn("running them serially", out + err)
        self.assertTrue(passed(out), out)
        # A setup fixture in another class than its user lands in both halves: serial fallback.
        crossing = {"pr-fast": ["t.a", "t.b", "t.e"], "gpu": ["t.c", "t.d"], "slow": []}
        code, out, err = run("split", crossing, tests, fixture=("t.a", "t.c"))
        self.assertEqual(code, 0, out + err)
        self.assertIn("running them serially", out + err)
        # Closed classes keep the split live with the same fixture shape.
        closed = {"pr-fast": ["t.b", "t.e"], "gpu": ["t.a", "t.c", "t.d"], "slow": []}
        code, out, err = run("split", closed, tests, fixture=("t.a", "t.c"))
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("running them serially", out + err)
        self.assertIn("ctest gpu tests", out)
        # A failing test in either process fails the step.
        for victim in ("t.c", "t.e"):
            code, out, err = run("split", classes, tests, failing=victim)
            self.assertNotEqual(code, 0, out + err)
            self.assertIn("ctest exited", out + err)


if __name__ == "__main__":
    unittest.main()
