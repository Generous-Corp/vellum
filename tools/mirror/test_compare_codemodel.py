#!/usr/bin/env python3
"""Negative controls for the per-source codemodel comparison."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_codemodel", HERE / "compare_codemodel.py")
assert spec and spec.loader
cc = importlib.util.module_from_spec(spec)
sys.modules["compare_codemodel"] = cc
spec.loader.exec_module(cc)

BASE = [{"fragment": "-O3 -DNDEBUG -std=c++20"}, {"fragment": "-Wall"}]


def group(language: str, sources: list[int], extra: list[str] = (), defines: list[str] = ()) -> dict:
    return {"language": language, "sourceIndexes": sources,
            "compileCommandFragments": BASE + [{"fragment": f} for f in extra],
            "defines": [{"define": d} for d in defines], "includes": []}


class Builds:
    """A Vellum landing and two File API replies for target `t`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.vellum = root / "vellum"
        receipt = {"pulp_sha": "a" * 40, "files": [{"pulp_path": p} for p in ("src/a.cpp", "src/b.cpp", "inc/a.h")],
                   "units": [{"closure_manifest": "closure.json"}]}
        (self.vellum / "provenance/mirror/receipts").mkdir(parents=True)
        (self.vellum / "provenance/mirror/receipts/r.json").write_text(json.dumps(receipt))
        (self.vellum / "provenance/mirror/receipts/index.json").write_text(
            json.dumps({"receipts": ["provenance/mirror/receipts/r.json"]}))
        (self.vellum / "closure.json").write_text(json.dumps({"targets": [{"name": "t"}]}))

    def build(self, name: str, source: str, prefix: str, groups: list[dict], sources: list[dict]) -> Path:
        build = self.root / name
        reply = build / ".cmake/api/v1/reply"
        reply.mkdir(parents=True)
        (reply / "target-t.json").write_text(json.dumps({"name": "t", "compileGroups": groups, "sources": sources}))
        (reply / "codemodel-v2-x.json").write_text(json.dumps({
            "paths": {"source": source, "build": str(build)},
            "configurations": [{"name": "Release", "targets": [{"name": "t", "jsonFile": "target-t.json"}]}]}))
        (reply / "index-1.json").write_text(json.dumps(
            {"reply": {"codemodel-v2": {"kind": "codemodel", "jsonFile": "codemodel-v2-x.json"}}}))
        return build

    def pair(self, pulp_b: dict, vellum_b: dict) -> tuple[Path, Path]:
        def sources(prefix: str, special: dict) -> list[dict]:
            return [{"path": f"{prefix}src/a.cpp", "compileGroupIndex": 0},
                    {"path": f"{prefix}src/b.cpp", "compileGroupIndex": 1 if special else 0},
                    {"path": f"{prefix}inc/a.h"}]
        pulp_groups = [group("CXX", [0, 1])] + ([group(**pulp_b)] if pulp_b else [])
        vellum_groups = [group("CXX", [0, 1])] + ([group(**vellum_b)] if vellum_b else [])
        pulp = self.build("pulp", "/p", "", pulp_groups, sources("", pulp_b))
        vellum = self.build("vellum-build", "/v", "mirror/", vellum_groups, sources("mirror/", vellum_b))
        return pulp, vellum


class CompareCodemodelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.builds = Builds(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def compare(self, pulp_b: dict, vellum_b: dict) -> dict:
        pulp, vellum = self.builds.pair(pulp_b, vellum_b)
        return cc.compare(pulp, vellum, self.builds.vellum)

    def test_same_source_level_option_matches(self) -> None:
        report = self.compare({"language": "CXX", "sources": [1], "extra": ["-fno-rtti"]},
                              {"language": "CXX", "sources": [1], "extra": ["-fno-rtti"]})
        self.assertEqual(report["status"], "match")
        self.assertEqual(report["compiled_exported_sources"], 2)
        self.assertEqual(list(report["pulp_source_level_groups"]), ["src/b.cpp"])

    def test_missing_option_define_or_language_mismatches(self) -> None:
        for pulp_b, vellum_b in (
            ({"language": "CXX", "sources": [1], "extra": ["-fno-rtti"]}, {}),
            ({"language": "CXX", "sources": [1], "defines": ["X=1"]}, {}),
            ({"language": "OBJCXX", "sources": [1]}, {}),
            ({}, {"language": "CXX", "sources": [1], "extra": ["-O0"]}),
        ):
            with self.subTest(pulp=pulp_b, vellum=vellum_b):
                report = self.compare(pulp_b, vellum_b)
                self.assertEqual(report["status"], "mismatch")
                self.assertEqual([m["source"] for m in report["mismatches"]], ["src/b.cpp"])
            self.tearDown()
            self.setUp()

    def test_target_level_flags_and_paths_are_not_compared(self) -> None:
        pulp, vellum = self.builds.pair({}, {})
        target = vellum / ".cmake/api/v1/reply/target-t.json"
        data = json.loads(target.read_text())
        data["compileGroups"][0]["compileCommandFragments"].append({"fragment": "-ffile-prefix-map=/v=vellum"})
        target.write_text(json.dumps(data))
        self.assertEqual(cc.compare(pulp, vellum, self.builds.vellum)["status"], "match")

    def test_a_missing_reply_is_unusable(self) -> None:
        with self.assertRaises(cc.Unusable):
            cc.compare(self.builds.root / "nowhere", self.builds.root / "nowhere", self.builds.vellum)


if __name__ == "__main__":
    unittest.main()
