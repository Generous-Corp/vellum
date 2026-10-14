#!/usr/bin/env python3
"""Unit tests for the D6 rung-2 packaging lane's comparison, configuration,
justification, package-binding, single-definition and single-provider rules.
The end-to-end run needs an origin build and an installed package, so it is
exercised by `calibrate` instead."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import packaging_lane as lane  # noqa: E402

DARWIN_TOOLS = sys.platform == "darwin" and all(shutil.which(t) for t in ("cc", "ar", "nm"))


class CompareTests(unittest.TestCase):
    def test_identical_passing_populations_match(self) -> None:
        both = {"a::x": "passed", "a::y": "passed"}
        self.assertEqual(lane.compare(both, dict(both)), [])

    def test_population_and_outcome_differences_are_reported(self) -> None:
        reference = {"a::x": "passed", "a::y": "passed"}
        self.assertTrue(lane.compare(reference, {"a::x": "passed"}))
        self.assertTrue(lane.compare(reference, dict(reference, **{"a::z": "passed"})))
        self.assertIn("a::y: reference passed != candidate failed",
                      lane.compare(reference, {"a::x": "passed", "a::y": "failed"}))
        both_failing = {"a::x": "passed", "a::f": "failed"}
        self.assertIn("a::f: failed on both sides", lane.compare(both_failing, dict(both_failing)))
        self.assertTrue(lane.compare({}, {}))

    def test_identical_skips_are_listed_not_counted(self) -> None:
        reference = {"a::x": "passed", "a::s": "skipped"}
        self.assertEqual(lane.compare(reference, dict(reference)), [])
        self.assertEqual(lane.skipped_on_both(reference, dict(reference)), ["a::s"])
        self.assertTrue(lane.compare({"a::s": "skipped"}, {"a::s": "skipped"}))  # nothing passed


class ConfigurationTests(unittest.TestCase):
    plain = {"configuration": "Release", "compile_definitions": ["PULP_HAS_SHEENBIDI=1"]}
    skia = {"configuration": "Release", "compile_definitions": ["PULP_HAS_SKIA=1"]}

    def test_matching_build_is_accepted(self) -> None:
        self.assertEqual(lane.configuration_errors([self.plain], {"CMAKE_BUILD_TYPE": "Release",
                                                                  "PULP_HAS_SKIA": "FALSE"}), [])
        # A Skia build serves a Skia closure and a non-Skia closure exported with it.
        self.assertEqual(lane.configuration_errors([self.plain, self.skia], {"CMAKE_BUILD_TYPE": "Release",
                                                                             "PULP_HAS_SKIA": "TRUE"}), [])

    def test_build_type_and_skia_mismatches_are_refused(self) -> None:
        self.assertTrue(lane.configuration_errors([self.plain], {"CMAKE_BUILD_TYPE": "Debug",
                                                                 "PULP_HAS_SKIA": "FALSE"}))
        self.assertTrue(lane.configuration_errors([self.plain], {"CMAKE_BUILD_TYPE": "Release",
                                                                 "PULP_HAS_SKIA": "TRUE"}))
        self.assertTrue(lane.configuration_errors([self.plain, self.skia], {"CMAKE_BUILD_TYPE": "Release",
                                                                            "PULP_HAS_SKIA": "FALSE"}))


@unittest.skipUnless(DARWIN_TOOLS, "needs Darwin cc, ar and nm -m")
class CensusTests(unittest.TestCase):
    def archive(self, root: Path, name: str, code: str) -> Path:
        source = root / f"{name}.c"
        source.write_text(code, encoding="utf-8")
        subprocess.run(["cc", "-c", str(source), "-o", str(root / f"{name}.o")], check=True)
        archive = root / f"lib{name}.a"
        subprocess.run(["ar", "rcs", str(archive), str(root / f"{name}.o")], check=True)
        return archive

    def executable(self, root: Path, name: str, code: str) -> Path:
        source = root / f"{name}.c"
        source.write_text(code, encoding="utf-8")
        subprocess.run(["cc", str(source), "-o", str(root / name)], check=True)
        return root / name

    def test_strong_duplicates_fail_and_weak_ones_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = self.archive(root, "one", "int shared(void) { return 1; }\n"
                                            "__attribute__((weak)) int inline_like(void) { return 3; }\n")
            two = self.archive(root, "two", "int other(void) { return 2; }\n"
                                            "__attribute__((weak)) int inline_like(void) { return 3; }\n")
            clean = lane.library_census([one, two])
            self.assertEqual(clean["duplicate_count"], 0)
            self.assertGreaterEqual(clean["symbols"], 2)
            dup = self.archive(root, "dup", "int shared(void) { return 9; }\n")
            dirty = lane.library_census([one, two, dup])
            self.assertEqual(dirty["duplicate_count"], 1)
            self.assertIn("_shared", dirty["duplicates"])

    def test_image_that_redefines_a_package_symbol_or_carries_a_provider_is_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = self.executable(root, "clean", "int main(void) { return 0; }\n")
            report = lane.image_census(clean, {"_shared"})
            self.assertEqual((report["shadowed_count"], report["provider_count"]), (0, 0))
            shadow = self.executable(root, "shadow", "int shared(void) { return 1; }\n"
                                                     "int main(void) { return shared(); }\n")
            self.assertEqual(lane.image_census(shadow, {"_shared"})["shadowed"], ["_shared"])
            provider = self.executable(root, "provider", "void *wgpuCreateInstance(void *d) { return d; }\n"
                                                         "int main(void) { return wgpuCreateInstance(0) != 0; }\n")
            self.assertEqual(lane.image_census(provider, set())["providers"], ["_wgpuCreateInstance"])


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@invalid")
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env=env).stdout.strip()


class BindingTests(unittest.TestCase):
    """The identity and the package must describe the same export."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin"
        self.exported = root / "exported"
        for repo in (self.origin, self.exported):
            repo.mkdir()
            git(repo, "init", "-q")
        (self.origin / "core").mkdir()
        (self.origin / "core" / "a.cpp").write_text("int a;\n", encoding="utf-8")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-q", "-m", "origin")
        self.origin_commit = git(self.origin, "rev-parse", "HEAD")
        (self.exported / "mirror" / "core").mkdir(parents=True)
        (self.exported / "mirror" / "core" / "a.cpp").write_text("int a;\n", encoding="utf-8")
        self.receipt = {"schema": lane.RECEIPT_SCHEMA, "pulp_sha": self.origin_commit}
        (self.exported / "receipts").mkdir()
        (self.exported / "receipts" / "r.json").write_text(json.dumps(self.receipt), encoding="utf-8")
        git(self.exported, "add", ".")
        git(self.exported, "commit", "-q", "-m", "export")
        self.closure = root / "closure.json"
        self.closure.write_text(json.dumps({"schema": "pulp.vellum.closure.v1", "files": ["core/a.cpp"]}),
                                encoding="utf-8")
        self.prefix = root / "prefix"
        (self.prefix / "mirror" / "share" / "provenance").mkdir(parents=True)
        shutil.copy2(self.exported / "receipts" / "r.json", self.prefix / "mirror" / "share" / "provenance" / "r.json")
        self.spec = {"closures": [str(self.closure)], "origin_repo": str(self.origin),
                     "origin_commit": self.origin_commit, "exported_repo": str(self.exported),
                     "exported_commit": git(self.exported, "rev-parse", "HEAD"), "exported_prefix": "mirror/",
                     "export_receipt": "receipts/r.json"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_identity_passes_and_one_mutated_blob_fails(self) -> None:
        self.assertEqual(lane.identity_result(self.spec)["status"], "pass")
        mutated = lane.mutated_export_commit(self.exported, self.spec["exported_commit"], "mirror/")
        result = lane.identity_result(self.spec, mutated)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("blob differs: core/a.cpp" in r for r in result["reasons"]))
        # The control commit is an unreferenced object: no branch moved.
        self.assertEqual(git(self.exported, "rev-parse", "HEAD"), self.spec["exported_commit"])

    def test_package_receipt_must_be_the_exported_one(self) -> None:
        self.assertEqual(lane.receipt_binding(self.spec, self.prefix), [])
        installed = self.prefix / "mirror" / "share" / "provenance" / "r.json"
        installed.write_text(json.dumps(dict(self.receipt, pulp_sha="0" * 40)), encoding="utf-8")
        reasons = lane.receipt_binding(self.spec, self.prefix)
        self.assertTrue(any("differs" in r for r in reasons))
        self.assertTrue(any("names origin" in r for r in reasons))
        shutil.copy2(installed, installed.with_name("second.json"))
        self.assertIn("expected exactly one", lane.receipt_binding(self.spec, self.prefix)[0])

    def test_redaction_removes_host_paths(self) -> None:
        roots = {"origin": str(self.origin), "prefix": str(self.prefix)}
        text = json.dumps(lane.redact({"a": [str(self.origin / "test"), str(self.prefix)]}, roots))
        self.assertNotIn(self.tmp.name, text)
        self.assertIn("<origin>/test", text)


JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites><testsuite name="t">
<testcase classname="bin.global.test_a" name="passes"/>
<testcase classname="bin.global.test_a" name="skips"><skipped message="SKIP"/></testcase>
<testcase classname="bin.global.test_b" name="fails"><failure message="x"/></testcase>
<testcase classname="bin.global.test_b" name="errors"><error message="y"/></testcase>
</testsuite></testsuites>
"""
PASSING = JUNIT.split("<testcase classname=\"bin.global.test_b\"")[0] + "</testsuite></testsuites>\n"


class JunitTests(unittest.TestCase):
    """A run's outcomes come only from that run, and its exit must agree."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.xml = self.root / "out.xml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake(self, name: str, body: str) -> Path:
        script = self.root / name
        script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        script.chmod(0o755)
        return script

    def writer(self, name: str, document: str, code: int) -> Path:
        payload = self.root / f"{name}.xml"
        payload.write_text(document, encoding="utf-8")
        # Catch2's --out is the last argument.
        return self.fake(name, f'for last; do :; done\ncp "{payload}" "$last"\nexit {code}\n')

    def test_skip_failure_and_error_are_parsed(self) -> None:
        self.xml.write_text(JUNIT, encoding="utf-8")
        self.assertEqual(lane.junit_outcomes(self.xml), {
            "test_a::passes": "passed", "test_a::skips": "skipped",
            "test_b::fails": "failed", "test_b::errors": "failed"})

    def test_stale_junit_is_never_read_for_a_binary_that_dies_before_main(self) -> None:
        self.xml.write_text(PASSING, encoding="utf-8")  # a previous run's file
        aborting = self.fake("abort", "kill -ABRT $$\n")
        with self.assertRaisesRegex(lane.LaneError, "killed by signal"):
            lane.run_junit(aborting, ["test_a"], self.xml)
        silent = self.fake("silent", "exit 0\n")
        self.xml.write_text(PASSING, encoding="utf-8")
        with self.assertRaisesRegex(lane.LaneError, "wrote no JUnit"):
            lane.run_junit(silent, ["test_a"], self.xml)

    def test_exit_status_must_agree_with_the_recorded_run(self) -> None:
        self.assertEqual(lane.run_junit(self.writer("ok", PASSING, 0), ["test_a"], self.xml)["exit"], 0)
        self.assertEqual(lane.run_junit(self.writer("fail", JUNIT, 42), ["test_a"], self.xml)["exit"], 42)
        with self.assertRaisesRegex(lane.LaneError, "no failed test to explain it"):
            lane.run_junit(self.writer("crash-at-exit", PASSING, 3), ["test_a"], self.xml)
        with self.assertRaisesRegex(lane.LaneError, "exited 0 although"):
            lane.run_junit(self.writer("liar", JUNIT, 0), ["test_a"], self.xml)


@unittest.skipUnless(shutil.which("ninja") and shutil.which("cc"), "needs ninja and cc")
class DependencyAuditTests(unittest.TestCase):
    """The candidate reads origin files only from test/, or exported bytes."""

    def test_private_origin_header_must_match_the_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, exported, build = root / "origin", root / "exported", root / "build"
            for repo in (origin, exported):
                repo.mkdir()
                git(repo, "init", "-q")
            (origin / "test").mkdir()
            (origin / "core" / "src").mkdir(parents=True)
            (origin / "core" / "src" / "access.h").write_text("int access_value(void);\n", encoding="utf-8")
            (origin / "test" / "t.c").write_text('#include "../core/src/access.h"\nint main(void){return 0;}\n',
                                                 encoding="utf-8")
            git(origin, "add", ".")
            git(origin, "commit", "-q", "-m", "o")
            (exported / "mirror" / "core" / "src").mkdir(parents=True)
            (exported / "mirror" / "core" / "src" / "access.h").write_text("int access_value(void);\n",
                                                                          encoding="utf-8")
            git(exported, "add", ".")
            git(exported, "commit", "-q", "-m", "e")
            build.mkdir()
            (build / "build.ninja").write_text(
                "rule cc\n  command = cc -MD -MF $out.d -c $in -o $out\n  depfile = $out.d\n  deps = gcc\n"
                f"build t.o: cc {origin / 'test' / 't.c'}\n", encoding="utf-8")
            subprocess.run(["ninja", "-C", str(build)], check=True, capture_output=True)
            ocommit, ecommit = git(origin, "rev-parse", "HEAD"), git(exported, "rev-parse", "HEAD")
            self.assertEqual(lane.dependency_violations(build, origin, ocommit, exported, ecommit, "mirror/"), [])
            (exported / "mirror" / "core" / "src" / "access.h").write_text("int other(void);\n", encoding="utf-8")
            git(exported, "commit", "-q", "-am", "diverge")
            changed = git(exported, "rev-parse", "HEAD")
            violations = lane.dependency_violations(build, origin, ocommit, exported, changed, "mirror/")
            self.assertTrue(any("core/src/access.h" in v for v in violations), violations)


class SpecRuleTests(unittest.TestCase):
    roots = ("origin", "exported", "reference-build", "prefix", "catch2", "divergent-prefix")

    def test_unbound_root_is_refused(self) -> None:
        self.assertEqual(lane.bind_roots({"a": ["<origin>/test"]}, {"origin": "/o"}), {"a": ["/o/test"]})
        with self.assertRaises(lane.LaneError):
            lane.bind_roots({"a": "<prefix>/lib"}, {"origin": "/o"})

    def test_committed_specs_bind_and_justify_every_exclusion(self) -> None:
        specs = sorted((Path(__file__).resolve().parent / "specs").glob("*.json"))
        self.assertTrue(specs)
        for path in specs:
            spec = lane.bind_roots(json.loads(path.read_text(encoding="utf-8")), {r: "/x" for r in self.roots})
            self.assertTrue(spec["groups"], path.name)
            lane.justified(spec.get("exclude_sources") or {}, "exclude_sources")
            for value in json.dumps(spec).split('"'):
                self.assertFalse(value.startswith("/Users/") or value.startswith("/Volumes/"), value)

    def test_unjustified_exclusion_is_refused(self) -> None:
        with self.assertRaises(lane.LaneError):
            lane.justified({"test_x": " "}, "exclude_sources")
        self.assertEqual(lane.justified({"test_x": "why"}, "exclude_sources"), {"test_x": "why"})


if __name__ == "__main__":
    unittest.main()
