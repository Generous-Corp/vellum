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
import subprocess
import unittest
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


def unclassified(registered: set[str], classes: dict[str, list[str]]) -> list[str]:
    """Registered tests that are not under exactly one class."""
    owners = classes_by_test(classes)
    return sorted(test for test in registered if len(owners.get(test, [])) != 1)


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


class CtestClassesTest(unittest.TestCase):
    def test_manifest_lists_each_test_once(self) -> None:
        classes = load_manifest()
        for name in CLASSES:
            self.assertEqual(classes[name], sorted(set(classes[name])), f"{name} is sorted, unique")
        repeated = {test: owners for test, owners in classes_by_test(classes).items()
                    if len(owners) != 1}
        self.assertEqual(repeated, {})

    def test_every_registered_test_has_one_class(self) -> None:
        registered = registered_tests()
        self.assertGreater(len(registered), 40, "the scan found the registrations")
        self.assertEqual(unclassified(registered, load_manifest()), [])

    def test_a_missing_label_is_reported(self) -> None:
        classes = load_manifest()
        registered = registered_tests()
        victim = classes["pr-fast"][0]
        self.assertIn(victim, registered)
        removed = {**classes, "pr-fast": [t for t in classes["pr-fast"] if t != victim]}
        self.assertEqual(unclassified(registered, removed), [victim])
        doubled = {**classes, "gpu": sorted({*classes["gpu"], victim})}
        self.assertEqual(unclassified(registered, doubled), [victim])

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


if __name__ == "__main__":
    unittest.main()
