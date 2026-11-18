#!/usr/bin/env python3
"""Negative controls for the landed-mirror build plan."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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

    def add_mirror_file(self, path: str, data: bytes) -> None:
        self.files[path] = data
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_bytes(data)

    def remove_mirror_file(self, path: str) -> None:
        del self.files[path]
        (self.repo / path).unlink()

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

    def test_recipe_link_target_no_closure_pins_is_refused(self) -> None:
        # yaml-cpp has a recipe, but no closure pins it, so there is nothing to build it from.
        self.fixture.closure["targets"][0]["link_targets"] = ["yaml-cpp"]
        self.fixture.write_receipt()
        self.assertRefused("links yaml-cpp, but no closure pins yaml-cpp")

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
MIRRORED_CHOKE_HEADER = "mirror/core/runtime/include/pulp/runtime/gpu_fwd.hpp"
MIRRORED_CHOKE_BYTES = b"#pragma once\nnamespace gpu_runtime { class GpuSurface; }\n"
GPU_RUNTIME_BUILD_INCLUDE = "@build/_deps/vellum_gpu_runtime-src/gpu-runtime/include"


class InTreeGpuRuntimeTest(unittest.TestCase):
    """The mirror links Vellum's own gpu-runtime only at the interface Pulp read.

    The fixture repository is a real git history: an old commit whose header
    differs, the pinned commit, and HEAD on top with an unrelated change.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))
        # A mirrored choke header: the in-tree gpu-runtime makes the GPU guard mandatory.
        self.fixture.add_mirror_file(MIRRORED_CHOKE_HEADER, MIRRORED_CHOKE_BYTES)
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

    def test_mirrored_choke_header_is_guarded_in_the_plan(self) -> None:
        plan = self.plan()
        headers = plan["gpu_guard"]["headers"]
        self.assertEqual([h["relative"] for h in headers], ["pulp/runtime/gpu_fwd.hpp"])
        copy = Path(headers[0]["copy"]).read_text()
        self.assertEqual(copy, "#pragma once\n#include <vellum/mirror/gpu_surface_unsupported.hpp>\n"
                               "namespace gpu_runtime { class GpuSurface; }\n")
        # The mirrored file itself is untouched.
        self.assertEqual((self.fixture.repo / MIRRORED_CHOKE_HEADER).read_bytes(), MIRRORED_CHOKE_BYTES)
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        text = output.read_text()
        self.assertIn("set(VELLUM_MIRROR_GPU_GUARD_COUNT 1)", text)
        # cmake_quote escapes the backslash and the dollar; CMake reads /pulp/runtime/gpu_fwd\\.hpp$.
        self.assertIn('set(VELLUM_MIRROR_GPU_GUARD_0_EXCLUDE "/pulp/runtime/gpu_fwd\\\\.hpp\\$")', text)

    def test_pinned_gpu_runtime_with_no_header_to_guard_is_refused(self) -> None:
        self.fixture.remove_mirror_file(MIRRORED_CHOKE_HEADER)
        self.fixture.write_receipt()
        self.assertRefused("no mirrored header was found to guard")

    def test_unlisted_gpu_runtime_target_is_refused(self) -> None:
        # The CMake file API never reports the header-only interface target
        # as a link; one that appears is unexplained and refused.
        self.use_pin(self.pin, link="gpu-runtime-device-api")
        self.assertRefused("neither mirrored nor a recipe target")

    def header_only_without_skia(self) -> None:
        # The shape Pulp's closures record for every target that reaches
        # pulp-platform: gpu-runtime's include directory under the build
        # tree's FetchContent spelling, and no gpu-runtime link.
        self.use_pin(self.pin)
        target = self.fixture.closure["targets"][0]
        target["link_targets"] = []
        target["include_dirs"] = [d for d in target["include_dirs"] if "gpu-runtime" not in d]
        target["include_dirs"].append(GPU_RUNTIME_BUILD_INCLUDE)
        target["external_link_inputs"] = []
        target["include_dirs"] = [d for d in target["include_dirs"] if "skia" not in d]
        self.fixture.closure["external_inputs"] = [r for r in self.fixture.closure["external_inputs"]
                                                   if "skia" not in r["path"]]
        self.fixture.write_receipt()

    def test_header_only_use_from_a_unit_without_skia_plans(self) -> None:
        self.header_only_without_skia()
        target = self.plan()["units"][0]["targets"][0]
        self.assertEqual(target["external_links"], [])
        self.assertEqual(target["in_tree_interfaces"], ["VellumGpuRuntime::DeviceApi"])
        self.assertIn(str(self.fixture.repo.resolve() / "gpu-runtime/include"), target["include_dirs"])

    def test_header_only_use_is_held_to_the_definitions_comparison(self) -> None:
        self.header_only_without_skia()
        self.assertRefused("was exported with GPU_RUNTIME_* definitions []", ["GPU_RUNTIME_BENCHMARK=1"])
        self.set_definitions(["GPU_RUNTIME_BENCHMARK=1"])
        self.assertRefused("in-tree vellum-gpu-runtime carries []", [])
        self.plan(["GPU_RUNTIME_BENCHMARK=1"])

    def test_header_only_use_with_no_pinning_closure_is_refused(self) -> None:
        self.header_only_without_skia()
        self.fixture.closure["external_inputs"] = [r for r in self.fixture.closure["external_inputs"]
                                                   if "gpu-runtime" not in r["path"]]
        self.fixture.write_receipt()
        self.assertRefused("names vellum-gpu-runtime, but no closure pins it")

    def test_header_only_use_of_an_edited_build_tree_header_is_refused(self) -> None:
        # The closure records the digest of each gpu-runtime header Pulp read
        # from its FetchContent tree; a header edited there differs from the
        # in-tree file at the pin.
        self.header_only_without_skia()
        row = next(r for r in self.fixture.closure["external_inputs"] if "gpu-runtime" in r["path"])
        row["sha256"] = sha(b"struct GpuSurface { int edited_in_the_build_tree; };\n")
        self.fixture.write_receipt()
        self.assertRefused("does not match the closure digest")

    def test_header_only_use_of_an_extra_build_tree_header_is_refused(self) -> None:
        self.header_only_without_skia()
        self.fixture.closure["external_inputs"].append({
            "path": f"@external/vellum-gpu-runtime/{self.pin}/gpu-runtime/include/gpu_runtime/extra.hpp",
            "pin": self.pin, "source": VELLUM_SOURCE, "sha256": sha(b"// only in the build tree\n"),
        })
        self.fixture.write_receipt()
        self.assertRefused("has no gpu-runtime/include/gpu_runtime/extra.hpp")

    def add_include(self, include: str) -> None:
        self.fixture.closure["targets"][0]["include_dirs"].append(include)
        self.fixture.write_receipt()

    def test_pinned_interface_include_still_maps(self) -> None:
        self.add_include(f"@external/vellum-gpu-runtime/{self.pin}/gpu-runtime/include/gpu_runtime")
        target = self.plan()["units"][0]["targets"][0]
        self.assertIn(str(self.fixture.repo.resolve() / "gpu-runtime/include/gpu_runtime"), target["include_dirs"])

    def test_include_at_a_root_other_than_the_pin_is_refused(self) -> None:
        # Would otherwise map to native Vellum headers.
        self.add_include("@external/vellum-gpu-runtime/NOT-THE-PIN/runtime/include")
        self.assertRefused("at NOT-THE-PIN, not its pin")

    def test_include_outside_the_interface_tree_is_refused(self) -> None:
        # gpu-runtime/src is in this repository but not bound to the pin.
        self.add_include(f"@external/vellum-gpu-runtime/{self.pin}/gpu-runtime/src")
        self.assertRefused("outside vellum-gpu-runtime's interface tree gpu-runtime/include")

    def test_include_that_climbs_out_is_refused(self) -> None:
        for rel in ("gpu-runtime/include/../../../etc", "gpu-runtime/include//gpu_runtime",
                    "gpu-runtime/include/./gpu_runtime"):
            with self.subTest(rel=rel):
                target = self.fixture.closure["targets"][0]
                target["include_dirs"] = [d for d in target["include_dirs"] if "NOT" not in d and rel not in d]
                self.add_include(f"@external/vellum-gpu-runtime/{self.pin}/{rel}")
                self.assertRefused("is not confined to its dependency")
                target["include_dirs"].remove(f"@external/vellum-gpu-runtime/{self.pin}/{rel}")
                self.fixture.write_receipt()

    def test_static_link_from_a_unit_without_skia_is_still_refused(self) -> None:
        self.header_only_without_skia()
        self.fixture.closure["targets"][0]["link_targets"] = ["gpu-runtime-device"]
        self.fixture.write_receipt()
        self.assertRefused("does not route into vellum-skia-dawn-provider")


class ExternalIncludeConfinementTest(unittest.TestCase):
    """The generic @external include branch maps only the pinned root, inside it."""

    PIN = "1" * 40

    def map(self, path: str) -> str:
        deps = {"highway": {"pin": self.PIN, "root": self.PIN, "files": {}}}
        return mp.map_include(path, Path("/repo"), {"highway": Path("/deps/highway")}, {}, "B", deps)

    def test_pinned_root_maps(self) -> None:
        self.assertEqual(self.map(f"@external/highway/{self.PIN}"), "/deps/highway")
        self.assertEqual(self.map(f"@external/highway/{self.PIN}/hwy"), "/deps/highway/hwy")

    def test_other_root_is_refused(self) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.map("@external/highway/" + "2" * 40 + "/hwy")
        self.assertIn("is not at the root the closures pin", str(caught.exception))

    def test_climbing_or_empty_segment_is_refused(self) -> None:
        for rel in ("../../etc", "hwy/../..", "hwy//x", "./hwy"):
            with self.subTest(rel=rel), self.assertRaises(mp.PlanError) as caught:
                self.map(f"@external/highway/{self.PIN}/{rel}")
            self.assertIn("is not confined to its dependency", str(caught.exception))


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
        cmake = mp.mirrored(repo, "core/view/cmake/PulpViewJsPreludes.cmake").read_text(encoding="utf-8")
        js_paths = [js for js in re.findall(r"\$\{CMAKE_CURRENT_SOURCE_DIR\}/(js/[^\s)]+)", cmake)
                    if js != "js/embed_js.py"]
        header = mp.render_prelude_header(cmake, js_paths)
        # Once transferred, the renamed producer renders the renamed header,
        # which matches the closure's digest of Pulp's through the inverse.
        self.assertEqual(len(digests), 1)
        self.assertTrue(mp.generated_matches(repo, header.encode("utf-8"), next(iter(digests))))

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


class ChocV8ModuleResolutionPatchTest(unittest.TestCase):
    def header(self, root: Path, edits) -> Path:
        path = root / mp.CHOC_V8_HEADER
        path.parent.mkdir(parents=True)
        path.write_text("start\n" + "\nmiddle\n".join(edits) + "\nend\n", encoding="utf-8")
        return path

    def test_edits_apply_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.header(root, [before for before, _ in mp.CHOC_V8_MODULE_EDITS])
            mp.apply_patch("choc", "v8-module-resolution", root)
            patched = path.read_text(encoding="utf-8")
            self.assertIn("Cannot find module '", patched)
            self.assertIn("if (message.IsEmpty())", patched)
            mp.apply_patch("choc", "v8-module-resolution", root)
            self.assertEqual(path.read_text(encoding="utf-8"), patched)

    def test_drifted_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.header(root, [mp.CHOC_V8_MODULE_EDITS[0][0]])
            with self.assertRaises(mp.PlanError):
                mp.apply_patch("choc", "v8-module-resolution", root)

    def test_recipe_runs_it_after_the_closure_digest_check(self) -> None:
        self.assertIn("v8-module-resolution", mp.DEPENDENCIES["choc"]["post_patches"])
        self.assertNotIn("v8-module-resolution", mp.DEPENDENCIES["choc"]["patches"])
        self.assertEqual(mp.PATCHED_PATHS["v8-module-resolution"], mp.CHOC_V8_HEADER)

    def test_applies_to_the_pinned_choc_header(self) -> None:
        # VELLUM_TEST_CHOC_ROOT names a checkout of the pinned choc; the
        # pristine header is read from git so earlier patches do not matter.
        source = os.environ.get("VELLUM_TEST_CHOC_ROOT")
        if not source:
            self.skipTest("VELLUM_TEST_CHOC_ROOT is not set")
        shown = subprocess.run(["git", "show", f"HEAD:{mp.CHOC_V8_HEADER}"], cwd=source,
                               capture_output=True, text=True, check=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / mp.CHOC_V8_HEADER
            path.parent.mkdir(parents=True)
            path.write_text(shown.stdout, encoding="utf-8")
            mp.apply_choc_v8_patch(root)
            mp.apply_patch("choc", "v8-module-resolution", root)
            once = path.read_text(encoding="utf-8")
            mp.apply_patch("choc", "v8-module-resolution", root)
            self.assertEqual(path.read_text(encoding="utf-8"), once)
            self.assertIn("Cannot find module '", once)


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

    def test_each_target_system_sets_its_own_platform_variables(self) -> None:
        text = ('if(APPLE)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-fobjc-arc")\n'
                'elseif(UNIX)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-fPIC")\nendif()\n')
        linux = dict(self.RELEASE, CMAKE_SYSTEM_NAME="Linux")
        self.assertEqual(self.evaluate(text), {"core/x/src/a.cpp": ["-fobjc-arc"]})
        self.assertEqual(self.evaluate(text, linux), {"core/x/src/a.cpp": ["-fPIC"]})
        self.assertEqual(self.evaluate('if(WIN32)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "/W4")\nendif()\n',
                                       dict(self.RELEASE, CMAKE_SYSTEM_NAME="Windows")), {"core/x/src/a.cpp": ["/W4"]})
        # Off Darwin the compiler is not recorded, so a condition on it fails closed.
        with self.assertRaises(mp.source_compile_options.SourceOptionsError):
            self.evaluate('if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")\n'
                          '  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n', linux)
        with self.assertRaises(mp.source_compile_options.SourceOptionsError):
            self.evaluate(text, dict(self.RELEASE, CMAKE_SYSTEM_NAME="Haiku"))

    # The compiler facts Pulp's exporter records (vellum_closure.py
    # cxx_compiler_options): ids copied from CMakeCXXCompiler.cmake, the
    # simulate id "" when there is none, and MSVC "1" when either id is MSVC,
    # else "0". A closure with no compiler file records none of the three.
    COMPILERS = {
        "cl": {"CMAKE_CXX_COMPILER_ID": "MSVC", "CMAKE_CXX_SIMULATE_ID": "", "MSVC": "1"},
        "clang-cl": {"CMAKE_CXX_COMPILER_ID": "Clang", "CMAKE_CXX_SIMULATE_ID": "MSVC", "MSVC": "1"},
        "clang": {"CMAKE_CXX_COMPILER_ID": "Clang", "CMAKE_CXX_SIMULATE_ID": "", "MSVC": "0"},
        "gnu": {"CMAKE_CXX_COMPILER_ID": "GNU", "CMAKE_CXX_SIMULATE_ID": "", "MSVC": "0"},
    }
    NOT_MSVC = ('if(NOT MSVC)\n  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-fno-rtti")\n'
                'endif()\n')

    def test_a_recorded_compiler_decides_msvc_conditions(self) -> None:
        windows = dict(self.RELEASE, CMAKE_SYSTEM_NAME="Windows")
        self.assertEqual(self.evaluate(self.NOT_MSVC, {**windows, **self.COMPILERS["cl"]}), {})
        self.assertEqual(self.evaluate(self.NOT_MSVC, {**windows, **self.COMPILERS["clang-cl"]}), {})
        self.assertEqual(self.evaluate(self.NOT_MSVC, {**windows, **self.COMPILERS["clang"]}),
                         {"core/x/src/a.cpp": ["-fno-rtti"]})
        # Control: with no compiler recorded, Windows still fails closed.
        with self.assertRaises(mp.source_compile_options.SourceOptionsError):
            self.evaluate(self.NOT_MSVC, windows)

    def test_a_recorded_compiler_decides_compiler_id_conditions_off_darwin(self) -> None:
        text = ('if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")\n'
                '  set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n')
        linux = dict(self.RELEASE, CMAKE_SYSTEM_NAME="Linux")
        self.assertEqual(self.evaluate(text, {**linux, **self.COMPILERS["gnu"]}), {"core/x/src/a.cpp": ["-O1"]})
        self.assertEqual(self.evaluate(text, {**dict(self.RELEASE, CMAKE_SYSTEM_NAME="Windows"),
                                              **self.COMPILERS["cl"]}), {})
        # On Linux MSVC is false whatever the record says, as CMake sets it.
        self.assertEqual(self.evaluate(self.NOT_MSVC, {**linux, **self.COMPILERS["gnu"]}),
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


class SourceOptionVectorsTest(unittest.TestCase):
    """The shared decision vectors in fixtures/source-option-vectors.json, each evaluated as written."""

    def test_every_vector_decides_as_expected(self) -> None:
        document = json.loads((HERE / "fixtures/source-option-vectors.json").read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "vellum.source-option-vectors.v1")
        self.assertGreater(len(document["vectors"]), 10)  # control: the file was read
        for vector in document["vectors"]:
            with self.subTest(vector=vector["name"]):
                files = {"core/x/CMakeLists.txt": vector["cmake"]}
                if vector["expect"] == "refused":
                    with self.assertRaises(mp.source_compile_options.SourceOptionsError):
                        mp.source_compile_options.evaluate(files, vector["options"], {"core/x/src/a.cpp"})
                else:
                    result = mp.source_compile_options.evaluate(files, vector["options"], {"core/x/src/a.cpp"})
                    self.assertEqual(result.get("core/x/src/a.cpp", []), vector["expect"])


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



class FrameworkTransferTest(unittest.TestCase):
    """The packaged units read from framework/ once the 8b transfer is recorded."""

    REPO = HERE.parents[1]

    def transferred_repo(self, root: Path) -> Path:
        (root / mp.FRAMEWORK_DIR).mkdir()
        (root / mp.FRAMEWORK_TRANSFER).parent.mkdir(parents=True)
        (root / mp.FRAMEWORK_TRANSFER).write_text("{}", encoding="utf-8")
        return root

    def test_renamed_path_agrees_with_every_transfer_record(self) -> None:
        record_path = self.REPO / mp.FRAMEWORK_TRANSFER
        if not record_path.is_file():
            self.skipTest("this checkout records no framework transfer")
        records = json.loads(record_path.read_text(encoding="utf-8"))["records"]
        self.assertGreater(len(records), 0)  # control: the record is read
        for row in records:
            self.assertEqual(f"{mp.FRAMEWORK_DIR}/{mp.renamed_path(row['pulp_path'])}", row["framework_path"])
        self.assertEqual(mp.renamed_path("core/view/include/pulp/view/view.hpp"),
                         "core/view/include/vellum/view/view.hpp")
        self.assertEqual(mp.renamed_path("core/view/src/pulp/a.cpp"), "core/view/src/pulp/a.cpp")

    def test_sources_resolve_under_framework_only_when_transferred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(mp.mirrored(root, "core/a/include/pulp/a/a.hpp"),
                             root / "mirror/core/a/include/pulp/a/a.hpp")
            (root / mp.FRAMEWORK_DIR).mkdir()
            # A framework/ with no transfer record is a half transfer: refused.
            with self.assertRaisesRegex(mp.PlanError, "must exist together"):
                mp.transferred(root)
            self.transferred_repo_record_only(root)
            self.assertEqual(mp.mirrored(root, "core/a/include/pulp/a/a.hpp"),
                             root / "framework/core/a/include/vellum/a/a.hpp")

    def transferred_repo_record_only(self, root: Path) -> None:
        (root / mp.FRAMEWORK_TRANSFER).parent.mkdir(parents=True)
        (root / mp.FRAMEWORK_TRANSFER).write_text("{}", encoding="utf-8")

    def test_generated_header_is_checked_through_the_inverse(self) -> None:
        pulp = b"#pragma once\nnamespace pulp::runtime { inline constexpr bool kGitDirty = false; }\n"
        renamed = b"#pragma once\nnamespace vellum::runtime { inline constexpr bool kGitDirty = false; }\n"
        digest = hashlib.sha256(pulp).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = self.transferred_repo(Path(temporary))
            self.assertTrue(mp.generated_matches(root, renamed, digest))
            # A tampered generated header fails, renamed or not.
            self.assertFalse(mp.generated_matches(root, renamed.replace(b"false", b"true "), digest))
            self.assertFalse(mp.generated_matches(root, renamed + b"// extra\n", digest))
        with tempfile.TemporaryDirectory() as temporary:
            # Before the transfer the bytes are compared as they are.
            self.assertTrue(mp.generated_matches(Path(temporary), pulp, digest))
            self.assertFalse(mp.generated_matches(Path(temporary), renamed, digest))

    def test_framework_file_set_is_held_and_native_edits_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {"framework/core/a/src/a.cpp": b"int a();\n", "framework/core/a/src/b.cpp": b"int b();\n"}
            for relative, data in files.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(data)
            receipt = {"mirror_tree": "0" * 40}
            record = {"export_receipt": "provenance/mirror/receipts/r.json", "mirror_tree": "0" * 40,
                      "records": [{"framework_path": p, "framework_git_blob_sha": mp.git_blob_sha(d)}
                                  for p, d in files.items()]}
            (root / mp.FRAMEWORK_TRANSFER).parent.mkdir(parents=True)
            (root / mp.FRAMEWORK_TRANSFER).write_text(json.dumps(record), encoding="utf-8")
            self.assertEqual(mp.verify_framework_identity(root, "provenance/mirror/receipts/r.json", receipt), 2)
            with self.assertRaisesRegex(mp.PlanError, "newest receipt"):
                mp.verify_framework_identity(root, "provenance/mirror/receipts/other.json", receipt)
            self.assertEqual(mp.framework_native_edits(root), [])
            # framework/ is Vellum-native: an edited file plans, and is reported.
            (root / "framework/core/a/src/a.cpp").write_bytes(b"int a(int);\n")
            self.assertEqual(mp.verify_framework_identity(root, "provenance/mirror/receipts/r.json", receipt), 2)
            self.assertEqual(mp.framework_native_edits(root), ["framework/core/a/src/a.cpp"])
            (root / "framework/core/a/src/a.cpp").write_bytes(files["framework/core/a/src/a.cpp"])
            (root / "framework/core/a/src/c.cpp").write_bytes(b"int c();\n")
            with self.assertRaisesRegex(mp.PlanError, "does not list"):
                mp.verify_framework_identity(root, "provenance/mirror/receipts/r.json", receipt)
            (root / "framework/core/a/src/c.cpp").unlink()
            (root / "framework/core/a/src/b.cpp").unlink()
            with self.assertRaisesRegex(mp.PlanError, "is missing files"):
                mp.verify_framework_identity(root, "provenance/mirror/receipts/r.json", receipt)


PORT_COMMIT = "9" * 40
PRELUDES_CMAKE = r"""set(PULP_JS_PRELUDES
    ${CMAKE_CURRENT_SOURCE_DIR}/js/first.js
    ${CMAKE_CURRENT_SOURCE_DIR}/js/second.js
)
set(_pulp_prelude_decls "#pragma once\n// preludes\n")
foreach(_prelude ${PULP_JS_PRELUDES})
    string(APPEND _pulp_prelude_decls "extern const char* const ${_prelude_var};\n")
endforeach()
string(APPEND _pulp_prelude_decls "// end\n")
add_custom_command(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/js/embed_js.py)
"""
EMBEDDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "out = Path(sys.argv[1])\n"
    "out.write_bytes(b''.join(b'// ' + Path(p).name.encode() + b'\\n' + Path(p).read_bytes() for p in sys.argv[2:]))\n"
)
UNSTABLE_EMBEDDER = EMBEDDER + "import os\nout.write_bytes(out.read_bytes() + os.urandom(8).hex().encode())\n"


class TransferredFixture(Fixture):
    """The minimal export after the transfer: framework/ and its record, with a prelude producer."""

    VIEW = "framework/core/view"

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        (self.repo / "mirror").rename(self.repo / "framework")
        include = self.repo / "framework/core/runtime/include"
        (include / "pulp").rename(include / "vellum")
        self.transferred = {
            "framework/" + mp.renamed_path(path.removeprefix("mirror/")): data for path, data in self.files.items()
        }
        self.transferred.update({
            f"{self.VIEW}/cmake/PulpViewJsPreludes.cmake": PRELUDES_CMAKE.encode(),
            f"{self.VIEW}/js/embed_js.py": EMBEDDER.encode(),
            f"{self.VIEW}/js/first.js": b"var first = 1;\n",
            f"{self.VIEW}/js/second.js": b"var second = 2;\n",
        })
        for path, data in self.transferred.items():
            self.write(path, data)
        # The closure's digests are Pulp's: what the transferred producer
        # makes of the transferred sources, read through the rename's inverse.
        header = mp.render_prelude_header(PRELUDES_CMAKE, ["js/first.js", "js/second.js"]).encode()
        source = b"// first.js\nvar first = 1;\n// second.js\nvar second = 2;\n"
        self.closure["generated_inputs"] += [
            {"path": PRELUDE_HEADER_PATH, "sha256": sha(mp.inverse_renamed(header)), "producer_paths": []},
            {"path": "@build/core/view/web_compat_preludes_gen.cpp", "sha256": sha(mp.inverse_renamed(source)),
             "producer_paths": []},
        ]
        target = self.closure["targets"][0]
        target["sources"].append("@build/core/view/web_compat_preludes_gen.cpp")
        target["include_dirs"].append("@build/core/view")
        self.write_receipt()
        record = {"export_receipt": f"provenance/mirror/receipts/{EXPORT_ID}.json", "mirror_tree": "e" * 40,
                  "records": [{"framework_path": path, "framework_git_blob_sha": mp.git_blob_sha(data)}
                              for path, data in sorted(self.transferred.items())]}
        self.write(mp.FRAMEWORK_TRANSFER, json.dumps(record).encode())

    def write(self, path: str, data: bytes) -> None:
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_bytes(data)

    def write_ledger(self, rows: list[dict]) -> None:
        self.write(mp.framework_ports.LEDGER, json.dumps({"schema": mp.framework_ports.SCHEMA, "rows": rows}).encode())

    @staticmethod
    def row(path: str, change: str = "added", target: str | None = None) -> dict:
        return {"framework_path": path, "change": change, "target": target, "origin": "pulp",
                "pulp_path": path.removeprefix("framework/"), "pulp_commits": [PORT_COMMIT],
                "framework_blob": "b" * 40 if change == "added" else None, "reason": "port"}

    def prelude(self, suffix: str = "cpp") -> bytes:
        return (self.generated / f"preludes/web_compat_preludes_gen.{suffix}").read_bytes()


class FrameworkPortsPlanTest(unittest.TestCase):
    """The build plan holds framework/ to the transfer record and the ports ledger, and compiles ported sources."""

    NEW = "framework/core/runtime/src/ported.mm"
    OLD = "framework/core/runtime/src/a.cpp"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = TransferredFixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def assertRefused(self, fragment: str) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn(fragment, str(caught.exception))

    def sources(self, plan: dict) -> list[str]:
        return plan["units"][0]["targets"][0]["sources"]

    def planned(self, relative: str) -> str:
        """A framework file as the plan spells it: under the resolved checkout."""
        return str(self.fixture.repo.resolve() / relative)

    def test_the_transferred_tree_plans_with_no_ledger(self) -> None:
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_ports"], {"added": [], "removed": []})
        self.assertEqual(plan["mirror_files_verified"], len(self.fixture.transferred))
        self.assertIn(self.planned(self.OLD), self.sources(plan))
        self.assertEqual(plan["framework_native_edits"], [])

    def test_an_unrecorded_file_is_refused_until_a_ledger_row_adds_it(self) -> None:
        self.fixture.write(self.NEW, b"int ported() { return 2; }\n")
        self.assertRefused("does not list")
        self.fixture.write_ledger([self.fixture.row(self.NEW, target="pulp-runtime")])
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_ports"], {"added": [self.NEW], "removed": []})
        self.assertEqual(plan["mirror_files_verified"], len(self.fixture.transferred) + 1)
        # The ported source is compiled: it is in the target's source list and
        # in the CMake the build reads.
        self.assertIn(self.planned(self.NEW), self.sources(plan))
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        listed = output.read_text().split("set(VELLUM_MIRROR_TARGET_PULP_RUNTIME_SOURCES")[1].split(")")[0]
        self.assertIn(self.NEW, listed)
        self.assertIn(self.OLD, listed)  # control: the closure's own source is still there
        # A ported file is a port, not a native edit of a transferred file.
        self.assertEqual(plan["framework_native_edits"], [])

    def test_a_ledger_row_with_no_file_is_refused(self) -> None:
        self.fixture.write_ledger([self.fixture.row(self.NEW, target="pulp-runtime")])
        self.assertRefused("is missing files")

    def test_a_ported_source_for_a_target_no_closure_builds_is_refused(self) -> None:
        self.fixture.write(self.NEW, b"int ported() { return 2; }\n")
        self.fixture.write_ledger([self.fixture.row(self.NEW, target="pulp-nowhere")])
        self.assertRefused("adds sources to targets no closure builds")

    def test_a_ported_header_joins_the_set_and_is_not_compiled(self) -> None:
        header = "framework/core/runtime/include/vellum/runtime/ported.hpp"
        self.fixture.write(header, b"int ported();\n")
        self.fixture.write_ledger([self.fixture.row(header)])
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_ports"]["added"], [header])
        self.assertNotIn(self.planned(header), self.sources(plan))

    def test_a_removal_row_accepts_a_transferred_files_absence(self) -> None:
        # Deleted with no row: missing. A row with the file still there: extra.
        (self.fixture.repo / self.OLD).unlink()
        self.assertRefused("is missing files")
        self.fixture.write(self.OLD, self.fixture.transferred[self.OLD])
        self.fixture.write_ledger([self.fixture.row(self.OLD, "removed")])
        self.assertRefused("does not list")
        (self.fixture.repo / self.OLD).unlink()
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_ports"], {"added": [], "removed": [self.OLD]})
        self.assertEqual(plan["mirror_files_verified"], len(self.fixture.transferred) - 1)
        # The closure still names the source; the plan leaves it out.
        self.assertNotIn(self.planned(self.OLD), self.sources(plan))
        self.assertEqual(plan["framework_native_edits"], [])

    def test_a_closure_source_that_is_absent_without_a_row_is_still_refused(self) -> None:
        # Control for the removal: only the ledger excuses a closure source.
        # The header's removal is recorded, the source's is not.
        header = "framework/core/runtime/include/vellum/runtime/a.hpp"
        (self.fixture.repo / header).unlink()
        self.fixture.write_ledger([self.fixture.row(header, "removed")])
        self.fixture.plan()
        (self.fixture.repo / self.OLD).unlink()
        self.assertRefused("is missing files")

    def test_a_ledger_that_does_not_replay_is_refused(self) -> None:
        self.fixture.write_ledger([self.fixture.row(self.OLD, target="pulp-runtime")])
        self.assertRefused("a ledger row may only remove it")
        self.fixture.write(mp.framework_ports.LEDGER, b"[]")
        self.assertRefused("must be an object with schema")

    def test_untouched_preludes_are_still_held_to_the_closure_digest(self) -> None:
        plan = self.fixture.plan()
        self.assertEqual(plan["native_preludes"], {})
        self.assertEqual(self.fixture.prelude(), b"// first.js\nvar first = 1;\n// second.js\nvar second = 2;\n")
        # Control: with every input still the transferred blob, a closure
        # digest the producer does not reproduce is refused.
        self.fixture.closure["generated_inputs"][-1]["sha256"] = sha(b"other")
        self.fixture.write_receipt()
        self.assertRefused("does not match the closure digest")

    def test_an_edited_prelude_source_is_generated_from_the_framework(self) -> None:
        self.fixture.plan()
        before = self.fixture.prelude()
        self.fixture.write(f"{self.fixture.VIEW}/js/second.js", b"var second = 22;\n")
        plan = self.fixture.plan()
        after = self.fixture.prelude()
        self.assertNotEqual(after, before)
        self.assertIn(b"var second = 22;", after)
        # Both generated files are now the framework's own, with their digests in the plan.
        self.assertEqual(plan["native_preludes"], {
            "web_compat_preludes_gen.cpp": sha(after),
            "web_compat_preludes_gen.hpp": sha(self.fixture.prelude("hpp")),
        })
        self.assertEqual(plan["framework_native_edits"], [f"{self.fixture.VIEW}/js/second.js"])
        # Deterministic: a second plan over the same sources gives the same bytes.
        self.assertEqual(self.fixture.plan()["native_preludes"], plan["native_preludes"])
        self.assertEqual(self.fixture.prelude(), after)
        # Control: restoring the transferred bytes restores the Pulp proof.
        self.fixture.write(f"{self.fixture.VIEW}/js/second.js", b"var second = 2;\n")
        self.assertEqual(self.fixture.plan()["native_preludes"], {})
        self.assertEqual(self.fixture.prelude(), before)

    def test_a_ported_prelude_source_is_embedded(self) -> None:
        third = f"{self.fixture.VIEW}/js/third.js"
        self.fixture.write(third, b"var third = 3;\n")
        self.fixture.write(f"{self.fixture.VIEW}/cmake/PulpViewJsPreludes.cmake", PRELUDES_CMAKE.replace(
            "/js/second.js\n", "/js/second.js\n    ${CMAKE_CURRENT_SOURCE_DIR}/js/third.js\n").encode())
        self.assertRefused("does not list")
        self.fixture.write_ledger([self.fixture.row(third)])
        plan = self.fixture.plan()
        self.assertIn(b"// third.js\nvar third = 3;\n", self.fixture.prelude())
        self.assertIn(b"extern const char* const third;", self.fixture.prelude("hpp"))
        self.assertEqual(sorted(plan["native_preludes"]),
                         ["web_compat_preludes_gen.cpp", "web_compat_preludes_gen.hpp"])

    def test_a_prelude_input_outside_the_file_set_is_refused(self) -> None:
        producer = f"{self.fixture.VIEW}/cmake/PulpViewJsPreludes.cmake"
        self.fixture.write("native_secret.js", b"var secret = 1;\n")
        for listed in ("js/../../../../native_secret.js", "js/unlisted.js"):
            self.fixture.write(producer, PRELUDES_CMAKE.replace(
                "/js/second.js\n", f"/js/second.js\n    ${{CMAKE_CURRENT_SOURCE_DIR}}/{listed}\n").encode())
            self.assertRefused(f"prelude input {listed} is not a file of the framework's file set")
        # Control: the same producer edit naming a ledger-added file plans
        # (test_a_ported_prelude_source_is_embedded), and so does the untouched one.
        self.fixture.write(producer, PRELUDES_CMAKE.encode())
        self.fixture.plan()

    def test_removing_a_file_the_plan_generates_from_is_refused(self) -> None:
        for relative in ("framework/core/canvas/CMakeLists.txt",
                         "framework/core/runtime/include/vellum/runtime/build_info.hpp.in",
                         f"{self.fixture.VIEW}/cmake/PulpViewJsPreludes.cmake",
                         f"{self.fixture.VIEW}/js/embed_js.py"):
            data = (self.fixture.repo / relative).read_bytes()
            (self.fixture.repo / relative).unlink()
            self.fixture.write_ledger([self.fixture.row(relative, "removed")])
            with self.assertRaises(mp.PlanError, msg=relative):
                self.fixture.plan()
            self.fixture.write(relative, data)
        # Control: a removal the plan does not generate from still plans.
        header = "framework/core/runtime/include/vellum/runtime/a.hpp"
        (self.fixture.repo / header).unlink()
        self.fixture.write_ledger([self.fixture.row(header, "removed")])
        self.fixture.plan()

    def test_a_prelude_generator_that_is_not_deterministic_is_refused(self) -> None:
        self.fixture.write(f"{self.fixture.VIEW}/js/embed_js.py", UNSTABLE_EMBEDDER.encode())
        self.assertRefused("is not deterministic")
        # Control: the same edit-shaped change with a stable generator plans.
        self.fixture.write(f"{self.fixture.VIEW}/js/embed_js.py", (EMBEDDER + "# edited\n").encode())
        self.assertEqual(sorted(self.fixture.plan()["native_preludes"]),
                         ["web_compat_preludes_gen.cpp", "web_compat_preludes_gen.hpp"])


SNAPSHOTS = HERE / "fixtures" / "macos-plan-snapshot"


def snapshot_scenarios() -> dict:
    """The landed ledger shapes (schema v1, no scope) the macOS plan must keep planning byte for byte."""
    def none(fixture: "TransferredFixture") -> None:
        del fixture

    def compiled_and_header(fixture: "TransferredFixture") -> None:
        fixture.write("framework/core/runtime/src/ported.mm", b"int ported() { return 2; }\n")
        fixture.write("framework/core/runtime/include/vellum/runtime/ported.hpp", b"int ported();\n")
        write_v1_ledger(fixture, [fixture.row("framework/core/runtime/src/ported.mm", target="pulp-runtime"),
                                  fixture.row("framework/core/runtime/include/vellum/runtime/ported.hpp")])

    def removal(fixture: "TransferredFixture") -> None:
        (fixture.repo / "framework/core/runtime/src/a.cpp").unlink()
        write_v1_ledger(fixture, [fixture.row("framework/core/runtime/src/a.cpp", "removed")])

    return {"no-ledger": none, "v1-ledger-compiled-and-header": compiled_and_header, "v1-ledger-removal": removal}


def write_v1_ledger(fixture: "TransferredFixture", rows: list) -> None:
    fixture.write("provenance/framework/ports.json",
                  json.dumps({"schema": "vellum.framework-ports.v1", "rows": rows}).encode())


def render_snapshot(module, name: str) -> tuple[str, str]:
    """(plan JSON, plan CMake) for one scenario, planned by `module`, with the temporary root normalized."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fixture = TransferredFixture(root)
        module.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}
        try:
            snapshot_scenarios()[name](fixture)
            plan = module.plan(fixture.repo, fixture.deps, fixture.generated, fixture.skia, fixture.skia_asset)
            output = root / "plan.cmake"
            module.write_cmake(plan, output)
            texts = (json.dumps(plan, indent=2, sort_keys=True) + "\n", output.read_text())
        finally:
            module.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        for prefix in (str(root.resolve()), str(root)):
            texts = tuple(text.replace(prefix, "<tmp>") for text in texts)
        return texts


class MacosPlanSnapshotTest(unittest.TestCase):
    """The macOS plan for the landed ledger shapes is byte-identical to the snapshot.

    The snapshots under fixtures/macos-plan-snapshot/ were rendered by
    render_snapshot with Vellum main e57c5d0eb's mirror_package.py, before
    platform scope existed. A change to them is a change to what macOS builds,
    and needs its own review.
    """

    def test_the_macos_plan_matches_the_snapshot(self) -> None:
        for name in snapshot_scenarios():
            with self.subTest(scenario=name):
                plan_json, plan_cmake = render_snapshot(mp, name)
                self.assertEqual(plan_json, (SNAPSHOTS / f"{name}.plan.json").read_text())
                self.assertEqual(plan_cmake, (SNAPSHOTS / f"{name}.plan.cmake").read_text())

    def test_the_snapshot_sees_a_scope_change(self) -> None:
        # Control: a scope that leaves macOS out changes the plan, so the
        # comparison above can fail.
        plan_json, _ = render_snapshot(mp, "v1-ledger-compiled-and-header")
        with tempfile.TemporaryDirectory() as directory:
            fixture = TransferredFixture(Path(directory))
            snapshot_scenarios()["v1-ledger-compiled-and-header"](fixture)
            rows = json.loads((fixture.repo / "provenance/framework/ports.json").read_text())["rows"]
            rows[0]["platforms"] = ["linux-x64"]
            fixture.write_ledger(rows)
            mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}
            try:
                scoped = json.dumps(fixture.plan(), indent=2, sort_keys=True) + "\n"
            finally:
                mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.assertNotEqual(scoped.replace(str(Path(directory).resolve()), "<tmp>"), plan_json)


class PlatformRecordPlanTest(unittest.TestCase):
    """A platform other than macOS plans only from its own verified closure record."""

    LINUX_HOST = "framework/core/runtime/src/linux_host.cpp"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = TransferredFixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def write_record(self, extra_sources: list[str] | None = None, **overrides) -> dict:
        closure = json.loads(json.dumps(self.fixture.closure))
        options = {"CMAKE_SYSTEM_NAME": "Linux", "CMAKE_SYSTEM_PROCESSOR": "x86_64", "CMAKE_BUILD_TYPE": "Release",
                   "PULP_ENABLE_GPU": "ON", "PULP_HAS_SKIA": "1"}
        identity = {"gpu": True, "skia": True, "text_shaping": True, "build_type": "Release",
                    "system": "Linux", "processor": "x86_64"}
        closure["configure"] = {"options": options, "identity": identity}
        closure["targets"][0]["sources"] += extra_sources or []
        root = self.fixture.repo / "provenance/framework/platforms/linux-x64"
        root.mkdir(parents=True, exist_ok=True)
        (root / "foundation.json").write_text(json.dumps(closure), encoding="utf-8")
        record = {
            "schema": "vellum.framework-platform-closure.v1", "platform": "linux-x64",
            "pulp_repository": "Generous-Corp/pulp", "pulp_sha": PULP_SHA,
            "tool": {"path": "tools/scripts/vellum_closure.py", "pulp_blob": "f" * 40},
            "configure": {"identity": identity, "options_sha256": mp.platform_closures.options_sha256(options)},
            "export": {"run_url": "https://github.com/Generous-Corp/pulp/actions/runs/123456789",
                       "artifact_sha256": "e" * 64},
            "runner_image": {"name": "ubuntu-26.04", "version": "20261111.196.1"},
            "renderer_asset": {"name": "skia-build-linux-x64-gpu-release.zip", "sha256": "c" * 64},
            "units": [{"name": "foundation",
                       "closure_manifest": "provenance/framework/platforms/linux-x64/foundation.json",
                       "closure_sha256": mp.sha256_file(root / "foundation.json")}],
        }
        record.update(overrides)
        (root / "record.json").write_text(json.dumps(record), encoding="utf-8")
        (root.parent / "index.json").write_text(json.dumps({
            "schema": "vellum.framework-platform-index.v1",
            "platforms": {"linux-x64": "provenance/framework/platforms/linux-x64/record.json"}}), encoding="utf-8")
        return record

    def plan(self, platform: str) -> dict:
        return mp.plan(self.fixture.repo, self.fixture.deps, self.fixture.generated, self.fixture.skia,
                       self.fixture.skia_asset, platform=platform)

    def sources(self, plan: dict) -> list[str]:
        return [s for unit in plan["units"] for t in unit["targets"] for s in t["sources"]]

    def planned(self, relative: str) -> str:
        return str(self.fixture.repo.resolve() / relative)

    def port_linux_host(self, platforms: list[str]) -> None:
        self.fixture.write(self.LINUX_HOST, b"int linux_host() { return 4; }\n")
        row = self.fixture.row(self.LINUX_HOST, target="pulp-runtime")
        row["platforms"] = platforms
        self.fixture.write_ledger([row])

    def test_a_platform_with_a_record_plans_from_it(self) -> None:
        self.port_linux_host(["linux-x64"])
        self.write_record(["core/runtime/src/linux_host.cpp"])
        plan = self.plan("linux-x64")
        self.assertEqual(plan["platform"], "linux-x64")
        self.assertEqual(plan["closures"], ["provenance/framework/platforms/linux-x64/foundation.json"])
        sources = self.sources(plan)
        self.assertEqual(sources.count(self.planned(self.LINUX_HOST)), 1)  # once, though closure and row both name it

    def test_the_macos_plan_is_unchanged_by_another_platforms_record(self) -> None:
        before = render_snapshot(mp, "no-ledger")
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}  # render_snapshot clears it
        self.write_record()
        self.assertEqual(render_snapshot_in(self.fixture, mp), json.loads(before[0]))

    def test_a_record_without_its_export_is_refused(self) -> None:
        record = self.write_record()
        del record["export"]
        (self.fixture.repo / "provenance/framework/platforms/linux-x64/record.json").write_text(
            json.dumps(record), encoding="utf-8")
        with self.assertRaises(mp.PlanError) as caught:
            self.plan("linux-x64")
        self.assertIn("must have exactly", str(caught.exception))
        # macOS has no record at all, so it needs no export: its plan still loads.
        self.assertNotIn("platform", self.plan("macos-arm64"))

    def test_no_record_or_a_broken_record_is_refused(self) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.plan("linux-x64")
        self.assertIn("lists no record for it", str(caught.exception))
        for overrides, fragment in (
            ({"platform": "windows-x64"}, "names platform"),
            ({"pulp_sha": "x"}, "pulp_sha is not a full commit id"),
            ({"renderer_asset": {"name": "a", "sha256": "d" * 64}}, "links skia asset"),
            ({"configure": {"identity": {"system": "Darwin", "processor": "arm64"}, "options_sha256": "0" * 64}},
             "is built for Linux x86_64"),
            ({"units": []}, "one or more closure manifests"),
            ({"export": None}, "export names the Pulp run"),
            ({"export": {"run_url": "https://github.com/Generous-Corp/pulp/actions/runs/1"}},
             "export names the Pulp run"),
            ({"export": {"run_url": "https://github.com/someone/pulp/actions/runs/1", "artifact_sha256": "e" * 64}},
             "is not a https://github.com/Generous-Corp/pulp/actions/runs/<id> URL"),
            ({"export": {"run_url": "https://github.com/Generous-Corp/pulp/actions/runs/1/job/2",
                         "artifact_sha256": "e" * 64}}, "is not a https://github.com/Generous-Corp/pulp/actions/runs"),
            ({"export": {"run_url": "https://github.com/Generous-Corp/pulp/actions/runs/1", "artifact_sha256": "E" * 64}},
             "export.artifact_sha256 is not a lowercase SHA-256"),
        ):
            with self.subTest(overrides=overrides):
                self.write_record(**overrides)
                with self.assertRaises(mp.PlanError) as caught:
                    self.plan("linux-x64")
                self.assertIn(fragment, str(caught.exception))
        # A manifest edited after the record pinned it is refused.
        self.write_record()
        path = self.fixture.repo / "provenance/framework/platforms/linux-x64/foundation.json"
        path.write_text(path.read_text() + " ", encoding="utf-8")
        with self.assertRaises(mp.PlanError) as caught:
            self.plan("linux-x64")
        self.assertIn("does not match the record's digest", str(caught.exception))

    def rewrite_closure(self, mutate) -> None:
        self.write_record()
        root = self.fixture.repo / "provenance/framework/platforms/linux-x64"
        closure = json.loads((root / "foundation.json").read_text())
        mutate(closure)
        (root / "foundation.json").write_text(json.dumps(closure), encoding="utf-8")
        record = json.loads((root / "record.json").read_text())
        record["units"][0]["closure_sha256"] = mp.sha256_file(root / "foundation.json")
        if "options" in closure.get("configure", {}) if isinstance(closure.get("configure"), dict) else False:
            record["configure"]["options_sha256"] = mp.platform_closures.options_sha256(closure["configure"]["options"])
        (root / "record.json").write_text(json.dumps(record), encoding="utf-8")

    def test_options_that_configure_another_platform_are_refused(self) -> None:
        for change, fragment in (({"CMAKE_SYSTEM_NAME": "Windows"}, "but linux-x64 is Linux"),
                                 ({"CMAKE_SYSTEM_PROCESSOR": "aarch64"}, "but linux-x64 is x86_64")):
            with self.subTest(change=change):
                self.rewrite_closure(lambda c: c["configure"]["options"].update(change))
                with self.assertRaises(mp.PlanError) as caught:
                    self.plan("linux-x64")
                self.assertIn(fragment, str(caught.exception))
        # Control: untouched options plan.
        self.write_record()
        self.plan("linux-x64")

    def test_ios_options_must_name_the_indexed_sdk(self) -> None:
        pc = mp.platform_closures
        pc._check_options_platform("at", "ios-device-arm64", {
            "CMAKE_SYSTEM_NAME": "iOS", "CMAKE_SYSTEM_PROCESSOR": "arm64", "CMAKE_OSX_SYSROOT": "iphoneos"})
        pc._check_options_platform("at", "ios-simulator-arm64", {
            "CMAKE_SYSTEM_NAME": "iOS", "CMAKE_SYSTEM_PROCESSOR": "arm64", "CMAKE_OSX_SYSROOT": "iphonesimulator"})
        for key, sdk in (("ios-device-arm64", "iphonesimulator"), ("ios-simulator-arm64", "iphoneos"),
                         ("ios-device-arm64", None)):
            options = {"CMAKE_SYSTEM_NAME": "iOS", "CMAKE_SYSTEM_PROCESSOR": "arm64"}
            if sdk:
                options["CMAKE_OSX_SYSROOT"] = sdk
            with self.subTest(key=key, sdk=sdk), self.assertRaises(pc.RecordError):
                pc._check_options_platform("at", key, options)

    def test_malformed_closure_shapes_are_record_errors(self) -> None:
        for name, mutate in (
            ("targets not a list", lambda c: c.update(targets={"a": 1})),
            ("target not an object", lambda c: c.update(targets=["x"])),
            ("rows not a list", lambda c: c["targets"][0].update(external_link_inputs=5)),
            ("row not an object", lambda c: c["targets"][0].update(external_link_inputs=["x"])),
            ("configure not an object", lambda c: c.update(configure=[1])),
        ):
            with self.subTest(name):
                self.rewrite_closure(mutate)
                with self.assertRaises(mp.PlanError):
                    self.plan("linux-x64")
                self.assertTrue(mp.platform_closures.errors(self.fixture.repo))

    def test_a_platform_without_a_record_is_refused_before_any_other_work(self) -> None:
        # framework/ is broken too; the missing record is still what is reported.
        self.fixture.write("framework/core/runtime/src/stray.cpp", b"int stray;\n")
        with self.assertRaises(mp.PlanError) as caught:
            self.plan("linux-x64")
        self.assertIn("lists no record for it", str(caught.exception))

    def test_a_closure_source_framework_lacks_must_be_ported_first(self) -> None:
        self.write_record(["core/runtime/src/linux_host.cpp"])
        with self.assertRaises(mp.PlanError) as caught:
            self.plan("linux-x64")
        self.assertIn("port it through the ledger first", str(caught.exception))

    def test_a_closure_source_scoped_away_from_the_platform_is_refused(self) -> None:
        self.port_linux_host(["macos-arm64"])
        self.write_record(["core/runtime/src/linux_host.cpp"])
        with self.assertRaises(mp.PlanError) as caught:
            self.plan("linux-x64")
        self.assertIn("scope it to linux-x64", str(caught.exception))
        # Control: scoped to Linux, it plans.
        self.port_linux_host(["linux-x64"])
        self.assertIn(self.planned(self.LINUX_HOST), self.sources(self.plan("linux-x64")))

    def test_the_platform_key_comes_from_the_configure(self) -> None:
        pc = mp.platform_closures
        self.assertEqual(pc.platform_of("Linux", "x86_64"), "linux-x64")
        self.assertEqual(pc.platform_of("Windows", "AMD64"), "windows-x64")
        self.assertEqual(pc.platform_of("Emscripten", "wasm32"), "wasm32")
        self.assertEqual(pc.platform_of("iOS", "arm64", "/x/iPhoneSimulator.sdk"), "ios-simulator-arm64")
        self.assertEqual(pc.platform_of("iOS", "arm64", "/x/iPhoneOS.sdk"), "ios-device-arm64")
        with self.assertRaises(pc.RecordError):
            pc.platform_of("Haiku", "x86_64")


def render_snapshot_in(fixture: "TransferredFixture", module) -> dict:
    """The macOS plan of an existing fixture, normalized like render_snapshot."""
    plan = module.plan(fixture.repo, fixture.deps, fixture.generated, fixture.skia, fixture.skia_asset)
    text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    root = fixture.repo.parent
    for prefix in (str(root.resolve()), str(root)):
        text = text.replace(prefix, "<tmp>")
    return json.loads(text)


class PlatformScopePlanTest(unittest.TestCase):
    """A ledger-added source or declared target is planned only on the platforms its scope lists."""

    NEW = "framework/core/runtime/src/ported.cpp"
    LIB = "framework/core/example/src/example.cpp"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = TransferredFixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def scoped_row(self, path: str, target: str, platforms: list[str] | None) -> dict:
        if not (self.fixture.repo / path).exists():
            self.fixture.write(path, b"int ported() { return 2; }\n")
        row = self.fixture.row(path, target=target)
        if platforms is not None:
            row["platforms"] = platforms
        return row

    def sources(self, plan: dict) -> list[str]:
        return [source for unit in plan["units"] for target in unit["targets"] for source in target["sources"]]

    def planned(self, relative: str) -> str:
        return str(self.fixture.repo.resolve() / relative)

    def test_an_unscoped_row_compiles_on_macos(self) -> None:
        # A v1 row names no platform; it keeps meaning the macOS build it was proven on.
        self.fixture.write_ledger([self.scoped_row(self.NEW, "pulp-runtime", None)])
        plan = self.fixture.plan()
        self.assertIn(self.planned(self.NEW), self.sources(plan))

    def test_a_source_scoped_to_another_platform_is_not_compiled_on_macos(self) -> None:
        self.fixture.write_ledger([self.scoped_row(self.NEW, "pulp-runtime", ["linux-x64"])])
        plan = self.fixture.plan()
        self.assertNotIn(self.planned(self.NEW), self.sources(plan))
        self.assertNotIn(self.NEW, plan["framework_ports"]["added"])
        # Control: the same row scoped to macOS is compiled.
        self.fixture.write_ledger([self.scoped_row(self.NEW, "pulp-runtime", ["linux-x64", "macos-arm64"])])
        self.assertIn(self.planned(self.NEW), self.sources(self.fixture.plan()))

    def test_a_scoped_row_brings_a_source_into_the_macos_build(self) -> None:
        added = self.scoped_row(self.NEW, "pulp-runtime", ["linux-x64"])
        self.fixture.write_ledger([added])
        self.assertNotIn(self.planned(self.NEW), self.sources(self.fixture.plan()))
        widened = {**added, "change": "scoped", "framework_blob": None, "platforms": ["linux-x64", "macos-arm64"]}
        self.fixture.write_ledger([added, widened])
        self.assertIn(self.planned(self.NEW), self.sources(self.fixture.plan()))
        # Narrowing it back out of macOS is refused.
        self.fixture.write_ledger([added, widened, {**widened, "platforms": ["linux-x64"]}])
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn("cannot take it out of the macos-arm64 build", str(caught.exception))

    def test_a_removal_cannot_carry_a_source_out_of_the_macos_build(self) -> None:
        added = self.scoped_row(self.NEW, "pulp-runtime", None)
        removed = {**added, "change": "removed", "target": None, "framework_blob": None}
        self.fixture.write_ledger([added, removed, {**added, "platforms": ["wasm32"]}])
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn("comes back only with macos-arm64", str(caught.exception))
        # Control: back with macOS in scope, it is compiled again.
        self.fixture.write_ledger([added, removed, {**added, "platforms": ["macos-arm64", "wasm32"]}])
        self.assertIn(self.planned(self.NEW), self.sources(self.fixture.plan()))

    def test_a_row_scoped_off_macos_still_names_a_real_target(self) -> None:
        # Nothing else builds it yet, so the plan checks its target whatever its scope.
        self.fixture.write_ledger([self.scoped_row(self.NEW, "no-such-target", ["linux-x64"])])
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn("adds sources to targets no closure builds", str(caught.exception))
        self.assertIn("no-such-target", str(caught.exception))
        # Control: the same row naming a closure target plans (and is not compiled on macOS).
        self.fixture.write_ledger([self.scoped_row(self.NEW, "pulp-runtime", ["linux-x64"])])
        self.assertNotIn(self.planned(self.NEW), self.sources(self.fixture.plan()))

    def test_per_source_options_follow_the_scope(self) -> None:
        cmake = "framework/core/runtime/CMakeLists.txt"
        self.fixture.write(cmake, (b'add_library(pulp-runtime STATIC src/a.cpp)\n'
                                   b'set_source_files_properties(src/ported.cpp PROPERTIES COMPILE_OPTIONS "-O1")\n'))
        cmake_row = {"framework_path": cmake, "change": "added", "target": None, "origin": "native",
                     "pulp_path": None, "pulp_commits": [], "framework_blob": None, "reason": "per-source options"}
        source = "core/runtime/src/ported.cpp"
        # Scoped away from macOS: no options are computed for it, and no
        # "unattached options" refusal fires for a source the plan leaves out.
        self.fixture.write_ledger([cmake_row, self.scoped_row(self.NEW, "pulp-runtime", ["linux-x64"])])
        plan = self.fixture.plan()
        self.assertNotIn(source, plan["source_compile_options"])
        # Control: compiled on macOS, the same option is attached to it.
        self.fixture.write_ledger([cmake_row, self.scoped_row(self.NEW, "pulp-runtime", ["macos-arm64"])])
        plan = self.fixture.plan()
        self.assertEqual(plan["source_compile_options"].get(source), ["-O1"])
        self.assertIn(self.planned(self.NEW), self.sources(plan))

    def test_a_platform_with_no_verified_closure_is_refused(self) -> None:
        self.fixture.write_ledger([self.scoped_row(self.NEW, "pulp-runtime", ["linux-x64"])])
        with self.assertRaises(mp.PlanError) as caught:
            mp.plan(self.fixture.repo, self.fixture.deps, self.fixture.generated, self.fixture.skia,
                    self.fixture.skia_asset, platform="linux-x64")
        self.assertIn("no verified closure for platform linux-x64", str(caught.exception))
        with self.assertRaises(mp.PlanError) as caught:
            mp.plan(self.fixture.repo, self.fixture.deps, self.fixture.generated, self.fixture.skia,
                    self.fixture.skia_asset, platform="beos-ppc")
        self.assertIn("unknown platform", str(caught.exception))
        # Control: the platform the landed closures describe plans.
        mp.plan(self.fixture.repo, self.fixture.deps, self.fixture.generated, self.fixture.skia,
                self.fixture.skia_asset, platform="macos-arm64")

    def test_a_declared_target_for_another_platform_is_not_planned(self) -> None:
        self.fixture.write(self.LIB, b"int example() { return 3; }\n")
        rows = [self.scoped_row(self.LIB, "vellum-example", ["linux-x64"])]
        self.fixture.write_ledger(rows)
        library = {"name": "vellum-example", "type": "STATIC_LIBRARY", "unit": "example", "sources": [self.LIB],
                   "include_dirs": [], "compile_definitions": [], "link_targets": ["pulp-runtime"],
                   "reason": "a unit no closure builds", "platforms": ["linux-x64"]}
        self.fixture.write(mp.framework_ports.UNITS, json.dumps(
            {"schema": mp.framework_ports.UNITS_SCHEMA, "targets": [library]}).encode())
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_units"], [])
        self.assertNotIn(self.planned(self.LIB), self.sources(plan))
        # Control: scoped to macOS as well, it is planned.
        rows[0]["platforms"] = library["platforms"] = ["linux-x64", "macos-arm64"]
        self.fixture.write_ledger(rows)
        self.fixture.write(mp.framework_ports.UNITS, json.dumps(
            {"schema": mp.framework_ports.UNITS_SCHEMA, "targets": [library]}).encode())
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_units"], ["vellum-example"])
        self.assertIn(self.planned(self.LIB), self.sources(plan))

    def test_a_declared_target_wider_than_its_source_is_refused(self) -> None:
        self.fixture.write(self.LIB, b"int example() { return 3; }\n")
        self.fixture.write_ledger([self.scoped_row(self.LIB, "vellum-example", ["linux-x64"])])
        library = {"name": "vellum-example", "type": "STATIC_LIBRARY", "unit": "example", "sources": [self.LIB],
                   "include_dirs": [], "compile_definitions": [], "link_targets": ["pulp-runtime"],
                   "reason": "a unit no closure builds", "platforms": ["linux-x64", "macos-arm64"]}
        self.fixture.write(mp.framework_ports.UNITS, json.dumps(
            {"schema": mp.framework_ports.UNITS_SCHEMA, "targets": [library]}).encode())
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn("is not scoped", str(caught.exception))


class DeclaredUnitPlanTest(unittest.TestCase):
    """A target no closure builds is compiled only as provenance/framework/units.json declares it."""

    HEADER = "framework/core/example/include/vellum/example/example.hpp"
    LIB = "framework/core/example/src/example.cpp"
    TOOL = "framework/core/example/src/example_tool.cpp"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = TransferredFixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}
        self.fixture.write(self.HEADER, b"#pragma once\nint example();\n")
        self.fixture.write(self.LIB, b'#include "vellum/example/example.hpp"\nint example() { return 3; }\n')
        self.fixture.write(self.TOOL, b'#include "vellum/example/example.hpp"\nint main() { return example(); }\n')
        self.rows = [self.fixture.row(self.HEADER), self.fixture.row(self.LIB, target="vellum-example"),
                     self.fixture.row(self.TOOL, target="vellum-example-tool")]
        self.fixture.write_ledger(self.rows)
        self.library = {"name": "vellum-example", "type": "STATIC_LIBRARY", "unit": "example",
                        "sources": [self.LIB], "include_dirs": ["core/example/include"],
                        "compile_definitions": ["VELLUM_EXAMPLE=1"], "link_targets": ["pulp-runtime"],
                        "reason": "a unit no closure builds"}
        self.tool = {"name": "vellum-example-tool", "type": "EXECUTABLE", "unit": None, "sources": [self.TOOL],
                     "include_dirs": [], "compile_definitions": [], "link_targets": ["vellum-example"],
                     "reason": "a tool over the unit"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def declare(self, targets: list[dict]) -> None:
        self.fixture.write(mp.framework_ports.UNITS, json.dumps(
            {"schema": mp.framework_ports.UNITS_SCHEMA, "targets": targets}).encode())

    def assertRefused(self, fragment: str) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn(fragment, str(caught.exception))

    def planned(self, relative: str) -> str:
        return str(self.fixture.repo.resolve() / relative)

    def cmake(self, plan: dict) -> str:
        output = Path(self.temporary.name) / "plan.cmake"
        mp.write_cmake(plan, output)
        return output.read_text()

    def test_reserved_definitions_never_reach_a_unit_interface(self) -> None:
        for item in ("VELLUM_MIRROR_BUILD=1", "GPU_RUNTIME_BENCHMARK=1"):
            self.declare([{**self.library, "compile_definitions": [item]}, self.tool])
            self.assertRefused("is reserved")

    def test_undeclared_targets_are_refused_and_declared_ones_build(self) -> None:
        # The rows name targets no closure builds: refused while nothing declares them.
        self.assertRefused("adds sources to targets no closure builds")
        self.declare([self.library, self.tool])
        plan = self.fixture.plan()
        self.assertEqual(plan["framework_units"], ["vellum-example", "vellum-example-tool"])
        closure_unit, unit = plan["units"]
        self.assertEqual(closure_unit["name"], "foundation")  # the closure's unit comes first
        self.assertEqual((unit["name"], [t["name"] for t in unit["targets"]]), ("example", ["vellum-example"]))
        library = unit["targets"][0]
        self.assertEqual(library["sources"], [self.planned(self.LIB)])
        self.assertEqual(library["internal_links"], ["pulp-runtime"])
        # Its own include directory first, then what the linked target compiles with.
        runtime = closure_unit["targets"][0]
        self.assertEqual(library["include_dirs"],
                         [self.planned("framework/core/example/include")] + runtime["include_dirs"])
        # Its definitions, plus the interface definitions of the unit it links.
        self.assertEqual(library["compile_definitions"], ["VELLUM_EXAMPLE=1", "X=1"])
        self.assertEqual(unit["compile_definitions"], ["VELLUM_EXAMPLE=1", "X=1"])
        [tool] = plan["executables"]
        self.assertEqual((tool["name"], tool["type"], tool["sources"], tool["internal_links"]),
                         ("vellum-example-tool", "EXECUTABLE", [self.planned(self.TOOL)], ["vellum-example"]))
        # The closure's own target is unchanged: it does not compile the declared sources.
        self.assertNotIn(self.planned(self.LIB), runtime["sources"])
        text = self.cmake(plan)
        self.assertIn('set(VELLUM_MIRROR_UNITS\n    "foundation"\n    "example"\n)', text)
        self.assertIn('set(VELLUM_MIRROR_EXECUTABLES\n    "vellum-example-tool"\n)', text)
        self.assertIn('set(VELLUM_MIRROR_UNIT_EXAMPLE_TARGETS\n    "vellum-example"\n)', text)
        self.assertIn(self.LIB, text.split("set(VELLUM_MIRROR_TARGET_VELLUM_EXAMPLE_SOURCES")[1].split(")")[0])
        self.assertIn('set(VELLUM_MIRROR_TARGET_VELLUM_EXAMPLE_TOOL_TYPE "EXECUTABLE")', text)
        self.assertIn("set(VELLUM_MIRROR_TARGET_VELLUM_EXAMPLE_DECLARED ON)", text)
        self.assertIn("set(VELLUM_MIRROR_TARGET_PULP_RUNTIME_DECLARED OFF)", text)
        self.assertIn('set(VELLUM_MIRROR_TARGET_PULP_RUNTIME_TYPE "STATIC_LIBRARY")', text)

    def test_every_ledger_source_of_a_declared_target_is_listed_and_every_listed_source_has_its_row(self) -> None:
        self.declare([self.library, dict(self.tool, sources=[self.LIB])])
        self.assertRefused("is added by its ledger row to vellum-example, not vellum-example-tool")
        self.declare([self.library, self.tool])
        self.fixture.write_ledger(self.rows[:2] + [self.fixture.row(self.TOOL, target="vellum-example")])
        self.assertRefused("is added by its ledger row to vellum-example, not vellum-example-tool")
        # A source the ledger does not add is not in the file set at all.
        self.fixture.write_ledger(self.rows[:2])
        self.assertRefused("does not list")

    def test_a_link_must_be_mirrored_declared_earlier_or_a_pinned_recipe(self) -> None:
        self.declare([dict(self.library, link_targets=["pulp-runtime", "nowhere"]), self.tool])
        self.assertRefused("links nowhere, which is neither a mirrored target")
        self.declare([self.tool, self.library])
        self.assertRefused("which is declared after it")
        # yaml-cpp has a recipe, but no closure pins it and the recipe names no pin.
        self.declare([dict(self.library, link_targets=["yaml-cpp"]), self.tool])
        self.assertRefused("no closure pins yaml-cpp and its recipe names no pinned commit")
        # A recipe that is not a cmake build cannot be linked by a declared target.
        self.declare([dict(self.library, link_targets=["gpu-runtime-device"]), self.tool])
        self.assertRefused("only through a cmake recipe")

    def test_a_declared_target_may_not_take_an_existing_name_or_join_a_closure_unit(self) -> None:
        for library, fragment in (
            (dict(self.library, unit="foundation"), "which a closure describes"),
            (dict(self.library, name="hwy"), "is a target the highway recipe provides"),
        ):
            rows = [self.rows[0], self.fixture.row(self.LIB, target=library["name"]), self.rows[2]]
            self.fixture.write_ledger(rows)
            self.declare([library, dict(self.tool, link_targets=[library["name"]])])
            self.assertRefused(fragment)

    def test_a_closure_target_name_is_refused(self) -> None:
        # pulp-runtime is not a Vellum name either; the shape rule refuses it before the plan.
        self.fixture.write_ledger([self.rows[0], self.fixture.row(self.LIB, target="pulp-runtime"), self.rows[2]])
        self.declare([dict(self.library, name="pulp-runtime"), dict(self.tool, link_targets=["pulp-runtime"])])
        self.assertRefused("may not carry a Pulp name")
        # With the spelling rule out of the way, the plan still refuses the name.
        with mock.patch.object(mp.framework_ports, "units_errors", return_value=[]), \
                mock.patch.object(mp.framework_ports, "unit_target_errors", return_value=[]):
            self.assertRefused("is already a mirrored target of unit foundation")

    def test_the_targets_of_a_unit_are_declared_together(self) -> None:
        other = "framework/core/example/src/other.cpp"
        self.fixture.write(other, b"int other() { return 4; }\n")
        second = "framework/core/example/src/second.cpp"
        self.fixture.write(second, b"int second() { return 5; }\n")
        self.fixture.write_ledger(self.rows + [self.fixture.row(other, target="vellum-example-other"),
                                               self.fixture.row(second, target="vellum-second")])
        second_unit = dict(self.library, name="vellum-second", unit="second", sources=[second])
        other_target = dict(self.library, name="vellum-example-other", sources=[other])
        self.declare([self.library, second_unit, other_target, self.tool])
        self.assertRefused("the targets of unit example are not declared together")
        # Control: declared together, the unit carries both; an executable between them splits nothing.
        self.declare([self.library, self.tool, other_target, second_unit])
        units = self.fixture.plan()["units"]
        self.assertEqual([(u["name"], [t["name"] for t in u["targets"]]) for u in units[1:]],
                         [("example", ["vellum-example", "vellum-example-other"]), ("second", ["vellum-second"])])

    def test_a_broken_manifest_is_refused(self) -> None:
        self.fixture.write(mp.framework_ports.UNITS, b"[]")
        self.assertRefused("must be an object with schema")
        self.declare([dict(self.library, include_dirs=["core/missing/include"]), self.tool])
        self.assertRefused("is not a directory framework/ holds")

    def test_a_recipe_with_its_own_pin_is_fetched_at_that_commit(self) -> None:
        source = Path(self.temporary.name) / "upstream"
        source.mkdir()
        (source / "LICENSE").write_text("MIT\n")
        (source / "CMakeLists.txt").write_text("add_library(fixture-dep STATIC dep.cpp)\n")
        (source / "dep.cpp").write_text("int dep() { return 5; }\n")
        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True,
                                  text=True).stdout.strip()
        git("init", "-q")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "dep")
        pin = git("rev-parse", "HEAD")
        recipe = {"kind": "cmake", "provides": ["fixture-dep"], "options": {"X": "OFF"}, "pin": pin,
                  "source": source.as_uri()}
        self.declare([dict(self.library, link_targets=["pulp-runtime", "fixture-dep"]), self.tool])
        with mock.patch.dict(mp.DEPENDENCIES, {"fixture-dep": recipe}):
            plan = self.fixture.plan()
            self.assertEqual(plan["units"][1]["targets"][0]["external_links"], ["fixture-dep"])
            self.assertEqual(plan["dependencies"]["fixture-dep"]["pin"], pin)
            self.assertIn("fixture-dep", plan["cmake_dependencies"])
            self.assertEqual([Path(p).name for p in plan["licenses"]["fixture-dep"]], ["LICENSE"])
            # Its license travels with it: a pinned tree without one is refused.
            git("rm", "-q", "LICENSE")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "drop license")
            recipe["pin"] = git("rev-parse", "HEAD")
            self.assertRefused("carries no license file")
            # A recipe naming no commit is refused.
            recipe["pin"] = "main"
            self.assertRefused("its recipe names no pinned commit")


class MalformedReceiptRefusalTest(unittest.TestCase):
    """Receipts, indexes and closures that are unreadable or malformed are each refused."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))
        self.receipt_path = self.fixture.repo / f"provenance/mirror/receipts/{EXPORT_ID}.json"
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def assertRefused(self, fragment: str) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn(fragment, str(caught.exception))

    def edit_receipt(self, edit) -> None:
        receipt = json.loads(self.receipt_path.read_text())
        edit(receipt)
        self.receipt_path.write_text(json.dumps(receipt))

    def test_a_json_file_that_is_missing_or_malformed_is_refused(self) -> None:
        base = Path(self.temporary.name)
        (base / "broken.json").write_text("{")
        for name in ("absent.json", "broken.json"):
            with self.subTest(name=name), self.assertRaisesRegex(mp.PlanError, "cannot read"):
                mp.load_json(base / name)

    def test_an_index_of_another_schema_is_refused(self) -> None:
        (self.fixture.repo / mp.INDEX_PATH).write_text(json.dumps({"schema": "other", "receipts": ["x"]}))
        self.assertRefused("index")

    def test_a_receipt_of_another_schema_is_refused(self) -> None:
        self.edit_receipt(lambda receipt: receipt.update(schema="other"))
        self.assertRefused(f"is not a {mp.RECEIPT_SCHEMA} receipt")

    def test_a_receipt_id_that_is_not_a_full_sha_is_refused(self) -> None:
        for key in ("pulp_sha", "mirror_sha", "mirror_tree"):
            with self.subTest(key=key):
                self.fixture.write_receipt()
                self.edit_receipt(lambda receipt: receipt.update({key: "abc123"}))
                self.assertRefused(f"{key} is not a full commit/tree SHA")

    def test_a_receipt_with_another_rename_is_refused(self) -> None:
        self.edit_receipt(lambda receipt: receipt.update(rename={"from_prefix": "core/", "to_prefix": "mirror/"}))
        self.assertRefused("unsupported rename")

    def test_a_receipt_file_row_outside_the_mirror_is_refused(self) -> None:
        self.edit_receipt(lambda receipt: receipt["files"].append({"mirror_path": "elsewhere/x.cpp", "blob": "1" * 40}))
        self.assertRefused("receipt file row is malformed")

    def test_a_closure_manifest_that_is_absent_is_refused(self) -> None:
        (self.fixture.repo / f"provenance/mirror/closures/{EXPORT_ID}/foundation.json").unlink()
        self.assertRefused("closure manifest is missing")

    def test_a_closure_of_another_schema_is_refused(self) -> None:
        self.fixture.closure["schema"] = "other"
        self.fixture.write_receipt()
        self.assertRefused(f"is not a {mp.CLOSURE_SCHEMA} manifest")

    def test_a_receipt_that_names_no_units_is_refused(self) -> None:
        self.edit_receipt(lambda receipt: receipt.update(units=[]))
        self.assertRefused("names no units")

    def test_closures_exported_under_different_configures_are_refused(self) -> None:
        import copy
        second = copy.deepcopy(self.fixture.closure)
        second["configure"]["options"]["CMAKE_BUILD_TYPE"] = "Debug"
        relative = f"provenance/mirror/closures/{EXPORT_ID}/second.json"
        (self.fixture.repo / relative).write_text(json.dumps(second))
        digest = mp.sha256_file(self.fixture.repo / relative)
        self.edit_receipt(lambda receipt: receipt["units"].append(
            {**receipt["units"][0], "name": "second", "closure_manifest": relative, "closure_sha256": digest}))
        self.assertRefused("exported under different configures")

    def test_a_per_source_option_the_planner_cannot_decide_is_refused(self) -> None:
        self.fixture.add_mirror_file("mirror/core/runtime/CMakeLists.txt", (
            b'add_library(pulp-runtime STATIC src/a.cpp)\n'
            b'if(TARGET undecidable)\n'
            b'    set_source_files_properties(src/a.cpp PROPERTIES COMPILE_OPTIONS "-O1")\n'
            b'endif()\n'))
        self.fixture.write_receipt()
        self.assertRefused("per-source compile options:")

    def test_closures_that_record_no_configure_options_are_refused(self) -> None:
        self.fixture.closure["configure"] = {"options": None}
        self.fixture.write_receipt()
        self.assertRefused("record no configure options")


class ExternalDependencyRefusalTest(unittest.TestCase):
    """external_dependencies refuses a pinned input that is malformed or that two closures disagree on."""

    PIN = "b" * 40

    def row(self, **overrides) -> dict:
        row = {"path": f"@external/skia/{self.PIN}/include/a.h", "pin": self.PIN, "source": "https://example.invalid/skia.git",
               "asset_sha256": "c" * 64, "sha256": "d" * 64}
        row.update(overrides)
        return row

    def refused(self, *rows: dict) -> str:
        with self.assertRaises(mp.PlanError) as caught:
            mp.external_dependencies([("foundation", {"external_inputs": list(rows)})])
        return str(caught.exception)

    def test_a_row_that_plans_is_the_control(self) -> None:
        deps = mp.external_dependencies([("foundation", {"external_inputs": [self.row()]})])
        self.assertEqual(deps["skia"]["files"], {"include/a.h": "d" * 64})

    def test_a_malformed_external_path_is_refused(self) -> None:
        self.assertIn("external input path is malformed", self.refused(self.row(path="@external/skia")))

    def test_an_input_pinned_to_a_short_id_is_refused(self) -> None:
        self.assertIn("is not pinned to a full commit", self.refused(self.row(pin="abc123")))

    def test_closures_that_disagree_on_a_pin_are_refused(self) -> None:
        self.assertIn("closures disagree on the pin of skia",
                      self.refused(self.row(), self.row(path=f"@external/skia/{self.PIN}/include/b.h",
                                                        source="https://example.invalid/other.git")))

    def test_an_input_with_no_digest_is_refused(self) -> None:
        self.assertIn("has no recorded digest", self.refused(self.row(sha256=None)))

    def test_closures_that_disagree_on_a_digest_are_refused(self) -> None:
        self.assertIn("closures disagree on the digest of", self.refused(self.row(), self.row(sha256="e" * 64)))


class DependencyPreparationRefusalTest(unittest.TestCase):
    """Fetching, patching and preparing a dependency refuses what it cannot prove."""

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def test_a_fetch_that_fails_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mp.PlanError, "fetching fixture-dep failed"):
                mp.fetch_git("fixture-dep", str(Path(tmp) / "no-such-repository"), "a" * 40,
                             Path(tmp) / "checkout", submodules=False)

    def test_a_checkout_that_is_not_the_pin_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            self.git(source, "init", "-q", "-b", "main")
            (source / "a.txt").write_text("a\n")
            self.git(source, "add", "-A")
            self.git(source, "commit", "-q", "-m", "a")
            # A branch name fetches, but what it checks out is a commit id, not the name pinned.
            with self.assertRaisesRegex(mp.PlanError, "not the pinned main"):
                mp.fetch_git("fixture-dep", str(source), "main", Path(tmp) / "checkout", submodules=False)
            # Control: the commit id itself checks out.
            mp.fetch_git("fixture-dep", str(source), self.git(source, "rev-parse", "HEAD"),
                         Path(tmp) / "pinned", submodules=False)
            self.assertTrue((Path(tmp) / "pinned" / "a.txt").is_file())

    def test_an_unknown_patch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mp.PlanError, "unknown patch 'nope' for choc"):
                mp.apply_patch("choc", "nope", Path(tmp))
            with self.assertRaisesRegex(mp.PlanError, "unknown patch 'v8-compatibility' for yoga"):
                mp.apply_patch("yoga", "v8-compatibility", Path(tmp))

    def test_an_in_tree_dependency_with_no_repository_is_refused(self) -> None:
        entry = {"pin": "a" * 40, "source": "s", "asset_sha256": None, "root": "a" * 40, "files": {}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mp.PlanError, "is an in-tree dependency but no repository was given"):
                mp.prepare_dependencies({"vellum-gpu-runtime": entry}, Path(tmp), None, None)

    def test_the_skia_archive_must_be_given(self) -> None:
        entry = {"pin": "a" * 40, "source": "s", "asset_sha256": "c" * 64, "root": "a" * 40, "files": {}}
        with tempfile.TemporaryDirectory() as tmp:
            for skia_dir, asset in ((None, "c" * 64), (Path(tmp), None)):
                with self.subTest(skia_dir=skia_dir, asset=asset), \
                        self.assertRaisesRegex(mp.PlanError, "no extracted Skia/Dawn archive was given"):
                    mp.prepare_dependencies({"skia": entry}, Path(tmp), skia_dir, asset)

    def test_a_closure_exported_against_another_skia_archive_is_refused_when_preparing(self) -> None:
        entry = {"pin": "a" * 40, "source": "s", "asset_sha256": "c" * 64, "root": "a" * 40, "files": {}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mp.PlanError, f"exported against skia archive {'c' * 64}, but Vellum locks {'d' * 64}"):
                mp.prepare_dependencies({"skia": entry}, Path(tmp), Path(tmp), "d" * 64)

    def test_a_recorded_file_the_dependency_lacks_is_refused(self) -> None:
        entry = {"pin": "a" * 40, "source": "s", "asset_sha256": "c" * 64, "root": "a" * 40,
                 "files": {"include/absent.h": "d" * 64}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mp.PlanError, "has no include/absent.h"):
                mp.prepare_dependencies({"skia": entry}, Path(tmp), Path(tmp), "c" * 64)

    def test_a_template_naming_an_unknown_value_is_refused(self) -> None:
        with self.assertRaisesRegex(mp.PlanError, "unknown value @NOT_A_VALUE@"):
            mp.render_build_info("@NOT_A_VALUE@", {"pulp_sha": PULP_SHA}, "1.2.3", "Release", "t")


class TransferredTreeRefusalTest(unittest.TestCase):
    """A transferred checkout is refused when mirror/ lingers, framework/ holds a link, or a prelude cannot be made."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = TransferredFixture(Path(self.temporary.name))
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def assertRefused(self, fragment: str) -> None:
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn(fragment, str(caught.exception))

    def test_a_mirror_directory_beside_framework_is_refused(self) -> None:
        self.fixture.plan()  # control: the transferred tree alone plans
        (self.fixture.repo / "mirror").mkdir()
        self.assertRefused("both exist")

    def test_a_symlink_in_framework_is_refused(self) -> None:
        relative = "framework/core/runtime/src/a.cpp"
        data = (self.fixture.repo / relative).read_bytes()
        (self.fixture.repo / relative).unlink()
        elsewhere = Path(self.temporary.name) / "elsewhere.cpp"
        elsewhere.write_bytes(data)
        (self.fixture.repo / relative).symlink_to(elsewhere)
        self.assertRefused("framework/ holds symlinks")

    def test_a_prelude_generator_that_fails_is_refused(self) -> None:
        self.fixture.write(f"{self.fixture.VIEW}/js/embed_js.py", b"import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
        self.assertRefused("prelude generator failed: boom")


class GeneratedInputRefusalTest(unittest.TestCase):
    """prepare_generated refuses a generated input it cannot reproduce from the mirrored producers."""

    ENCODER = "mirror/tools/cmake/scripts/encode_binary_data.py"
    FONT = "mirror/external/fonts/A.ttf"

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

    def generated(self, path: str, digest: str | None = "f" * 64) -> None:
        self.fixture.closure["generated_inputs"].append({"path": path, "sha256": digest, "producer_paths": []})
        self.fixture.write_receipt()

    def encoder(self, body: str = "") -> None:
        """A mirrored encoder that writes `x` to --out, then runs `body`."""
        self.fixture.add_mirror_file(self.ENCODER, (
            "import sys\nout = sys.argv[sys.argv.index('--out') + 1]\n"
            "open(out, 'wb').write(b'x')\n" + body).encode())
        self.fixture.add_mirror_file(self.FONT, b"font")

    def test_a_generated_input_with_no_digest_is_refused(self) -> None:
        self.fixture.closure["generated_inputs"][0]["sha256"] = None
        self.fixture.write_receipt()
        self.assertRefused("generated input @build/core/runtime/generated/Release/pulp/runtime/build_info.hpp has no recorded digest")

    def test_a_build_info_header_for_an_export_with_no_recorded_inputs_is_refused(self) -> None:
        del mp.BUILD_INFO_INPUTS[EXPORT_ID]
        self.assertRefused(f"no build-info inputs are recorded for export {EXPORT_ID}")

    def test_a_build_info_header_that_is_not_timestamp_normalized_is_refused(self) -> None:
        self.fixture.closure["generated_inputs"][0]["normalization"] = {}
        self.fixture.write_receipt()
        self.assertRefused("is not timestamp-normalized")

    def test_a_binary_data_target_the_canvas_cmake_does_not_declare_is_refused(self) -> None:
        self.generated("@build/core/canvas/unknown-fonts_data.hpp")
        self.assertRefused("names binary-data target unknown-fonts")

    def test_a_binary_data_source_the_target_does_not_declare_is_refused(self) -> None:
        self.generated("@build/core/canvas/pulp-bundled-fonts_data_B_ttf.cpp")
        self.assertRefused("has no matching binary-data source")

    def test_an_encoder_that_fails_is_refused(self) -> None:
        self.encoder("sys.stderr.write('bad font')\nsys.exit(2)\n")
        self.generated("@build/core/canvas/pulp-bundled-fonts_data_A_ttf.cpp")
        self.assertRefused("mirrored encoder failed for external/fonts/A.ttf: bad font")

    def test_encoded_bytes_that_do_not_match_the_closure_digest_are_refused(self) -> None:
        self.encoder()
        self.generated("@build/core/canvas/pulp-bundled-fonts_data_A_ttf.cpp", "f" * 64)
        self.assertRefused("encoded by the mirrored producer does not match the closure digest")

    def test_a_binary_data_target_with_no_header_digest_is_refused(self) -> None:
        self.encoder()
        self.generated("@build/core/canvas/pulp-bundled-fonts_data_A_ttf.cpp", sha(b"x"))
        self.assertRefused("binary-data target pulp-bundled-fonts has no recorded header digest")

    def test_a_binary_data_target_whose_rows_miss_a_declared_source_is_refused(self) -> None:
        self.encoder()
        self.fixture.add_mirror_file("mirror/core/canvas/CMakeLists.txt", (
            "pulp_add_binary_data(pulp-bundled-fonts\n    SOURCES\n"
            "        ${PULP_ROOT_DIR}/external/fonts/A.ttf\n        ${PULP_ROOT_DIR}/external/fonts/B.ttf\n"
            "    NAMESPACE pulp_bundled_fonts\n)\n").encode())
        self.generated("@build/core/canvas/pulp-bundled-fonts_data_A_ttf.cpp", sha(b"x"))
        self.generated("@build/core/canvas/pulp-bundled-fonts_data.hpp")
        self.assertRefused("closure rows do not cover its declared sources")


class IncludeDirectoryRefusalTest(unittest.TestCase):
    """map_include maps a closure's include directory only to a place the plan can prove."""

    PIN = "1" * 40
    RECIPE_PATH = f"@external/vellum-gpu-runtime/{PIN}/gpu-runtime/include"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        (self.repo / "mirror/core/a/include").mkdir(parents=True)
        self.deps = {"vellum-gpu-runtime": {"pin": self.PIN, "root": self.PIN, "files": {}},
                     "highway": {"pin": self.PIN, "root": self.PIN, "files": {}}}
        self.roots = {"vellum-gpu-runtime": self.repo, "highway": Path("/deps/highway")}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def map(self, path: str, roots: dict | None = None, deps: dict | None = None) -> str:
        return mp.map_include(path, self.repo, self.roots if roots is None else roots, {}, "B",
                              self.deps if deps is None else deps)

    def refused(self, path: str, **kwargs) -> str:
        with self.assertRaises(mp.PlanError) as caught:
            self.map(path, **kwargs)
        return str(caught.exception)

    def test_an_in_tree_include_maps_when_pinned_and_inside_the_interface_tree(self) -> None:
        self.assertEqual(self.map(self.RECIPE_PATH), str(self.repo / "gpu-runtime/include"))
        self.assertEqual(self.map("core/a/include"),
                         str(self.repo / "mirror/core/a/include"))

    def test_an_in_tree_include_no_closure_pins_is_refused(self) -> None:
        self.assertIn("names vellum-gpu-runtime, but no closure pins it", self.refused(self.RECIPE_PATH, roots={}))
        self.assertIn("names vellum-gpu-runtime, but no closure pins it", self.refused(self.RECIPE_PATH, deps={}))

    def test_an_in_tree_include_at_another_root_is_refused(self) -> None:
        self.assertIn("not its pin", self.refused(f"@external/vellum-gpu-runtime/{'2' * 40}/gpu-runtime/include"))

    def test_an_in_tree_include_that_climbs_out_is_refused(self) -> None:
        self.assertIn("is not confined to its dependency",
                      self.refused(f"@external/vellum-gpu-runtime/{self.PIN}/gpu-runtime/include/../../x"))

    def test_an_in_tree_include_outside_the_interface_tree_is_refused(self) -> None:
        self.assertIn("is outside vellum-gpu-runtime's interface tree",
                      self.refused(f"@external/vellum-gpu-runtime/{self.PIN}/gpu-runtime/src"))

    def test_an_external_include_with_no_prepared_source_is_refused(self) -> None:
        self.assertIn("names a dependency with no prepared source",
                      self.refused(f"@external/lonely/{self.PIN}/include"))

    def test_an_external_include_no_closure_names_is_refused(self) -> None:
        self.assertIn("is not at the root the closures pin", self.refused(f"@external/highway/{self.PIN}/hwy", deps={}))

    def test_a_path_that_is_not_mirror_relative_is_refused(self) -> None:
        for path in ("@other/include", "/etc/include", "core/../../outside"):
            with self.subTest(path=path):
                self.assertIn("is not a mirror-relative path", self.refused(path))

    def test_a_mirror_directory_the_mirror_does_not_hold_is_refused(self) -> None:
        self.assertIn("is not in the landed mirror", self.refused("core/absent/include"))


class PlanTargetRefusalTest(unittest.TestCase):
    """plan refuses a closure target it cannot package or prove."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))
        self.target = self.fixture.closure["targets"][0]
        mp.BUILD_INFO_INPUTS[EXPORT_ID] = {"version": "1.2.3"}

    def tearDown(self) -> None:
        mp.BUILD_INFO_INPUTS.pop(EXPORT_ID, None)
        self.temporary.cleanup()

    def assertRefused(self, fragment: str) -> None:
        self.fixture.write_receipt()
        with self.assertRaises(mp.PlanError) as caught:
            self.fixture.plan()
        self.assertIn(fragment, str(caught.exception))

    def test_a_target_that_is_not_a_static_library_is_refused(self) -> None:
        self.target["type"] = "EXECUTABLE"
        self.assertRefused("foundation/pulp-runtime: only static mirrored targets are packaged")

    def test_an_external_link_input_with_no_recipe_is_refused(self) -> None:
        self.target["external_link_inputs"].append({"dependency": "openssl", "pin": "b" * 40})
        self.assertRefused("has external link inputs with no recipe")

    def test_a_source_the_landed_mirror_lacks_is_refused(self) -> None:
        self.target["sources"].append("core/runtime/src/missing.cpp")
        self.assertRefused("source core/runtime/src/missing.cpp is not in the landed mirror")

    def test_a_generated_source_that_was_not_produced_is_refused(self) -> None:
        self.target["sources"].append("@build/core/view/web_compat_preludes_gen.cpp")
        self.assertRefused("generated source @build/core/view/web_compat_preludes_gen.cpp was not produced")

    def test_a_target_two_units_both_describe_is_refused(self) -> None:
        import copy
        second = copy.deepcopy(self.fixture.closure)
        relative = f"provenance/mirror/closures/{EXPORT_ID}/second.json"
        self.fixture.write_receipt()
        (self.fixture.repo / relative).write_text(json.dumps(second))
        receipt_path = self.fixture.repo / f"provenance/mirror/receipts/{EXPORT_ID}.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["units"].append({**receipt["units"][0], "name": "second", "closure_manifest": relative,
                                 "closure_sha256": mp.sha256_file(self.fixture.repo / relative)})
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(mp.PlanError, "target pulp-runtime appears in two units"):
            self.fixture.plan()

    def test_a_choke_header_the_guard_cannot_carry_is_refused(self) -> None:
        self.fixture.add_mirror_file("mirror/core/runtime/include/pulp/runtime/gpu_fwd.hpp",
                                     b"namespace gpu_runtime { class GpuSurface; }\n")
        self.assertRefused("needs #pragma once to carry the guard")


if __name__ == "__main__":
    unittest.main()
