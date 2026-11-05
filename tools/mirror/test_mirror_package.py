#!/usr/bin/env python3
"""Negative controls for the landed-mirror build plan."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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


# The two declaration formats PulpViewJsPreludes.cmake has shipped: plain
# string pointers, and the zlib-compressed EmbeddedPrelude records.
LEGACY_PRELUDES_CMAKE = r"""set(PULP_JS_PRELUDES
    ${CMAKE_CURRENT_SOURCE_DIR}/js/web-compat.js
    ${CMAKE_CURRENT_SOURCE_DIR}/js/react-shim.js
)
set(_pulp_prelude_decls
    "#pragma once\n// Auto-generated from the JS prelude file list — do not edit\n\nnamespace pulp::view::preludes {\n\n")
foreach(_prelude ${PULP_JS_PRELUDES})
    string(APPEND _pulp_prelude_decls "extern const char* const ${_prelude_var};\n")
endforeach()
string(APPEND _pulp_prelude_decls "\n}  // namespace pulp::view::preludes\n")
add_custom_command(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/js/embed_js.py)
"""
LEGACY_PRELUDES_HEADER = (
    "#pragma once\n// Auto-generated from the JS prelude file list — do not edit\n\n"
    "namespace pulp::view::preludes {\n\n"
    "extern const char* const web_compat;\nextern const char* const react_shim;\n"
    "\n}  // namespace pulp::view::preludes\n"
)
COMPRESSED_PRELUDES_CMAKE = r"""set(PULP_JS_PRELUDES
    ${CMAKE_CURRENT_SOURCE_DIR}/js/web-compat.js
    ${CMAKE_CURRENT_SOURCE_DIR}/js/react-shim.js
)
set(_pulp_prelude_decls
    "#pragma once\n// Auto-generated\n\n#include <cstddef>\n\nnamespace pulp::view::preludes {\n\nstruct EmbeddedPrelude {\n    const char* file_name;\n};\n\n")
foreach(_prelude ${PULP_JS_PRELUDES})
    string(APPEND _pulp_prelude_decls "extern const EmbeddedPrelude ${_prelude_var};\n")
endforeach()
string(APPEND _pulp_prelude_decls
    "\nextern const EmbeddedPrelude* const kAll[];\nextern const std::size_t kAllCount;\n\n}  // namespace pulp::view::preludes\n")
add_custom_command(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/js/embed_js.py)
"""
COMPRESSED_PRELUDES_HEADER = (
    "#pragma once\n// Auto-generated\n\n#include <cstddef>\n\nnamespace pulp::view::preludes {\n\n"
    "struct EmbeddedPrelude {\n    const char* file_name;\n};\n\n"
    "extern const EmbeddedPrelude web_compat;\nextern const EmbeddedPrelude react_shim;\n"
    "\nextern const EmbeddedPrelude* const kAll[];\nextern const std::size_t kAllCount;\n\n"
    "}  // namespace pulp::view::preludes\n"
)
PRELUDE_HEADER_PATH = "@build/core/view/web_compat_preludes_gen.hpp"


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
            "configure": {"options": {"CMAKE_SYSTEM_NAME": "Darwin", "CMAKE_BUILD_TYPE": "Release"}},
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

    def add_prelude_header(self, cmake: str, header_sha256: str) -> None:
        """Land a prelude producer and record its generated header in the closure."""
        self.files["mirror/core/view/cmake/PulpViewJsPreludes.cmake"] = cmake.encode()
        for path, data in self.files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_bytes(data)
        self.closure["generated_inputs"].append({
            "path": PRELUDE_HEADER_PATH, "sha256": header_sha256, "producer_paths": [],
        })
        self.write_receipt()

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

    def test_compressed_prelude_header_follows_the_mirrored_producer(self) -> None:
        self.fixture.add_prelude_header(COMPRESSED_PRELUDES_CMAKE, sha(COMPRESSED_PRELUDES_HEADER.encode()))
        self.fixture.plan()
        out = self.fixture.generated / "preludes/web_compat_preludes_gen.hpp"
        self.assertEqual(out.read_text(encoding="utf-8"), COMPRESSED_PRELUDES_HEADER)

    def test_legacy_prelude_header_still_renders_for_an_older_export(self) -> None:
        self.fixture.add_prelude_header(LEGACY_PRELUDES_CMAKE, sha(LEGACY_PRELUDES_HEADER.encode()))
        self.fixture.plan()
        out = self.fixture.generated / "preludes/web_compat_preludes_gen.hpp"
        self.assertEqual(out.read_text(encoding="utf-8"), LEGACY_PRELUDES_HEADER)

    def test_prelude_header_in_the_other_format_is_refused(self) -> None:
        # The closure recorded the compressed header, but the landed producer
        # still declares string pointers: the digest must not match.
        self.fixture.add_prelude_header(LEGACY_PRELUDES_CMAKE, sha(COMPRESSED_PRELUDES_HEADER.encode()))
        self.assertRefused("does not match the closure digest")

    def test_prelude_producer_without_a_closing_declaration_is_refused(self) -> None:
        cmake = COMPRESSED_PRELUDES_CMAKE.replace("string(APPEND _pulp_prelude_decls\n", "set(_unrelated\n")
        self.fixture.add_prelude_header(cmake, sha(COMPRESSED_PRELUDES_HEADER.encode()))
        self.assertRefused("does not have exactly one leading, one per-prelude and one closing")

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

    def add_mirror_file(self, path: str, data: bytes) -> None:
        (self.fixture.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.fixture.repo / path).write_bytes(data)
        self.fixture.files[path] = data
        self.fixture.write_receipt()

    def test_per_source_options_reach_the_target_and_the_cmake(self) -> None:
        self.add_mirror_file("mirror/core/runtime/CMakeLists.txt", (
            b'add_library(pulp-runtime STATIC src/a.cpp)\n'
            b'if(APPLE)\n'
            b'    set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-fno-rtti")\n'
            b'endif()\n'))
        plan = self.fixture.plan()
        self.assertEqual(plan["source_compile_options"], {"core/runtime/src/a.cpp": ["-fno-rtti"]})
        target = plan["units"][0]["targets"][0]
        self.assertEqual(target["source_compile_options"], [{
            "source": str(self.fixture.repo.resolve() / "mirror/core/runtime/src/a.cpp"), "options": ["-fno-rtti"]}])
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        text = output.read_text()
        self.assertIn("set(VELLUM_MIRROR_TARGET_PULP_RUNTIME_OPTION_SOURCE_COUNT 1)", text)
        self.assertIn('set(VELLUM_MIRROR_TARGET_PULP_RUNTIME_OPTION_SOURCE_0_OPTIONS\n    "-fno-rtti"\n)', text)

    def test_per_source_option_for_an_uncompiled_source_is_refused(self) -> None:
        self.add_mirror_file("mirror/core/runtime/include/pulp/runtime/a.hpp", b"int a();\n")
        self.add_mirror_file("mirror/core/runtime/CMakeLists.txt", (
            b'add_library(pulp-runtime STATIC src/a.cpp)\n'
            b'set_source_files_properties(include/pulp/runtime/a.hpp PROPERTIES COMPILE_OPTIONS "-O2")\n'))
        self.assertRefused("no mirrored target compiles")

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

    def test_target_linking_another_skia_archive_is_refused(self) -> None:
        self.fixture.closure["targets"][0]["external_link_inputs"][0]["asset_sha256"] = "0" * 64
        self.fixture.write_receipt()
        self.assertRefused("links skia archive")

    def test_dependency_source_subdir_is_the_built_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text("add_subdirectory(yoga)\nadd_subdirectory(tests)\n")
            (root / "yoga").mkdir()
            (root / "yoga" / "CMakeLists.txt").write_text("add_library(yogacore STATIC x.cpp)\n")
            self.assertEqual(mp.dependency_source_dir("yoga", root), root / "yoga")
            self.assertEqual(mp.dependency_source_dir("sdl3", root), root)
            (root / "yoga" / "CMakeLists.txt").unlink()
            with self.assertRaisesRegex(mp.PlanError, "no yoga/CMakeLists.txt"):
                mp.dependency_source_dir("yoga", root)

    def test_dependency_source_subdir_must_stay_inside_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "yoga-checkout"
            root.mkdir()
            (base / "outside").mkdir()
            (base / "outside" / "CMakeLists.txt").write_text("project(outside)\n")
            (root / "yoga").symlink_to(base / "outside")
            with self.assertRaisesRegex(mp.PlanError, "leaves the fetched checkout"):
                mp.dependency_source_dir("yoga", root)
            with mock.patch.dict(mp.DEPENDENCIES["yoga"], {"source_subdir": "../outside"}):
                with self.assertRaisesRegex(mp.PlanError, "leaves the fetched checkout"):
                    mp.dependency_source_dir("yoga", root)

    def test_plan_builds_a_cmake_dependency_from_its_source_subdir(self) -> None:
        yoga = Path(self.temporary.name) / "yoga-checkout"
        (yoga / "yoga").mkdir(parents=True)
        (yoga / "CMakeLists.txt").write_text("add_subdirectory(yoga)\nadd_subdirectory(tests)\n")
        (yoga / "yoga" / "CMakeLists.txt").write_text("add_library(yogacore STATIC x.cpp)\n")
        (yoga / "LICENSE").write_text("MIT\n")
        prepare = mp.prepare_dependencies

        def with_yoga(*args, **kwargs):
            return {**prepare(*args, **kwargs), "yoga": yoga}

        with mock.patch.object(mp, "prepare_dependencies", with_yoga):
            plan = self.fixture.plan()
        self.assertEqual(plan["cmake_dependencies"]["yoga"]["dir"], str(yoga / "yoga"))
        # Pulp builds yogacore with RTTI on; the mirror build must too.
        self.assertEqual(plan["cmake_dependencies"]["yoga"]["cxx_options"], ["-frtti"])
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        self.assertIn('set(VELLUM_MIRROR_DEP_YOGA_CXX_OPTIONS\n    "-frtti"\n)', output.read_text())

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


VELLUM_SOURCE = "https://github.com/Generous-Corp/vellum.git"
GPU_RUNTIME_HEADER = "gpu-runtime/include/gpu_runtime/gpu_surface.hpp"


class InTreeGpuRuntimeTest(unittest.TestCase):
    """The mirror links Vellum's own gpu-runtime only at the interface Pulp read.

    The fixture repository is a real git history: an old commit whose header
    differs, the pinned commit, and HEAD on top with an unrelated change.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}
        repo = self.fixture.repo
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        header = repo / GPU_RUNTIME_HEADER
        header.parent.mkdir(parents=True)
        header.write_bytes(b"struct GpuSurface; // old\n")
        self.commit("old interface")
        self.old = self.head()
        self.header_bytes = b"struct GpuSurface;\n"
        header.write_bytes(self.header_bytes)
        self.commit("pinned interface")
        self.pin = self.head()
        (repo / "gpu-runtime/src").mkdir(parents=True)
        (repo / "gpu-runtime/src/impl.cpp").write_bytes(b"int impl() { return 0; }\n")
        self.commit("implementation change after the pin")
        self.use_pin(self.pin)

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.fixture.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", "gpu-runtime")
        self.git("commit", "-q", "-m", message)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def use_pin(self, pin: str, *, source: str = VELLUM_SOURCE, name: str = "vellum-gpu-runtime",
                relative: str = GPU_RUNTIME_HEADER, digest: str | None = None,
                link: str = "gpu-runtime-device") -> None:
        closure = self.fixture.closure
        closure["external_inputs"] = [row for row in closure["external_inputs"]
                                      if "gpu-runtime" not in row["path"]]
        closure["external_inputs"].append({
            "path": f"@external/{name}/{pin}/{relative}", "pin": pin, "source": source,
            "sha256": digest or sha(self.header_bytes),
        })
        target = closure["targets"][0]
        target["include_dirs"] = [d for d in target["include_dirs"] if "gpu-runtime" not in d]
        target["include_dirs"].append(f"@external/{name}/{pin}/gpu-runtime/include")
        target["link_targets"] = [link]
        self.fixture.write_receipt()

    def plan(self, definitions: list[str] | None = None):
        return mp.plan(self.fixture.repo, self.fixture.deps, self.fixture.generated, self.fixture.skia,
                       self.fixture.skia_asset,
                       {"vellum-gpu-runtime": definitions if definitions is not None else []})

    def assertRefused(self, fragment: str, definitions: list[str] | None = None) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.plan(definitions)
        self.assertIn(fragment, str(caught.exception))

    def set_definitions(self, definitions: list[str]) -> None:
        self.fixture.closure["targets"][0]["compile_definitions"] = definitions
        self.fixture.write_receipt()

    def test_matching_pin_links_the_in_tree_targets_without_fetching(self) -> None:
        plan = self.plan()
        target = plan["units"][0]["targets"][0]
        self.assertEqual(target["external_links"], ["gpu-runtime-device"])
        self.assertIn(str(self.fixture.repo.resolve() / "gpu-runtime/include"), target["include_dirs"])
        self.assertFalse(self.fixture.deps.exists() and any(self.fixture.deps.iterdir()))
        self.assertNotIn("vellum-gpu-runtime", plan["cmake_dependencies"])
        self.assertNotIn("vellum-gpu-runtime", plan["licenses"])
        self.assertEqual(plan["dependencies"]["vellum-gpu-runtime"]["pin"], self.pin)
        self.assertEqual(target["in_tree_interfaces"], ["VellumGpuRuntime::DeviceApi"])
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        self.assertIn('set(VELLUM_MIRROR_TARGET_PULP_RUNTIME_IN_TREE_INTERFACES\n    "VellumGpuRuntime::DeviceApi"\n)',
                      output.read_text())

    def test_ignored_interface_header_is_refused(self) -> None:
        (self.fixture.repo / ".gitignore").write_text("*.inc\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore rule")
        (self.fixture.repo / "gpu-runtime/include/gpu_runtime/extra.inc").write_bytes(b"// ignored\n")
        self.assertRefused("has uncommitted changes")

    def test_matching_abi_definitions_plan(self) -> None:
        self.set_definitions(["X=1", "GPU_RUNTIME_BENCHMARK=1"])
        self.plan(["GPU_RUNTIME_BENCHMARK=1"])

    def test_definition_only_the_closure_has_is_refused(self) -> None:
        self.set_definitions(["X=1", "GPU_RUNTIME_BENCHMARK=1"])
        self.assertRefused("in-tree vellum-gpu-runtime carries []", [])

    def test_definition_only_the_in_tree_build_has_is_refused(self) -> None:
        self.assertRefused("was exported with GPU_RUNTIME_* definitions []", ["GPU_RUNTIME_BENCHMARK=1"])

    def test_missing_in_tree_definitions_is_refused(self) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            mp.plan(self.fixture.repo, self.fixture.deps, self.fixture.generated, self.fixture.skia,
                    self.fixture.skia_asset)
        self.assertIn("passed no in-tree interface definitions", str(caught.exception))

    def test_unit_that_does_not_route_into_the_provider_is_refused(self) -> None:
        # Without Skia the unit's third-party links go into its own dylib,
        # which would carry a second gpu-runtime beside the provider's.
        target = self.fixture.closure["targets"][0]
        target["external_link_inputs"] = []
        target["include_dirs"] = [d for d in target["include_dirs"] if "skia" not in d]
        self.fixture.closure["external_inputs"] = [r for r in self.fixture.closure["external_inputs"]
                                                   if "skia" not in r["path"]]
        self.fixture.write_receipt()
        self.assertRefused("does not route into vellum-skia-dawn-provider")

    def test_pin_whose_interface_tree_differs_is_refused(self) -> None:
        self.use_pin(self.old, digest=sha(b"struct GpuSurface; // old\n"))
        self.assertRefused("is not the tree at HEAD")

    def test_edited_header_in_the_working_tree_is_refused(self) -> None:
        (self.fixture.repo / GPU_RUNTIME_HEADER).write_bytes(b"struct GpuSurface { int edited; };\n")
        self.assertRefused("has uncommitted changes")

    def test_untracked_interface_header_is_refused(self) -> None:
        (self.fixture.repo / "gpu-runtime/include/gpu_runtime/extra.hpp").write_bytes(b"// new\n")
        self.assertRefused("has uncommitted changes")

    def test_recorded_digest_that_differs_from_the_tree_is_refused(self) -> None:
        self.use_pin(self.pin, digest="0" * 64)
        self.assertRefused("does not match the closure digest")

    def test_pin_that_is_not_a_commit_here_is_refused(self) -> None:
        self.use_pin("f" * 40)
        self.assertRefused("is not a commit of this repository")

    def test_pin_that_is_not_an_ancestor_of_head_is_refused(self) -> None:
        self.git("checkout", "-q", "-b", "side", self.old)
        (self.fixture.repo / "gpu-runtime/side.txt").write_bytes(b"side\n")
        self.commit("side branch")
        side = self.head()
        self.git("checkout", "-q", "main")
        self.use_pin(side)
        self.assertRefused("is not an ancestor of HEAD")

    def test_other_repository_source_is_refused(self) -> None:
        self.use_pin(self.pin, source="https://github.com/someone/vellum.git")
        self.assertRefused("not this repository")

    def test_recorded_file_outside_the_subtree_is_refused(self) -> None:
        self.use_pin(self.pin, relative="runtime/include/vellum/x.hpp")
        self.assertRefused("outside gpu-runtime/")

    def test_unknown_external_is_refused(self) -> None:
        self.use_pin(self.pin, name="vellum-gpu-runtime-copy")
        self.assertRefused("has no Vellum build recipe")

    def test_unlisted_gpu_runtime_target_is_refused(self) -> None:
        self.use_pin(self.pin, link="gpu-runtime-device-api")
        self.assertRefused("neither mirrored nor a recipe target")


class PreludeHeaderTest(unittest.TestCase):
    def js_paths(self, cmake: str) -> list[str]:
        return ["js/web-compat.js", "js/react-shim.js"]

    def test_each_format_renders_its_own_declarations(self) -> None:
        for cmake, header in ((LEGACY_PRELUDES_CMAKE, LEGACY_PRELUDES_HEADER),
                              (COMPRESSED_PRELUDES_CMAKE, COMPRESSED_PRELUDES_HEADER)):
            self.assertEqual(mp.render_prelude_header(cmake, self.js_paths(cmake)), header)

    def test_unsupported_escape_is_refused(self) -> None:
        cmake = COMPRESSED_PRELUDES_CMAKE.replace("// Auto-generated", "// Auto\\qgenerated")
        with self.assertRaises(mp.PlanError) as caught:
            mp.render_prelude_header(cmake, self.js_paths(cmake))
        self.assertIn("unsupported CMake escape", str(caught.exception))

    def test_landed_mirror_renders_its_receipts_header_digest(self) -> None:
        # The renderer against the real landed producer: the header it writes
        # must hash to the digest the newest receipt's closures recorded.
        repo = HERE.parents[1]
        if not (repo / mp.INDEX_PATH).is_file():
            self.skipTest("no landed mirror export in this checkout")
        _, receipt = mp.newest_receipt(repo)
        digests = {
            row["sha256"]
            for _, closure in mp.load_closures(repo, receipt)
            for row in closure.get("generated_inputs", [])
            if row.get("path") == PRELUDE_HEADER_PATH
        }
        if not digests:
            self.skipTest("the newest export generates no prelude header")
        cmake = (repo / mp.MIRROR_DIR / "core/view/cmake/PulpViewJsPreludes.cmake").read_text(encoding="utf-8")
        js_paths = [js for js in re.findall(r"\$\{CMAKE_CURRENT_SOURCE_DIR\}/(js/[^\s)]+)", cmake)
                    if js != "js/embed_js.py"]
        header = mp.render_prelude_header(cmake, js_paths)
        self.assertEqual(digests, {sha(header.encode("utf-8"))})

    def test_quoted_quote_and_backslash_are_unescaped(self) -> None:
        self.assertEqual(mp.cmake_unquote(r'a\"b\\c\nd'), 'a"b\\c\nd')


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



class ChocV8PatchTest(unittest.TestCase):
    UTF8 = (
        "        auto len = s.Utf8Length (isolate);\n"
        "        result.resize (static_cast<std::string::size_type> (len));\n"
        "        s.WriteUtf8 (isolate, result.data(), len);"
    )

    def header(self, root: Path, *, iterate: bool) -> Path:
        path = root / mp.CHOC_V8_HEADER
        path.parent.mkdir(parents=True)
        body = '#include "choc_javascript.h"\n' + self.UTF8 + "\n"
        if iterate:
            body += mp.CHOC_V8_ITERATE_BEFORE + "\n"
        path.write_text(body, encoding="utf-8")
        return path

    def test_property_walk_is_rewritten_by_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.header(root, iterate=True)
            mp.apply_choc_v8_patch(root)
            text = path.read_text(encoding="utf-8")
            self.assertIn(mp.CHOC_V8_ITERATE_AFTER, text)
            self.assertNotIn("->Iterate (", text)
            self.assertIn("Utf8LengthV2 (isolate)", text)

    def test_header_without_the_property_walk_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.header(root, iterate=False)
            with self.assertRaises(mp.PlanError):
                mp.apply_choc_v8_patch(root)


class ChocFileWatcherPatchTest(unittest.TestCase):
    def header(self, root: Path, edits) -> Path:
        path = root / mp.CHOC_FILE_WATCHER_HEADER
        path.parent.mkdir(parents=True)
        path.write_text("start\n" + "\nmiddle\n".join(edits) + "\nend\n", encoding="utf-8")
        return path

    def test_both_edits_apply_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.header(root, [before for before, _ in mp.CHOC_FILE_WATCHER_EDITS])
            mp.apply_patch("choc", "file-watcher-lifetime", root)
            patched = path.read_text(encoding="utf-8")
            for _, after in mp.CHOC_FILE_WATCHER_EDITS:
                self.assertEqual(patched.count(after), 1)
            self.assertIn("dispatch_sync_f (dispatchQueue", patched)
            mp.apply_patch("choc", "file-watcher-lifetime", root)
            self.assertEqual(path.read_text(encoding="utf-8"), patched)

    def test_drifted_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.header(root, [mp.CHOC_FILE_WATCHER_EDITS[0][0]])
            with self.assertRaises(mp.PlanError):
                mp.apply_patch("choc", "file-watcher-lifetime", root)

    def test_choc_recipe_applies_the_patch(self) -> None:
        self.assertIn("file-watcher-lifetime", mp.DEPENDENCIES["choc"]["patches"])
        self.assertEqual(mp.PATCHED_PATHS["file-watcher-lifetime"], mp.CHOC_FILE_WATCHER_HEADER)


class SourceCompileOptionsTest(unittest.TestCase):
    """Per-source options are read from CMake under the closure's configure."""

    RELEASE = {"CMAKE_SYSTEM_NAME": "Darwin", "CMAKE_BUILD_TYPE": "Release", "PULP_SANITIZER": ""}
    EXPORTED = {"core/x/src/a.cpp", "core/x/src/b.cpp"}

    def evaluate(self, text: str, options: dict | None = None, path: str = "core/x/CMakeLists.txt") -> dict:
        return mp.source_compile_options.evaluate({path: text}, options or self.RELEASE, self.EXPORTED)

    def test_apple_branch_applies(self) -> None:
        self.assertEqual(self.evaluate(
            'if(APPLE)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-fno-rtti")\nendif()\n'),
            {"core/x/src/a.cpp": ["-fno-rtti"]})

    def test_debug_only_option_follows_the_build_type(self) -> None:
        text = ('get_property(_multi GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)\n'
                'if(NOT _multi AND CMAKE_BUILD_TYPE STREQUAL "Debug" AND CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")\n'
                '  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-O2")\nendif()\n')
        self.assertEqual(self.evaluate(text), {})
        self.assertEqual(self.evaluate(text, dict(self.RELEASE, CMAKE_BUILD_TYPE="Debug")),
                         {"core/x/src/a.cpp": ["-O2"]})

    def test_local_flag_set_in_a_branch_is_tracked(self) -> None:
        text = ('set(_ubsan OFF)\nif(PULP_SANITIZER STREQUAL "undefined")\n  set(_ubsan ON)\nendif()\n'
                'if(_ubsan)\n  set_source_files_properties(src/b.cpp PROPERTIES COMPILE_OPTIONS "-fno-sanitize=undefined")\nendif()\n')
        self.assertEqual(self.evaluate(text), {})
        self.assertEqual(self.evaluate(text, dict(self.RELEASE, PULP_SANITIZER="undefined")),
                         {"core/x/src/b.cpp": ["-fno-sanitize=undefined"]})

    def test_elseif_and_else_take_one_branch(self) -> None:
        text = ('if(WIN32)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "/W4")\n'
                'elseif(APPLE)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-Wall")\n'
                'else()\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-Wextra")\nendif()\n')
        self.assertEqual(self.evaluate(text), {"core/x/src/a.cpp": ["-Wall"]})

    def test_module_resolves_against_its_includer(self) -> None:
        text = 'set_source_files_properties(src/b.cpp PROPERTIES COMPILE_OPTIONS -fno-exceptions)\n'
        files = {"core/x/CMakeLists.txt": 'add_library(x STATIC src/b.cpp)\ninclude(cmake/Mod.cmake)\n',
                 "core/x/cmake/Mod.cmake": text}
        self.assertEqual(mp.source_compile_options.evaluate(files, self.RELEASE, self.EXPORTED, {"x"}),
                         {"core/x/src/b.cpp": ["-fno-exceptions"]})
        # Only a directory that creates an exported target is judged.
        self.assertEqual(mp.source_compile_options.evaluate(files, self.RELEASE, self.EXPORTED, {"y"}), {})

    def test_complete_tree_requires_every_target_and_include(self) -> None:
        files = {"core/x/CMakeLists.txt": 'add_library(x STATIC src/a.cpp)\ninclude(cmake/Missing.cmake)\n'}
        self.assertEqual(mp.source_compile_options.evaluate(files, self.RELEASE, self.EXPORTED, {"x"}), {})
        for targets in ({"x"}, {"x", "y"}):
            with self.subTest(targets=targets), self.assertRaises(mp.source_compile_options.SourceOptionsError):
                mp.source_compile_options.evaluate(files, self.RELEASE, self.EXPORTED, targets, complete=True)

    def test_undecidable_condition_on_an_exported_source_fails_closed(self) -> None:
        with self.assertRaises(mp.source_compile_options.SourceOptionsError):
            self.evaluate('if(TARGET foo)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n')
        # The same call on a source the export does not carry is not its business.
        self.assertEqual(self.evaluate(
            'if(TARGET foo)\n  set_source_files_properties(src/c.cpp PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n'), {})

    def assertFailsClosed(self, text: str, fragment: str) -> None:
        with self.assertRaises(mp.source_compile_options.SourceOptionsError) as caught:
            self.evaluate(text)
        self.assertIn(fragment, str(caught.exception))

    def test_variable_the_configure_does_not_record_is_undecidable(self) -> None:
        # A parent scope's variable is not in the closure's configure; CMake
        # would read the bare word as itself, which here would quietly be false.
        self.assertFailsClosed(
            'if(SOME_PARENT_VAR STREQUAL "ON")\n'
            '  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n',
            "cannot decide whether COMPILE_OPTIONS applies to core/x/src/a.cpp")

    def test_compile_definitions_on_an_unresolvable_file_fails_closed(self) -> None:
        self.assertFailsClosed(
            'set_source_files_properties(${GENERATED_SOURCE} PROPERTIES COMPILE_DEFINITIONS X=1)\n',
            "COMPILE_DEFINITIONS on a file that cannot be resolved")

    def test_any_other_property_on_an_exported_source_fails_closed(self) -> None:
        for prop, value in (("LANGUAGE", "OBJCXX"), ("HEADER_FILE_ONLY", "ON"), ("SKIP_UNITY_BUILD_INCLUSION", "ON")):
            with self.subTest(prop=prop):
                self.assertFailsClosed(
                    f'set_source_files_properties(src/a.cpp PROPERTIES {prop} {value})\n',
                    f"{prop} on exported source core/x/src/a.cpp is not reconstructed")
        # The same properties on a file the export does not carry are not its business.
        self.assertEqual(self.evaluate('set_source_files_properties(src/c.cpp PROPERTIES LANGUAGE OBJCXX)\n'), {})

    def test_unresolvable_file_and_other_properties_fail_closed(self) -> None:
        for text in ('set_source_files_properties(${UNKNOWN_DIR}/a.cpp PROPERTIES COMPILE_OPTIONS "-O1")\n',
                     'set_source_files_properties(src/a.cpp PROPERTIES COMPILE_DEFINITIONS X=1)\n',
                     'function(f)\n  set_source_files_properties(${ARGV0} PROPERTIES COMPILE_OPTIONS "-O1")\nendfunction()\nf(src/a.cpp)\n'):
            with self.subTest(text=text), self.assertRaises(mp.source_compile_options.SourceOptionsError):
                self.evaluate(text)


class SourceCompileOptionsBypassTest(unittest.TestCase):
    """The ways CMake can set a per-source property from somewhere other than
    the target's own directory, injected into a Pulp-shaped tree."""

    RELEASE = {"CMAKE_SYSTEM_NAME": "Darwin", "CMAKE_BUILD_TYPE": "Release", "PULP_SANITIZER": ""}
    EXPORTED = {"core/x/src/a.cpp", "core/x/src/b.cpp"}

    def tree(self, **overrides: str) -> dict[str, str]:
        files = {
            "CMakeLists.txt": ('list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/tools/cmake")\n'
                               "add_subdirectory(core/x)\nadd_subdirectory(test)\n"),
            "core/x/CMakeLists.txt": ("add_library(x STATIC src/a.cpp src/b.cpp)\n"
                                      "if(APPLE)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS -fno-rtti)\nendif()\n"),
            "test/CMakeLists.txt": "add_executable(t t.cpp)\n",
            "tools/cmake/Helpers.cmake": "function(noop)\nendfunction()\n",
        }
        files.update({path.replace("__", "/").replace("_DOT_", "."): text for path, text in overrides.items()})
        return files

    def evaluate(self, files: dict[str, str]) -> dict:
        return mp.source_compile_options.evaluate(files, self.RELEASE, self.EXPORTED, {"x"}, complete=True)

    def assertFailsClosed(self, files: dict[str, str], fragment: str) -> None:
        with self.assertRaises(mp.source_compile_options.SourceOptionsError) as caught:
            self.evaluate(files)
        self.assertIn(fragment, str(caught.exception))

    def test_baseline(self) -> None:
        self.assertEqual(self.evaluate(self.tree()), {"core/x/src/a.cpp": ["-fno-rtti"]})

    def test_directory_from_an_unjudged_scope_fails_closed(self) -> None:
        for clause in ("DIRECTORY ${CMAKE_SOURCE_DIR}/core/x", "TARGET_DIRECTORY x", "DIRECTORY ${SOMEWHERE}"):
            with self.subTest(clause=clause):
                self.assertFailsClosed(self.tree(**{"test/CMakeLists.txt": (
                    "add_executable(t t.cpp)\n"
                    f"set_source_files_properties(${{CMAKE_SOURCE_DIR}}/core/x/src/b.cpp {clause} "
                    "PROPERTIES COMPILE_OPTIONS -O0)\n")}), "per-source property")
        # In the judged scope itself it is not attributed to that scope either.
        self.assertFailsClosed(self.tree(**{"core/x/CMakeLists.txt": (
            "add_library(x STATIC src/a.cpp src/b.cpp)\n"
            "set_source_files_properties(src/b.cpp DIRECTORY ${CMAKE_SOURCE_DIR}/core/x PROPERTIES COMPILE_OPTIONS -O0)\n")}),
            "per-source property for directory core/x")

    def test_transitive_function_call_fails_closed(self) -> None:
        helpers = ("function(inner f)\n  set_source_files_properties(${f} PROPERTIES COMPILE_OPTIONS -O0)\nendfunction()\n"
                   "function(outer f)\n  inner(${f})\nendfunction()\n")
        self.assertFailsClosed(self.tree(**{
            "tools/cmake/Helpers.cmake": helpers,
            "core/x/CMakeLists.txt": "include(Helpers)\nadd_library(x STATIC src/a.cpp src/b.cpp)\nouter(src/b.cpp)\n"}),
            "outer() can set per-source properties")

    def test_include_inside_a_function_fails_closed(self) -> None:
        self.assertFailsClosed(self.tree(**{
            "tools/cmake/Helpers.cmake": "function(loader)\n  include(${CMAKE_CURRENT_LIST_DIR}/Opts.cmake)\nendfunction()\n",
            "tools/cmake/Opts.cmake": "set_source_files_properties(src/b.cpp PROPERTIES COMPILE_OPTIONS -O0)\n",
            "core/x/CMakeLists.txt": "include(Helpers)\nadd_library(x STATIC src/a.cpp src/b.cpp)\nloader()\n"}),
            "loader() can set per-source properties")

    def test_cmake_language_fails_closed(self) -> None:
        for call in ("cmake_language(CALL noop)", 'cmake_language(EVAL CODE "noop()")'):
            with self.subTest(call=call):
                self.assertFailsClosed(self.tree(**{
                    "core/x/CMakeLists.txt": f"include(Helpers)\nadd_library(x STATIC src/a.cpp src/b.cpp)\n{call}\n"}),
                    "cmake_language(")

    def test_bare_include_follows_the_scope_module_path(self) -> None:
        opts = "set_source_files_properties(src/b.cpp PROPERTIES COMPILE_OPTIONS -O0)\n"
        appended = self.tree(**{
            "core/x/cmake/Opts_DOT_cmake": opts,
            "tools/cmake/Opts_DOT_cmake": "",
            "core/x/CMakeLists.txt": ('add_library(x STATIC src/a.cpp src/b.cpp)\n'
                                      'list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")\ninclude(Opts)\n')})
        self.assertEqual(self.evaluate(appended), {"core/x/src/b.cpp": ["-O0"]})
        # The same module off every known module path is not a CMake-shipped one.
        self.assertFailsClosed(self.tree(**{
            "core/x/cmake/Opts_DOT_cmake": opts,
            "core/x/CMakeLists.txt": "add_library(x STATIC src/a.cpp src/b.cpp)\ninclude(Opts)\n"}),
            "finds no module on the known CMAKE_MODULE_PATH")


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
            (root / "planted.c").write_text("int planted;\n")
            self.assertFalse(mp.reusable_checkout(root, pin, {"patched.h"}))
            (root / "planted.c").unlink()
            (root / ".gitignore").write_text("*.o\n")
            git("add", ".gitignore")
            git("commit", "-q", "-m", "ignore")
            pin = git("rev-parse", "HEAD")
            (root / "patched.h").write_text("patched\n")
            self.assertTrue(mp.reusable_checkout(root, pin, {"patched.h"}))
            (root / "planted.o").write_text("x\n")
            self.assertFalse(mp.reusable_checkout(root, pin, {"patched.h"}))
            (root / "planted.o").unlink()
            (root / "a.h").write_text("tampered\n")
            self.assertFalse(mp.reusable_checkout(root, pin, {"patched.h"}))


if __name__ == "__main__":
    unittest.main()
