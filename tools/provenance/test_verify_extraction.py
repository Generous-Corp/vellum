#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

try:
    from .git_test_environment import disable_auto_maintenance
except ImportError:  # Direct script execution.
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))  # -I drops the script dir; append keeps the standard library first
    from git_test_environment import disable_auto_maintenance

disable_auto_maintenance()


MODULE_PATH = Path(__file__).with_name("verify_extraction.py")
SPEC = importlib.util.spec_from_file_location("verify_extraction", MODULE_PATH)
assert SPEC and SPEC.loader
verify_extraction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_extraction)
ROOT = MODULE_PATH.parents[2]
content_scan_policy = verify_extraction.content_scan_policy
ARTIFACT_MODULE_PATH = MODULE_PATH.parents[2] / "scripts/verify_sdk_artifact.py"
ARTIFACT_SPEC = importlib.util.spec_from_file_location(
    "verify_sdk_artifact", ARTIFACT_MODULE_PATH
)
assert ARTIFACT_SPEC and ARTIFACT_SPEC.loader
verify_sdk_artifact = importlib.util.module_from_spec(ARTIFACT_SPEC)
ARTIFACT_SPEC.loader.exec_module(verify_sdk_artifact)


class VerifyExtractionTests(unittest.TestCase):
    def test_authority_phase_is_cross_bound_to_active_lock(self) -> None:
        start = "a" * 40
        record = "b" * 40
        activation = "c" * 40
        extraction = {
            "status": "active",
            "authority": {
                "state": "active",
                "ownership_start_commit": start,
                "authority_record_commit": record,
                "authority_record_path": "provenance/authority/records/native.json",
                "authority_ref": "refs/tags/authority/native",
                "pulp_activation_commit": activation,
                "pulp_authority_event_path": ".github/vellum-change-events/active.json",
                "pulp_authority_event_id": "active",
                "accepted_by": "@owner",
                "accepted_at": "2026-07-24T02:00:00Z",
            },
        }
        lock = {
            "state": "active",
            "vellum_authority_start_commit": start,
            "vellum_authority_record_commit": record,
            "pulp_activation_commit": activation,
        }
        self.assertEqual(
            verify_extraction.verify_authority_phase(extraction, lock), (True, [])
        )
        extraction["authority"]["pulp_activation_commit"] = "d" * 40
        transferred, errors = verify_extraction.verify_authority_phase(
            extraction, lock
        )
        self.assertFalse(transferred)
        self.assertIn(
            "active extraction authority has invalid pulp_activation_commit", errors
        )

    def test_git_blob_identity_matches_git_format(self) -> None:
        self.assertEqual(
            verify_extraction.git_blob_sha(b"test content\n"),
            "d670460b4b4aece5915caf5c68d12f560a9fe3e4",
        )

    def test_active_scan_finds_pulp_namespace_and_plugin_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "foundation").mkdir()
            (root / "foundation/bad.cpp").write_text(
                "auto* x = pulp::audio::engine();\nAudioUnit thing;\n", encoding="utf-8"
            )
            findings = verify_extraction.scan_active_surface(root, ("foundation",))
            self.assertEqual({finding["rule"] for finding in findings}, {
                "pulp-public-namespace", "audio-plugin-sdk"
            })

    def test_active_scan_reads_build_inputs_and_configure_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            demo = root / "apps/demo"
            demo.mkdir(parents=True)
            (demo / "CMakeLists.txt").write_text(
                "target_link_libraries(demo PRIVATE pulp-audio)\n", encoding="utf-8"
            )
            (demo / "config.hpp.in").write_text(
                "#include <pulp/audio/engine.hpp>\n", encoding="utf-8"
            )
            (demo / "main.cpp.template").write_text(
                "auto* x = pulp::audio::engine();\n", encoding="utf-8"
            )
            # Python is the contamination tooling itself, so the native scan
            # does not read it (content_scan_policy.NATIVE_SOURCE_EXCLUDED_SUFFIXES).
            (demo / "policy.py").write_text("FORBIDDEN = 'pulp::'\n", encoding="utf-8")
            findings = verify_extraction.scan_active_surface(root, ("apps",))
            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {
                    ("apps/demo/CMakeLists.txt", "pulp-package-or-target"),
                    ("apps/demo/config.hpp.in", "pulp-public-namespace"),
                    ("apps/demo/main.cpp.template", "pulp-public-namespace"),
                },
            )

    def test_module_defines_each_top_level_name_once(self) -> None:
        import ast

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])

    def test_default_inventory_scans_authoring_and_ui_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "authoring").mkdir()
            (root / "authoring/host.mm").write_text(
                "auto legacy = pulp::view::host();\n", encoding="utf-8"
            )
            (root / "packages/vellum-ui/src").mkdir(parents=True)
            (root / "packages/vellum-ui/src/runtime.js").write_text(
                "const forbidden = '@pulp/audio';\n", encoding="utf-8"
            )

            findings = verify_extraction.scan_active_surface(root)

            self.assertEqual(
                {finding["path"] for finding in findings},
                {
                    "authoring/host.mm",
                    "packages/vellum-ui/src/runtime.js",
                },
            )

    def test_default_inventory_scans_c_abi_and_swift_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bindings/include").mkdir(parents=True)
            (root / "bindings/include/vellum.h").write_text(
                "auto* x = pulp::audio::engine();\n", encoding="utf-8"
            )
            (root / "packages/vellum-swift/Sources").mkdir(parents=True)
            (root / "packages/vellum-swift/Sources/Bridge.swift").write_text(
                "let x = pulp::audio::engine()\n", encoding="utf-8"
            )

            findings = verify_extraction.scan_active_surface(root)

            self.assertEqual(
                {finding["path"] for finding in findings},
                {
                    "bindings/include/vellum.h",
                    "packages/vellum-swift/Sources/Bridge.swift",
                },
            )

    def test_c_abi_and_swift_package_are_active_source_roots(self) -> None:
        new_roots = ("bindings", "packages/vellum-swift")
        boundary = verify_extraction.configured_boundary(ROOT)
        for entry in new_roots:
            self.assertIn(entry, verify_extraction.EXPECTED_ACTIVE_ROOTS)
            self.assertIn(entry, boundary["active_source_roots"])
        self.assertEqual(
            tuple(boundary["active_source_roots"]), verify_extraction.EXPECTED_ACTIVE_ROOTS
        )

    def test_active_scan_ignores_historical_evidence_outside_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "provenance").mkdir()
            (root / "provenance/history.txt").write_text(
                "pulp::audio::engine();\n", encoding="utf-8"
            )
            self.assertEqual(
                verify_extraction.scan_active_surface(root, ("foundation",)), []
            )

    def test_artifact_scan_finds_retired_path_namespace_and_plugin_sdk(self) -> None:
        findings = verify_sdk_artifact.payload_contamination_findings(
            "sdk/include/pulp/audio.hpp", b"namespace pulp { AudioUnit x; }\n"
        )
        self.assertEqual(
            {finding["rule"] for finding in findings},
            {"pulp-named-payload", "pulp-public-namespace", "audio-plugin-sdk"},
        )

    def write_mirror(self, root: Path, relative: str, content: bytes) -> dict[str, str]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "mirror_path": relative,
            "git_blob_sha": verify_extraction.git_blob_sha(content),
            "pulp_commit": "a" * 40,
            "pulp_path": relative.removeprefix("mirror/"),
            "export_receipt": "provenance/mirror/receipts/first.json",
        }

    def test_mirror_accepts_recorded_extracted_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = b"#include <pulp/canvas/canvas.hpp>\nnamespace pulp::view { int PULP_X; }\n"
            record = self.write_mirror(root, "mirror/core/view/src/view.cpp", source)

            self.assertEqual(verify_extraction.mirror_origin_findings(root, [record]), [])
            self.assertEqual(verify_extraction.mirror_edge_findings(root), [])
            self.assertEqual(verify_extraction.scan_active_surface(root), [])

    def test_mirror_rejects_unrecorded_and_edited_pulp_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorded = self.write_mirror(root, "mirror/core/view/src/a.cpp", b"int a;\n")
            self.write_mirror(root, "mirror/core/view/src/b.cpp", b"int b;\n")
            (root / recorded["mirror_path"]).write_bytes(b"int a = 1;\n")

            findings = verify_extraction.mirror_origin_findings(root, [recorded])

            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {
                    ("mirror/core/view/src/a.cpp", "mirror-blob-differs-from-origin-record"),
                    ("mirror/core/view/src/b.cpp", "mirror-file-without-origin-record"),
                },
            )

    def test_mirror_keeps_superseded_append_only_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.write_mirror(root, "mirror/core/view/src/a.cpp", b"int a;\n")
            second = self.write_mirror(root, "mirror/core/view/src/a.cpp", b"int a = 2;\n")
            removed = dict(first, mirror_path="mirror/core/view/src/gone.cpp")

            self.assertEqual(
                verify_extraction.mirror_origin_findings(root, [first, second, removed]),
                [],
            )

    def test_native_root_rejects_copy_of_recorded_pulp_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"namespace detail { int copied; }\n"
            record = self.write_mirror(root, "mirror/core/view/src/a.cpp", content)
            (root / "graphics").mkdir()
            (root / "graphics/a.cpp").write_bytes(content)

            findings = verify_extraction.copied_seed_findings(
                root, ("graphics",), {record["git_blob_sha"]}
            )

            self.assertEqual([finding["path"] for finding in findings], ["graphics/a.cpp"])

    def test_mirror_rejects_audio_plugin_host_and_product_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(
                root,
                "mirror/core/view/src/meter.cpp",
                b"#include <pulp/audio/buffer.hpp>\n"
                b"#include <pluginterfaces/base/funknown.h>\n"
                b"auto x = pulp::signal::gain(1.0f);\n",
            )
            self.write_mirror(
                root,
                "mirror/core/view/CMakeLists.txt",
                b"target_link_libraries(pulp-view-core PUBLIC pulp-format-core)\n"
                b"target_link_libraries(pulp-view-core PRIVATE \"-framework CoreAudio\")\n",
            )
            self.write_mirror(
                root,
                "mirror/core/view/src/host.mm",
                b"#import <AudioToolbox/AudioToolbox.h>\n",
            )

            findings = verify_extraction.mirror_edge_findings(root)

            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {
                    ("mirror/core/view/src/meter.cpp", "mirror-product-include"),
                    ("mirror/core/view/src/meter.cpp", "mirror-product-symbol"),
                    ("mirror/core/view/CMakeLists.txt", "mirror-product-symbol"),
                    ("mirror/core/view/CMakeLists.txt", "mirror-audio-framework-link"),
                    ("mirror/core/view/src/host.mm", "mirror-product-include"),
                },
            )
            self.assertEqual(
                sum(finding["path"].endswith("meter.cpp") for finding in findings), 3
            )

    def test_framework_rejects_renamed_product_edges(self) -> None:
        # framework/ keeps the mirror-edge rules, in the renamed spelling.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "framework/core/view/src/meter.cpp",
                              b"#include <vellum/audio/buffer.hpp>\nauto x = vellum::signal::gain(1.0f);\n")
            self.write_mirror(root, "framework/core/view/src/host.mm", b"#import <AudioToolbox/AudioToolbox.h>\n")
            self.write_mirror(root, "framework/core/view/src/clean.cpp", b"#include <vellum/view/view.hpp>\n")
            findings = verify_extraction.mirror_edge_findings(root, prefix=verify_extraction.FRAMEWORK_ROOT)
            self.assertEqual({f["path"] for f in findings},
                             {"framework/core/view/src/meter.cpp", "framework/core/view/src/host.mm"})
            # The mirror-root scan does not read framework/.
            self.assertEqual(verify_extraction.mirror_edge_findings(root), [])

    def test_pulp_namespace_is_scoped_to_the_mirror_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"namespace pulp::view { int x; }\n"
            self.write_mirror(root, "mirror/core/view/src/a.cpp", content)
            (root / "graphics").mkdir()
            (root / "graphics/a.cpp").write_bytes(content)

            findings = verify_extraction.scan_active_surface(root)

            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {("graphics/a.cpp", "pulp-public-namespace")},
            )

    def test_renamed_mirror_namespaces_are_refused_in_native_trees(self) -> None:
        # Each spelling a copied, renamed mirror file can take.
        evasions = {
            "graphics/qualified.cpp": b"namespace vellum::view { int x; }\n",
            "graphics/spaced.cpp": b"auto* v = vellum :: view::make();\n",
            "graphics/nested.cpp": b"namespace vellum {\nnamespace view {\nint x;\n}\n}\n",
            "graphics/imported.mm": b"#import <vellum/view/view.hpp>\n",
            "graphics/relative.cpp": b'#include "../include/vellum/view/view.hpp"\n',
            "graphics/probed.cpp": b"#if __has_include(<vellum/canvas/canvas.hpp>)\n#endif\n",
            "graphics/using.cpp": b"using namespace vellum;\nint f() { return view::g(); }\n",
            "graphics/nested_comment.cpp": b"namespace vellum { /* ui */ namespace view {\nint x;\n}}\n",
            "graphics/nested_between.cpp": b"namespace vellum {\nclass Fwd;\nnamespace view {\nint x;\n}\n}\n",
            "graphics/unqualified.cpp": b"namespace vellum {\nint f() { return canvas::g(); }\n}\n",
            "graphics/jni.cpp": b"namespace vellum::android { int x; }\n",
            "graphics/embed_js.py": b'out.write("namespace vellum::view::preludes {\\n\\n")\n',
            "tools/scratch/copied.cpp": b"namespace vellum::state { int x; }\n",
            "components/w/copied.hpp": b"#include <vellum/render/x.hpp>\n",
        }
        legitimate = {
            "graphics/own.cpp": b"#include <vellum/runtime/kernel.hpp>\nvellum::runtime::Kernel k;\n"
                                b"vellum::graphics::X g;\nusing namespace vellum;\nint y = graphics::z();\n",
            "graphics/near_miss.cpp": b"namespace vellum::graphics {\nauto r = std::ranges::views::all;\n"
                                      b"int canvas_size = 0; int view_count = state_of(1);\n}\n",
            "tools/mirror/consumer/main.cpp": b"#include <vellum/view/view.hpp>\n",
        }
        # The consumer exemption is per file: a new file there is scanned.
        evasions["tools/mirror/consumer/new_helper.cpp"] = b"#include <vellum/view/view.hpp>\n"
        evasions["tools/mirror/consumer/sub/deep.hpp"] = b"namespace vellum::canvas { int x; }\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/src/a.cpp", b"namespace vellum::view { int x; }\n")
            for relative, content in {**evasions, **legitimate}.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(content)

            findings = verify_extraction.scan_renamed_mirror(root)

            self.assertEqual(sorted({f["path"] for f in findings}), sorted(evasions))
            self.assertTrue(all(f["rule"] == "renamed-mirror-namespace" for f in findings))
            # The main native scan leaves this rule to the wider one.
            self.assertNotIn("renamed-mirror-namespace", verify_extraction.FORBIDDEN_ACTIVE_PATTERNS)

    def test_framework_tests_may_name_the_units_but_only_consume_them(self) -> None:
        legitimate = {
            "tools/framework-tests/README.md": b"Tests of vellum::view.\n",
            "tools/framework-tests/support.hpp": b"#include <vellum/view/view.hpp>\nnamespace support {}\n",
            "tools/framework-tests/widget_bridge_test.cpp":
                b"#include <vellum/view/widget_bridge.hpp>\nnamespace view = vellum::view;\n"
                b"using namespace vellum::view;\nint main() { view::View root; }\n",
            "tools/framework-tests/window_test.mm": b"#import <vellum/view/window_host.hpp>\n",
            "tools/framework-tests/calls_test.cpp":
                b"#include <vellum/view/view.hpp>\nstatic void f() {\n    if (vellum::view::is_x(1)) {\n    }\n"
                b"    auto g = [](vellum::view::View& v) {\n    };\n}\n",
        }
        refused = {
            # A vellum namespace opened in any spelling.
            "tools/framework-tests/defines_test.cpp": b"namespace vellum::view { int x; }\n",
            "tools/framework-tests/nested.hpp": b"#pragma once\n  namespace vellum {\nnamespace view {}\n}\n",
            "tools/framework-tests/inline.hpp": b"inline namespace vellum { }\n",
            "tools/framework-tests/split_namespace_test.cpp":
                b"namespace\nvellum::view { int x; }\n",
            "tools/framework-tests/inline_namespace_statement_test.cpp":
                b"int x; namespace vellum::view { }\n",
            "tools/framework-tests/macro_namespace_test.cpp":
                b"#define OPEN namespace vellum::view {\nOPEN\n",
            "tools/framework-tests/comment_namespace_test.cpp":
                b"namespace /* the framework namespace is forbidden */ vellum::view { int x; }\n",
            # Out-of-line definitions of unit symbols, directly or through an alias.
            "tools/framework-tests/qualified_test.cpp":
                b"void vellum::view::WidgetBridge::load_script(const std::string& code) {\n}\n",
            "tools/framework-tests/aliased_test.cpp":
                b"namespace view = vellum::view;\nbool view::Toggle::is_on() const noexcept {\n    return true;\n}\n",
            "tools/framework-tests/allman_qualified_test.cpp":
                b"void vellum::view::WidgetBridge::load_script(const std::string& code)\n{\n}\n",
            "tools/framework-tests/direct_ctor_initializer_test.cpp":
                b"vellum::view::WidgetBridge::WidgetBridge(int value)\n"
                b"    : state_(value)\n{\n}\n",
            "tools/framework-tests/aliased_ctor_initializer_test.cpp":
                b"namespace view = vellum::view;\n"
                b"view::Widget::Widget(int value)\n"
                b"    : value_(value)\n{\n}\n",
            "tools/framework-tests/comment_qualified_test.cpp":
                b"void view /* split the qualification */ :: Toggle::is_on() {\n}\n",
            # A library could be linked into something that ships.
            "tools/framework-tests/CMakeLists.txt": b"add_executable(t t_test.cpp)\nadd_library(kept STATIC k.cpp)\n",
            # Shapes a kept framework source would take.
            "tools/framework-tests/widget_bridge.cpp": b"#include <vellum/view/view.hpp>\n",
            "tools/framework-tests/sub/deep_test.cpp": b"#include <vellum/view/view.hpp>\n",
            "tools/framework-tests/prelude.js": b"function on() {}\n",
            # A Pulp file that kept its names.
            "tools/framework-tests/copied_test.cpp": b"#include <pulp/view/view.hpp>\npulp::view::View v;\n",
            # The same legitimate test anywhere else is held to the renamed-mirror rule.
            "tools/framework-tests-extra/widget_bridge_test.cpp": b"#include <vellum/view/widget_bridge.hpp>\n",
            "graphics/widget_bridge_test.cpp": b"namespace view = vellum::view;\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/src/a.cpp", b"namespace vellum::view { int x; }\n")
            for relative, content in {**legitimate, **refused}.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(content)

            findings = verify_extraction.scan_renamed_mirror(root)

            self.assertEqual(sorted({f["path"] for f in findings}), sorted(refused))
            self.assertEqual({f["rule"] for f in findings},
                             {"framework-test-defines-vellum", "framework-test-file-shape",
                              "pulp-public-namespace", "renamed-mirror-namespace", "framework-test-library",
                              "framework-test-qualified-definition"})
            self.assertEqual({f["path"] for f in findings if f["rule"] == "renamed-mirror-namespace"},
                             {"tools/framework-tests-extra/widget_bridge_test.cpp",
                              "graphics/widget_bridge_test.cpp"})

    def test_framework_tests_refuse_pulp_origin_blobs(self) -> None:
        recorded = b"// recorded in the transfer\nint recorded_marker = 1;\n"
        pasted = b"// a Pulp test no record names\nint pasted_marker = 2;\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "vellum"
            pulp = Path(temporary) / "pulp"
            (root / "provenance/framework").mkdir(parents=True)
            (root / verify_extraction.FRAMEWORK_TRANSFER_RECORD).write_text(json.dumps({"records": [
                {"pulp_git_blob_sha": verify_extraction.git_blob_sha(recorded),
                 "framework_git_blob_sha": verify_extraction.git_blob_sha(b"other\n")}]}))
            tests = root / "tools/framework-tests"
            tests.mkdir(parents=True)
            (tests / "recorded_test.cpp").write_bytes(recorded)
            (tests / "pasted_test.cpp").write_bytes(pasted)
            (tests / "native_test.cpp").write_bytes(b"int native_marker = 3;\n")
            (pulp / "test").mkdir(parents=True)
            (pulp / "test/test_pasted.cpp").write_bytes(pasted)
            git = ["git", "-C", str(pulp), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "pulp"], check=True)
            seed = verify_extraction.git_blob_sha(b"seed\n")
            (tests / "seed_test.cpp").write_bytes(b"seed\n")

            without_pulp, checked = verify_extraction.framework_test_blob_findings(root, {seed}, None)
            self.assertFalse(checked)
            self.assertEqual(sorted(f["path"] for f in without_pulp),
                             ["tools/framework-tests/recorded_test.cpp", "tools/framework-tests/seed_test.cpp"])
            with_pulp, checked = verify_extraction.framework_test_blob_findings(root, {seed}, pulp)
            self.assertTrue(checked)
            self.assertEqual(sorted(f["path"] for f in with_pulp),
                             ["tools/framework-tests/pasted_test.cpp", "tools/framework-tests/recorded_test.cpp",
                              "tools/framework-tests/seed_test.cpp"])

    def test_nothing_installs_framework_test_content(self) -> None:
        files = {
            "CMakeLists.txt": "install(TARGETS vellum-runtime EXPORT VellumTargets)\n"
                              "add_subdirectory(tools/framework-tests)\n"
                              "install(DIRECTORY\n    tools/framework-tests/\n    DESTINATION share)\n",
            "cmake/Other.cmake": "install(FILES \"${PROJECT_SOURCE_DIR}/tools/framework-tests/README.md\" DESTINATION doc)\n",
            "tools/framework-tests/CMakeLists.txt": "add_executable(t t_test.cpp)\n  install(TARGETS t)\n",
            "framework/core/view/CMakeLists.txt": "install(DIRECTORY framework-tests)\n",
            "framework/core/view/Other.cmake": "install(TARGETS vellum-view)\n",
            "mirror/core/CMakeLists.txt": "install(DIRECTORY framework-tests)\n",
            # Commands after another, nested, in any case, spaced before the parenthesis.
            "cmake/SameLine.cmake": "if(TRUE) install(DIRECTORY tools/framework-tests DESTINATION x) endif()\n",
            "cmake/Upper.cmake": "IF(ON)\n  INSTALL (\n    DIRECTORY tools/framework-tests)\nENDIF()\n",
            "cmake/Mixed.cmake": "Install\n(FILES tools/framework-tests/a.cpp DESTINATION x)\n",
            "cmake/Call.cmake": "cmake_language(CALL install DIRECTORY tools/framework-tests DESTINATION x)\n",
            "cmake/Eval.cmake": "cmake_language(EVAL CODE \"install(DIRECTORY tools/framework-tests)\")\n",
            # Only in comments, strings and bracket arguments: not an install rule.
            "cmake/Inert.cmake": "# install(DIRECTORY tools/framework-tests)\n"
                                 "#[[ install(DIRECTORY tools/framework-tests) ]]\n"
                                 "message(\"install(DIRECTORY tools/framework-tests)\")\n"
                                 "set(x [=[install(DIRECTORY tools/framework-tests)]=])\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, content in files.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text(content)

            findings = verify_extraction.framework_test_install_findings(root)

            self.assertEqual([(f["path"], f["line"]) for f in findings],
                             [("CMakeLists.txt", 3), ("cmake/Call.cmake", 1), ("cmake/Eval.cmake", 1),
                              ("cmake/Mixed.cmake", 1), ("cmake/Other.cmake", 1), ("cmake/SameLine.cmake", 1),
                              ("cmake/Upper.cmake", 2), ("framework/core/view/CMakeLists.txt", 1),
                              ("tools/framework-tests/CMakeLists.txt", 2)])

    def test_framework_test_cmake_commands_are_found_however_written(self) -> None:
        refused = {
            "add_executable(t t_test.cpp) add_library(k STATIC k.cpp)\n": "framework-test-library",
            "if(TRUE)\n  add_library(k STATIC k.cpp)\nendif()\n": "framework-test-library",
            "if(TRUE) add_library(k STATIC k.cpp) endif()\n": "framework-test-library",
            "ADD_LIBRARY(k STATIC k.cpp)\n": "framework-test-library",
            "Add_Library(k k.cpp)\n": "framework-test-library",
            "add_library (k k.cpp)\n": "framework-test-library",
            "add_library\n(k k.cpp)\n": "framework-test-library",
            "set(x \\() add_library(k k.cpp)\n": "framework-test-library",
            "cmake_language(CALL add_library k k.cpp)\n": "framework-test-cmake-indirection",
            "include(../../cmake/Lib.cmake)\n": "framework-test-cmake-indirection",
            "add_subdirectory(lib)\n": "framework-test-cmake-indirection",
        }
        inert = ("# add_library(k k.cpp)\n#[[ add_library(k) install(TARGETS k) ]]\n"
                 "message(\"add_library(k) install(TARGETS k) include(x)\")\n"
                 "set(x [==[add_library(k)]==])\nadd_executable(t t_test.cpp)\nfunction(helper)\nendfunction()\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "tools/framework-tests/CMakeLists.txt"
            path.parent.mkdir(parents=True)
            for content, rule in refused.items():
                path.write_text(content)
                findings = verify_extraction.framework_test_findings("tools/framework-tests/CMakeLists.txt", path)
                self.assertEqual([f["rule"] for f in findings], [rule], content)
            path.write_text(inert)
            self.assertEqual(verify_extraction.framework_test_findings("tools/framework-tests/CMakeLists.txt", path), [])
            self.assertEqual(verify_extraction.framework_test_install_findings(root), [])

    def test_unreadable_pulp_tree_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "vellum"
            (root / "tools/framework-tests").mkdir(parents=True)
            (root / "tools/framework-tests/native_test.cpp").write_bytes(b"int native_marker = 3;\n")
            notgit = Path(temporary) / "not-a-checkout"
            notgit.mkdir()

            findings, checked = verify_extraction.framework_test_blob_findings(root, set(), notgit)

            self.assertFalse(checked)
            self.assertEqual(len(findings), 1)
            self.assertIn("unreadable Pulp tree", findings[0]["path"])
            clean, _ = verify_extraction.framework_test_blob_findings(root, set(), None)
            self.assertEqual(clean, [])

    def test_framework_test_root_still_refuses_copies_of_framework_files(self) -> None:
        source = (b"namespace vellum::view {\nint widget_bridge_marker(int a, int b) {\n"
                  b"    return a * 31 + b * 17 + 5;\n}\n}\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "framework/core/view/src").mkdir(parents=True)
            (root / "framework/core/view/src/widget_bridge.cpp").write_bytes(source)
            (root / "tools/framework-tests").mkdir(parents=True)
            (root / "tools/framework-tests/widget_bridge_test.cpp").write_bytes(b"// kept\n" + source)

            findings = verify_extraction.normalized_copy_findings(root)

            self.assertEqual(findings, [{"path": "tools/framework-tests/widget_bridge_test.cpp",
                                         "mirror": "framework/core/view/src/widget_bridge.cpp"}])

    def test_renamed_mirror_units_match_the_mirror_namespace_census(self) -> None:
        # The refused units are the mirror's top-level pulp::<unit> namespaces
        # and include units, less the ones native Vellum owns. A new mirrored
        # unit (pulp::android once escaped an include-unit census) must join.
        rule = content_scan_policy.FORBIDDEN_NATIVE_CONTENT_PATTERNS["renamed-mirror-namespace"][0]
        refused = set(re.search(r"vellum\\s\*::\\s\*\(\?:([^)]*)\)", rule).group(1).split("|"))
        census: set[str] = set()
        units = ROOT / ("framework" if (ROOT / "framework").is_dir() else "mirror")
        for path in units.rglob("*"):
            if path.is_file() and not path.relative_to(units).as_posix().startswith("external/"):
                text = path.read_bytes().decode("utf-8", errors="replace")
                for spelling in ("pulp", "vellum"):
                    census.update(re.findall(rf"namespace\s+{spelling}\s*::\s*(\w+)", text))
                    census.update(re.findall(rf"namespace\s+{spelling}\s*\{{\s*namespace\s+(\w+)", text))
        census |= {p.name for p in units.glob("core/*/include/*/*")
                   if p.is_dir() and p.parent.name in {"pulp", "vellum"}}
        native = {p.name for top in ("foundation", "runtime", "graphics", "authoring")
                  for p in (ROOT / top / "include/vellum").glob("*") if p.is_dir()}
        self.assertIn("android", census)
        self.assertEqual(refused, census - native)

    def test_design_pipeline_folder_is_exempt_on_the_folder_boundary_only(self) -> None:
        body = b"#include <vellum/view/design_import.hpp>\nnamespace vellum::authoring { vellum::view::DesignIR x; }\n"
        allowed = {
            "authoring/design_pipeline/design_pipeline.cpp",
            "authoring/design_pipeline/include/vellum/authoring/design_pipeline.hpp",
            "authoring/design_pipeline/tests/deep/nested/golden_test.cpp",
        }
        refused = {
            "authoring/x.hpp",
            "authoring/design_pipeline.hpp",
            "authoring/other/design_pipeline.hpp",
            "authoring/other/design_pipeline/foo.hpp",
            "authoring/design_pipeline_evil/foo.hpp",
            "authoring/design_pipelines/foo.hpp",
            "authoring/src/design_pipeline/foo.hpp",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in allowed | refused:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(body)

            findings = verify_extraction.scan_renamed_mirror(root)

            self.assertEqual(sorted({f["path"] for f in findings}), sorted(refused))

    def test_renamed_mirror_exemptions_are_pinned(self) -> None:
        # Any change to what the rule skips must show up as an edit here.
        self.assertEqual(verify_extraction.RENAMED_MIRROR_EXEMPT_PREFIXES, ("authoring/design_pipeline/",))
        self.assertEqual(verify_extraction.FRAMEWORK_TEST_ROOT, "tools/framework-tests/")
        self.assertEqual(verify_extraction.RENAMED_MIRROR_EXEMPT_PATHS, {
            "tools/mirror/consumer/cross_image_main.cpp",
            "tools/mirror/consumer/direct_gpu_main.cpp",
            "tools/mirror/consumer/direct_main.cpp",
            "tools/mirror/consumer/main.cpp",
            "tools/mirror/consumer/plugin_view_host_main.cpp",
            "tools/mirror/consumer/runtime_image_census_main.cpp",
            "tools/mirror/consumer/v8_main.cpp",
            "tools/mirror/canvas_provider_anchor.cpp",
            "tools/mirror/gpu_surface_guard.py",
            "scripts/install_core.py",
            "tools/provenance/content_scan_policy.py",
            "tools/provenance/test_verify_extraction.py",
            "tools/provenance/verify_extraction.py",
            "tools/provenance/mirror_edge_patterns.json",
            "tools/mirror/verify_mirror_inertness.py",
            "tools/mirror/test_verify_mirror_inertness.py",
            "tools/mirror/test_rename_mirror_namespace.py",
        })
        self.assertEqual(verify_extraction.RENAMED_MIRROR_SCAN_ROOTS,
                         verify_extraction.EXPECTED_ACTIVE_ROOTS + ("components", "product", "tools", "web"))

    def hide_ci_git_base(self) -> None:
        # Fixture repositories do not contain the CI's VELLUM_GIT_BASE commit.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VELLUM_GIT_BASE", None)

    def git_commit_all(self, root: Path, message: str) -> str:
        git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
        if not (root / ".git").exists():
            subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", message], check=True)
        return subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    def test_framework_debt_baseline_may_not_rise_from_the_merge_base(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "framework/core/a/src/a.cpp"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"#if PULP_HAS_SKIA\n#endif\n")
            self.write_framework_debt(root, {"pulp-package-or-target": {"framework/core/a/src/a.cpp": 1}})
            base = self.git_commit_all(root, "base")
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "change"], check=True)
            os.environ["VELLUM_GIT_BASE"] = base
            # Control: the base itself passes.
            self.assertEqual(verify_extraction.framework_debt_errors(root)[0], [])
            # Adding a macro and raising the baseline with it is refused.
            source.write_bytes(b"#if PULP_HAS_SKIA && PULP_HAS_DAWN\n#endif\n")
            self.write_framework_debt(root, {"pulp-package-or-target": {"framework/core/a/src/a.cpp": 2}})
            self.git_commit_all(root, "raise")
            errors = verify_extraction.framework_debt_errors(root)[0]
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("the baseline may only fall", errors[0])
            # So is a new baseline entry for a file the base did not owe.
            source.write_bytes(b"#if PULP_HAS_SKIA\n#endif\n")
            (root / "framework/core/a/src/b.cpp").write_bytes(b"// VST3\n")
            self.write_framework_debt(root, {"pulp-package-or-target": {"framework/core/a/src/a.cpp": 1},
                                             "audio-plugin-sdk": {"framework/core/a/src/b.cpp": 1}})
            self.git_commit_all(root, "new entry")
            self.assertIn("audio-plugin-sdk in framework/core/a/src/b.cpp",
                          " ".join(verify_extraction.framework_debt_errors(root)[0]))
            # Lowering it, or dropping the entry, passes.
            (root / "framework/core/a/src/b.cpp").unlink()
            source.write_bytes(b"int a;\n")
            self.write_framework_debt(root, {})
            self.git_commit_all(root, "retire")
            self.assertEqual(verify_extraction.framework_debt_errors(root)[0], [])

    def test_framework_debt_counts_generators_and_refuses_undecodable_text(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "framework/tools").mkdir(parents=True)
            (root / "framework/tools/gen.py").write_bytes(b"NAME = 'PULP_EMBED'\n")
            self.write_framework_debt(root, {})
            errors = verify_extraction.framework_debt_errors(root)[0]
            self.assertEqual([e.split(" in ")[1].split(" is ")[0] for e in errors], ["framework/tools/gen.py"])
            (root / "framework/tools/gen.py").unlink()
            (root / "framework/core/a/src").mkdir(parents=True)
            (root / "framework/core/a/src/bad.cpp").write_bytes(b"int a; // \xff PULP_X\n")
            errors = verify_extraction.framework_debt_errors(root)[0]
            self.assertEqual(errors, ["framework name debt: framework/core/a/src/bad.cpp is not UTF-8, "
                                      "so the ratchet cannot count it"])

    def write_framework_debt(self, root: Path, rules: dict[str, dict[str, int]]) -> None:
        path = root / verify_extraction.FRAMEWORK_NAME_DEBT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "vellum.framework-name-debt.v1", "rules": rules}), encoding="utf-8")

    def test_framework_name_debt_may_only_fall(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "framework/core/a/src/a.cpp"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"#if PULP_HAS_SKIA\nint a();\n#endif\n")
            (root / "framework/external/x").mkdir(parents=True)
            (root / "framework/external/x/x.c").write_bytes(b"#define PULP_THIRD_PARTY 1\n")
            self.write_framework_debt(root, {"pulp-package-or-target": {"framework/core/a/src/a.cpp": 1}})
            errors, totals = verify_extraction.framework_debt_errors(root)
            self.assertEqual(errors, [])
            # Control: the baseline's hit is counted, and external/ is not.
            self.assertEqual(totals["pulp-package-or-target"], 1)
            # An added PULP_ macro raises the file's count past its baseline.
            source.write_bytes(b"#if PULP_HAS_SKIA && PULP_HAS_DAWN\nint a();\n#endif\n")
            errors, _ = verify_extraction.framework_debt_errors(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("pulp-package-or-target in framework/core/a/src/a.cpp is 2, baseline 1", errors[0])
            # So does a new file, and a newly ratcheted rule in an old one.
            source.write_bytes(b"#if PULP_HAS_SKIA\nint a();\n#endif\n")
            (root / "framework/core/a/src/b.cpp").write_bytes(b"// VST3 host\n")
            errors, _ = verify_extraction.framework_debt_errors(root)
            self.assertEqual([e.split(" in ")[0] for e in errors], ["framework name debt rose: audio-plugin-sdk"])
            # Falling below the baseline passes.
            (root / "framework/core/a/src/b.cpp").unlink()
            source.write_bytes(b"int a();\n")
            self.assertEqual(verify_extraction.framework_debt_errors(root)[0], [])
            (root / verify_extraction.FRAMEWORK_NAME_DEBT).write_text("{", encoding="utf-8")
            self.assertIn("unreadable", verify_extraction.framework_debt_errors(root)[0][0])

    def test_recorded_framework_name_debt_holds_on_this_tree(self) -> None:
        if not (ROOT / "framework").is_dir():
            self.skipTest("this checkout has no framework/")
        errors, totals = verify_extraction.framework_debt_errors(ROOT)
        self.assertEqual(errors, [])
        self.assertGreater(totals["pulp-package-or-target"], 0)  # control: the scan reads framework/

    def test_framework_refuses_residual_pulp_spellings_and_unknown_trees(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "framework/core/a/src").mkdir(parents=True)
            (root / "framework/core/a/src/a.cpp").write_bytes(b"namespace vellum::a { int x; }\n")
            (root / "framework/core/a/src/b.cpp").write_bytes(b"#include <pulp/a/a.hpp>\n")
            self.write_framework_debt(root, {})
            record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({"schema": verify_extraction.FRAMEWORK_TRANSFER_SCHEMA,
                                          "framework_tree": "f" * 40}), encoding="utf-8")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main")
            self.assertEqual(checks["framework_residual_pulp_spellings"], ["framework/core/a/src/b.cpp"])
            self.assertTrue(any("still spells pulp::" in e for e in errors), errors)
            self.assertTrue(any("is not a tree in this repository" in e for e in errors), errors)
            # A real tree with no Pulp checkout fails unless the proof is deferred.
            subprocess.run(["git", "-C", str(root), "add", "framework"], check=True)
            tree = subprocess.run(["git", "-C", str(root), "write-tree", "--prefix=framework/"], check=True,
                                  capture_output=True, text=True).stdout.strip()
            receipt = root / "provenance/mirror/receipts/r.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({"pulp_sha": "a" * 40, "mirror_tree": "b" * 40, "files": []}), encoding="utf-8")
            record.write_text(json.dumps({"schema": verify_extraction.FRAMEWORK_TRANSFER_SCHEMA, "framework_tree": tree,
                                          "export_receipt": "provenance/mirror/receipts/r.json",
                                          "pulp_sha": "a" * 40, "mirror_tree": "b" * 40, "records": []}),
                              encoding="utf-8")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main")
            self.assertTrue(any("no Pulp checkout proved the transfer" in e for e in errors), errors)
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertFalse(any("no Pulp checkout" in e for e in errors), errors)
            self.assertEqual(checks["framework_transfer_proved"], "deferred: no Pulp checkout")
            # With no history base nothing can say whether the record is landed
            # or introduced, so the transfer is not judged as either: it fails.
            self.assertTrue(any("no history base to judge the transfer against" in e for e in errors), errors)
            (root / "mirror").mkdir()
            errors, _ = verify_extraction.verify_framework(root, None, "origin/main")
            self.assertTrue(any("mirror/ and framework/ both exist" in e for e in errors), errors)

    def test_framework_transfer_record_is_immutable_and_confines_external(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "framework/external/x").mkdir(parents=True)
            (root / "framework/external/x/x.c").write_bytes(b"int x;\n")
            self.write_framework_debt(root, {})
            record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
            record.parent.mkdir(parents=True)
            body = {"schema": verify_extraction.FRAMEWORK_TRANSFER_SCHEMA, "framework_tree": "f" * 40,
                    "records": [{"framework_path": "framework/external/x/x.c",
                                 "framework_git_blob_sha": verify_extraction.git_blob_sha(b"int x;\n")}]}
            record.write_text(json.dumps(body), encoding="utf-8")
            base = self.git_commit_all(root, "transfer")
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "change"], check=True)
            os.environ["VELLUM_GIT_BASE"] = base
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            # Control: landed at the base and unchanged, so not an introduction,
            # and external/ holds only recorded files.
            self.assertIs(checks["framework_transfer_introduced"], False)
            self.assertFalse(any("immutable" in e or "external" in e for e in errors), errors)
            self.assertEqual(checks["framework_external_unrecorded"], [])
            (root / "framework/external/x/first_party.cpp").write_bytes(b"int y;\n")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_external_unrecorded"], ["framework/external/x/first_party.cpp"])
            (root / "framework/external/x/first_party.cpp").unlink()
            # Repointing the landed record is refused against the merge base.
            record.write_text(json.dumps(dict(body, export_receipt="provenance/mirror/receipts/older.json")),
                              encoding="utf-8")
            errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertTrue(any("changed from the merge base" in e for e in errors), errors)

    def framework_fixture(self, root: Path, files: dict[str, bytes]) -> None:
        for relative, data in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(data)
        self.write_framework_debt(root, {})
        record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"schema": verify_extraction.FRAMEWORK_TRANSFER_SCHEMA, "framework_tree": "f" * 40,
                                      "records": [{"framework_path": p,
                                                   "framework_git_blob_sha": verify_extraction.git_blob_sha(data)}
                                                  for p, data in files.items() if p.startswith("framework/")]}),
                          encoding="utf-8")
        self.track(root)

    def track(self, root: Path) -> None:
        # The census reads tracked files only; the fixture's files are staged.
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    def test_framework_file_set_is_the_transfer_records(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            _, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual((checks["framework_unrecorded_files"], checks["framework_missing_files"]), ([], []))
            (root / "framework/core/a/src/new.cpp").write_bytes(b"int n;\n")
            (root / "framework/core/a/src/a.cpp").unlink()
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_unrecorded_files"], ["framework/core/a/src/new.cpp"])
            self.assertEqual(checks["framework_missing_files"], ["framework/core/a/src/a.cpp"])
            self.assertTrue(any("does not list" in e for e in errors) and any("is missing" in e for e in errors))

    PORTED = "framework/core/a/src/new.cpp"

    def ports_row(self, path: str, change: str = "added", commits: list[str] | None = None,
                  pulp_path: str | None = None, blob: str | None = None) -> dict:
        """A ledger row: native unless Pulp commits are named."""
        target = "a-target" if change == "added" and path.endswith(".cpp") else None
        if commits is None:
            return {"framework_path": path, "change": change, "target": target, "origin": "native",
                    "pulp_path": None, "pulp_commits": [], "framework_blob": None, "reason": "port"}
        return {"framework_path": path, "change": change, "target": target, "origin": "pulp",
                "pulp_path": pulp_path or path.removeprefix("framework/"), "pulp_commits": commits,
                "framework_blob": (blob or "b" * 40) if change == "added" else None, "reason": "port"}

    RENAME = {"from": "pulp", "to": "vellum", "include_units": ["a"], "objc_classes": [],
              "objc_class_prefix": "VellumMirror", "literal_exclusions": [],
              "edge_table": {"suffixes": [".cpp", ".hpp"], "binary_suffixes": []}}

    def record_rename(self, root: Path) -> None:
        """Give the fixture's transfer record the rename table the transfer used."""
        path = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        path.write_text(json.dumps(dict(json.loads(path.read_text(encoding="utf-8")), rename=self.RENAME)),
                        encoding="utf-8")

    def write_ports(self, root: Path, rows: list[dict]) -> None:
        path = root / verify_extraction.FRAMEWORK_PORTS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "vellum.framework-ports.v1", "rows": rows}), encoding="utf-8")

    def ports_verdict(self, root: Path, pulp_root: Path | None = None, defer: bool = True) -> tuple[list[str], dict]:
        """verify_framework's errors about the file set and the ledger, and its checks."""
        errors, checks = verify_extraction.verify_framework(root, pulp_root, "origin/main", defer_identity=defer)
        return [e for e in errors if "file set" in e or verify_extraction.FRAMEWORK_PORTS in e], checks

    def test_the_file_set_changes_only_through_the_ports_ledger(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = "framework/core/a/src/a.cpp"
            self.framework_fixture(root, {old: b"int a;\n", "framework/core/a/src/b.cpp": b"int b;\n"})
            # A file with neither a transfer record nor a ledger row fails.
            (root / self.PORTED).write_bytes(b"int n;\n")
            errors, checks = self.ports_verdict(root)
            self.assertEqual(checks["framework_unrecorded_files"], [self.PORTED])
            self.assertEqual(len(errors), 1, errors)
            # The row that adds it makes it part of the set.
            self.write_ports(root, [self.ports_row(self.PORTED)])
            errors, checks = self.ports_verdict(root)
            self.assertEqual(errors, [])
            self.assertEqual((checks["framework_unrecorded_files"], checks["framework_missing_files"]), ([], []))
            self.assertEqual(checks["framework_ports_added"], [self.PORTED])
            self.assertIs(checks["framework_ports_proved"], True)
            # A row with no file fails.
            (root / self.PORTED).unlink()
            errors, checks = self.ports_verdict(root)
            self.assertEqual(checks["framework_missing_files"], [self.PORTED])
            self.assertTrue(any("is missing 1 files" in e for e in errors), errors)
            (root / self.PORTED).write_bytes(b"int n;\n")
            # A removal row for a transferred file: its presence is then the
            # extra, and its absence is accepted.
            self.write_ports(root, [self.ports_row(self.PORTED), self.ports_row(old, "removed")])
            errors, checks = self.ports_verdict(root)
            self.assertEqual(checks["framework_unrecorded_files"], [old])
            (root / old).unlink()
            errors, checks = self.ports_verdict(root)
            self.assertEqual(errors, [])
            self.assertEqual((checks["framework_unrecorded_files"], checks["framework_missing_files"]), ([], []))
            self.assertEqual(checks["framework_ports_removed"], [old])
            # Control: without its row, the same absence is a missing file.
            self.write_ports(root, [self.ports_row(self.PORTED)])
            self.assertEqual(self.ports_verdict(root)[1]["framework_missing_files"], [old])

    def test_a_ports_row_may_not_readd_a_transferred_file_or_reach_outside_framework(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = "framework/core/a/src/a.cpp"
            self.framework_fixture(root, {old: b"int a;\n", "framework/external/x/x.c": b"int x;\n",
                                          "graphics/native.cpp": b"int g;\n"})
            for rows, fragment in (
                ([self.ports_row(old)], "a ledger row may only remove it"),
                ([self.ports_row(old, "removed"), self.ports_row(old)], "a ledger row may only remove it"),
                ([self.ports_row("graphics/native.cpp")], "not a safe path inside framework/"),
                ([self.ports_row("framework/../graphics/native.cpp")], "not a safe path inside framework/"),
                ([self.ports_row("framework/external/x/x.c", "removed")], "third-party source"),
                ([self.ports_row("framework/external/x/first_party.cpp")], "third-party source"),
            ):
                self.write_ports(root, rows)
                errors, checks = self.ports_verdict(root)
                self.assertTrue(any(fragment in e for e in errors), (rows, errors))
                # A refused ledger changes nothing about the set.
                self.assertEqual((checks["framework_ports_added"], checks["framework_ports_removed"]), ([], []))
            # A first-party file under external/ is still an unrecorded extra with a row naming it.
            (root / "framework/external/x/first_party.cpp").write_bytes(b"int y;\n")
            _, checks = self.ports_verdict(root)
            self.assertEqual(checks["framework_external_unrecorded"], ["framework/external/x/first_party.cpp"])

    def write_units(self, root: Path, targets: list[dict]) -> None:
        path = root / verify_extraction.FRAMEWORK_UNITS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "vellum.framework-units.v1", "targets": targets}), encoding="utf-8")

    def test_a_target_no_closure_builds_is_declared_and_agrees_with_the_ledger(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n",
                                          "framework/core/a/include/vellum/a/a.hpp": b"int a();\n"})
            (root / self.PORTED).write_bytes(b"int n;\n")
            row = dict(self.ports_row(self.PORTED), target="vellum-a-tool")
            self.write_ports(root, [row])
            target = {"name": "vellum-a-tool", "type": "EXECUTABLE", "unit": None, "sources": [self.PORTED],
                      "include_dirs": ["core/a/include"], "compile_definitions": [], "link_targets": [],
                      "reason": "a tool no closure builds"}

            def unit_errors() -> tuple[list[str], dict]:
                errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                return [e for e in errors if verify_extraction.FRAMEWORK_UNITS in e
                        or verify_extraction.FRAMEWORK_PORTS in e], checks

            # Control: the declaration that agrees with the row passes.
            self.write_units(root, [target])
            errors, checks = unit_errors()
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_units_declared"], ["vellum-a-tool"])
            for declared, fragment in (
                ([dict(target, sources=["framework/core/a/src/other.cpp"])], "has no ports ledger row adding it"),
                ([dict(target, name="vellum-other")], "is added by its ledger row to vellum-a-tool"),
                ([dict(target, include_dirs=["core/b/include"])], "is not a directory framework/ holds"),
                ([dict(target, name="pulp-a-tool")], "may not carry a Pulp name"),
                ([dict(target, extra=1)], "must have exactly"),
            ):
                self.write_units(root, declared)
                errors, checks = unit_errors()
                self.assertTrue(any(fragment in e for e in errors), (declared, errors))
                self.assertEqual(checks["framework_units_declared"], [])
            # A row naming a declared target that does not list its source.
            other = "framework/core/a/src/other.cpp"
            (root / other).write_bytes(b"int o;\n")
            self.write_ports(root, [row, dict(self.ports_row(other), target="vellum-a-tool")])
            self.write_units(root, [target])
            errors, _ = unit_errors()
            self.assertTrue(any("which does not list it in" in e for e in errors), errors)

    def test_the_ports_ledger_is_append_only(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            second = "framework/core/a/src/second.cpp"
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n", self.PORTED: b"int n;\n",
                                          second: b"int s;\n"})
            record = json.loads((root / verify_extraction.FRAMEWORK_TRANSFER_RECORD).read_text(encoding="utf-8"))
            record["records"] = [r for r in record["records"] if r["framework_path"].endswith("/a.cpp")]
            (root / verify_extraction.FRAMEWORK_TRANSFER_RECORD).write_text(json.dumps(record), encoding="utf-8")
            first_row, second_row = self.ports_row(self.PORTED), self.ports_row(second)
            self.write_ports(root, [first_row])
            (root / second).unlink()
            base = self.git_commit_all(root, "a landed port")
            os.environ["VELLUM_GIT_BASE"] = base
            append_only = "the ledger is append-only"
            # Control: the landed ledger, unchanged, passes.
            self.assertEqual(self.ports_verdict(root)[0], [])
            # Appending a row, with its file, passes.
            (root / second).write_bytes(b"int s;\n")
            self.write_ports(root, [first_row, second_row])
            self.assertEqual(self.ports_verdict(root)[0], [])
            # Rewriting, reordering, replacing or deleting a landed row fails.
            for rows in ([dict(first_row, reason="rewritten"), second_row], [second_row, first_row],
                         [second_row], [dict(first_row, target="other-target"), second_row]):
                self.write_ports(root, rows)
                errors, _ = self.ports_verdict(root)
                self.assertTrue(any(append_only in e for e in errors), (rows, errors))
            # Deleting the row together with its file fails too, though the set then agrees.
            (root / self.PORTED).unlink()
            self.write_ports(root, [second_row])
            errors, checks = self.ports_verdict(root)
            self.assertEqual((checks["framework_unrecorded_files"], checks["framework_missing_files"]), ([], []))
            self.assertEqual(len(errors), 1, errors)
            self.assertIn(append_only, errors[0])
            # So does deleting the ledger, or emptying it.
            (root / second).unlink()
            self.write_ports(root, [])
            self.assertTrue(any(append_only in e for e in self.ports_verdict(root)[0]))
            (root / verify_extraction.FRAMEWORK_PORTS).unlink()
            errors, _ = self.ports_verdict(root)
            self.assertTrue(any("present at the merge base and removed" in e for e in errors), errors)
            # The recorded way to take a ported file out is a removal row.
            self.write_ports(root, [first_row, self.ports_row(self.PORTED, "removed")])
            self.assertEqual(self.ports_verdict(root)[0], [])

    def write_ports_v2(self, root: Path, rows: list[dict]) -> None:
        path = root / verify_extraction.FRAMEWORK_PORTS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "vellum.framework-ports.v2", "rows": rows}), encoding="utf-8")

    def test_a_declared_target_is_judged_against_the_scoped_rows(self) -> None:
        # verify_framework passes the ledger's scopes, scoped rows included,
        # to the unit check, so it agrees with the build plan.
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n",
                                          "framework/core/a/include/vellum/a/a.hpp": b"int a();\n"})
            (root / self.PORTED).write_bytes(b"int n;\n")
            added = dict(self.ports_row(self.PORTED), target="vellum-a-tool", platforms=["linux-x64"])
            widened = dict(added, change="scoped", platforms=["linux-x64", "macos-arm64"])
            target = {"name": "vellum-a-tool", "type": "EXECUTABLE", "unit": None, "sources": [self.PORTED],
                      "include_dirs": ["core/a/include"], "compile_definitions": [], "link_targets": [],
                      "reason": "a tool no closure builds", "platforms": ["linux-x64", "macos-arm64"]}
            path = root / verify_extraction.FRAMEWORK_UNITS
            path.write_text(json.dumps({"schema": "vellum.framework-units.v2", "targets": [target]}),
                            encoding="utf-8")

            def unit_errors() -> tuple[list[str], dict]:
                errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                return [e for e in errors if verify_extraction.FRAMEWORK_UNITS in e
                        or verify_extraction.FRAMEWORK_PORTS in e], checks

            self.write_ports_v2(root, [added, widened])
            errors, checks = unit_errors()
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_units_declared"], ["vellum-a-tool"])
            # Control: without the scoped row the source does not cover macOS.
            self.write_ports_v2(root, [added])
            errors, checks = unit_errors()
            self.assertTrue(any("is not scoped" in e for e in errors), errors)
            self.assertEqual(checks["framework_units_declared"], [])

    def test_a_scoped_pulp_row_names_a_file_pulp_holds(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            run = lambda *args: subprocess.check_output(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "core/a/src").mkdir(parents=True)
            (pulp / "core/a/src/old.cpp").write_text("int old;\n")
            run("add", "-A"); run("commit", "-q", "-m", "before the port's source")
            before = run("rev-parse", "HEAD")
            (pulp / "core/a/src/new.cpp").write_text("int n;\n")
            run("add", "-A"); run("commit", "-q", "-m", "the port's source")
            on_main = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", on_main)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.record_rename(root)
            (root / self.PORTED).write_bytes(b"int n;\n")
            blob = verify_extraction.git_blob_sha(b"int n;\n")
            added = dict(self.ports_row(self.PORTED, commits=[on_main], blob=blob), platforms=["linux-x64"])
            scoped = dict(added, change="scoped", framework_blob=None, platforms=["linux-x64", "windows-x64"])
            # The scoped row's last named commit holds the file: proved.
            self.write_ports_v2(root, [added, scoped])
            errors, checks = self.ports_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual(errors, [])
            self.assertIs(checks["framework_ports_proved"], True)
            # A scoped row naming other commits than its adding row is refused,
            # so it can make no Pulp claim the adding row's proof does not cover.
            self.write_ports_v2(root, [added, dict(scoped, pulp_commits=[before, on_main])])
            errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            self.assertTrue(any("a scope change names the same" in e for e in errors), errors)
            # Both rows' commits must still be commits on Pulp main.
            self.write_ports_v2(root, [dict(added, pulp_commits=["d" * 40]), dict(scoped, pulp_commits=["d" * 40])])
            errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual(sum(f"is not a commit on Pulp origin/main" in e for e in errors), 2, errors)

    def write_platform_record(self, root: Path, pulp_sha: str, blob: str) -> None:
        records = verify_extraction.load_platform_closures()
        options = {"CMAKE_SYSTEM_NAME": "Linux", "CMAKE_SYSTEM_PROCESSOR": "x86_64", "CMAKE_BUILD_TYPE": "Release",
                   "PULP_ENABLE_GPU": "ON", "PULP_HAS_SKIA": "ON", "PULP_TEXT_SHAPING": "ON"}
        identity = {"gpu": True, "skia": True, "text_shaping": True, "build_type": "Release",
                    "system": "Linux", "processor": "x86_64"}
        base = root / records.ROOT / "linux-x64"
        base.mkdir(parents=True, exist_ok=True)
        (base / "foundation.json").write_text(json.dumps({
            "schema": "pulp.vellum.closure.v1", "configure": {"options": options, "identity": identity},
            "targets": []}), encoding="utf-8")
        units = [{"name": "foundation", "closure_manifest": f"{records.ROOT}/linux-x64/foundation.json",
                  "closure_sha256": records.sha256_file(base / "foundation.json")}]
        (base / "record.json").write_text(json.dumps({
            "schema": records.RECORD_SCHEMA, "platform": "linux-x64", "pulp_repository": "Generous-Corp/pulp",
            "pulp_sha": pulp_sha, "tool": {"path": records.EXPORTER, "pulp_blob": blob},
            "configure": {"identity": identity, "options_sha256": records.options_sha256(options)},
            "export": {"run_url": "https://github.com/Generous-Corp/pulp/actions/runs/123456789",
                       "artifact_sha256": "e" * 64,
                       "manifest_sha256": records.manifest_binding_sha256("e" * 64, units)},
            "runner_image": {"name": "ubuntu-26.04", "version": "1"},
            "renderer_asset": {"name": "skia-build-linux-x64-gpu-release.zip", "sha256": "c" * 64},
            "units": units}), encoding="utf-8")
        (root / records.INDEX).write_text(json.dumps({"schema": records.INDEX_SCHEMA, "platforms": {
            "linux-x64": f"{records.ROOT}/linux-x64/record.json"}}), encoding="utf-8")

    def platform_github(self, *, token: bool) -> None:
        """Set the platform export proof's GitHub access for this test: a token, or none at all."""
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VELLUM_REQUIRE_PLATFORM_EXPORT_ARTIFACT", None)
        if token:
            os.environ["GH_TOKEN"] = "test-token"
        else:
            os.environ.pop("GH_TOKEN", None)

    def write_platform_export(self, root: Path, pulp_sha: str, blob: str, *,
                              run: dict | None = None, artifact: dict | None = None,
                              members: dict[str, bytes | None] | None = None,
                              claimed_sha256: str | None = None) -> mock._patch:
        """A linux-x64 record and GitHub's view of the export run that made it.

        Returns a patch of verify_extraction's gh_api and gh_download serving
        the run (actions/runs/<id>), its artifact listing and the artifact zip
        in the shapes GitHub's REST API serves; `run`, `artifact` and
        `members` override fields, and a None member is left out of the zip.
        """
        records = verify_extraction.load_platform_closures()
        self.write_platform_record(root, pulp_sha, blob)
        base = root / records.ROOT / "linux-x64"
        record = json.loads((base / "record.json").read_text(encoding="utf-8"))
        meta = {"platform": "linux-x64", "pulp_sha": pulp_sha, "runner_image": record["runner_image"],
                "renderer_asset_name": record["renderer_asset"]["name"], "image_os": "ubuntu26"}
        files: dict[str, bytes | None] = {"export.json": json.dumps(meta, indent=2, sort_keys=True).encode(),
                                          "foundation.json": (base / "foundation.json").read_bytes()}
        files.update(members or {})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, data in files.items():
                if data is not None:
                    # Keep the archive digest stable when the fixture is rebuilt after
                    # a landed record; ZipFile otherwise stamps each entry with now.
                    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    bundle.writestr(entry, data)
        archive = buffer.getvalue()
        digest = hashlib.sha256(archive).hexdigest()
        claimed = claimed_sha256 or digest
        record["export"]["artifact_sha256"] = claimed
        record["export"]["manifest_sha256"] = records.manifest_binding_sha256(claimed, record["units"])
        (base / "record.json").write_text(json.dumps(record), encoding="utf-8")
        run_doc = {"id": 123456789, "name": "Vellum platform closure",
                   "path": ".github/workflows/vellum-platform-closure.yml",
                   "head_sha": pulp_sha, "head_branch": "main", "event": "workflow_dispatch",
                   "status": "completed", "conclusion": "success",
                   "repository": {"full_name": "Generous-Corp/pulp"},
                   "head_repository": {"full_name": "Generous-Corp/pulp"}}
        run_doc.update(run or {})
        row = {"id": 4242, "name": "vellum-closure-linux-x64", "size_in_bytes": len(archive),
               "expired": False, "digest": f"sha256:{digest}",
               "workflow_run": {"id": 123456789, "head_sha": pulp_sha}}
        row.update(artifact or {})
        served = {"repos/Generous-Corp/pulp/actions/runs/123456789": run_doc,
                  "repos/Generous-Corp/pulp/actions/runs/123456789/artifacts?per_page=100":
                      {"total_count": 1, "artifacts": [row]}}

        def api(path: str) -> object:
            if path not in served:
                raise subprocess.CalledProcessError(1, ["gh", "api", path])
            return served[path]

        def download(path: str) -> bytes:
            if path != "repos/Generous-Corp/pulp/actions/artifacts/4242/zip":
                raise subprocess.CalledProcessError(1, ["gh", "api", path])
            return archive

        return mock.patch.multiple(verify_extraction, gh_api=mock.Mock(side_effect=api),
                                   gh_download=mock.Mock(side_effect=download))

    def platform_pulp(self, pulp: Path) -> tuple[str, str]:
        """A Pulp repository whose main holds the exporter: (commit, exporter blob)."""
        run = lambda *args: subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
        run("init", "-q", "-b", "main")
        (pulp / "tools/scripts").mkdir(parents=True)
        (pulp / "tools/scripts/vellum_closure.py").write_text("# exporter\n")
        run("add", "-A"); run("commit", "-q", "-m", "exporter")
        on_main = run("rev-parse", "HEAD")
        run("update-ref", "refs/remotes/origin/main", on_main)
        return on_main, run("rev-parse", f"{on_main}:tools/scripts/vellum_closure.py")

    def test_a_platform_record_is_proved_by_the_bytes_pulps_export_run_uploaded(self) -> None:
        self.hide_ci_git_base()
        self.platform_github(token=True)
        records = verify_extraction.load_platform_closures()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            sha, blob = self.platform_pulp(pulp)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            base = root / records.ROOT / "linux-x64"

            def verdict(defer: bool = False, **fixture):
                with self.write_platform_export(root, sha, blob, **fixture):
                    errors, checks = verify_extraction.verify_framework(root, pulp, "origin/main",
                                                                        defer_identity=defer)
                return [e for e in errors if "platforms" in e], checks

            errors, checks = verdict()
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_platform_exports"], {"linux-x64": "proved"})
            self.assertIs(checks["framework_platform_records_proved"], True)

            # Manifests swapped after the export, every record digest recomputed: the record is
            # consistent with itself, but its manifest is not the one the artifact holds.
            with self.write_platform_export(root, sha, blob):
                swapped = json.loads((base / "foundation.json").read_text(encoding="utf-8"))
                swapped["compile_definitions"] = ["FORGED=1"]
                (base / "foundation.json").write_text(json.dumps(swapped), encoding="utf-8")
                record = json.loads((base / "record.json").read_text(encoding="utf-8"))
                record["units"][0]["closure_sha256"] = records.sha256_file(base / "foundation.json")
                record["export"]["manifest_sha256"] = records.manifest_binding_sha256(
                    record["export"]["artifact_sha256"], record["units"])
                (base / "record.json").write_text(json.dumps(record), encoding="utf-8")
                records.load_record(root, "linux-x64")
                errors, _ = verify_extraction.verify_framework(root, pulp, "origin/main")
            self.assertTrue(any("downloaded foundation.json differs" in e for e in errors), errors)

            # An artifact digest the run's artifact does not have.
            errors, _ = verdict(claimed_sha256="a" * 64)
            self.assertTrue(any("downloaded Pulp artifact digest differs" in e for e in errors), errors)

            # A run outside Generous-Corp/pulp, from another workflow, at another commit,
            # unsuccessful, or another run than the record names.
            for run in ({"repository": {"full_name": "attacker/pulp"}},
                        {"head_repository": {"full_name": "attacker/pulp"}},
                        {"path": ".github/workflows/build.yml"},
                        {"head_sha": "d" * 40},
                        {"conclusion": "failure"},
                        {"id": 99}):
                errors, checks = verdict(run=run)
                self.assertTrue(any("export run is not the successful Pulp closure workflow" in e
                                    for e in errors), (run, errors))
                self.assertIs(checks["framework_platform_records_proved"], False)

            # An artifact uploaded by another run, expired, or named for another platform.
            for artifact in ({"workflow_run": {"id": 1}}, {"expired": True},
                             {"name": "vellum-closure-linux-arm64"}):
                errors, _ = verdict(artifact=artifact)
                self.assertTrue(any("0 unexpired vellum-closure-linux-x64 artifacts" in e for e in errors),
                                (artifact, errors))

            # The artifact holds exactly export.json and the record's manifests, and export.json
            # names the record's platform and commit.
            errors, _ = verdict(members={"canvas.json": b"{}"})
            self.assertTrue(any("do not equal" in e for e in errors), errors)
            errors, _ = verdict(members={"foundation.json": None})
            self.assertTrue(any("do not equal" in e for e in errors), errors)
            errors, _ = verdict(members={"export.json": json.dumps({
                "platform": "linux-x64", "pulp_sha": "d" * 40, "renderer_asset_name": "skia-build-linux-x64-gpu-release.zip",
                "runner_image": {"name": "ubuntu-26.04", "version": "1"}}).encode()})
            self.assertTrue(any("downloaded export.json does not match the record" in e for e in errors), errors)

            # A run_url outside Generous-Corp/pulp never reaches GitHub.
            with self.write_platform_export(root, sha, blob):
                record = json.loads((base / "record.json").read_text(encoding="utf-8"))
                record["export"]["run_url"] = "https://github.com/attacker/pulp/actions/runs/123456789"
                (base / "record.json").write_text(json.dumps(record), encoding="utf-8")
                errors, _ = verify_extraction.verify_framework(root, pulp, "origin/main")
                self.assertFalse(verify_extraction.gh_api.called)
            self.assertTrue(any("export.run_url" in e and "is not a" in e for e in errors), errors)

    def test_platform_export_artifact_listing_is_paginated(self) -> None:
        records = verify_extraction.load_platform_closures()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir(); pulp.mkdir()
            sha, blob = self.platform_pulp(pulp)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            patch = self.write_platform_export(root, sha, blob)
            with patch:
                record = json.loads((root / records.ROOT / "linux-x64/record.json").read_text())
                original = verify_extraction.gh_api.side_effect
                expected = original("repos/Generous-Corp/pulp/actions/runs/123456789/artifacts?per_page=100")["artifacts"][0]
                filler = [{"id": i, "name": "other", "expired": False,
                            "workflow_run": {"id": 123456789}} for i in range(1, 101)]
                def api(path: str) -> object:
                    if path.endswith("/artifacts?per_page=100"):
                        return {"total_count": 101, "artifacts": filler}
                    if path.endswith("/artifacts?per_page=100&page=2"):
                        return {"total_count": 101, "artifacts": [expected]}
                    return original(path)
                verify_extraction.gh_api.side_effect = api
                verify_extraction.verify_platform_export_artifact(records, "linux-x64", record)
                self.assertIn("page=2", " ".join(c.args[0] for c in verify_extraction.gh_api.call_args_list))

    def test_without_github_a_platform_record_is_never_proved(self) -> None:
        self.hide_ci_git_base()
        self.platform_github(token=False)
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            sha, blob = self.platform_pulp(pulp)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            with self.write_platform_export(root, sha, blob):
                errors, checks = verify_extraction.verify_framework(root, pulp, "origin/main")
                self.assertTrue(any("no fetched Pulp export proved the record's manifests" in e for e in errors),
                                errors)
                self.assertIs(checks["framework_platform_records_proved"], False)
                errors, checks = verify_extraction.verify_framework(root, pulp, "origin/main", defer_identity=True)
                self.assertEqual([e for e in errors if "platforms" in e], [])
                self.assertEqual(checks["framework_platform_records_proved"], "deferred: no fetched Pulp export")
                self.assertEqual(checks["framework_platform_exports"], {"linux-x64": "deferred: no fetched Pulp export"})
                self.assertFalse(verify_extraction.gh_api.called)

    def test_an_unchanged_platform_record_was_proved_when_it_landed(self) -> None:
        self.hide_ci_git_base()
        self.platform_github(token=True)
        records = verify_extraction.load_platform_closures()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            sha, blob = self.platform_pulp(pulp)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            with self.write_platform_export(root, sha, blob):
                pass
            self.track(root)
            landed = self.git_commit_all(root, "land the record")
            os.environ["VELLUM_GIT_BASE"] = landed
            # The export has expired; the record is the base's, byte for byte.
            with self.write_platform_export(root, sha, blob, artifact={"expired": True}):
                errors, checks = verify_extraction.verify_framework(root, pulp, "origin/main")
                self.assertFalse(verify_extraction.gh_api.called)
            self.assertEqual([e for e in errors if "platforms" in e], [])
            self.assertEqual(checks["framework_platform_exports"], {"linux-x64": f"unchanged since {landed}"})
            self.assertIs(checks["framework_platform_records_proved"], True)
            # One byte in a manifest, digests recomputed: the record changed, so it must prove
            # its export again, and the expired artifact cannot.
            base = root / records.ROOT / "linux-x64"
            (base / "foundation.json").write_bytes((base / "foundation.json").read_bytes() + b" ")
            record = json.loads((base / "record.json").read_text(encoding="utf-8"))
            record["units"][0]["closure_sha256"] = records.sha256_file(base / "foundation.json")
            record["export"]["manifest_sha256"] = records.manifest_binding_sha256(
                record["export"]["artifact_sha256"], record["units"])
            (base / "record.json").write_text(json.dumps(record), encoding="utf-8")
            os.environ.pop("GH_TOKEN")
            errors, _ = verify_extraction.verify_framework(root, pulp, "origin/main")
            self.assertTrue(any("no fetched Pulp export proved the record's manifests" in e for e in errors), errors)
            # An untracked file in the record directory is a change too.
            subprocess.run(["git", "-C", str(root), "checkout", "--", records.ROOT], check=True)
            (base / "extra.json").write_text("{}", encoding="utf-8")
            _, checks = verify_extraction.verify_framework(root, pulp, "origin/main")
            self.assertNotEqual(checks.get("framework_platform_exports"),
                                {"linux-x64": f"unchanged since {landed}"})

    def test_a_platform_record_names_pulps_exporter_at_a_commit_on_main(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            run = lambda *args: subprocess.check_output(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "tools/scripts").mkdir(parents=True)
            (pulp / "tools/scripts/vellum_closure.py").write_text("# exporter\n")
            run("add", "-A"); run("commit", "-q", "-m", "exporter")
            on_main = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", on_main)
            blob = run("rev-parse", f"{on_main}:tools/scripts/vellum_closure.py")
            run("checkout", "-q", "-b", "side")
            (pulp / "tools/scripts/vellum_closure.py").write_text("# side\n")
            run("add", "-A"); run("commit", "-q", "-m", "side")
            off_main = run("rev-parse", "HEAD")
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})

            self.platform_github(token=True)

            def verdict(sha: str, tool_blob: str, pulp_root: Path | None = pulp, defer: bool = False):
                with self.write_platform_export(root, sha, tool_blob):
                    errors, checks = verify_extraction.verify_framework(root, pulp_root, "origin/main",
                                                                        defer_identity=defer)
                return [e for e in errors if "platforms" in e], checks

            errors, checks = verdict(on_main, blob)
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_platform_records"], ["linux-x64"])
            self.assertIs(checks["framework_platform_records_proved"], True)
            errors, _ = verdict(off_main, blob)
            self.assertTrue(any("is not a commit on Pulp origin/main" in e for e in errors), errors)
            errors, _ = verdict(on_main, "d" * 40)
            self.assertTrue(any("is not Pulp" in e and "vellum_closure.py" in e for e in errors), errors)
            errors, _ = verdict(on_main, blob, pulp_root=None)
            self.assertTrue(any("no Pulp checkout proved the records' Pulp commits" in e for e in errors), errors)
            errors, checks = verdict(on_main, blob, pulp_root=None, defer=True)
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_platform_records_proved"], "deferred: no Pulp checkout")
            # A record directory the index does not list is refused.
            (root / "provenance/framework/platforms/wasm32").mkdir()
            errors, _ = verdict(on_main, blob)
            self.assertTrue(any("wasm32/ is not listed" in e for e in errors), errors)

    def test_platform_record_must_match_the_downloaded_pulp_artifact(self) -> None:
        records = verify_extraction.load_platform_closures()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir(); pulp.mkdir()
            run = lambda *args: subprocess.check_output(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "tools/scripts").mkdir(parents=True)
            (pulp / "tools/scripts/vellum_closure.py").write_text("# exporter\n")
            run("add", "-A"); run("commit", "-q", "-m", "exporter")
            pulp_sha = run("rev-parse", "HEAD")
            tool_blob = run("rev-parse", f"{pulp_sha}:tools/scripts/vellum_closure.py")
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.write_platform_record(root, pulp_sha, tool_blob)
            record_path = root / records.ROOT / "linux-x64/record.json"
            record = json.loads(record_path.read_text())
            manifest = root / records.ROOT / "linux-x64/foundation.json"
            export_meta = {"platform": "linux-x64", "pulp_sha": pulp_sha,
                           "runner_image": record["runner_image"],
                           "renderer_asset_name": record["renderer_asset"]["name"]}
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("export.json", json.dumps(export_meta, sort_keys=True))
                archive.writestr("foundation.json", manifest.read_bytes())
            artifact = output.getvalue()
            record["export"]["artifact_sha256"] = hashlib.sha256(artifact).hexdigest()
            units = record["units"]
            record["export"]["manifest_sha256"] = records.manifest_binding_sha256(
                record["export"]["artifact_sha256"], units)
            record_path.write_text(json.dumps(record))
            api = lambda path: (
                {"id": 123456789, "head_sha": pulp_sha, "status": "completed", "conclusion": "success",
                 "repository": {"full_name": records.PULP_REPOSITORY},
                 "head_repository": {"full_name": records.PULP_REPOSITORY},
                 "path": ".github/workflows/vellum-platform-closure.yml"}
                if path.endswith("/actions/runs/123456789") else
                {"artifacts": [{"id": 7, "name": "vellum-closure-linux-x64", "expired": False,
                                "workflow_run": {"id": 123456789}}]})
            with mock.patch.object(verify_extraction, "gh_api", side_effect=api), \
                    mock.patch.object(verify_extraction, "gh_download", return_value=artifact):
                verify_extraction.verify_platform_export_artifact(records, "linux-x64", record)
                run("update-ref", "refs/remotes/origin/main", pulp_sha)
                with mock.patch.dict(os.environ, {"GH_TOKEN": "test-token"}, clear=False):
                    errors, checks = verify_extraction.framework_platform_errors(
                        root, pulp, "origin/main", defer_identity=False)
                self.assertEqual(errors, [])
                self.assertIs(checks["framework_platform_artifact_proved"], True)
                with self.assertRaisesRegex(records.RecordError, "downloaded foundation.json differs"):
                    verify_extraction.verify_platform_export_artifact(records, "linux-x64",
                                                                       {**record, "units": [{**units[0],
                                                                                              "closure_sha256": "0" * 64}]})

    def write_respell(self, root: Path, entries: list[dict]) -> None:
        path = root / verify_extraction.FRAMEWORK_RESPELL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "vellum.framework-respell.v1", "entries": entries}), encoding="utf-8")

    @staticmethod
    def respell_entry(old: str, evidence: str | None, kind: str = "extern-c") -> dict:
        new = ("VELLUM_" + old[5:]) if old.startswith("PULP_") else ("vellum_" + old[5:])
        return {"old": old, "new": new, "kind": kind, "evidence": evidence}

    def transfer_fixture(self, root: Path, origin: dict[str, bytes], current: dict[str, bytes] | None = None) -> None:
        """Transferred files whose records hold `origin`'s bytes and whose tree holds `current` (origin by default)."""
        self.framework_fixture(root, origin)
        for relative, data in (current or {}).items():
            (root / relative).write_bytes(data)
        self.track(root)

    def respell_verdict(self, root: Path, pulp_root: Path | None = None, defer: bool = True) -> tuple[list[str], dict]:
        errors, checks = verify_extraction.verify_framework(root, pulp_root, "origin/main", defer_identity=defer)
        return [e for e in errors if verify_extraction.FRAMEWORK_RESPELL in e], checks

    def pulp_checkout(self, pulp: Path, files: dict[str, str]) -> str:
        """Commit `files` to a Pulp checkout and point its origin/main there; returns the commit."""
        run = lambda *args: subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
        pulp.mkdir(parents=True, exist_ok=True)
        if not (pulp / ".git").exists():
            run("init", "-q", "-b", "main")
        for relative, text in files.items():
            (pulp / relative).parent.mkdir(parents=True, exist_ok=True)
            (pulp / relative).write_text(text, encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", "pulp")
        sha = run("rev-parse", "HEAD")
        run("update-ref", "refs/remotes/origin/main", sha)
        return sha

    HOOKS = "framework/core/a/src/hooks.cpp"
    CALLER = "framework/core/a/src/caller.cpp"
    HOOKS_PULP = b'extern "C" void pulp_anchor() {}\n'
    CALLER_PULP = b'extern "C" void pulp_anchor();\nvoid f() { pulp_anchor(); }\n'

    @staticmethod
    def vellum(data: bytes, *names: str) -> bytes:
        for name in names or ("pulp_anchor",):
            data = data.replace(name.encode(), ("vellum_" + name[5:]).encode())
        return data

    def anchored_fixture(self, root: Path) -> dict:
        """hooks.cpp defines pulp_anchor, caller.cpp calls it; both transferred, both respelled now."""
        self.transfer_fixture(root, {self.HOOKS: self.HOOKS_PULP, self.CALLER: self.CALLER_PULP},
                              {self.HOOKS: self.vellum(self.HOOKS_PULP), self.CALLER: self.vellum(self.CALLER_PULP)})
        return self.respell_entry("pulp_anchor", self.HOOKS)

    def test_the_respell_table_is_the_c_linkage_definitions_the_record_implies(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anchor = self.anchored_fixture(root)
            self.write_respell(root, [anchor])
            errors, checks = self.respell_verdict(root)
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_respell_entries"], 1)
            # A hand-added entry nothing implies is refused.
            self.write_respell(root, [anchor, self.respell_entry("pulp_invented", self.HOOKS)])
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any("pulp_invented" in e and "not written by hand" in e for e in errors), errors)
            # A name the record defines that the table lacks is refused, here also as a hand respell.
            self.write_respell(root, [])
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any("imply pulp_anchor" in e and "does not list" in e for e in errors), errors)
            self.assertTrue(any(self.CALLER in e and "the Vellum way" in e for e in errors), errors)
            # A caller left in its Pulp spelling is stale.
            self.write_respell(root, [anchor])
            (root / self.CALLER).write_bytes(self.CALLER_PULP)
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any(self.CALLER in e and "still spells pulp_anchor" in e for e in errors), errors)

    def test_only_the_extern_c_kind_is_recognised(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anchor = self.anchored_fixture(root)
            for kind, old in (("closure-macro", "PULP_GPU_RUNTIME"), ("file-macro", "PULP_KEEP"),
                              ("link-export", "pulp_exp"), ("opt-in-switch", "PULP_SW")):
                with self.subTest(kind=kind):
                    self.write_respell(root, [self.respell_entry(old, self.HOOKS, kind), anchor])
                    errors, _ = self.respell_verdict(root)
                    self.assertTrue(any(f"kind must be extern-c, not '{kind}'" in e for e in errors), errors)

    def test_without_a_respell_table_the_rule_is_inert(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.transfer_fixture(root, {self.HOOKS: self.HOOKS_PULP + b"#define CAT(a) pulp_##a\n"})
            errors, checks = self.respell_verdict(root)
            # Pulp-spelled bytes a table would have to list raise nothing, and no respell check is reported.
            self.assertEqual(errors, [])
            self.assertFalse([key for key in checks if key.startswith("framework_respell")], checks)
            # Once a table exists, the same bytes are judged by it.
            self.write_respell(root, [])
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any("imply pulp_anchor" in e for e in errors), errors)

    def test_a_transferred_file_spells_the_vellum_way_only_what_the_table_respells(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = self.HOOKS_PULP + b"void pulp_other();\n#define PULP_TAG 1\nint t = PULP_TAG;\nint x = 1;\n"
            anchor = self.respell_entry("pulp_anchor", self.HOOKS)
            self.transfer_fixture(root, {self.HOOKS: origin}, {self.HOOKS: self.vellum(origin)})
            self.write_respell(root, [anchor])
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])
            # A reviewed edit that is no respell is still allowed.
            (root / self.HOOKS).write_bytes(self.vellum(origin).replace(b"int x = 1;", b"int x = 2;"))
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])
            # A name respelled by hand, C or macro, is refused whether or not the file is evidence.
            for name in ("pulp_other", "PULP_TAG"):
                with self.subTest(name=name):
                    hand = self.vellum(origin, "pulp_anchor").replace(name.encode(), self.respell_entry(name, None)["new"].encode())
                    (root / self.HOOKS).write_bytes(hand)
                    errors, _ = self.respell_verdict(root)
                    self.assertTrue(any(f"spells ['{name}'] the Vellum way" in e for e in errors), errors)

    def test_without_pulp_a_transferred_or_ported_entry_is_deferred_never_proved(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anchor = self.anchored_fixture(root)
            errors, checks = verify_extraction.framework_respell_errors(root, [anchor], None, {})
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_respell_proved"],
                             "deferred: no Pulp checkout proved the transferred and ported evidence")
            # Control: with a Pulp checkout the same table is proved.
            errors, checks = verify_extraction.framework_respell_errors(root, [anchor], {}, {})
            self.assertEqual((errors, checks["framework_respell_proved"]), ([], True))
            # An entry invented from a transferred file is an error, never reported proved or deferred.
            invented = self.respell_entry("pulp_invented", self.HOOKS)
            errors, checks = verify_extraction.framework_respell_errors(root, [anchor, invented], None, {})
            self.assertTrue(any("pulp_invented" in e for e in errors), errors)
            self.assertIs(checks["framework_respell_proved"], False)
            # A ported file's entry rests on Pulp's bytes: without them, deferred either way.
            ported = "framework/core/a/src/ported.cpp"
            (root / ported).write_bytes(b'extern "C" int vellum_role() { return 1; }\n')
            row = {"origin": "pulp"}
            role = self.respell_entry("pulp_role", ported)
            for table in ([anchor, role], [anchor]):
                errors, checks = verify_extraction.framework_respell_errors(root, table, None, {ported: row})
                self.assertFalse([e for e in errors if "pulp_role" in e], errors)
                self.assertTrue(str(checks["framework_respell_proved"]).startswith("deferred"), checks)
            # With Pulp's bytes the ported entry is judged: Pulp defines it, so the table must list it.
            errors, _ = verify_extraction.framework_respell_errors(
                root, [anchor], {ported: 'extern "C" int pulp_role() { return 1; }\n'}, {ported: row})
            self.assertTrue(any("imply pulp_role" in e for e in errors), errors)

    def test_a_landed_port_takes_a_later_respell_and_stays_provable(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            source = 'extern "C" void pulp_anchor();\nvoid g() { pulp_anchor(); }\nint y = 1;\n'
            sha = self.pulp_checkout(pulp, {"core/a/src/new.cpp": source})
            self.transfer_fixture(root, {self.HOOKS: self.HOOKS_PULP})
            self.record_rename(root)
            (root / self.PORTED).write_text(source, encoding="utf-8")
            self.write_ports(root, [self.ports_row(self.PORTED, commits=[sha],
                                                   blob=verify_extraction.git_blob_sha(source.encode()))])
            os.environ["VELLUM_GIT_BASE"] = self.git_commit_all(root, "the port landed")
            # The change that adds the entry respells the landed port by a reviewed edit.
            anchor = self.respell_entry("pulp_anchor", self.HOOKS)
            self.write_respell(root, [anchor])
            (root / self.HOOKS).write_bytes(self.vellum(self.HOOKS_PULP))
            (root / self.PORTED).write_bytes(self.vellum(source.encode()))
            self.track(root)
            ports_errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            errors, checks = self.respell_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual((ports_errors, errors), ([], []))
            self.assertIs(checks["framework_respell_proved"], True)
            # The landed port's respell goes through the edit check: an extra edit is refused.
            (root / self.PORTED).write_bytes(self.vellum(source.encode()).replace(b"y = 1", b"y = 2"))
            self.track(root)
            errors, _ = self.respell_verdict(root, pulp_root=pulp, defer=False)
            self.assertTrue(any(self.PORTED in e and "changed by more than" in e for e in errors), errors)
            # Once the respell has landed, the next change still proves the row.
            (root / self.PORTED).write_bytes(self.vellum(source.encode()))
            os.environ["VELLUM_GIT_BASE"] = self.git_commit_all(root, "the respell landed")
            ports_errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            errors, _ = self.respell_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual((ports_errors, errors), ([], []))

    def test_a_landed_port_left_partly_respelled_fails_the_stale_spelling_check(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            source = 'extern "C" void pulp_anchor();\nextern "C" void pulp_anchor2();\nvoid g() { pulp_anchor(); pulp_anchor2(); }\n'
            sha = self.pulp_checkout(pulp, {"core/a/src/new.cpp": source})
            hooks = self.HOOKS_PULP + b'extern "C" void pulp_anchor2() {}\n'
            self.transfer_fixture(root, {self.HOOKS: hooks})
            self.record_rename(root)
            (root / self.PORTED).write_text(source, encoding="utf-8")
            self.write_ports(root, [self.ports_row(self.PORTED, commits=[sha],
                                                   blob=verify_extraction.git_blob_sha(source.encode()))])
            os.environ["VELLUM_GIT_BASE"] = self.git_commit_all(root, "the port landed")
            self.write_respell(root, [self.respell_entry("pulp_anchor", self.HOOKS),
                                      self.respell_entry("pulp_anchor2", self.HOOKS)])
            (root / self.HOOKS).write_bytes(self.vellum(hooks, "pulp_anchor2", "pulp_anchor"))
            # The landed port takes only one of the two respells.
            (root / self.PORTED).write_bytes(self.vellum(source.encode(), "pulp_anchor2"))
            self.track(root)
            ports_errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            errors, _ = self.respell_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual(ports_errors, [])  # the row's blob, as it landed, is still provable
            self.assertTrue(any(self.PORTED in e and "still spells pulp_anchor;" in e for e in errors), errors)
            # Control: the full respell passes.
            (root / self.PORTED).write_bytes(self.vellum(source.encode(), "pulp_anchor2", "pulp_anchor"))
            self.track(root)
            errors, _ = self.respell_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual(errors, [])

    def test_an_appended_port_is_proved_through_the_current_table(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            source = 'extern "C" int pulp_role_of(int pulp_role) { return pulp_role; }\n'
            sha = self.pulp_checkout(pulp, {"core/a/src/new.cpp": source})
            self.transfer_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.record_rename(root)
            ported = source.replace("pulp_role_of", "vellum_role_of").encode()
            self.assertIn(b"int pulp_role)", ported)  # a parameter, not C linkage: left as Pulp spelled it
            (root / self.PORTED).write_bytes(ported)
            blob = verify_extraction.git_blob_sha(ported)
            self.write_ports(root, [self.ports_row(self.PORTED, commits=[sha], blob=blob)])
            entries = [self.respell_entry("pulp_role_of", self.PORTED)]
            self.write_respell(root, entries)
            self.track(root)
            ports_errors, checks = self.ports_verdict(root, pulp_root=pulp, defer=False)
            errors, checks = self.respell_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual((ports_errors, errors), ([], []))
            self.assertIs(checks["framework_ports_proved"], True)
            self.assertIs(checks["framework_respell_proved"], True)
            # Control: an appended row is not let off with a subset; without the entry its blob is not Pulp's.
            self.write_respell(root, [])
            errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            self.assertTrue(any("put through the transfer's rename (" in e for e in errors), errors)
            # Pulp bytes that already spell an entry's Vellum name cannot be respelled.
            self.write_respell(root, entries)
            spelled = self.pulp_checkout(pulp, {"core/a/src/new.cpp": source + "// see vellum_role_of\n"})
            self.write_ports(root, [self.ports_row(self.PORTED, commits=[spelled], blob=blob)])
            errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            self.assertTrue(any("cannot be respelled" in e and "use reapply()" in e for e in errors), errors)

    def test_a_new_entry_edits_a_transferred_file_only_by_its_respell(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = "framework/core/a/src/native.cpp"
            origin = self.HOOKS_PULP + b"int other = 1;\n"
            other = "framework/core/a/src/other.cpp"
            self.transfer_fixture(root, {self.HOOKS: origin, other: b"void pulp_other();\nint o = 1;\n"})
            (root / native).write_bytes(b"void n() { pulp_anchor(); }\nint n2 = 1;\n")
            self.write_ports(root, [self.ports_row(native)])
            base = self.git_commit_all(root, "before the respell")
            os.environ["VELLUM_GIT_BASE"] = base
            self.write_respell(root, [self.respell_entry("pulp_anchor", self.HOOKS)])
            (root / self.HOOKS).write_bytes(self.vellum(origin))
            # A native file is not judged by the edit check: Vellum wrote it.
            (root / native).write_bytes(b"void n() { vellum_anchor(); }\nint n2 = 2;\n")
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])
            # Control: the same change plus any other edit to the transferred file is refused.
            (root / self.HOOKS).write_bytes(self.vellum(origin).replace(b"other = 1", b"other = 2"))
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any(self.HOOKS in e and "changed by more than the respell" in e for e in errors), errors)
            # An entry cannot excuse a hand respell in another transferred file, evidence or not.
            (root / self.HOOKS).write_bytes(self.vellum(origin))
            (root / other).write_bytes(b"void vellum_other();\nint o = 1;\n")
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any(other in e and "spells ['pulp_other'] the Vellum way" in e for e in errors), errors)
            (root / other).write_bytes(b"void pulp_other();\nint o = 1;\n")
            # A base that spells both the old and the new name cannot be respelled.
            self.write_respell(root, [])
            (root / self.HOOKS).write_bytes(origin + b"void vellum_anchor();\n")
            (root / native).write_bytes(b"int n2 = 1;\n")
            os.environ["VELLUM_GIT_BASE"] = self.git_commit_all(root, "both spellings")
            self.write_respell(root, [self.respell_entry("pulp_anchor", self.HOOKS)])
            (root / self.HOOKS).write_bytes(self.vellum(origin) + b"void vellum_anchor();\n")
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any("at the history base cannot be respelled" in e for e in errors), errors)

    def test_an_entry_is_new_unless_its_name_kind_and_evidence_all_landed(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = "framework/core/a/src/f.cpp"
            self.transfer_fixture(root, {path: b"void g() { pulp_a(); }\nint x = 1;\n"})
            base = self.git_commit_all(root, "base")
            (root / path).write_bytes(b"void g() { vellum_a(); }\nint x = 2;\n")
            self.track(root)
            landed = [self.respell_entry("pulp_a", "framework/core/a/src/x.cpp")]
            moved = [self.respell_entry("pulp_a", "framework/core/a/src/y.cpp")]
            errors = verify_extraction.framework_respell_edit_errors(root, moved, base, landed, {path})
            self.assertTrue(any("changed by more than" in e for e in errors), errors)
            # Control: the identical landed entry is not new, so the file is not judged by it.
            self.assertEqual(verify_extraction.framework_respell_edit_errors(root, landed, base, landed, {path}), [])

    def test_deleting_the_table_is_refused_only_while_its_respells_remain(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anchor = self.anchored_fixture(root)
            self.write_respell(root, [anchor])
            os.environ["VELLUM_GIT_BASE"] = self.git_commit_all(root, "with the table")
            (root / verify_extraction.FRAMEWORK_RESPELL).unlink()
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any("cannot be removed while" in e for e in errors), errors)
            # With every respell reverted the table may go.
            (root / self.HOOKS).write_bytes(self.HOOKS_PULP)
            (root / self.CALLER).write_bytes(self.CALLER_PULP)
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])
            # Control: the same base with the table kept and the respells in place raises nothing.
            self.write_respell(root, [anchor])
            (root / self.HOOKS).write_bytes(self.vellum(self.HOOKS_PULP))
            (root / self.CALLER).write_bytes(self.vellum(self.CALLER_PULP))
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])

    def removal_fixture(self, root: Path, recorded: bytes, current: bytes) -> str:
        """A transferred file whose record names `recorded`'s blob and whose tree holds `current`."""
        self.transfer_fixture(root, {self.HOOKS: recorded}, {self.HOOKS: current})
        return self.HOOKS

    def test_a_respelled_transferred_file_without_a_table_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.removal_fixture(root, self.HOOKS_PULP, self.vellum(self.HOOKS_PULP))
            errors, _ = self.respell_verdict(root)
            self.assertTrue(any(path in e and "pulp_anchor" in e and "no respell table" in e for e in errors), errors)

    def test_without_a_table_or_respells_the_removal_guard_is_quiet(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            same = self.HOOKS_PULP + b"void vellum_native() {}\n"
            self.removal_fixture(root, same, same)
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])
            # An unrelated edit that adds a native vellum_ name is not a respell.
            self.removal_fixture(root, same, same + b"void vellum_extra() {}\n")
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])

    def test_a_missing_record_blob_is_an_error_not_a_silent_pass(self) -> None:
        self.hide_ci_git_base()
        for with_table in (False, True):
            with self.subTest(with_table=with_table), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                anchor = self.anchored_fixture(root)
                if with_table:
                    self.write_respell(root, [anchor])
                record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
                data = json.loads(record.read_text(encoding="utf-8"))
                data["records"][0]["framework_git_blob_sha"] = "1" * 40  # absent, as in a shallow clone
                record.write_text(json.dumps(data), encoding="utf-8")
                errors, _ = self.respell_verdict(root)
                self.assertTrue(any("is not in this repository" in e for e in errors), errors)

    def test_a_pasted_pulp_name_is_refused_while_a_table_is_in_force(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            anchor = self.anchored_fixture(root)
            self.write_respell(root, [anchor])
            paste = "framework/core/a/src/paste.cpp"
            self.write_ports(root, [self.ports_row(paste)])
            for body in (b"#define CAT(a) pulp_##a\n", b"#define CAT(a) a ## pulp_anchor\n"):
                with self.subTest(body=body):
                    (root / paste).write_bytes(body + b"void p() { CAT(anchor)(); }\n")
                    self.track(root)
                    errors, _ = self.respell_verdict(root)
                    self.assertTrue(any(paste in e and "pastes" in e for e in errors), errors)
            # A header name in angle brackets is a literal: never stale, never respelled.
            (root / paste).write_bytes(b"#include <pulp_anchor.h>\n")
            self.track(root)
            errors, _ = self.respell_verdict(root)
            self.assertEqual(errors, [])

    def test_malformed_transfer_records_are_errors_not_crashes(self) -> None:
        self.hide_ci_git_base()
        for label, document in (("a string row", {"records": [self.HOOKS]}), ("a list", [1]),
                                ("a row and a number", {"records": [{"framework_path": self.HOOKS}, 7]})):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                anchor = self.anchored_fixture(root)
                (root / verify_extraction.FRAMEWORK_TRANSFER_RECORD).write_text(json.dumps(document), encoding="utf-8")
                records, errors = verify_extraction.framework_transfer_records(root)
                self.assertEqual(records, [])
                self.assertTrue(any("records must be a list of objects" in e for e in errors), errors)
                self.assertTrue(verify_extraction.framework_respell_removal_errors(root, []))
                errors, checks = verify_extraction.framework_respell_errors(root, [anchor], None, {})
                self.assertTrue(errors)
                self.assertIs(checks["framework_respell_proved"], False)
                if isinstance(document, dict):
                    document = dict(document, schema=verify_extraction.FRAMEWORK_TRANSFER_SCHEMA,
                                    framework_tree="f" * 40)
                    (root / verify_extraction.FRAMEWORK_TRANSFER_RECORD).write_text(json.dumps(document),
                                                                                   encoding="utf-8")
                errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                self.assertTrue(errors)

    def test_a_malformed_or_mixed_file_is_a_recorded_error_not_a_crash(self) -> None:
        self.hide_ci_git_base()
        path = self.HOOKS
        entry = self.respell_entry("pulp_anchor", path)
        for label, body in (("mixed", b'extern "C" void vellum_anchor() {}\nvoid g() { pulp_anchor(); }\n'),
                            ("not utf-8", b'extern "C" void vellum_anchor() {}\n// \xff\xfe\n'),
                            ("unterminated", b'extern "C" void vellum_anchor() {}\n/* "\n')):
            for with_pulp in (False, True):
                with self.subTest(label=label, with_pulp=with_pulp), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.transfer_fixture(root, {path: body.replace(b"vellum_", b"pulp_")}, {path: body})
                    errors, _ = verify_extraction.framework_respell_errors(root, [entry], {} if with_pulp else None, {})
                    self.assertIsInstance(errors, list)
                    if label == "mixed":
                        self.assertTrue(any("still spells pulp_anchor" in e for e in errors), errors)

    def test_provenance_and_mirror_scripts_parse_before_python_3_12(self) -> None:
        """No f-string reuses its own quote inside a replacement field (PEP 701, 3.12 only).

        CI pins 3.12 for some steps but runs bare `python3` in others, so the
        scripts must stay valid on older interpreters. The check reads the
        source of each field, which a 3.12 parser accepts.
        """
        import ast
        tools = Path(__file__).resolve().parents[1]
        checked, nested = 0, []
        for path in sorted([*(tools / "provenance").glob("*.py"), *(tools / "mirror").glob("*.py")]):
            source = path.read_text(encoding="utf-8")
            checked += 1
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.JoinedStr):
                    continue
                literal = (ast.get_source_segment(source, node) or "").lstrip("fFrRbB")
                quote = literal[:3] if literal[:3] in ('"""', "'''") else literal[:1]
                for part in node.values:
                    if isinstance(part, ast.FormattedValue):
                        field = ast.get_source_segment(source, part.value) or ""
                        if quote in field:
                            nested.append(f"{path.name}:{node.lineno}")
        self.assertGreater(checked, 10)  # control: the scan read the scripts
        self.assertEqual(nested, [])

    def test_ports_rows_name_real_commits_on_pulp_main(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            run = lambda *args: subprocess.check_output(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "core/a/src").mkdir(parents=True)
            (pulp / "core/a/src/old.cpp").write_text("int old;\n")
            run("add", "-A"); run("commit", "-q", "-m", "before the port's source")
            before = run("rev-parse", "HEAD")
            (pulp / "core/a/src/new.cpp").write_text("int n;\n")
            run("add", "-A"); run("commit", "-q", "-m", "the port's source")
            on_main = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", on_main)
            run("checkout", "-q", "-b", "side")
            (pulp / "core/a/src/new.cpp").write_text("int side;\n")
            run("add", "-A"); run("commit", "-q", "-m", "never landed")
            off_main = run("rev-parse", "HEAD")
            tree = run("rev-parse", f"{on_main}^{{tree}}")
            run("tag", "-a", "-m", "names a commit on main", "port-tag", on_main)
            tag = run("rev-parse", "port-tag")
            self.assertNotEqual(tag, on_main)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.record_rename(root)
            (root / self.PORTED).write_bytes(b"int n;\n")
            blob = verify_extraction.git_blob_sha(b"int n;\n")

            def verdict(commits: list[str], pulp_path: str | None = None, **kwargs) -> tuple[list[str], dict]:
                self.write_ports(root, [self.ports_row(self.PORTED, commits=commits, pulp_path=pulp_path, blob=blob)])
                return self.ports_verdict(root, **kwargs)

            # Real commits on Pulp main, the last of which holds the file: proved.
            for commits in ([on_main], [before, on_main]):
                errors, checks = verdict(commits, pulp_root=pulp, defer=False)
                self.assertEqual(errors, [])
                self.assertIs(checks["framework_ports_proved"], True)
            # A commit that is not on Pulp main, an id Pulp does not have, a
            # tree's id, and the id of a tag object that peels to a commit on
            # main are each refused, alone or beside a real commit.
            for bad in (off_main, "d" * 40, tree, tag):
                for commits in ([bad], [bad, on_main]):
                    errors, checks = verdict(commits, pulp_root=pulp, defer=False)
                    self.assertEqual(errors, [f"{verify_extraction.FRAMEWORK_PORTS} rows[0]: Pulp commit {bad} is "
                                              "not a commit on Pulp origin/main"])
                    self.assertIs(checks["framework_ports_proved"], False)
            # The last named commit must hold the file: one that never did, or
            # that holds a directory there, is refused.
            for commits in ([before], [on_main, before]):
                errors, _ = verdict(commits, pulp_root=pulp, defer=False)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(f"the last named Pulp commit {before[:12]} holds no file at core/a/src/new.cpp", errors[0])
            errors, _ = verdict([on_main], pulp_path="core/a/src", pulp_root=pulp, defer=False)
            self.assertIn("holds no file at core/a/src", errors[0])
            # A symlink at the path is not the content either.
            run("checkout", "-q", "--detach", on_main)
            os.symlink("new.cpp", pulp / "core/a/src/link.cpp")
            run("add", "-A"); run("commit", "-q", "-m", "a link")
            linked = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", linked)
            errors, _ = verdict([linked], pulp_path="core/a/src/link.cpp", pulp_root=pulp, defer=False)
            self.assertIn("holds no file at core/a/src/link.cpp", errors[0])
            self.assertEqual(verdict([linked], pulp_root=pulp, defer=False)[0], [])  # control: the file beside it
            # Without a Pulp checkout the proof fails closed; only a lane that
            # defers to the required provenance lane may skip it.
            errors, checks = verdict([on_main], pulp_root=None, defer=False)
            self.assertEqual(errors, [f"{verify_extraction.FRAMEWORK_PORTS}: no Pulp checkout proved the Pulp commits "
                                      "it names; pass --pulp-root"])
            errors, checks = verdict(["d" * 40], pulp_root=None, defer=True)
            self.assertEqual(errors, [])
            self.assertEqual(checks["framework_ports_proved"], "deferred: no Pulp checkout")
            # A checkout with no main ref proves nothing.
            run("update-ref", "-d", "refs/remotes/origin/main")
            errors, _ = verdict([on_main], pulp_root=pulp, defer=False)
            self.assertIn("has no origin/main", errors[0])
            # A native row names no Pulp commit, so it needs no checkout.
            (root / self.PORTED).write_bytes(b"int written_here;\n")
            self.write_ports(root, [self.ports_row(self.PORTED)])
            errors, checks = self.ports_verdict(root, pulp_root=None, defer=False)
            self.assertEqual(errors, [])
            self.assertIs(checks["framework_ports_proved"], True)

    def test_a_pulp_row_s_file_is_pulp_s_renamed_bytes(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            run = lambda *args: subprocess.check_output(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            pulp_path, ported = "core/a/include/pulp/a/new.hpp", "framework/core/a/include/vellum/a/new.hpp"
            first = b"#include <pulp/a/a.hpp>\nnamespace pulp::a { int first(); }\n"
            second = b"#include <pulp/a/a.hpp>\nnamespace pulp::a { int second(); }\n"
            renamed = {first: b"#include <vellum/a/a.hpp>\nnamespace vellum::a { int first(); }\n",
                       second: b"#include <vellum/a/a.hpp>\nnamespace vellum::a { int second(); }\n"}
            run("init", "-q", "-b", "main")
            (pulp / pulp_path).parent.mkdir(parents=True)
            commits = {}
            for data in (first, second):
                (pulp / pulp_path).write_bytes(data)
                run("add", "-A"); run("commit", "-q", "-m", "a version")
                commits[data] = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", commits[second])
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.record_rename(root)
            (root / ported).parent.mkdir(parents=True)

            def verdict(data: bytes, commit: str, blob_of: bytes | None = None, path: str = ported, **kwargs):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data)
                recorded = verify_extraction.git_blob_sha(data if blob_of is None else blob_of)
                self.write_ports(root, [self.ports_row(path, commits=[commit], pulp_path=pulp_path, blob=recorded)])
                errors, _ = self.ports_verdict(root, **kwargs)
                (root / path).unlink()
                return errors

            checked = dict(pulp_root=pulp, defer=False)
            # The file is Pulp's file at the named commit, put through the transfer's rename: proved.
            for data in (first, second):
                self.assertEqual(verdict(renamed[data], commits[data], **checked), [])
            # One byte changed, with the row recording the changed file's own blob.
            errors = verdict(renamed[second] + b" ", commits[second], **checked)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("is not Pulp", errors[0])
            self.assertIn("put through the transfer's rename", errors[0])
            # Bytes that are another commit's version of the file.
            errors = verdict(renamed[first], commits[second], **checked)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("put through the transfer's rename", errors[0])
            # Pulp's bytes copied without the rename are not the transferred form.
            errors = verdict(second, commits[second], **checked)
            self.assertTrue(any("put through the transfer's rename" in e for e in errors), errors)
            # Arbitrary content labelled as Pulp.
            errors = verdict(b"int anything;\n", commits[second], **checked)
            self.assertTrue(any("put through the transfer's rename" in e for e in errors), errors)
            # The row records Pulp's renamed blob but the file is something else:
            # refused with or without a Pulp checkout.
            for kwargs in (checked, dict(pulp_root=None, defer=True)):
                errors = verdict(b"int anything;\n", commits[second], blob_of=renamed[second], **kwargs)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("not the recorded", errors[0])
            # The path is the renamed path too.
            errors = verdict(renamed[second], commits[second], path="framework/core/a/include/vellum/a/moved.hpp",
                             **checked)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn(f"renames to {ported}", errors[0])
            # With no rename table in the transfer record nothing can be proved.
            record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
            body = json.loads(record.read_text(encoding="utf-8"))
            record.write_text(json.dumps({k: v for k, v in body.items() if k != "rename"}), encoding="utf-8")
            errors = verdict(renamed[second], commits[second], **checked)
            self.assertIn("carries no rename table", errors[0])

    def test_a_landed_pulp_row_s_file_may_be_edited(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            before_the_port = self.git_commit_all(root, "the transfer")
            (root / self.PORTED).write_bytes(b"int n;\n")
            row = self.ports_row(self.PORTED, commits=["9" * 40], blob=verify_extraction.git_blob_sha(b"int n;\n"))
            self.write_ports(root, [row])
            landed = self.git_commit_all(root, "a landed port")
            (root / self.PORTED).write_bytes(b"int n = 1;\n")
            # Landed: the file is framework source and a reviewed edit may change it.
            os.environ["VELLUM_GIT_BASE"] = landed
            self.assertEqual(self.ports_verdict(root)[0], [])
            # Control: judged as the change that appends the row, the same bytes are refused.
            os.environ["VELLUM_GIT_BASE"] = before_the_port
            errors, _ = self.ports_verdict(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("not the recorded", errors[0])
            # A row appended after a landed one is judged, by its own index.
            os.environ["VELLUM_GIT_BASE"] = landed
            second = "framework/core/a/src/second.cpp"
            (root / second).write_bytes(b"int s = 2;\n")
            self.write_ports(root, [row, self.ports_row(second, commits=["9" * 40],
                                                        blob=verify_extraction.git_blob_sha(b"int s;\n"))])
            errors, _ = self.ports_verdict(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("rows[1]", errors[0])
            self.assertIn("not the recorded", errors[0])

    def test_a_native_row_may_not_carry_pulp_bytes(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root, pulp = Path(temporary) / "vellum", Path(temporary) / "pulp"
            root.mkdir()
            pulp.mkdir()
            run = lambda *args: subprocess.check_output(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "new.cpp").write_bytes(b"int from_the_other_side;\n")
            run("add", "-A"); run("commit", "-q", "-m", "source")
            commit = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", commit)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.write_ports(root, [self.ports_row(self.PORTED)])
            refused = "is added as native, but its bytes are a Pulp blob"
            # Control: a file Vellum wrote passes as native, with or without a checkout.
            (root / self.PORTED).write_bytes(b"int written_here;\n")
            for checkout in (None, pulp):
                self.assertEqual(self.ports_verdict(root, pulp_root=checkout, defer=False)[0], [])
            # Bytes Pulp's object store holds are refused under a native row.
            (root / self.PORTED).write_bytes(b"int from_the_other_side;\n")
            errors, _ = self.ports_verdict(root, pulp_root=pulp, defer=False)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn(refused, errors[0])
            # The same bytes under a row that names the commit are a port.
            self.record_rename(root)
            self.write_ports(root, [self.ports_row(
                "framework/new.cpp", commits=[commit], pulp_path="new.cpp",
                blob=verify_extraction.git_blob_sha(b"int from_the_other_side;\n"))])
            (root / "framework/new.cpp").write_bytes(b"int from_the_other_side;\n")
            (root / self.PORTED).unlink()
            self.assertEqual(self.ports_verdict(root, pulp_root=pulp, defer=False)[0], [])
            (root / "framework/new.cpp").unlink()
            (root / self.PORTED).write_bytes(b"int from_the_other_side;\n")
            # Without a checkout, a blob the mirror's origin records list is still refused.
            self.write_ports(root, [self.ports_row(self.PORTED)])
            self.assertEqual(self.ports_verdict(root)[0], [])
            origins = root / verify_extraction.MIRROR_ORIGIN_RECORDS
            origins.parent.mkdir(parents=True, exist_ok=True)
            origins.write_text(json.dumps({"schema": verify_extraction.MIRROR_ORIGIN_SCHEMA,
                                           "pulp_repository": "Generous-Corp/pulp", "records": [{
                "mirror_path": "mirror/new.cpp", "pulp_path": "new.cpp", "pulp_commit": commit,
                "git_blob_sha": verify_extraction.git_blob_sha(b"int from_the_other_side;\n"),
                "export_receipt": "provenance/mirror/receipts/r.json"}]}), encoding="utf-8")
            errors, _ = self.ports_verdict(root)
            self.assertTrue(any(refused in e for e in errors), errors)

    def test_a_ported_file_is_held_to_the_framework_content_rules(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            self.write_ports(root, [self.ports_row(self.PORTED)])
            (root / self.PORTED).write_bytes(b"int n;\n")
            errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            # Control: a clean ported file raises no debt and no residual spelling.
            self.assertFalse(any("name debt" in e or "still spells" in e for e in errors), errors)
            # It starts with no name debt, and none may be recorded for it.
            (root / self.PORTED).write_bytes(b"#if PULP_HAS_SKIA\nint n;\n#endif\n")
            errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertIn(f"framework name debt rose: pulp-package-or-target in {self.PORTED} is 1, baseline 0", errors)
            base = self.git_commit_all(root, "base")
            os.environ["VELLUM_GIT_BASE"] = base
            self.write_framework_debt(root, {"pulp-package-or-target": {self.PORTED: 1}})
            errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertTrue(any("the baseline may only fall" in e for e in errors), errors)
            # The old spelling is refused in it as in any framework file.
            (root / self.PORTED).write_bytes(b"namespace pulp { int n; }\n")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_residual_pulp_spellings"], [self.PORTED])

    def test_a_gitlink_under_framework_is_an_unrecorded_entry(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            subprocess.run(["git", "-C", str(root), "update-index", "--add", "--cacheinfo",
                            f"160000,{'1' * 40},framework/zz_sub"], check=True)
            _, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_unrecorded_files"], ["framework/zz_sub"])

    def test_framework_collision_census_refuses_a_native_redeclaration(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {
                "framework/core/runtime/include/vellum/runtime/log.hpp":
                    b"#pragma once\nnamespace vellum::runtime {\nclass Logger;\nvoid log_line(int level);\n}\n",
                "runtime/include/vellum/runtime/kernel.hpp":
                    b"#pragma once\nnamespace vellum::runtime {\nclass Kernel;\n}\n",
            })
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            census = checks["framework_collision_census"]
            # Control: both sides are seen, and nothing collides yet.
            self.assertGreater(census["declarations"]["mirror"], 0)
            self.assertGreater(census["declarations"]["native"], 0)
            self.assertFalse(any("collision census" in e for e in errors), errors)
            # A native declaration of a framework/ name is refused.
            # An untracked tree (CI checks Pulp out at .mirror-pulp inside the
            # checkout) is not Vellum source: its copy collides with nothing.
            stray = root / ".mirror-pulp/core/runtime/include/pulp/runtime/log.hpp"
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"#pragma once\nnamespace vellum::runtime {\nclass Logger;\n}\n")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_collision_census"]["name_collisions"], 0)
            self.assertFalse(any("collision census" in e for e in errors), errors)
            # The same declaration in a tracked native file is refused.
            (root / "runtime/include/vellum/runtime/kernel.hpp").write_bytes(
                b"#pragma once\nnamespace vellum::runtime {\nclass Kernel;\nclass Logger;\n}\n")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_collision_census"]["name_collisions"], 1)
            self.assertTrue(any("framework collision census: 1 name collisions" in e for e in errors), errors)

    def test_framework_census_reads_every_tracked_native_file(self) -> None:
        # The census must see a tracked second copy wherever it sits: under
        # each directory name the pinned helper's own walk skips, and under a
        # build* component (here also git-ignored, so it is force-added).
        self.hide_ci_git_base()
        helper = verify_extraction.load_rename_tool().collisions
        unit = (b"#pragma once\nnamespace vellum::runtime {\nclass Logger;\nvoid log_line(int level);\n}\n")
        names = sorted(helper.SKIP_DIRS - {".git"}) + ["build-plant", "builders"]
        self.assertGreater(len(names), 2)  # control: the helper's list was read
        for name in names:
            with self.subTest(directory=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.framework_fixture(root, {"framework/core/runtime/include/vellum/runtime/log.hpp": unit})
                (root / ".gitignore").write_text("build*/\n", encoding="utf-8")
                copy = root / "runtime" / name / "zz_dup.hpp"
                copy.parent.mkdir(parents=True)
                copy.write_bytes(b"#pragma once\nnamespace vellum::runtime {\nclass Logger;\n}\n")
                subprocess.run(["git", "-C", str(root), "add", "-f", "-A"], check=True)
                errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                self.assertEqual(checks["framework_collision_census"]["name_collisions"], 1)
                self.assertTrue(any("framework collision census: 1 name collisions" in e for e in errors), errors)
        # .git cannot hold a tracked file: git declines to add a path under it,
        # so there is no tracked copy there for the census to miss.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "runtime/.git").mkdir(parents=True)
            (root / "runtime/.git/zz.hpp").write_text("int x;\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-f", "runtime/.git/zz.hpp"],
                           capture_output=True, check=False)
            listed = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True)
            self.assertEqual(listed.stdout, "")

    def test_framework_census_quarantines_a_byte_copy_under_build(self) -> None:
        self.hide_ci_git_base()
        unit = b"#pragma once\nnamespace vellum::runtime {\nclass SpscQueue;\n}\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/runtime/include/vellum/runtime/spsc_queue.hpp": unit})
            (root / ".gitignore").write_text("build*/\n", encoding="utf-8")
            (root / "build-plant/runtime").mkdir(parents=True)
            (root / "build-plant/runtime/spsc_queue.hpp").write_bytes(unit)
            subprocess.run(["git", "-C", str(root), "add", "-f", "-A"], check=True)
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            census = checks["framework_collision_census"]
            self.assertEqual(census["blob_quarantine"], 1)
            self.assertTrue(any("blob quarantine" in e for e in errors), errors)

    EXTERNAL = "framework/external/lib/lib.h"
    EXTERNAL_BYTES = b"/* third-party */\nint lib(void);\n"

    def external_fixture(self, root: Path) -> dict[str, str]:
        (root / self.EXTERNAL).parent.mkdir(parents=True)
        (root / self.EXTERNAL).write_bytes(self.EXTERNAL_BYTES)
        return {self.EXTERNAL: verify_extraction.git_blob_sha(self.EXTERNAL_BYTES)}

    def write_updates(self, root: Path, entries) -> None:
        target = root / verify_extraction.FRAMEWORK_EXTERNAL_UPDATES
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(entries), encoding="utf-8")

    def update(self, path: str, before: bytes, after: bytes) -> dict[str, str]:
        return {"path": path, "from_blob": verify_extraction.git_blob_sha(before),
                "to_blob": verify_extraction.git_blob_sha(after), "reason": "security fix",
                "dependency": "lib", "version": "1.2.4"}

    def test_framework_external_files_are_pinned_to_their_blobs(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorded = self.external_fixture(root)
            # An unmodified file passes.
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])
            # An in-place edit with no update entry is refused.
            smuggled = self.EXTERNAL_BYTES + b"int lib_v2(void);\n"  # an edit; clean bytes
            (root / self.EXTERNAL).write_bytes(smuggled)
            errors, checks = verify_extraction.framework_external_errors(root, recorded)
            self.assertEqual(checks["framework_external_edited"], [self.EXTERNAL])
            self.assertTrue(any("only through the dependency process" in e for e in errors), errors)
            # A matching entry makes the same bytes a reviewed update.
            self.write_updates(root, [self.update(self.EXTERNAL, self.EXTERNAL_BYTES, smuggled)])
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])
            # An entry whose to_blob is not the file is refused.
            self.write_updates(root, [self.update(self.EXTERNAL, self.EXTERNAL_BYTES, b"other\n")])
            self.assertTrue(any("differ from their pinned blobs" in e for e in verify_extraction.framework_external_errors(root, recorded)[0]))
            # A broken chain is refused.
            self.write_updates(root, [self.update(self.EXTERNAL, b"not the recorded bytes\n", smuggled)])
            self.assertTrue(any("breaks the chain" in e for e in verify_extraction.framework_external_errors(root, recorded)[0]))
            # A chain of two updates resolves to the last to_blob.
            later = smuggled + b"// 1.2.5\n"
            (root / self.EXTERNAL).write_bytes(later)
            self.write_updates(root, [self.update(self.EXTERNAL, self.EXTERNAL_BYTES, smuggled),
                                      self.update(self.EXTERNAL, smuggled, later)])
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])
            # An entry for a path the transfer record does not list is refused.
            self.write_updates(root, [self.update("framework/external/lib/new.h", b"a", b"b")])
            self.assertTrue(any("is not a framework/external/ file the transfer record lists" in e
                                for e in verify_extraction.framework_external_errors(root, recorded)[0]))
            # Malformed lists and entries fail closed.
            for bad in ("{", json.dumps({"path": self.EXTERNAL}), json.dumps([{"path": self.EXTERNAL}])):
                (root / verify_extraction.FRAMEWORK_EXTERNAL_UPDATES).write_text(bad, encoding="utf-8")
                self.assertTrue(verify_extraction.framework_external_errors(root, recorded)[0], bad)

    def test_framework_external_updates_are_append_only(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorded = self.external_fixture(root)
            first = self.EXTERNAL_BYTES + b"// 1.2.4\n"
            (root / self.EXTERNAL).write_bytes(first)
            entry = self.update(self.EXTERNAL, self.EXTERNAL_BYTES, first)
            self.write_updates(root, [entry])
            base = self.git_commit_all(root, "land an update")
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "change"], check=True)
            os.environ["VELLUM_GIT_BASE"] = base
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])  # control
            # Appending passes.
            second = first + b"// 1.2.5\n"
            (root / self.EXTERNAL).write_bytes(second)
            self.write_updates(root, [entry, self.update(self.EXTERNAL, first, second)])
            self.git_commit_all(root, "append")
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])
            # Editing the landed entry, or removing it, is refused.
            (root / self.EXTERNAL).write_bytes(first)
            self.write_updates(root, [dict(entry, reason="rewritten")])
            self.git_commit_all(root, "edit")
            self.assertTrue(any("append-only" in e for e in verify_extraction.framework_external_errors(root, recorded)[0]))
            (root / self.EXTERNAL).write_bytes(self.EXTERNAL_BYTES)
            self.write_updates(root, [])
            self.git_commit_all(root, "remove")
            self.assertTrue(any("append-only" in e for e in verify_extraction.framework_external_errors(root, recorded)[0]))

    def test_verify_framework_refuses_an_in_place_external_edit(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n",
                                          self.EXTERNAL: self.EXTERNAL_BYTES})
            record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
            value = json.loads(record.read_text(encoding="utf-8"))
            for row in value["records"]:
                row["framework_git_blob_sha"] = verify_extraction.git_blob_sha((root / row["framework_path"]).read_bytes())
            record.write_text(json.dumps(value), encoding="utf-8")
            _, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_external_edited"], [])
            (root / self.EXTERNAL).write_bytes(self.EXTERNAL_BYTES + b"#define PULP_SMUGGLED 1\n")
            errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_external_edited"], [self.EXTERNAL])
            self.assertTrue(any("only through the dependency process" in e for e in errors), errors)
            # An unrecorded external file is still refused, as before.
            (root / "framework/external/lib/extra.h").write_bytes(b"int extra;\n")
            _, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertEqual(checks["framework_external_unrecorded"], ["framework/external/lib/extra.h"])

    HTTPLIB = "framework/external/cpp-httplib/httplib.h"
    HTTPLIB_BYTES = b"// cpp-httplib\nnamespace httplib {\nclass Client;\n}\n"
    SPSC = "framework/core/runtime/include/vellum/runtime/spsc_queue.hpp"
    SPSC_BYTES = b"#pragma once\nnamespace vellum::runtime {\nclass SpscQueue;\n}\n"

    def updated_external_fixture(self, root: Path, updated: bytes) -> list[str]:
        """framework/ with a runtime header and a third-party file updated through a well-formed entry."""
        self.framework_fixture(root, {self.SPSC: self.SPSC_BYTES, self.HTTPLIB: self.HTTPLIB_BYTES,
                                      "runtime/include/vellum/runtime/kernel.hpp":
                                          b"#pragma once\nnamespace vellum::runtime {\nclass Kernel;\n}\n"})
        record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        value = json.loads(record.read_text(encoding="utf-8"))
        for row in value["records"]:
            row["framework_git_blob_sha"] = verify_extraction.git_blob_sha((root / row["framework_path"]).read_bytes())
        record.write_text(json.dumps(value), encoding="utf-8")
        (root / self.HTTPLIB).write_bytes(updated)
        self.write_updates(root, [self.update(self.HTTPLIB, self.HTTPLIB_BYTES, updated)])
        self.track(root)
        errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
        return errors, checks

    def test_the_laundering_plant_through_an_update_entry_is_refused(self) -> None:
        self.hide_ci_git_base()
        plant = (self.HTTPLIB_BYTES + b"namespace vellum::runtime { class SpscQueue { int x; }; }\n"
                 b"#define PULP_SMUGGLED 1\n")
        with tempfile.TemporaryDirectory() as temporary:
            errors, checks = self.updated_external_fixture(Path(temporary), plant)
            self.assertEqual(checks["framework_external_updated"], [self.HTTPLIB])
            self.assertEqual(checks["framework_external_edited"], [])  # the entry itself is well formed
            self.assertTrue(any("declares or names the vellum namespace" in e for e in errors), errors)
            self.assertTrue(any("carries pulp-package-or-target spellings" in e for e in errors), errors)
            # The updated file is native to the census: its SpscQueue collides.
            self.assertEqual(checks["framework_collision_census"]["name_collisions"], 1)

    def test_an_updated_external_file_is_a_native_byte_copy_too(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            # An "update" that replaces the file with a framework/ file's bytes.
            errors, checks = self.updated_external_fixture(Path(temporary), self.SPSC_BYTES)
            self.assertEqual(checks["framework_collision_census"]["blob_quarantine"], 1)

    def test_an_updated_external_file_carrying_a_plugin_sdk_name_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            errors, _ = self.updated_external_fixture(
                Path(temporary), self.HTTPLIB_BYTES + b"// works as a VST3 host helper\n")
            self.assertTrue(any("carries audio-plugin-sdk spellings" in e for e in errors), errors)

    def test_a_clean_third_party_update_passes(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            errors, checks = self.updated_external_fixture(
                Path(temporary), self.HTTPLIB_BYTES + b"namespace httplib {\nclass Server;\n}\n")
            self.assertEqual(checks["framework_external_updated"], [self.HTTPLIB])
            self.assertEqual([e for e in errors if "external" in e or "census" in e], [], errors)
            census = checks["framework_collision_census"]
            self.assertEqual((census["name_collisions"], census["macro_collisions"], census["blob_quarantine"]),
                             (0, 0, 0))

    def introduce_transfer(self, root: Path) -> tuple[str, dict]:
        """A base with a mirror export, and a change that introduces its rename as the landed tool makes it."""
        files = {
            "mirror/core/a/include/pulp/a/a.hpp": b"#pragma once\nnamespace pulp::a {\nint f();\n}\n",
            "mirror/core/a/src/a.cpp": b"#include <pulp/a/a.hpp>\n#if PULP_HAS_A\n#endif\n"
                                       b"namespace pulp::a {\nint f() { return 1; }\n}\n",
        }
        for relative, data in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(data)
        receipt = {"pulp_repository": "Generous-Corp/pulp", "pulp_sha": "a" * 40,
                   "files": [{"pulp_path": p.removeprefix("mirror/"), "blob": verify_extraction.git_blob_sha(d)}
                             for p, d in files.items()]}
        (root / "provenance/mirror/receipts").mkdir(parents=True)
        (root / "provenance/mirror/receipts/r.json").write_text(json.dumps(receipt), encoding="utf-8")
        (root / "provenance/mirror/receipts/index.json").write_text(
            json.dumps({"receipts": ["provenance/mirror/receipts/r.json"]}), encoding="utf-8")
        base = self.git_commit_all(root, "base: the mirror export")
        subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "transfer"], check=True)
        with tempfile.TemporaryDirectory() as scratch:
            derived = verify_extraction.derive_framework_transfer(root, base, Path(scratch))
            shutil.copytree(derived["framework"], root / "framework")
        subprocess.run(["git", "-C", str(root), "rm", "-q", "-r", "mirror"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "framework"], check=True)
        tree = subprocess.run(["git", "-C", str(root), "write-tree", "--prefix=framework/"], check=True,
                              capture_output=True, text=True).stdout.strip()
        record = dict(derived["record"], framework_tree=tree)
        target = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        counts, _ = verify_extraction.framework_name_debt(root)
        self.write_framework_debt(root, {rule: files for rule, files in counts.items() if files})
        self.git_commit_all(root, "introduce the transfer")
        return base, record

    def test_introducing_the_transfer_is_judged_by_the_landed_rules(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, record = self.introduce_transfer(root)
            # Control: the honest introduction passes, and the derivation saw
            # the rename (a renamed include path) and a debt hit.
            self.assertEqual(verify_extraction.framework_introduction_errors(root, base, record), [])
            self.assertIn("framework/core/a/include/vellum/a/a.hpp", {r["framework_path"] for r in record["records"]})
            self.assertTrue(json.loads((root / verify_extraction.FRAMEWORK_NAME_DEBT).read_text())["rules"])
            os.environ["VELLUM_GIT_BASE"] = base
            _, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
            self.assertIs(checks["framework_transfer_introduced"], True)
            self.assertEqual(checks["framework_introduction_errors"], [])

    def test_an_introduction_that_edits_a_rule_file_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, record = self.introduce_transfer(root)
            (root / "tools/mirror").mkdir(parents=True)
            (root / "tools/mirror/rename_mirror_namespace.py").write_text("# looser\n", encoding="utf-8")
            self.git_commit_all(root, "edit a rule")
            errors = verify_extraction.framework_introduction_errors(root, base, record)
            self.assertTrue(any("edits the rules that judge it" in e for e in errors), errors)

    def test_an_introduced_tree_that_is_not_the_tool_output_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, record = self.introduce_transfer(root)
            target = root / "framework/core/a/src/a.cpp"
            target.write_bytes(target.read_bytes() + b"// smuggled\n")
            self.git_commit_all(root, "edit the transferred tree")
            errors = verify_extraction.framework_introduction_errors(root, base, record)
            self.assertTrue(any("is not the landed rename tool's output" in e for e in errors), errors)

    def test_an_introduced_record_that_is_not_derived_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, record = self.introduce_transfer(root)
            for key, value in (("pulp_sha", "b" * 40), ("export_receipt", "provenance/mirror/receipts/x.json"),
                               ("framework_tree", "f" * 40)):
                with self.subTest(key=key):
                    errors = verify_extraction.framework_introduction_errors(root, base, dict(record, **{key: value}))
                    self.assertTrue(any(f"{key} is not the one the landed rename derives" in e for e in errors), errors)
            edited = json.loads(json.dumps(record))
            edited["records"][0]["pulp_git_blob_sha"] = "0" * 40
            errors = verify_extraction.framework_introduction_errors(root, base, edited)
            self.assertTrue(any("records is not the one" in e for e in errors), errors)
            edited = json.loads(json.dumps(record))
            edited["rename"]["tool_git_blob"] = "0" * 40
            errors = verify_extraction.framework_introduction_errors(root, base, edited)
            self.assertTrue(any("rename is not the one" in e for e in errors), errors)
            # The interpreter version is a note, not part of the rename.
            edited = json.loads(json.dumps(record))
            edited["rename"]["python"] = "2.7"
            self.assertEqual(verify_extraction.framework_introduction_errors(root, base, edited), [])

    def test_an_introduced_baseline_must_equal_the_counts(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, record = self.introduce_transfer(root)
            path = root / verify_extraction.FRAMEWORK_NAME_DEBT
            value = json.loads(path.read_text(encoding="utf-8"))
            rule, files = next(iter(value["rules"].items()))
            name = next(iter(files))
            files[name] += 1  # a looser baseline than the tree needs
            path.write_text(json.dumps(value), encoding="utf-8")
            self.git_commit_all(root, "loosen the baseline")
            errors = verify_extraction.framework_introduction_errors(root, base, record)
            self.assertTrue(any("is not the transferred tree's counts" in e for e in errors), errors)

    def test_no_framework_is_a_clean_no_op(self) -> None:
        # Today's main: mirror/ and no framework/ or transfer record.
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mirror/core/a/src").mkdir(parents=True)
            (root / "mirror/core/a/src/a.cpp").write_bytes(b"namespace pulp::a { int x; }\n")
            with mock.patch.object(verify_extraction, "load_rename_tool", side_effect=AssertionError("not read")):
                errors, checks = verify_extraction.verify_framework(root, None, "origin/main")
            self.assertEqual((errors, checks["framework"]), ([], "absent"))
            self.assertFalse(checks["framework_transfer_state"]["head_framework"])

    def test_symlinks_in_native_trees_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/src/a.cpp", b"namespace vellum::view { int x; }\n")
            (root / "graphics").mkdir()
            (root / "graphics/linkdir").symlink_to(root / "mirror/core/view/src", target_is_directory=True)
            (root / "tools").mkdir()
            (root / "tools/linkfile.cpp").symlink_to(root / "mirror/core/view/src/a.cpp")
            # Behind the directory link the file is invisible to the content scan ...
            self.assertEqual(verify_extraction.scan_renamed_mirror(root, ("graphics",)), [])
            # ... so the link itself is refused.
            self.assertEqual(verify_extraction.symlink_findings(root, ("graphics", "tools")),
                             ["graphics/linkdir", "tools/linkfile.cpp"])

    def test_installed_dependency_symlinks_are_not_refused(self) -> None:
        # `npm ci` leaves node_modules/.bin links on disk in CI. node_modules is
        # gitignored, so those links are not part of the tree being verified.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tools/pkg/node_modules/.bin").mkdir(parents=True)
            (root / "tools/pkg/node_modules/esbuild.js").write_text("", encoding="utf-8")
            (root / "tools/pkg/node_modules/.bin/esbuild").symlink_to(root / "tools/pkg/node_modules/esbuild.js")
            (root / "tools/real.cpp").symlink_to(root / "tools/pkg/node_modules/esbuild.js")
            self.assertEqual(verify_extraction.symlink_findings(root, ("tools",)), ["tools/real.cpp"])

    def test_comment_stripping_keeps_string_literals(self) -> None:
        source = (b'const char* glob = "include/*.hpp";\nint keep_this_function_body(int a) { return a * 1234567; }\n'
                  b'const char* end = "*/";\n')
        stripped = verify_extraction._strip_comments(source)
        self.assertIn(b"keep_this_function_body", stripped)
        self.assertIn(b'"include/*.hpp"', stripped)
        self.assertEqual(verify_extraction._strip_comments(b"a /* x */ b // y\n'/*' c"), b"a  b \n'/*' c")
        self.assertIsNotNone(verify_extraction.normalized_fingerprint(source))
        self.assertIn(".inc", verify_extraction.NORMALIZED_SUFFIXES)
        self.assertIn(".in", verify_extraction.NORMALIZED_SUFFIXES)

    def test_undecodable_native_text_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "graphics").mkdir()
            (root / "graphics/bad.cpp").write_bytes(b"int x; \xff\xfe namespace vellum::view {}\n")
            for findings in (verify_extraction.scan_active_surface(root, ("graphics",)),
                             verify_extraction.scan_renamed_mirror(root, ("graphics",))):
                self.assertEqual([(f["path"], f["rule"]) for f in findings],
                                 [("graphics/bad.cpp", "undecodable-native-text")])

    def test_normalized_copies_of_mirror_files_are_refused(self) -> None:
        mirror_source = (b"// Copyright Pulp\n#include <pulp/runtime/log.hpp>\n\nnamespace pulp::runtime {\n"
                         b"/* the logger */\nvoid log_line(const char* message) { write_out(message, 42); }\n}\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/runtime/src/log.cpp", mirror_source)
            self.write_mirror(root, "mirror/external/lib/vendored.c", b"int vendored_function_body(void) { return 1234567; }\n")
            (root / "runtime/src").mkdir(parents=True)
            # Renamed, re-commented, re-indented and CRLF: still the same file.
            edited = (b"#include <vellum/runtime/log.hpp>\r\nnamespace vellum::runtime\r\n{\r\n"
                      b"    // mine now\r\n    void log_line(const char*   message)\r\n"
                      b"    {   write_out(message, 42);   }\r\n}\r\n")
            (root / "runtime/src/copied.cpp").write_bytes(edited)
            # A real edit is a different file to this check.
            (root / "runtime/src/changed.cpp").write_bytes(edited.replace(b"42", b"43"))
            # Third-party files may ship natively too.
            (root / "runtime/src/vendored.c").write_bytes(b"int vendored_function_body(void) { return 1234567; }\n")
            # Too short to fingerprint.
            self.write_mirror(root, "mirror/core/x/tiny.hpp", b"#pragma once\n")
            (root / "runtime/src/tiny.hpp").write_bytes(b"#pragma once\n")

            findings = verify_extraction.normalized_copy_findings(root, ("runtime",))

            self.assertEqual(findings, [{"path": "runtime/src/copied.cpp", "mirror": "mirror/core/runtime/src/log.cpp"}])

    def test_origin_records_are_strictly_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "blob-origins.json"
            good = {
                "mirror_path": "mirror/core/a.cpp",
                "git_blob_sha": "b" * 40,
                "pulp_commit": "c" * 40,
                "pulp_path": "core/a.cpp",
                "export_receipt": "provenance/mirror/receipts/first.json",
            }
            path.write_text(
                json.dumps(
                    {
                        "schema": "vellum.mirror-blob-origins.v1",
                        "pulp_repository": "Generous-Corp/pulp",
                        "records": [
                            good,
                            dict(good),
                            dict(good, mirror_path="graphics/a.cpp"),
                            dict(good, pulp_path="../secret"),
                            dict(good, pulp_commit="main"),
                            dict(good, extra="x"),
                        ],
                    }
                ),
                encoding="utf-8",
            )

            records, errors = verify_extraction.load_mirror_origin_records(path)

            self.assertEqual(len(records), 5)
            joined = "\n".join(errors)
            for expected in (
                "record 1 duplicates an earlier record",
                "record 2 mirror_path is outside mirror/",
                "record 3 pulp_path is not a repository-relative path",
                "record 4 pulp_commit is not a full commit SHA",
                "record 5 has unexpected keys",
            ):
                self.assertIn(expected, joined)
            missing, missing_errors = verify_extraction.load_mirror_origin_records(
                Path(temporary) / "absent.json"
            )
            self.assertEqual(missing, [])
            self.assertTrue(missing_errors)

    def test_origin_records_are_proved_against_pulp_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)

            def run(*args: str) -> str:
                return subprocess.check_output(
                    ["git", *args], cwd=pulp, text=True
                ).strip()

            run("init", "-q")
            (pulp / "core").mkdir()
            (pulp / "core/a.cpp").write_bytes(b"int a;\n")
            run("add", "core/a.cpp")
            run(
                "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                "commit", "-q", "-m", "origin",
            )
            commit = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", commit)
            record = {
                "mirror_path": "mirror/core/a.cpp",
                "git_blob_sha": verify_extraction.git_blob_sha(b"int a;\n"),
                "pulp_commit": commit,
                "pulp_path": "core/a.cpp",
                "export_receipt": "provenance/mirror/receipts/first.json",
            }

            self.assertEqual(verify_extraction.verify_origins_against_pulp(pulp, [record]), [])
            forged = dict(record, git_blob_sha=verify_extraction.git_blob_sha(b"int b;\n"))
            wrong_path = dict(record, pulp_path="core/missing.cpp")
            self.assertEqual(
                len(verify_extraction.verify_origins_against_pulp(pulp, [forged, wrong_path])),
                2,
            )

    def test_origin_proof_requires_origin_main_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=pulp)
            (pulp / "a.cpp").write_text("int a;\n")
            subprocess.check_call(["git", "add", "a.cpp"], cwd=pulp)
            subprocess.check_call(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "main"], cwd=pulp)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=pulp, text=True).strip()
            record = {"mirror_path": "mirror/a.cpp", "git_blob_sha": verify_extraction.git_blob_sha(b"int a;\n"), "pulp_commit": commit, "pulp_path": "a.cpp", "export_receipt": "provenance/mirror/receipts/x.json"}
            errors = verify_extraction.verify_origins_against_pulp(pulp, [record])
            self.assertIn("has no refs/remotes/origin/main", errors[0])

    def test_origin_proof_rejects_head_commit_off_origin_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            run = lambda *args: subprocess.check_output(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "a.cpp").write_text("int a;\n")
            run("add", "a.cpp"); run("commit", "-q", "-m", "main")
            main = run("rev-parse", "HEAD"); run("update-ref", "refs/remotes/origin/main", main)
            run("checkout", "-q", "-b", "side")
            (pulp / "a.cpp").write_text("int side;\n")
            run("add", "a.cpp"); run("commit", "-q", "-m", "side")
            side = run("rev-parse", "HEAD")
            record = {"mirror_path": "mirror/a.cpp", "git_blob_sha": verify_extraction.git_blob_sha(b"int side;\n"), "pulp_commit": side, "pulp_path": "a.cpp", "export_receipt": "provenance/mirror/receipts/x.json"}
            errors = verify_extraction.verify_origins_against_pulp(pulp, [record])
            self.assertIn("not on the checked-out Pulp main", errors[0])

    def test_required_provenance_workflow_does_not_defer_mirror_identity(self) -> None:
        workflow = MODULE_PATH.parents[2] / ".github/workflows/provenance.yml"
        self.assertNotIn("--defer-mirror-identity", workflow.read_text(encoding="utf-8"))

    def test_origin_records_must_cite_pulp_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)

            def run(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                    cwd=pulp, text=True,
                ).strip()

            run("init", "-q", "-b", "main")
            (pulp / "a.cpp").write_bytes(b"int a;\n")
            run("add", "a.cpp")
            run("commit", "-q", "-m", "main")
            run("checkout", "-q", "-b", "side")
            (pulp / "core").mkdir()
            (pulp / "core/a.cpp").write_bytes(b"int side;\n")
            run("add", "core/a.cpp")
            run("commit", "-q", "-m", "side")
            side = run("rev-parse", "HEAD")
            run("checkout", "-q", "main")
            run("update-ref", "refs/remotes/origin/main", run("rev-parse", "main"))
            record = {
                "mirror_path": "mirror/core/a.cpp",
                "git_blob_sha": verify_extraction.git_blob_sha(b"int side;\n"),
                "pulp_commit": side,
                "pulp_path": "core/a.cpp",
                "export_receipt": "provenance/mirror/receipts/x.json",
            }
            errors = verify_extraction.verify_origins_against_pulp(pulp, [record])
            self.assertEqual(len(errors), 1)
            self.assertIn("not on the checked-out Pulp main", errors[0])

    def test_mirror_rejects_pulp_kept_view_audio_and_content_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(
                root,
                "mirror/core/view/src/panel.cpp",
                b"#include <pulp/view/view.hpp>\n"
                b"#include <pulp/view/eq_curve_view.hpp>\n"
                b"#include \"pulp/view/widgets/graph_editor_view.hpp\"\n"
                b"#include <pulp/view/waveform_overview_view.hpp>\n"
                b"#include <pulp/view/audio/meter.hpp>\n"
                b"#include <pulp/state/content_registry.hpp>\n"
                b"#include <pulp/state/store.hpp>\n",
            )
            self.write_mirror(root, "mirror/core/view/src/eq_curve_view.cpp", b"int eq;\n")
            self.write_mirror(
                root, "mirror/core/state/src/content_registry.cpp", b"int registry;\n"
            )
            self.write_mirror(root, "mirror/core/view/src/view.cpp", b"int view;\n")

            findings = verify_extraction.mirror_edge_findings(root)

            kept_includes = [
                finding["line"] for finding in findings
                if finding["rule"] == "mirror-pulp-kept-include"
            ]
            self.assertEqual(sorted(kept_includes), [2, 3, 4, 5, 6])
            self.assertEqual(
                sorted(
                    finding["path"] for finding in findings
                    if finding["rule"] == "mirror-pulp-kept-file"
                ),
                [
                    "mirror/core/state/src/content_registry.cpp",
                    "mirror/core/view/src/eq_curve_view.cpp",
                ],
            )

    def clone_repository(self, temporary: str) -> Path:
        clone = Path(temporary) / "vellum"
        subprocess.check_call(
            ["git", "clone", "-q", "--no-hardlinks", str(MODULE_PATH.parents[2]), str(clone)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for relative in (
            "tools/provenance/verify_extraction.py",
            "tools/provenance/verify_mirror_export.py",
            "provenance/active-source-boundary.json",
            "provenance/mirror/blob-origins.json",
            "provenance/mirror/link-policy.json",
            "provenance/mirror/receipts/index.json",
        ):
            source = MODULE_PATH.parents[2] / relative
            (clone / relative).parent.mkdir(parents=True, exist_ok=True)
            (clone / relative).write_bytes(source.read_bytes())
        return clone

    def test_fabricated_origin_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = self.clone_repository(temporary)
            content = b"namespace pulp::view { int forged; }\n"
            record = self.write_mirror(clone, "mirror/core/view/src/forged.cpp", content)
            record["pulp_commit"] = "f" * 40
            (clone / "provenance/mirror/blob-origins.json").write_text(
                json.dumps({
                    "schema": "vellum.mirror-blob-origins.v1",
                    "pulp_repository": "Generous-Corp/pulp",
                    "records": [record],
                }),
                encoding="utf-8",
            )
            subprocess.check_call(["git", "add", "-A"], cwd=clone)
            subprocess.check_call(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "commit", "-q", "-m", "forge"],
                cwd=clone,
            )

            unproved = verify_extraction.verify(clone)
            deferred = verify_extraction.verify(clone, defer_mirror_identity=True)
            mirror_file_count = len(list(verify_extraction.mirror_files(clone)))
            landed = json.loads(
                (clone / "provenance/mirror/receipts/index.json").read_text(encoding="utf-8")
            )["receipts"]
            forged_export_error = (
                f"mirror export: {landed[-1]}: mirror/ still exists at HEAD after the framework transfer"
                if (clone / verify_extraction.FRAMEWORK_TRANSFER_RECORD).is_file()
                else f"mirror export: {landed[-1]}: HEAD:mirror is not the newest receipt's mirror tree"
                if landed
                else "mirror export: mirror/ exists at HEAD without an export receipt"
            )
            pulp = Path(temporary) / "pulp"
            pulp.mkdir()
            subprocess.check_call(["git", "init", "-q"], cwd=pulp)
            (pulp / "x").write_text("x\n")
            subprocess.check_call(["git", "add", "x"], cwd=pulp)
            subprocess.check_call(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "commit", "-q", "-m", "pulp"],
                cwd=pulp,
            )
            main = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=pulp, text=True).strip()
            subprocess.check_call(["git", "update-ref", "refs/remotes/origin/main", main], cwd=pulp)
            proved = verify_extraction.verify(clone, pulp)

            self.assertEqual(unproved["status"], "fail")
            self.assertIn(
                f"mirror root has {mirror_file_count} files but no Pulp checkout proved them; pass --pulp-root",
                unproved["errors"],
            )
            for report in (unproved, deferred, proved):
                self.assertEqual(report["status"], "fail")
                self.assertIn(
                    "blob-origin records name 1 receipts absent from the receipt index",
                    report["errors"],
                )
                # The forged file changes HEAD:mirror, so the newest landed receipt
                # (or, with none landed, the receipt requirement) must reject it.
                self.assertIn(forged_export_error, report["errors"])
            self.assertNotIn(
                f"mirror root has {mirror_file_count} files but no Pulp checkout proved them; pass --pulp-root",
                deferred["errors"],
            )
            self.assertTrue(any("not on the checked-out Pulp main" in e for e in proved["errors"]))

    def test_declared_rules_must_match_enforced_rules(self) -> None:
        root = MODULE_PATH.parents[2]
        boundary = verify_extraction.configured_boundary(root)
        stale = dict(
            boundary,
            rules={
                "retired_paths_at_active_tip": 0,
                "active_files_matching_historical_seed_blobs": 0,
                "forbidden_namespace_or_product_references": 0,
                "open_extraction_debts": 0,
            },
        )
        with mock.patch.object(verify_extraction, "configured_boundary", return_value=stale):
            report = verify_extraction.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertIn("declared boundary rules differ from the enforced rules", report["errors"])

    def test_mirror_edge_rules_come_from_the_shared_table(self) -> None:
        table = json.loads(verify_extraction.MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
        self.assertEqual(table["schema"], "pulp.vellum.mirror-edge-patterns.v1")
        self.assertEqual(
            {name: rule.pattern for name, rule in verify_extraction.FORBIDDEN_MIRROR_EDGE_PATTERNS.items()},
            table["content_patterns"],
        )
        self.assertEqual(verify_extraction.MIRROR_EDGE_SUFFIXES, set(table["suffixes"]))
        self.assertTrue({"", ".md", ".ps1", ".sh", ".ts", ".tsx", ".yaml", ".yml"} <= verify_extraction.MIRROR_EDGE_SUFFIXES)
        self.assertEqual(set(table), {"schema", "description", "roots", "suffixes", "binary_suffixes",
                                      "content_patterns", "path_patterns", "approved_exceptions", "controls"})
        self.assertEqual(set(table["content_patterns"]), {
            "mirror-product-include", "mirror-product-symbol", "mirror-audio-framework-link",
            "mirror-pulp-kept-include", "mirror-pulp-kept-symbol"})
        self.assertEqual(set(table["path_patterns"]), {"mirror-pulp-kept-file"})
        for row in table["approved_exceptions"]:
            self.assertEqual(set(row), {"rule", "path", "line", "debt"})
            self.assertIn(row["rule"], table["content_patterns"])
            self.assertFalse(row["path"].startswith("mirror/"))
        self.assertEqual(
            verify_extraction.MIRROR_EDGE_EXCEPTIONS,
            {(row["rule"], row["path"], row["line"]) for row in table["approved_exceptions"]},
        )

    def test_every_mirror_edge_rule_matches_its_controls(self) -> None:
        # The vendored rules are spelled from the table's roots; Pulp's
        # generator test proves they follow a respelling. Here each rule must
        # match every control the table gives it, under every root it names,
        # so a rule the scan applies can never silently match nothing.
        table = json.loads(verify_extraction.MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
        roots = table["roots"]
        self.assertEqual(set(roots), {"namespace", "include", "target"})
        # Controls are instantiated with one spelling for every placeholder.
        self.assertEqual(roots["namespace"], roots["include"])
        self.assertEqual(roots["namespace"], roots["target"])
        rules = {**verify_extraction.FORBIDDEN_MIRROR_EDGE_PATTERNS, **verify_extraction.MIRROR_EDGE_PATH_PATTERNS}
        self.assertEqual(set(table["controls"]), set(rules))
        spellings = set(roots["namespace"])
        for rule, compiled in rules.items():
            templates = table["controls"].get(rule, [])
            self.assertTrue(templates, rule)
            for template in templates:
                for name in sorted(spellings):
                    text = template
                    for kind in roots:
                        text = text.replace(f"@{kind}@", name)
                    with self.subTest(rule=rule, control=text):
                        self.assertNotIn("@", text)
                        self.assertTrue(compiled.search(text))

    def test_pulp_kept_view_audio_edges_are_findings(self) -> None:
        # The edges the Vellum #92 review planted: five kept view-audio
        # headers and the register_audio_widgets symbol, with its control.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            headers = ("audio_widgets", "register_audio_widgets", "visualizers",
                       "visualization_bridge", "hosted_editor_attachment", "audio_inspector_panel")
            body = "".join(f"#include <pulp/view/{name}.hpp>\n" for name in headers)
            body += "void f() { pulp::view::register_audio_widgets(); }\n"
            self.write_mirror(root, "mirror/core/view/src/a.cpp", body.encode())
            # Not kept: exported view headers, lookalike names and the gate macro.
            self.write_mirror(root, "mirror/core/view/src/b.cpp", (
                b"#include <pulp/view/widgets.hpp>\n#include <pulp/view/host_surfaces.hpp>\n"
                b"int register_audio_widgets_count = 0;\n#if defined(PULP_VIEW_HAS_AUDIO_WIDGETS)\n#endif\n"))
            findings = verify_extraction.mirror_edge_findings(root)
        self.assertEqual(
            sorted((f["rule"], f["path"], f["line"]) for f in findings),
            sorted([("mirror-pulp-kept-include", "mirror/core/view/src/a.cpp", line) for line in range(1, 7)]
                   # The register_audio_widgets.hpp include names the symbol too.
                   + [("mirror-pulp-kept-symbol", "mirror/core/view/src/a.cpp", line) for line in (2, 7)]),
        )

    def test_only_the_recorded_seam_debt_line_is_exempt(self) -> None:
        debt = (b"#pragma once\n#if defined(PULP_VIEW_HAS_AUDIO_WIDGETS)\n"
                b"#include <pulp/view/audio_widgets.hpp>\n#endif\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/include/pulp/view/widgets.hpp", debt)
            self.assertEqual(verify_extraction.mirror_edge_findings(root), [])
            # The same line in another file, or a second kept include in this one, is a finding.
            self.write_mirror(root, "mirror/core/view/src/a.cpp", debt)
            self.write_mirror(root, "mirror/core/view/include/pulp/view/widgets.hpp",
                              debt + b"#include <pulp/view/visualizers.hpp>\n")
            findings = verify_extraction.mirror_edge_findings(root)
        self.assertEqual(
            sorted((f["rule"], f["path"], f["line"]) for f in findings),
            [("mirror-pulp-kept-include", "mirror/core/view/include/pulp/view/widgets.hpp", 5),
             ("mirror-pulp-kept-include", "mirror/core/view/src/a.cpp", 3)],
        )
        self.assertEqual(
            verify_extraction.MIRROR_EDGE_EXCEPTIONS,
            {("mirror-pulp-kept-include", "core/view/include/pulp/view/widgets.hpp",
              "#include <pulp/view/audio_widgets.hpp>"),
             ("mirror-pulp-kept-include", "core/view/include/vellum/view/widgets.hpp",
              "#include <vellum/view/audio_widgets.hpp>")},
        )

    def test_a_renamed_seam_debt_line_is_exempt_only_through_its_respelled_row(self) -> None:
        debt = (b"#pragma once\n#if defined(VELLUM_VIEW_HAS_AUDIO_WIDGETS)\n"
                b"#include <vellum/view/audio_widgets.hpp>\n#endif\n")
        table = json.loads(verify_extraction.MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
        pulp_only = dict(table, approved_exceptions=[
            row for row in table["approved_exceptions"] if "/vellum/" not in row["path"]])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/include/vellum/view/widgets.hpp", debt)
            self.assertEqual(verify_extraction.mirror_edge_findings(root), [])
            self.assertEqual(verify_extraction.mirror_edge_findings(root, table), [])
            # The pulp-spelled row alone cannot exempt the renamed line.
            self.assertEqual(
                [(f["rule"], f["path"], f["line"]) for f in verify_extraction.mirror_edge_findings(root, pulp_only)],
                [("mirror-pulp-kept-include", "mirror/core/view/include/vellum/view/widgets.hpp", 3)],
            )

    def test_vendored_table_must_match_pulp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pulp = Path(directory)
            target = pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE
            self.assertTrue(verify_extraction.mirror_edge_table_drift(pulp))
            target.parent.mkdir(parents=True)
            target.write_bytes(verify_extraction.MIRROR_EDGE_TABLE.read_bytes())
            self.assertEqual(verify_extraction.mirror_edge_table_drift(pulp), [])
            target.write_bytes(verify_extraction.MIRROR_EDGE_TABLE.read_bytes().replace(b"gpu_audio", b"gpuaudio", 1))
            self.assertTrue(verify_extraction.mirror_edge_table_drift(pulp))

    def test_table_may_advance_past_the_export_commit_only_on_pulp_main(self) -> None:
        def commit(pulp: Path, data: bytes) -> str:
            target = pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            subprocess.run(["git", "add", "-A"], cwd=pulp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "t"], cwd=pulp, check=True)
            return verify_extraction.git(pulp, "rev-parse", "HEAD")

        def checkout(pulp: Path, commit_sha: str) -> None:
            subprocess.run(["git", "checkout", "-q", "--detach", commit_sha], cwd=pulp, check=True)

        current = verify_extraction.MIRROR_EDGE_TABLE.read_bytes()
        older = current.replace(b"register_audio_widgets|", b"", 1)
        weaker = older.replace(b"visualizers\\\\.hpp|", b"", 1)
        self.assertNotEqual(older, current)
        self.assertNotEqual(weaker, older)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            pulp, pin_path = work / "pulp", work / "pin.json"
            pulp.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=pulp, check=True)
            export = commit(pulp, older)
            advanced = commit(pulp, current)
            subprocess.run(["git", "update-ref", "refs/remotes/origin/main", advanced], cwd=pulp, check=True)
            base_pin = json.loads(verify_extraction.MIRROR_EDGE_TABLE_PIN.read_text(encoding="utf-8"))

            def drift(pinned: str, vendored: bytes) -> list[str]:
                table = work / "vendored.json"
                table.write_bytes(vendored)
                pin_path.write_text(json.dumps(dict(base_pin, pulp_commit=pinned,
                                                    git_blob=verify_extraction.git_blob_sha(vendored))),
                                    encoding="utf-8")
                return verify_extraction.mirror_edge_table_drift(pulp, table=table, pin_path=pin_path)

            # The export commit holds the older table; a pin on Pulp main that
            # descends from it and holds the vendored table passes.
            checkout(pulp, export)
            self.assertEqual(drift(advanced, current), [])

            # An unreviewed side-branch commit that descends from the export
            # and holds a weaker table is refused: it is not on Pulp main.
            checkout(pulp, export)
            side = commit(pulp, weaker)
            checkout(pulp, export)
            errors = drift(side, weaker)
            self.assertEqual(errors, [f"vendored.json differs from Pulp's {verify_extraction.PULP_MIRROR_EDGE_TABLE}, "
                                      f"and the pinned commit {side} is not on Pulp main"])

            # Without origin/main nothing proves the pin is on main.
            subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=pulp, check=True)
            errors = drift(advanced, current)
            self.assertTrue(any("has no refs/remotes/origin/main" in error for error in errors), errors)
            subprocess.run(["git", "update-ref", "refs/remotes/origin/main", advanced], cwd=pulp, check=True)

            # A pin on main that does not descend from the export commit fails.
            checkout(pulp, advanced)
            later = commit(pulp, older + b"\n")
            subprocess.run(["git", "update-ref", "refs/remotes/origin/main", later], cwd=pulp, check=True)
            checkout(pulp, later)
            errors = drift(advanced, current)
            self.assertTrue(any("does not descend" in error for error in errors), errors)

            # A pin on main whose commit holds a different table fails.
            checkout(pulp, export)
            pin_path.write_text(json.dumps(dict(base_pin, pulp_commit=later,
                                                git_blob=verify_extraction.git_blob_sha(current))), encoding="utf-8")
            table = work / "vendored.json"
            table.write_bytes(current)
            errors = verify_extraction.mirror_edge_table_drift(pulp, table=table, pin_path=pin_path)
            self.assertTrue(any("does not hold the vendored table" in error for error in errors), errors)

    def test_mirror_is_scanned_under_the_export_commit_table_too(self) -> None:
        table = json.loads(verify_extraction.MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
        # An export table that is stricter than the vendored one: it refuses
        # a header the vendored table admits.
        stricter = json.loads(json.dumps(table))
        stricter["content_patterns"]["mirror-pulp-kept-include"] = r"#\s*include\s*<pulp/view/label\.hpp>"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/src/a.cpp", b"#include <pulp/view/label.hpp>\n")
            self.assertEqual(verify_extraction.mirror_edge_findings(root), [])
            self.assertEqual(
                [(f["rule"], f["line"]) for f in verify_extraction.mirror_edge_findings(root, stricter)],
                [("mirror-pulp-kept-include", 1)],
            )
            # verify() fails the repository when the export commit's table
            # refuses something in today's mirror the vendored table admits.
            pulp = root / "pulp"
            export_table = json.loads(json.dumps(table))
            # Today's units spell vellum once transferred to framework/.
            spelling = "vellum" if (MODULE_PATH.parents[2] / "framework").is_dir() else "pulp"
            export_table["content_patterns"]["mirror-pulp-kept-include"] = rf"#\s*include\s*<{spelling}/"
            (pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE).parent.mkdir(parents=True)
            (pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE).write_text(json.dumps(export_table), encoding="utf-8")
            with mock.patch.object(verify_extraction, "mirror_edge_table_drift", return_value=[]), \
                    mock.patch.object(verify_extraction, "verify_origins_against_pulp", return_value=[]):
                report = verify_extraction.verify(MODULE_PATH.parents[2], pulp)
        self.assertTrue(report["checks"]["mirror_edge_findings_under_export_table"])
        self.assertTrue(any("under the export commit's mirror-edge table" in error for error in report["errors"]))
        self.assertEqual(report["checks"]["mirror_edge_findings"], [])

    def test_unclassified_and_non_utf8_mirror_files_are_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/src/x.weird", b"int x;\n")
            self.write_mirror(root, "mirror/core/view/src/y.cpp", b"\xff\xfe // pulp::audio\n")
            self.write_mirror(root, "mirror/external/fonts/Inter.ttf", b"\x00\x01pulp::audio")
            findings = verify_extraction.mirror_edge_findings(root)
        self.assertEqual(
            sorted((finding["rule"], finding["path"]) for finding in findings),
            [("mirror-product-symbol", "mirror/core/view/src/y.cpp"),
             ("mirror-unclassified-suffix", "mirror/core/view/src/x.weird")],
        )

    def test_vendored_table_is_the_pinned_blob(self) -> None:
        self.assertEqual(verify_extraction.mirror_edge_table_pin_errors(), [])
        pin = json.loads(verify_extraction.MIRROR_EDGE_TABLE_PIN.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            edited = Path(directory) / "table.json"
            edited.write_bytes(verify_extraction.MIRROR_EDGE_TABLE.read_bytes().replace(b"gpu_audio", b"gpuaudio", 1))
            self.assertTrue(verify_extraction.mirror_edge_table_pin_errors(table=edited))
            forged = Path(directory) / "pin.json"
            forged.write_text(json.dumps(dict(pin, pulp_path="tools/other.json")), encoding="utf-8")
            self.assertTrue(verify_extraction.mirror_edge_table_pin_errors(pin_path=forged))

    def test_pinned_blob_is_proved_against_a_pulp_checkout(self) -> None:
        def commit(pulp: Path, data: bytes) -> str:
            target = pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            subprocess.run(["git", "add", "-A"], cwd=pulp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "t"], cwd=pulp, check=True)
            return verify_extraction.git(pulp, "rev-parse", "HEAD")

        table = verify_extraction.MIRROR_EDGE_TABLE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            pulp, pin_path = Path(directory) / "pulp", Path(directory) / "pin.json"
            pulp.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=pulp, check=True)
            good = commit(pulp, table)
            pin = dict(json.loads(verify_extraction.MIRROR_EDGE_TABLE_PIN.read_text(encoding="utf-8")), pulp_commit=good)
            pin_path.write_text(json.dumps(pin), encoding="utf-8")
            self.assertEqual(verify_extraction.mirror_edge_table_pin_errors(pulp, pin_path=pin_path), [])

            # Vendored bytes and pin agree, but Pulp holds a different blob at the pinned commit.
            edited = table.replace(b"gpu_audio", b"gpuaudio", 1)
            vendored = Path(directory) / "vendored.json"
            vendored.write_bytes(edited)
            pin_path.write_text(json.dumps(dict(pin, git_blob=verify_extraction.git_blob_sha(edited))), encoding="utf-8")
            self.assertEqual(
                verify_extraction.mirror_edge_table_pin_errors(pulp, table=vendored, pin_path=pin_path),
                [f"Pulp's {verify_extraction.PULP_MIRROR_EDGE_TABLE} at {good} is blob "
                 f"{verify_extraction.git_blob_sha(table)}, not the pinned {verify_extraction.git_blob_sha(edited)}"],
            )

            # The checkout is not at the pinned commit.
            pin_path.write_text(json.dumps(pin), encoding="utf-8")
            moved = commit(pulp, table + b"\n")
            errors = verify_extraction.mirror_edge_table_pin_errors(pulp, pin_path=pin_path)
            self.assertIn(f"Pulp checkout {pulp} is at {moved}, not the pinned {good}", errors)

    def test_repository_passes_full_retirement_verification(self) -> None:
        root = MODULE_PATH.parents[2]
        # The required provenance lanes check out the receipt's Pulp commit and
        # name it in MIRROR_PULP_ROOT, so there the mirror's Pulp identity is
        # proved. Elsewhere that proof is deferred to those lanes, exactly as
        # `verify_extraction.py --defer-mirror-identity` does; every other check,
        # including the mirror edge scan, runs in full.
        pulp_root_env = os.environ.get("MIRROR_PULP_ROOT")
        pulp_root = Path(pulp_root_env).resolve() if pulp_root_env else None
        report = verify_extraction.verify(
            root, pulp_root, defer_mirror_identity=pulp_root is None
        )
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertFalse(report["raw_seed_present_at_active_tip"])
        self.assertEqual(report["checks"]["retired_path_findings"], [])
        self.assertEqual(report["checks"]["active_historical_blob_matches"], [])
        self.assertEqual(report["checks"]["mirror_origin_findings"], [])
        self.assertEqual(report["checks"]["mirror_edge_findings"], [])
        if pulp_root is not None:
            self.assertTrue(report["checks"]["mirror_origins_verified_against_pulp"])

    def derive_refusal(self, files: dict[str, bytes]) -> str:
        """The refusal derive_framework_transfer gives a base holding exactly these files."""
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as scratch:
            root = Path(temporary)
            for relative, data in files.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(data)
            base = self.git_commit_all(root, "a base")
            with self.assertRaises(ValueError) as caught:
                verify_extraction.derive_framework_transfer(root, base, Path(scratch))
            return str(caught.exception)

    def test_a_transfer_cannot_be_derived_from_a_base_the_rename_cannot_take(self) -> None:
        source = {"mirror/core/a/include/pulp/a/a.hpp": b"#pragma once\nnamespace pulp::a {\nint f();\n}\n"}
        self.assertIn("has no mirror/ to rename", self.derive_refusal({"README.md": b"no mirror here\n"}))
        self.assertIn("the landed rename tool refuses the base's mirror/",
                      self.derive_refusal({"mirror/core/a/src/a.cpp": b"int a;\n"}))
        # A base whose mirror renames but whose newest export receipt is unreadable.
        self.assertIn("the base's newest export receipt is unreadable", self.derive_refusal(source))
        self.assertIn("the base's newest export receipt is unreadable", self.derive_refusal(
            {**source, "provenance/mirror/receipts/index.json": json.dumps({"receipts": []}).encode()}))

    def test_the_ports_history_base_that_cannot_be_found_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("a root commit\n")
            self.git_commit_all(root, "a root commit")
            ports = verify_extraction.load_framework_ports()
            self.assertEqual(verify_extraction.framework_ports_history_errors(root, ports), [])
            # The CI base names no commit and a root commit has no parent to fall back to.
            with mock.patch.dict(os.environ, {"VELLUM_GIT_BASE": "d" * 40}):
                errors = verify_extraction.framework_ports_history_errors(root, ports)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn(f"{verify_extraction.FRAMEWORK_PORTS}: history base is unavailable", errors[0])

    def test_a_ports_ledger_the_history_base_holds_unreadable_is_refused(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / verify_extraction.FRAMEWORK_PORTS
            ledger.parent.mkdir(parents=True)
            ledger.write_text("not json")
            base = self.git_commit_all(root, "a ledger nothing can read")
            self.write_ports(root, [])
            self.git_commit_all(root, "a readable ledger")
            ports = verify_extraction.load_framework_ports()
            with mock.patch.dict(os.environ, {"VELLUM_GIT_BASE": base}):
                errors = verify_extraction.framework_ports_history_errors(root, ports)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn(f"{verify_extraction.FRAMEWORK_PORTS}: unreadable at {base}", errors[0])

    TRANSFERRED = "framework/core/a/src/a.cpp"

    def origin_guard(self, rows_at_head: list[dict], rows_at_base: list[dict], *, event: str = "pull_request",
                     ref: str = "refs/pull/1/merge", through_verify: bool = False) -> list[str]:
        """The origin guard's errors for a fixture whose merge base holds rows_at_base and whose tree holds rows_at_head.

        With through_verify the errors come from verify_framework, the call site CI runs."""
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {self.TRANSFERRED: b"int a;\n"})  # a transferred file
            self.write_ports(root, rows_at_base)
            base = self.git_commit_all(root, "the base")
            self.write_ports(root, rows_at_head)
            env = {"GITHUB_EVENT_NAME": event, "GITHUB_REF": ref, "VELLUM_GIT_BASE": base}
            with mock.patch.dict(os.environ, env):
                if through_verify:
                    errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                    return [e for e in errors if "origin is not `pulp`" in e]
                return verify_extraction.framework_ports_origin_errors(
                    root, verify_extraction.load_framework_ports(), {self.TRANSFERRED})

    def test_a_change_cannot_move_a_pulp_origin_file_to_another_origin(self) -> None:
        landed_pulp = self.ports_row("framework/core/a/src/p.cpp", commits=["a" * 40])
        flipped = dict(landed_pulp, origin="native", pulp_path=None, pulp_commits=[], framework_blob=None)
        # A removal row does not rewrite the file's origin.
        self.assertEqual(self.origin_guard([self.ports_row(self.TRANSFERRED, "removed")], []), [])
        # A landed Pulp row edited to native.
        self.assertEqual(len(self.origin_guard([flipped], [landed_pulp])), 1)
        # A new native row for a path the base's ledger took from Pulp.
        self.assertEqual(self.origin_guard([landed_pulp, dict(flipped, change="removed")], [landed_pulp]), [])

    def test_a_new_removal_of_a_transferred_file_does_not_move_its_origin(self) -> None:
        # Removing the file is a valid ledger operation; it does not rewrite
        # the file's origin and therefore must not trip the origin guard.
        self.assertEqual(self.origin_guard([self.ports_row(self.TRANSFERRED, "removed")], [], through_verify=True), [])

    def test_the_origin_guard_judges_every_event_but_the_push_that_lands_on_main(self) -> None:
        row = self.ports_row(self.TRANSFERRED)
        for event, ref in (("pull_request", "refs/pull/1/merge"), ("merge_group", "refs/heads/gh-readonly-queue/main/pr-1-x"),
                           ("workflow_dispatch", "refs/heads/feature"), ("schedule", "refs/heads/main"),
                           ("push", "refs/tags/v1.0.0"), ("push", "refs/heads/feature")):
            with self.subTest(event=event, ref=ref):
                self.assertEqual(len(self.origin_guard([row], [], event=event, ref=ref)), 1)
        self.assertEqual(self.origin_guard([row], [], event="push", ref="refs/heads/main"), [])

    def test_verify_framework_runs_the_origin_guard(self) -> None:
        # Through the call site CI runs: removing the hook in framework_ports_state fails this test.
        row = self.ports_row(self.TRANSFERRED)
        for event in ("pull_request", "merge_group"):
            with self.subTest(event=event):
                self.assertEqual(len(self.origin_guard([row], [], event=event, through_verify=True)), 1)
        self.assertEqual(self.origin_guard([], [], through_verify=True), [])

    def test_the_origin_guard_leaves_native_rows_and_landed_rows_alone(self) -> None:
        native_new = self.ports_row("framework/core/a/src/n.cpp")  # a file Vellum wrote
        self.assertEqual(self.origin_guard([native_new], []), [])
        # A landed native row for a transferred file stands; only a row the change adds or edits moves a file.
        landed = self.ports_row(self.TRANSFERRED, "removed")
        self.assertEqual(self.origin_guard([landed], [landed]), [])

    def pulp_with_file(self, pulp: Path, pulp_path: str, data: bytes) -> tuple[str, str]:
        """A Pulp checkout whose main holds one file: (commit, the file's blob)."""
        run = lambda *args: subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
        pulp.mkdir(parents=True)
        run("init", "-q", "-b", "main")
        (pulp / pulp_path).parent.mkdir(parents=True)
        (pulp / pulp_path).write_bytes(data)
        run("add", "-A")
        run("commit", "-q", "-m", "the port's source")
        commit = run("rev-parse", "HEAD")
        run("update-ref", "refs/remotes/origin/main", commit)
        return commit, run("rev-parse", f"{commit}:{pulp_path}")

    def test_a_ports_row_s_pulp_proof_refuses_each_way_the_row_can_misstate_pulp(self) -> None:
        pulp_path, ported = "core/a/include/pulp/a/new.hpp", "framework/core/a/include/vellum/a/new.hpp"
        data = b"#include <pulp/a/a.hpp>\nnamespace pulp::a { int first(); }\n"
        renamed = verify_extraction.git_blob_sha(
            b"#include <vellum/a/a.hpp>\nnamespace vellum::a { int first(); }\n")
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary) / "pulp"
            commit, blob = self.pulp_with_file(pulp, pulp_path, data)

            def errors(rename: dict | None = self.RENAME, **row_fields) -> list[str]:
                row = self.ports_row(ported, commits=[commit], pulp_path=pulp_path, blob=renamed)
                row.update(row_fields)
                return verify_extraction.framework_ports_pulp_errors(pulp, "origin/main", [row], rename)

            self.assertEqual(errors(), [])  # control: the row that states Pulp truthfully
            self.assertEqual(len(errors(None)), 1)
            self.assertIn("carries no rename table to put core/a/include/pulp/a/new.hpp through", errors(None)[0])
            wrong_path = errors(framework_path="framework/core/a/include/vellum/a/other.hpp")
            self.assertEqual(len(wrong_path), 1, wrong_path)
            self.assertIn(f"renames to {ported}, not framework/core/a/include/vellum/a/other.hpp", wrong_path[0])
            wrong_blob = errors(framework_blob="b" * 40)
            self.assertEqual(len(wrong_blob), 1, wrong_blob)
            self.assertIn(f"framework_blob {'b' * 12} is not Pulp {commit[:12]}:{pulp_path} put through the "
                          f"transfer's rename ({renamed[:12]})", wrong_blob[0])
            # A file listed at the commit whose object the checkout lost cannot be read.
            loose = pulp / ".git/objects" / blob[:2] / blob[2:]
            self.assertTrue(loose.is_file())
            loose.chmod(0o644)
            loose.unlink()
            unreadable = errors()
            self.assertEqual(len(unreadable), 1, unreadable)
            self.assertIn(f"cannot read Pulp {commit[:12]}:{pulp_path}", unreadable[0])

    def test_the_ports_proof_of_a_native_row_needs_a_pulp_checkout_that_can_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, not_a_checkout = Path(temporary) / "vellum", Path(temporary) / "empty"
            not_a_checkout.mkdir()
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            (root / self.PORTED).write_bytes(b"int written_here;\n")
            added = {self.PORTED: self.ports_row(self.PORTED)}
            self.assertEqual(verify_extraction.framework_ports_native_errors(root, added, None), [])
            errors = verify_extraction.framework_ports_native_errors(root, added, not_a_checkout)
            self.assertEqual(errors, [f"{verify_extraction.FRAMEWORK_PORTS}: Pulp checkout {not_a_checkout} could "
                                      "not be asked for the native rows' blobs"])

    def test_a_ports_row_s_file_must_be_the_blob_it_records(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.framework_fixture(root, {"framework/core/a/src/a.cpp": b"int a;\n"})
            (root / self.PORTED).write_bytes(b"int n;\n")
            recorded = verify_extraction.git_blob_sha(b"int n;\n")
            row = self.ports_row(self.PORTED, commits=["9" * 40], blob=recorded)
            self.write_ports(root, [row])
            self.assertEqual(self.ports_verdict(root)[0], [])  # control: the file is the recorded blob
            (root / self.PORTED).write_bytes(b"int different;\n")
            wrong = verify_extraction.git_blob_sha(b"int different;\n")
            expected = (f"{verify_extraction.FRAMEWORK_PORTS} rows[0]: {self.PORTED} is blob {wrong[:12]}, not the "
                        f"recorded {recorded[:12]}")
            errors, _ = self.ports_verdict(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertTrue(errors[0].startswith(expected), errors)
            # The same refusal from the function that judges it, for a row this change appends.
            self.assertEqual(len(verify_extraction.framework_ports_joining_errors(root, [row], {self.PORTED: row}, 0)), 1)
            # A row the base already holds is ordinary framework source: its bytes may change.
            self.assertEqual(verify_extraction.framework_ports_joining_errors(root, [row], {self.PORTED: row}, 1), [])

    def test_the_transfer_state_refuses_a_checkout_that_is_not_consistent(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_framework_debt(root, {})
            _, errors = verify_extraction.framework_transfer_state(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("exist without framework/: the transfer's data lands only with the transferred tree", errors[0])
            self.assertIn(verify_extraction.FRAMEWORK_NAME_DEBT, errors[0])
            (root / "framework").mkdir()
            _, errors = verify_extraction.framework_transfer_state(root)
            self.assertIn(f"framework/ exists without {verify_extraction.FRAMEWORK_TRANSFER_RECORD}", errors)

    def test_the_transfer_state_refuses_a_history_base_that_is_missing_or_that_lost_framework(self) -> None:
        self.hide_ci_git_base()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "framework/core/a").mkdir(parents=True)
            (root / "framework/core/a/a.cpp").write_text("int a;\n")
            base = self.git_commit_all(root, "framework lands")
            subprocess.run(["git", "-C", str(root), "rm", "-q", "-r", "framework"], check=True)
            self.git_commit_all(root, "framework is removed")
            with mock.patch.dict(os.environ, {"VELLUM_GIT_BASE": base}):
                _, errors = verify_extraction.framework_transfer_state(root)
            self.assertEqual(errors, ["framework/ was transferred at the base and is gone at HEAD; it cannot be removed"])
            # A CI base that names no commit, on a root commit, leaves nothing to judge against.
            solo = Path(temporary) / "solo"
            solo.mkdir()
            (solo / "README.md").write_text("root\n")
            self.git_commit_all(solo, "root")
            with mock.patch.dict(os.environ, {"VELLUM_GIT_BASE": "d" * 40}):
                _, errors = verify_extraction.framework_transfer_state(solo)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("framework transfer: history base is unavailable", errors[0])

    def test_an_introduction_that_cannot_be_listed_against_its_base_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("root\n")
            self.git_commit_all(root, "root")
            self.assertEqual(verify_extraction.framework_introduction_errors(root, "e" * 40, {}),
                             ["cannot list the change against " + "e" * 40])


class RendererPinTests(unittest.TestCase):
    """A mirror export's Pulp commit must pin the renderer Vellum's lock names."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "vellum"
        (self.root / "provenance/mirror/receipts").mkdir(parents=True)
        lock = json.loads((MODULE_PATH.parents[2] / "provenance/third-party-lock.json").read_text())
        (self.root / "provenance/third-party-lock.json").write_text(json.dumps(lock))
        (self.root / "gpu-runtime").mkdir()
        (self.root / "gpu-runtime/renderer-lock.json").write_bytes(
            (MODULE_PATH.parents[2] / "gpu-runtime/renderer-lock.json").read_bytes())
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        artifact, identities = renderer["artifact"], renderer["source_identities"]
        self.matching_pin = {
            "skia_branch": identities["skia"]["ref"].removeprefix("refs/heads/"),
            "skia_commit": identities["skia"]["commit"],
            "built_dawn": identities["dawn"]["commit"],
            "skia_builder_ref": identities["builder"]["commit"],
            "release_assets": {"mac-universal": {"url": artifact["url"], "sha256": artifact["sha256"]}},
        }
        self.pulp = base / "pulp"
        self.pulp.mkdir()
        subprocess.run(["git", "init", "-q", str(self.pulp)], check=True)

    def pulp_commit(self, pin: dict) -> str:
        manifest = self.pulp / "tools/deps/manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"dependencies": [
            {"name": "Dawn"}, {"name": "Skia", "determinism": pin}]}))
        git = ["git", "-C", str(self.pulp), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "pin"], check=True)
        return subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()

    def receipt(self, pulp_sha: str) -> str:
        path = "provenance/mirror/receipts/r.json"
        (self.root / path).write_text(json.dumps({"pulp_sha": pulp_sha}))
        return path

    def test_receipt_from_a_commit_pinning_the_locked_renderer_passes(self) -> None:
        receipt = self.receipt(self.pulp_commit(self.matching_pin))
        errors, rows = verify_extraction.renderer_pin_errors(self.root, self.pulp, [receipt])
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]["matches_lock"])

    def test_reexported_receipt_with_recorded_builder_change_passes(self) -> None:
        pin = json.loads(json.dumps(self.matching_pin))
        pin["skia_builder_ref"] = "312f05e24a5d5a45d4a3e70ca70ff3ccff1b4d97"
        receipt = self.receipt(self.pulp_commit(pin))
        errors, rows = verify_extraction.renderer_pin_errors(self.root, self.pulp, [receipt])
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]["matches_lock"])

    def test_an_unrecorded_builder_change_is_refused(self) -> None:
        pin = json.loads(json.dumps(self.matching_pin))
        pin["skia_builder_ref"] = "9" * 40
        receipt = self.receipt(self.pulp_commit(pin))
        errors, rows = verify_extraction.renderer_pin_errors(self.root, self.pulp, [receipt])
        self.assertFalse(rows[0]["matches_lock"])
        self.assertIn("skia_builder_ref", errors[0])

    def test_export_against_another_milestone_fails(self) -> None:
        # The case this check exists for: an export from a Pulp commit that
        # pins another milestone than the lock (for example m157 against m153).
        pin = json.loads(json.dumps(self.matching_pin))
        pin["skia_branch"] = "chrome/m999"
        pin["skia_commit"] = "1" * 40
        pin["built_dawn"] = "2" * 40
        pin["release_assets"]["mac-universal"] = {
            "url": "https://github.com/danielraffel/skia-builder/releases/download/chrome/m999/"
                   "skia-build-mac-universal-gpu-release.zip",
            "sha256": "3" * 64}
        receipt = self.receipt(self.pulp_commit(pin))
        errors, rows = verify_extraction.renderer_pin_errors(self.root, self.pulp, [receipt])
        self.assertFalse(rows[0]["matches_lock"])
        self.assertEqual(len(errors), 1)
        for key in ("skia_branch", "skia_commit", "built_dawn", "archive_url", "archive_sha256"):
            self.assertIn(key, errors[0])

    def test_same_milestone_different_archive_bytes_fails(self) -> None:
        pin = json.loads(json.dumps(self.matching_pin))
        pin["release_assets"]["mac-universal"]["sha256"] = "0" * 64
        receipt = self.receipt(self.pulp_commit(pin))
        errors, _ = verify_extraction.renderer_pin_errors(self.root, self.pulp, [receipt])
        self.assertEqual(len(errors), 1)
        self.assertIn("archive_sha256", errors[0])

    def test_unreadable_pulp_commit_fails_closed(self) -> None:
        self.pulp_commit(self.matching_pin)
        receipt = self.receipt("f" * 40)
        errors, rows = verify_extraction.renderer_pin_errors(self.root, self.pulp, [receipt])
        self.assertEqual(rows, [])
        self.assertIn("cannot read Pulp's tools/deps/manifest.json", errors[0])

    def commit_mirror(self, content: bytes) -> str:
        """Commit a mirror/ tree in the Vellum root; return its tree id."""
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t"]
        if not (self.root / ".git").exists():
            subprocess.run(git + ["init", "-q"], check=True)
        (self.root / "mirror").mkdir(exist_ok=True)
        (self.root / "mirror/a.cpp").write_bytes(content)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "mirror"], check=True)
        return subprocess.run(git + ["rev-parse", "HEAD:mirror"], check=True, capture_output=True,
                              text=True).stdout.strip()

    def landed(self, name: str, pulp_sha: str, mirror_tree: str) -> str:
        path = f"provenance/mirror/receipts/{name}.json"
        (self.root / path).write_text(json.dumps({"pulp_sha": pulp_sha, "mirror_tree": mirror_tree}))
        return path

    def other_milestone(self) -> dict:
        pin = json.loads(json.dumps(self.matching_pin))
        pin["skia_branch"] = "chrome/m999"
        pin["release_assets"]["mac-universal"]["sha256"] = "3" * 64
        return pin

    def test_older_receipt_with_another_renderer_is_not_judged(self) -> None:
        old_tree = self.commit_mirror(b"old\n")
        older = self.landed("older", self.pulp_commit(self.other_milestone()), old_tree)
        new_tree = self.commit_mirror(b"new\n")
        newest = self.landed("newest", self.pulp_commit(self.matching_pin), new_tree)
        errors, rows = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [older, newest])
        self.assertEqual(errors, [])
        self.assertEqual({row["receipt"]: row["matches_lock"] for row in rows}, {newest: True, older: None})

    def test_current_receipt_must_match_the_lock(self) -> None:
        old_tree = self.commit_mirror(b"old\n")
        older = self.landed("older", self.pulp_commit(self.matching_pin), old_tree)
        new_tree = self.commit_mirror(b"new\n")
        newest = self.landed("newest", self.pulp_commit(self.other_milestone()), new_tree)
        errors, rows = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [older, newest])
        self.assertEqual(len(errors), 1)
        self.assertIn("provenance/mirror/receipts/newest.json", errors[0])
        self.assertIn("skia_branch", errors[0])

    def test_two_receipts_naming_head_mirror_fail_closed(self) -> None:
        tree = self.commit_mirror(b"current\n")
        first = self.landed("first", self.pulp_commit(self.matching_pin), tree)
        second = self.landed("second", self.pulp_commit(self.other_milestone()), tree)
        errors, rows = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [first, second])
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("2 landed receipts name HEAD:mirror", errors[0])

    def test_no_receipt_naming_head_mirror_fails_closed(self) -> None:
        self.commit_mirror(b"current\n")
        stale = self.landed("stale", self.pulp_commit(self.matching_pin), "0" * 40)
        errors, rows = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [stale])
        self.assertEqual(len(errors), 1)
        self.assertIn("0 landed receipts name HEAD:mirror", errors[0])

    def test_transferred_receipt_is_current_after_mirror_is_gone(self) -> None:
        old_tree = self.commit_mirror(b"old\n")
        older = self.landed("older", self.pulp_commit(self.other_milestone()), old_tree)
        new_tree = self.commit_mirror(b"new\n")
        newest = self.landed("newest", self.pulp_commit(self.matching_pin), new_tree)
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["rm", "-q", "-r", "mirror"], check=True)
        (self.root / "framework").mkdir()
        (self.root / "framework/a.cpp").write_bytes(b"new\n")
        subprocess.run(git + ["add", "framework"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "transfer"], check=True)
        # No transfer record: nothing names the current receipt.
        errors, _ = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [older, newest])
        self.assertIn("HEAD has no mirror/ tree", errors[0])
        record = self.root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"export_receipt": newest, "mirror_tree": new_tree}), encoding="utf-8")
        errors, rows = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [older, newest])
        self.assertEqual(errors, [])
        self.assertEqual({row["receipt"]: row["matches_lock"] for row in rows}, {newest: True, older: None})
        # The record must name a landed receipt.
        record.write_text(json.dumps({"export_receipt": "provenance/mirror/receipts/x.json",
                                      "mirror_tree": new_tree}), encoding="utf-8")
        errors, _ = verify_extraction.mirror_renderer_pin_check(self.root, self.pulp, [older, newest])
        self.assertIn("which is not a landed receipt", errors[0])


if __name__ == "__main__":
    unittest.main()
