#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from .git_test_environment import disable_auto_maintenance
except ImportError:  # Direct script execution.
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

    def test_renamed_mirror_exemptions_are_pinned(self) -> None:
        # Any change to what the rule skips must show up as an edit here.
        self.assertEqual(verify_extraction.RENAMED_MIRROR_EXEMPT_PREFIXES, ())
        self.assertEqual(verify_extraction.RENAMED_MIRROR_EXEMPT_PATHS, {
            "tools/mirror/consumer/cross_image_main.cpp",
            "tools/mirror/consumer/direct_gpu_main.cpp",
            "tools/mirror/consumer/direct_main.cpp",
            "tools/mirror/consumer/main.cpp",
            "tools/mirror/consumer/runtime_image_census_main.cpp",
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
            # This fixture's record is not the pinned one.
            self.assertTrue(any("differ from the pinned transfer record" in e for e in errors), errors)
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
            pinned = hashlib.sha256(record.read_bytes()).hexdigest()
            base = self.git_commit_all(root, "transfer")
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "change"], check=True)
            os.environ["VELLUM_GIT_BASE"] = base
            with mock.patch.object(verify_extraction, "FRAMEWORK_TRANSFER_SHA256", pinned):
                errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                # Control: unchanged and pinned, and external/ holds only recorded files.
                self.assertFalse(any("immutable" in e or "external" in e for e in errors), errors)
                self.assertEqual(checks["framework_external_unrecorded"], [])
                (root / "framework/external/x/first_party.cpp").write_bytes(b"int y;\n")
                errors, checks = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                self.assertEqual(checks["framework_external_unrecorded"], ["framework/external/x/first_party.cpp"])
                (root / "framework/external/x/first_party.cpp").unlink()
                # Repointing the record changes its bytes: refused against the
                # pin and against the merge base.
                record.write_text(json.dumps(dict(body, export_receipt="provenance/mirror/receipts/older.json")),
                                  encoding="utf-8")
                errors, _ = verify_extraction.verify_framework(root, None, "origin/main", defer_identity=True)
                self.assertTrue(any("differ from the pinned transfer record" in e for e in errors), errors)
                self.assertTrue(any("changed from the merge base" in e for e in errors), errors)

    def framework_fixture(self, root: Path, files: dict[str, bytes]) -> None:
        for relative, data in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(data)
        self.write_framework_debt(root, {})
        record = root / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"schema": verify_extraction.FRAMEWORK_TRANSFER_SCHEMA, "framework_tree": "f" * 40,
                                      "records": [{"framework_path": p} for p in files if p.startswith("framework/")]}),
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
            smuggled = self.EXTERNAL_BYTES + b"namespace vellum::runtime { class SpscQueue { int x; }; }\n"
            (root / self.EXTERNAL).write_bytes(smuggled)
            errors, checks = verify_extraction.framework_external_errors(root, recorded)
            self.assertEqual(checks["framework_external_edited"], [self.EXTERNAL])
            self.assertIn("only through the dependency process", errors[0])
            # A matching entry makes the same bytes a reviewed update.
            self.write_updates(root, [self.update(self.EXTERNAL, self.EXTERNAL_BYTES, smuggled)])
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])
            # An entry whose to_blob is not the file is refused.
            self.write_updates(root, [self.update(self.EXTERNAL, self.EXTERNAL_BYTES, b"other\n")])
            self.assertIn("differ from their pinned blobs", verify_extraction.framework_external_errors(root, recorded)[0][0])
            # A broken chain is refused.
            self.write_updates(root, [self.update(self.EXTERNAL, b"not the recorded bytes\n", smuggled)])
            self.assertIn("breaks the chain", verify_extraction.framework_external_errors(root, recorded)[0][0])
            # A chain of two updates resolves to the last to_blob.
            later = smuggled + b"// 1.2.5\n"
            (root / self.EXTERNAL).write_bytes(later)
            self.write_updates(root, [self.update(self.EXTERNAL, self.EXTERNAL_BYTES, smuggled),
                                      self.update(self.EXTERNAL, smuggled, later)])
            self.assertEqual(verify_extraction.framework_external_errors(root, recorded)[0], [])
            # An entry for a path the transfer record does not list is refused.
            self.write_updates(root, [self.update("framework/external/lib/new.h", b"a", b"b")])
            self.assertIn("is not a framework/external/ file the transfer record lists",
                          verify_extraction.framework_external_errors(root, recorded)[0][0])
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

    def test_landed_transfer_record_matches_its_pin(self) -> None:
        record = ROOT / verify_extraction.FRAMEWORK_TRANSFER_RECORD
        if not record.is_file():
            self.skipTest("this checkout records no framework transfer")
        self.assertEqual(hashlib.sha256(record.read_bytes()).hexdigest(), verify_extraction.FRAMEWORK_TRANSFER_SHA256)

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
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        artifact, identities = renderer["artifact"], renderer["source_identities"]
        self.matching_pin = {
            "skia_branch": artifact["release_tag"],
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
