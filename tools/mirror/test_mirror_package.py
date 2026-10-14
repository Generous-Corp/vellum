#!/usr/bin/env python3
"""Negative controls for the landed-mirror build plan."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mirror_package", HERE / "mirror_package.py")
assert spec and spec.loader
mp = importlib.util.module_from_spec(spec)
sys.modules["mirror_package"] = mp
spec.loader.exec_module(mp)

PULP_SHA = "a" * 40
EXPORT_ID = "20990101-aaaaaaaaaaaa"
TEMPLATE = (
    'inline constexpr std::string_view kBuildType = "@PULP_BUILD_TYPE@";\n'
    'inline constexpr std::string_view kBuildIso8601 = "@PULP_BUILD_ISO8601@";\n'
    'inline constexpr std::string_view kGitSha = "@PULP_GIT_SHA@";\n'
    'inline constexpr bool kGitDirty = @PULP_GIT_DIRTY@;\n'
    'inline constexpr std::string_view kSdkVersion = "@PULP_VERSION@";\n'
    'inline constexpr std::string_view kStampLabel = "@PULP_BUILD_STAMP@";\n'
)
CANVAS_CMAKE = (
    "pulp_add_binary_data(pulp-bundled-fonts\n"
    "    SOURCES\n        ${PULP_ROOT_DIR}/external/fonts/A.ttf\n"
    "    NAMESPACE pulp_bundled_fonts\n)\n"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    """A minimal landed export: one unit, one target, Skia headers, build info."""

    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.skia = root / "skia"
        self.deps = root / "deps"
        self.generated = root / "generated"
        self.skia_asset = "c" * 64
        self.files = {
            "mirror/core/runtime/src/a.cpp": b"int a() { return 1; }\n",
            "mirror/core/runtime/include/pulp/runtime/a.hpp": b"int a();\n",
            "mirror/core/runtime/include/pulp/runtime/build_info.hpp.in": TEMPLATE.encode(),
            "mirror/core/canvas/CMakeLists.txt": CANVAS_CMAKE.encode(),
        }
        for path, data in self.files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_bytes(data)
        header = b"// skia header\n"
        (self.skia / "build/include/core").mkdir(parents=True)
        (self.skia / "build/include/core/SkCanvas.h").write_bytes(header)
        rendered = mp.render_build_info(TEMPLATE, {"pulp_sha": PULP_SHA}, "1.2.3", "Release",
                                        mp.BUILD_TIMESTAMP_PLACEHOLDER)
        self.closure = {
            "schema": mp.CLOSURE_SCHEMA,
            "compile_definitions": ["X=1"],
            "external_inputs": [{
                "path": f"@external/skia/{'b' * 40}/build/include/core/SkCanvas.h",
                "pin": "b" * 40, "source": "https://skia.googlesource.com/skia.git",
                "asset_sha256": "c" * 64, "sha256": sha(header),
            }],
            "generated_inputs": [{
                "path": "@build/core/runtime/generated/Release/pulp/runtime/build_info.hpp",
                "sha256": sha(rendered.encode()),
                "normalization": {"build_timestamp": mp.BUILD_TIMESTAMP_PLACEHOLDER},
                "producer_paths": [],
            }],
            "targets": [{
                "name": "pulp-runtime", "type": "STATIC_LIBRARY",
                "sources": ["core/runtime/src/a.cpp"],
                "include_dirs": ["core/runtime/include", "@build/core/runtime/generated/Release",
                                 f"@external/skia/{'b' * 40}/build/include"],
                "compile_definitions": ["X=1"], "link_targets": [],
                "external_link_inputs": [{"dependency": "skia", "pin": "b" * 40,
                                          "source": "https://skia.googlesource.com/skia.git",
                                          "asset_sha256": "c" * 64}],
            }],
        }
        self.write_receipt()

    def write_receipt(self) -> None:
        closure_path = self.repo / f"provenance/mirror/closures/{EXPORT_ID}/foundation.json"
        closure_path.parent.mkdir(parents=True, exist_ok=True)
        closure_path.write_text(json.dumps(self.closure), encoding="utf-8")
        receipt = {
            "schema": mp.RECEIPT_SCHEMA, "export_id": EXPORT_ID,
            "pulp_sha": PULP_SHA, "mirror_sha": "d" * 40, "mirror_tree": "e" * 40,
            "created_at": "2099-01-01T00:00:00Z",
            "rename": {"from_prefix": "", "to_prefix": "mirror/"},
            "files": [{"mirror_path": path, "blob": mp.git_blob_sha(data)}
                      for path, data in self.files.items()],
            "units": [{
                "name": "foundation",
                "closure_manifest": f"provenance/mirror/closures/{EXPORT_ID}/foundation.json",
                "closure_sha256": mp.sha256_file(closure_path),
                "configure": {"identity": {"system": "Darwin", "processor": "arm64", "skia": True}},
            }],
        }
        receipt_path = self.repo / f"provenance/mirror/receipts/{EXPORT_ID}.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        (self.repo / mp.INDEX_PATH).write_text(json.dumps({
            "schema": mp.INDEX_SCHEMA,
            "receipts": [f"provenance/mirror/receipts/{EXPORT_ID}.json"],
        }), encoding="utf-8")

    def plan(self) -> dict:
        return mp.plan(self.repo, self.deps, self.generated, self.skia, self.skia_asset)


class MirrorPackagePlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def assertRefused(self, fragment: str) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn(fragment, str(caught.exception))

    def test_landed_export_plans_and_renders_its_origin(self) -> None:
        plan = self.fixture.plan()
        self.assertEqual(plan["export_id"], EXPORT_ID)
        self.assertEqual(plan["mirror_files_verified"], len(self.fixture.files))
        target = plan["units"][0]["targets"][0]
        self.assertEqual(target["name"], "pulp-runtime")
        self.assertTrue(target["skia"])
        self.assertEqual(target["include_dirs"][2], str(self.fixture.skia / "build/include"))
        header = (self.fixture.generated / "build-info/pulp/runtime/build_info.hpp").read_text()
        self.assertIn(PULP_SHA, header)
        # The export time, not the configure time: two builds agree.
        self.assertIn("2099-01-01T00:00:00Z", header)
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        text = output.read_text()
        self.assertIn("VELLUM_MIRROR_TARGET_PULP_RUNTIME_SOURCES", text)
        self.assertIn(f'set(VELLUM_MIRROR_ORIGIN_SHA "{PULP_SHA}")', text)

    def test_edited_mirror_file_is_refused(self) -> None:
        (self.fixture.repo / "mirror/core/runtime/src/a.cpp").write_bytes(b"int a() { return 2; }\n")
        self.assertRefused("differs from the landed mirror blob")

    def test_unrecorded_mirror_file_is_refused(self) -> None:
        (self.fixture.repo / "mirror/core/runtime/src/b.cpp").write_bytes(b"int b();\n")
        self.assertRefused("does not list")

    def test_missing_mirror_file_is_refused(self) -> None:
        (self.fixture.repo / "mirror/core/runtime/include/pulp/runtime/a.hpp").unlink()
        self.assertRefused("is missing files")

    def test_closure_that_does_not_match_the_receipt_is_refused(self) -> None:
        path = self.fixture.repo / f"provenance/mirror/closures/{EXPORT_ID}/foundation.json"
        path.write_text(path.read_text() + " ")
        self.assertRefused("does not match the receipt digest")

    def test_pinned_input_with_other_bytes_is_refused(self) -> None:
        (self.fixture.skia / "build/include/core/SkCanvas.h").write_bytes(b"// another header\n")
        self.assertRefused("does not match the closure digest")

    def test_closure_against_another_skia_archive_is_refused(self) -> None:
        self.fixture.skia_asset = "0" * 64
        self.assertRefused("but Vellum locks")

    def test_dependency_without_a_recipe_is_refused(self) -> None:
        self.fixture.closure["external_inputs"][0]["path"] = f"@external/unknown/{'b' * 40}/x.h"
        self.fixture.write_receipt()
        self.assertRefused("has no Vellum build recipe")

    def test_wrong_build_info_input_is_refused(self) -> None:
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "9.9.9"}
        self.assertRefused("does not match the closure digest")

    def test_generated_input_without_a_producer_is_refused(self) -> None:
        self.fixture.closure["generated_inputs"].append({
            "path": "@build/core/runtime/unknown.hpp", "sha256": "f" * 64, "producer_paths": []})
        self.fixture.write_receipt()
        self.assertRefused("has no Vellum producer")

    def test_unknown_link_target_is_refused(self) -> None:
        self.fixture.closure["targets"][0]["link_targets"] = ["pulp-unmirrored"]
        self.fixture.write_receipt()
        self.assertRefused("neither mirrored nor a recipe target")

    def test_unmapped_build_include_is_refused(self) -> None:
        self.fixture.closure["targets"][0]["include_dirs"].append("@build/somewhere")
        self.fixture.write_receipt()
        self.assertRefused("unmapped build-tree include")

    def test_receipt_for_another_platform_is_refused(self) -> None:
        receipt_path = self.fixture.repo / f"provenance/mirror/receipts/{EXPORT_ID}.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["units"][0]["configure"]["identity"]["skia"] = False
        receipt_path.write_text(json.dumps(receipt))
        self.assertRefused("Darwin arm64 Skia")

    def test_empty_index_is_refused(self) -> None:
        (self.fixture.repo / mp.INDEX_PATH).write_text(json.dumps({"schema": mp.INDEX_SCHEMA, "receipts": []}))
        self.assertRefused("no landed export receipt")


class ChocPatchTest(unittest.TestCase):
    def test_patch_applies_once_and_refuses_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            header = root / mp.CHOC_JSON_HEADER
            header.parent.mkdir(parents=True)
            header.write_text("prefix\n" + mp.CHOC_JSON_BEFORE + "\nsuffix\n", encoding="utf-8")
            mp.apply_patch("choc", "json-positive-exponent", root)
            patched = header.read_text(encoding="utf-8")
            self.assertIn(mp.CHOC_JSON_AFTER, patched)
            mp.apply_patch("choc", "json-positive-exponent", root)
            self.assertEqual(header.read_text(encoding="utf-8"), patched)
            header.write_text("something else\n", encoding="utf-8")
            with self.assertRaises(mp.PlanError):
                mp.apply_patch("choc", "json-positive-exponent", root)



class CachedCheckoutTest(unittest.TestCase):
    def test_cache_is_reused_only_at_the_pin_without_foreign_edits(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args: str) -> str:
                return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                      cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            (root / "a.h").write_text("a\n")
            (root / "patched.h").write_text("p\n")
            git("add", ".")
            git("commit", "-q", "-m", "x")
            pin = git("rev-parse", "HEAD")
            self.assertTrue(mp.reusable_checkout(root, pin, set()))
            self.assertFalse(mp.reusable_checkout(root, "0" * 40, set()))
            (root / "patched.h").write_text("patched\n")
            self.assertTrue(mp.reusable_checkout(root, pin, {"patched.h"}))
            self.assertFalse(mp.reusable_checkout(root, pin, set()))
            (root / "a.h").write_text("tampered\n")
            self.assertFalse(mp.reusable_checkout(root, pin, {"patched.h"}))


if __name__ == "__main__":
    unittest.main()
