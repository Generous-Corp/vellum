"""Tests for rename_mirror_namespace.py on a small fixture mirror tree."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import rename_mirror_namespace as rename  # noqa: E402
from verify_mirror_rename_identity import verify as identity_verify  # noqa: E402

FIXTURE = {
    "core/runtime/CMakeLists.txt": (
        "add_library(pulp-runtime src/log.cpp)\n"
        "target_include_directories(pulp-runtime PUBLIC include)\n"
        "configure_file(build_info.hpp.in ${OUT}/generated/pulp/runtime/build_info.hpp)\n"
        "install(FILES include/pulp/runtime/log.hpp DESTINATION include/pulp/runtime)\n"
        "pulp_add_binary_data(fonts)\n"
    ),
    "core/runtime/include/pulp/runtime/log.hpp": (
        "#pragma once\n"
        "#include <string>\n"
        "#define PULP_LOG_LEVEL 2\n"
        "namespace pulp::runtime {\n"
        "void log(const std::string& s);\n"
        "}  // namespace pulp::runtime\n"
    ),
    "core/runtime/src/log.cpp": (
        "#include \"pulp/runtime/log.hpp\"\n"
        "namespace pulp {\nnamespace runtime {\n"
        "void log(const std::string& s) { (void)s; }\n"
        "}\n}\n"
        "static const char* kSpelling = \"pulp::runtime::log\";\n"
        "void f() { ::pulp::runtime::log(\"pulpy pulp:: x\"); }\n"
    ),
    "core/view/include/pulp/view/view.hpp": (
        "#pragma once\n#include <pulp/runtime/log.hpp>\n"
        "namespace pulp::view { struct View {}; }\n"
    ),
    "core/view/src/mac_host.mm": (
        "#include <pulp/view/view.hpp>\n"
        "@interface PulpView : NSView\n@end\n"
        "@implementation PulpView\n@end\n"
        "@interface PulpWindowDelegate : NSObject\n@end\n"
        "static Class c() { return NSClassFromString(@\"PulpView\"); }\n"
        "// PulpViewJsEngines is not a class and keeps its name.\n"
        "// PulpTheme is not an Objective-C class either.\n"
    ),
    "core/view/cmake/Preludes.cmake": (
        "file(WRITE out.hpp \"#pragma once\\nnamespace pulp::view::preludes {\\n}\\n\")\n"
    ),
    "external/miniz/miniz.c": "/* third party, no pulp names */\nint miniz(void) { return 0; }\n",
    "external/fonts/Font.ttf": None,  # binary
}


SUFFIXES = ["", ".c", ".cmake", ".cpp", ".h", ".hpp", ".in", ".mm", ".txt"]


def write_table(path: Path, namespace: list[str], include: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "roots": {"namespace": namespace, "include": include or namespace, "target": namespace},
        "suffixes": SUFFIXES,
        "binary_suffixes": [".ttf"],
    }), encoding="utf-8")


def build(root: Path, extra: dict[str, str | None] | None = None) -> None:
    for relative, text in {**FIXTURE, **(extra or {})}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            path.write_bytes(b"\x00\x01binary font bytes \x00")
        else:
            path.write_text(text, encoding="utf-8")


NATIVE = {
    "runtime/include/vellum/runtime/kernel.hpp": (
        "#pragma once\n#define VELLUM_KERNEL_VERSION 1\n"
        "namespace vellum::runtime {\nenum class KernelState { idle };\nclass Kernel final {};\n"
        "void log(int level);\n}\n"
    ),
}


def build_native(repo: Path, extra: dict[str, str] | None = None) -> None:
    for relative, text in {**NATIVE, **(extra or {})}.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


EMPTY_EXCLUSIONS = Path(tempfile.mkdtemp()) / "none.json"
EMPTY_EXCLUSIONS.write_text("[]")


def run(*args: str, to: str = "vellum") -> tuple[int, dict]:
    """The tool on a fixture, with no literal exclusions unless the test names some."""
    out = io.StringIO()
    if "--literal-exclusions" not in args:
        args = (*args, "--literal-exclusions", str(EMPTY_EXCLUSIONS))
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = rename.main([*args, "--to", to])
    return code, json.loads(out.getvalue()) if out.getvalue() else {}


class RenameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "mirror"
        build(self.root)
        # The roots step already landed: the table lists both spellings.
        self.table = self.tmp / "tools/provenance/mirror_edge_patterns.json"
        write_table(self.table, ["pulp", "vellum"])
        build_native(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def test_dry_run_reports_and_touches_nothing(self) -> None:
        before = tree_digest(self.root)
        code, report = run(str(self.root))
        self.assertEqual(code, 0, report)
        self.assertEqual(tree_digest(self.root), before)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["before"]["namespace pulp"], 5)
        self.assertEqual(report["after"]["namespace vellum"], 5)
        self.assertEqual(report["after"]["pulp::"], 0)
        self.assertEqual(report["include_units"], ["runtime", "view"])
        self.assertEqual(report["objc_classes"], ["PulpView", "PulpWindowDelegate"])
        self.assertEqual(report["paths_moved"], 2)
        self.assertEqual(report["from"], "pulp")
        self.assertTrue(report["roots_step"]["ready"])

    def test_apply_rewrites_names_and_moves_include_dirs(self) -> None:
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 0, report)
        r = self.root
        self.assertFalse((r / "core/runtime/include/pulp").exists())
        log_h = (r / "core/runtime/include/vellum/runtime/log.hpp").read_text()
        self.assertIn("namespace vellum::runtime {", log_h)
        self.assertIn("// namespace vellum::runtime", log_h)
        self.assertIn("#define PULP_LOG_LEVEL 2", log_h)  # macros are kept
        log_cpp = (r / "core/runtime/src/log.cpp").read_text()
        self.assertIn('#include "vellum/runtime/log.hpp"', log_cpp)
        self.assertIn("namespace vellum {\nnamespace runtime {", log_cpp)
        self.assertIn('"vellum::runtime::log"', log_cpp)  # string literals follow the code
        self.assertIn("::vellum::runtime::log", log_cpp)
        self.assertIn('"pulpy vellum:: x"', log_cpp)  # `pulpy` is not `pulp`
        cmake = (r / "core/runtime/CMakeLists.txt").read_text()
        self.assertIn("add_library(pulp-runtime", cmake)  # targets are kept
        self.assertIn("pulp_add_binary_data(fonts)", cmake)
        self.assertIn("generated/vellum/runtime/build_info.hpp", cmake)
        self.assertIn("install(FILES include/vellum/runtime/log.hpp DESTINATION include/vellum/runtime)", cmake)
        mm = (r / "core/view/src/mac_host.mm").read_text()
        self.assertIn("@interface VellumMirrorView : NSView", mm)
        self.assertIn("@implementation VellumMirrorView", mm)
        self.assertIn("@interface VellumMirrorWindowDelegate", mm)
        self.assertIn('NSClassFromString(@"VellumMirrorView")', mm)
        self.assertIn("PulpViewJsEngines", mm)  # whole-word only
        self.assertIn("PulpTheme", mm)  # not a declared class
        self.assertIn("\\nnamespace vellum::view::preludes {", (r / "core/view/cmake/Preludes.cmake").read_text())
        # Third-party and binary files are untouched.
        self.assertEqual((r / "external/fonts/Font.ttf").read_bytes(), b"\x00\x01binary font bytes \x00")
        self.assertEqual(report["before"]["macro PULP_*"], report["after"]["macro PULP_*"])

    def test_apply_waits_for_the_roots_step(self) -> None:
        write_table(self.table, ["pulp"])
        before = tree_digest(self.root)
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "roots-not-listed")
        self.assertEqual(report["roots_step"]["required"]["namespace"], ["pulp", "vellum"])
        self.assertEqual(report["roots_step"]["required"]["include"], ["pulp", "vellum"])
        self.assertEqual(report["roots_step"]["required"]["target"], ["pulp", "vellum"])
        self.assertEqual(tree_digest(self.root), before)
        # A dry run still reports, and a scratch copy may override the gate.
        self.assertEqual(run(str(self.root))[0], 0)
        self.assertEqual(run(str(self.root), "--apply", "--allow-unlisted-roots")[0], 0)

    def test_spellings_come_from_the_table(self) -> None:
        write_table(self.table, ["acme"])
        other = self.tmp / "acme-mirror"
        other.mkdir()
        (other / "core/x/include/acme/x").mkdir(parents=True)
        (other / "core/x/include/acme/x/a.hpp").write_text(
            "#include <acme/x/b.hpp>\nnamespace acme::x { int pulp; }\n@interface AcmeView\n@end\n")
        code, report = run(str(other), "--edge-table", str(self.table), "--apply",
                           "--allow-unlisted-roots", "--no-collision-census", to="zed")
        self.assertEqual(code, 0, report)
        text = (other / "core/x/include/zed/x/a.hpp").read_text()
        self.assertIn("#include <zed/x/b.hpp>", text)
        self.assertIn("namespace zed::x { int pulp; }", text)
        self.assertIn("@interface ZedMirrorView", text)

    def test_unclassified_suffix_fails(self) -> None:
        build(self.root, {"core/view/notes.rst": "pulp::view\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(report["unclassified_suffix_files"], ["core/view/notes.rst"])

    def test_missing_table_is_a_usage_error(self) -> None:
        code, _ = run(str(self.root), "--edge-table", str(self.tmp / "absent.json"))
        self.assertEqual(code, 2)

    def test_collision_census_is_clean_and_sighted(self) -> None:
        code, report = run(str(self.root))
        self.assertEqual(code, 0, report)
        c = report["collisions"]
        self.assertEqual(c["status"], "checked")
        self.assertGreater(c["declarations"]["mirror"], 0)
        self.assertGreater(c["declarations"]["native"], 0)
        self.assertEqual(c["shared_namespaces"], ["vellum::runtime"])
        self.assertEqual(c["name_collisions"], [])
        # `log(int)` and `log(const std::string&)` share a name, not a signature.
        self.assertEqual([o["name"] for o in c["shared_overload_sets"]], ["vellum::runtime::log"])

    def test_collision_census_catches_planted_duplicates(self) -> None:
        build(self.root, {"core/runtime/include/pulp/runtime/guarded.h":
                          "#ifndef SHARED_GUARD_H\n#define SHARED_GUARD_H\nnamespace pulp::runtime { int g(); }\n#endif\n"})
        build_native(self.tmp, {
            "runtime/include/vellum/runtime/planted.hpp": (
                "#pragma once\n#include <string>\n#define PULP_LOG_LEVEL 3\n"
                "namespace vellum {\nnamespace view { struct View { int x; }; }\n"
                "namespace runtime {\n"
                "// parameter name and default argument differ; the signature does not\n"
                "void log(const std::string &value = \"x\");\n"
                "}\n}\n"
            ),
            "runtime/include/vellum/runtime/other_guard.h": "#ifndef SHARED_GUARD_H\n#define SHARED_GUARD_H\n#endif\n",
            "runtime/include/vellum/runtime/log.hpp": "#pragma once\n",
        })
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        c = report["collisions"]
        kinds = {x["name"]: x["kind"] for x in c["name_collisions"]}
        self.assertEqual(kinds["vellum::runtime::log"], "same-signature")
        self.assertEqual(kinds["vellum::view::View"], "type vs type")
        self.assertEqual([m["name"] for m in c["macro_collisions"]], ["PULP_LOG_LEVEL"])
        self.assertEqual([g["name"] for g in c["include_guard_collisions"]], ["SHARED_GUARD_H"])
        self.assertEqual([h["include"] for h in c["header_path_collisions"]], ["vellum/runtime/log.hpp"])
        self.assertTrue(any("name collisions" in e for e in report["errors"]), report["errors"])

    def test_each_collision_class_alone_fails(self) -> None:
        cases = {
            "macro collisions": {"runtime/include/vellum/runtime/m.hpp": "#pragma once\n#define PULP_LOG_LEVEL 3\n"},
            "header path collisions": {"runtime/include/vellum/runtime/log.hpp": "#pragma once\n"},
            "name collisions": {"runtime/include/vellum/runtime/t.hpp":
                                "#pragma once\nnamespace vellum::view { struct View; }\n"},
        }
        for expected, files in cases.items():
            with self.subTest(expected):
                shutil.rmtree(self.tmp / "runtime")
                build_native(self.tmp, files)
                code, report = run(str(self.root))
                self.assertEqual(code, 1, report.get("errors"))
                self.assertEqual([e for e in report["errors"] if "collisions" in e], [f"1 {expected}"])

    def test_return_type_does_not_separate_signatures(self) -> None:
        build_native(self.tmp, {"runtime/include/vellum/runtime/r.hpp":
                                "#pragma once\nnamespace vellum::runtime { int log(const std::string&); }\n"})
        _, report = run(str(self.root))
        kinds = {x["name"]: x["kind"] for x in report["collisions"]["name_collisions"]}
        self.assertEqual(kinds.get("vellum::runtime::log"), "same-signature")

    def test_blind_native_census_fails(self) -> None:
        (self.tmp / "runtime/include/vellum/runtime/kernel.hpp").write_text("#pragma once\n")
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertTrue(any("blind" in e for e in report["errors"]), report["errors"])

    def test_apply_refuses_before_writing_when_the_dry_run_fails(self) -> None:
        build_native(self.tmp, {"runtime/include/vellum/runtime/log.hpp": "#pragma once\n"})
        before = tree_digest(self.root)
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "refused")
        self.assertEqual(tree_digest(self.root), before)

    def make_pulp(self, roots: list[str]) -> Path:
        import subprocess
        pulp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pulp)
        write_table(pulp / "tools/scripts/vellum_mirror_edge_patterns.json", roots)
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "table"]):
            subprocess.run(["git", "-C", str(pulp), *args], check=True, capture_output=True)
        return pulp

    def vellum_side(self) -> None:
        write_table(self.table, ["pulp"])
        (self.tmp / "tools/provenance/mirror_edge_patterns.pin.json").write_text(
            json.dumps({"schema": "pin", "pulp_commit": "0" * 40, "git_blob": "0" * 40}, indent=2) + "\n")
        checker = self.tmp / "tools/mirror/verify_mirror_inertness.py"
        checker.parent.mkdir(parents=True, exist_ok=True)
        checker.write_text('X = 1\nMIRROR_ROOTS = {"namespace": ("pulp",), "include": ("pulp",)}\nY = 2\n')

    def test_roots_flip_lands_with_the_rename(self) -> None:
        import subprocess
        self.vellum_side()
        pulp = self.make_pulp(["pulp", "vellum"])
        before = tree_digest(self.tmp)
        code, report = run(str(self.root), "--flip-roots-from", str(pulp))
        self.assertEqual(code, 0, report.get("errors"))
        self.assertEqual(tree_digest(self.tmp), before)  # a dry run writes nothing
        self.assertTrue(report["roots_step"]["ready"])
        code, report = run(str(self.root), "--flip-roots-from", str(pulp), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        vendored = self.table.read_bytes()
        self.assertEqual(vendored, (pulp / "tools/scripts/vellum_mirror_edge_patterns.json").read_bytes())
        pin = json.loads((self.tmp / "tools/provenance/mirror_edge_patterns.pin.json").read_text())
        head = subprocess.run(["git", "-C", str(pulp), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        blob = subprocess.run(["git", "-C", str(pulp), "rev-parse", "HEAD:tools/scripts/vellum_mirror_edge_patterns.json"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual((pin["pulp_commit"], pin["git_blob"], pin["schema"]), (head, blob, "pin"))
        checker = (self.tmp / "tools/mirror/verify_mirror_inertness.py").read_text()
        self.assertIn('MIRROR_ROOTS = {"namespace": ("pulp", "vellum"), "include": ("pulp", "vellum")}\n', checker)
        self.assertTrue(checker.startswith("X = 1\n") and checker.endswith("Y = 2\n"))
        self.assertTrue((self.root / "core/runtime/include/vellum/runtime/log.hpp").exists())

    def test_roots_flip_waits_for_the_pulp_roots_pr(self) -> None:
        self.vellum_side()
        pulp = self.make_pulp(["pulp"])
        before = tree_digest(self.tmp)
        # Even a dry run fails: the change it describes cannot land yet.
        code, report = run(str(self.root), "--flip-roots-from", str(pulp))
        self.assertEqual((code, report["status"]), (1, "roots-not-listed"))
        code, report = run(str(self.root), "--flip-roots-from", str(pulp), "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "roots-not-listed")
        self.assertEqual(tree_digest(self.tmp), before)

    def test_boundary_free_postcondition_catches_what_the_rules_skip(self) -> None:
        # `mypulp::` is not renamed (the rules have a left boundary), so the
        # boundary-free count must refuse it rather than pass it silently.
        build(self.root, {"core/runtime/src/odd.cpp": "// mypulp::other\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertTrue(any("'pulp::' substrings remain" in e for e in report["errors"]), report["errors"])
        self.assertIn("// mypulp::other", (self.root / "core/runtime/src/odd.cpp").read_text())

    def test_binary_files_are_probed(self) -> None:
        build(self.root, {"external/fonts/Font.ttf": None})
        (self.root / "external/fonts/Font.ttf").write_bytes(b"\x00 pulp:: \x00")
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(report["bytes_after"]["substring pulp::"], 1)

    def test_escaped_newline_qualified_name_is_renamed(self) -> None:
        build(self.root, {"core/runtime/src/gen.cpp": 'const char* k = "x\\npulp::runtime::log();";\n'})
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        self.assertIn("\\nvellum::runtime::log", (self.root / "core/runtime/src/gen.cpp").read_text())

    def test_literal_exclusions_and_length_coupled_literals(self) -> None:
        seed = 'seed(ctx, "pulp::license::v2", 17);\n'
        build(self.root, {"core/runtime/src/license.cpp": seed})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        coupled = report["length_coupled_literals"]
        self.assertEqual([(c["path"], c["before"]) for c in coupled],
                         [("core/runtime/src/license.cpp", '"pulp::license::v2"')])
        listed = {(c["path"], c["before"]) for c in report["literal_changes"]}
        self.assertIn(("core/runtime/src/log.cpp", '"pulp::runtime::log"'), listed)
        exclusions = self.tmp / "exclusions.json"
        exclusions.write_text(json.dumps([{"path": "core/runtime/src/license.cpp", "literal": '"pulp::license::v2"',
                                           "reason": "length-coupled DRBG personalization; fix upstream"}]))
        code, report = run(str(self.root), "--literal-exclusions", str(exclusions), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        self.assertEqual((self.root / "core/runtime/src/license.cpp").read_text(), seed)
        self.assertEqual(report["excluded_literal_old_references"], 1)
        self.assertEqual(report["receipt"]["literal_exclusions"],
                         [{"path": "core/runtime/src/license.cpp", "literal": '"pulp::license::v2"'}])

    def test_stale_literal_exclusion_fails(self) -> None:
        exclusions = self.tmp / "exclusions.json"
        exclusions.write_text(json.dumps([{"path": "core/x.cpp", "literal": '"pulp::gone"', "reason": "r"}]))
        code, report = run(str(self.root), "--literal-exclusions", str(exclusions))
        self.assertEqual(code, 1)
        self.assertTrue(any("matches nothing" in e for e in report["errors"]))

    def test_input_with_target_spellings_is_refused(self) -> None:
        build(self.root, {"core/runtime/src/early.cpp": "int vellum::runtime::x;\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertTrue(any("not a raw export" in e for e in report["errors"]), report["errors"])

    def test_native_refusals_and_blob_quarantine(self) -> None:
        build_native(self.tmp, {
            "graphics/src/uses_mirror.cpp": "#include <vellum/view/view.hpp>\nvellum::view::View v;\n",
            "graphics/src/copied.cpp": FIXTURE["core/runtime/src/log.cpp"],
        })
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        c = report["collisions"]
        self.assertEqual(c["refused_units"], ["view"])  # native owns runtime, so runtime is legal
        self.assertEqual(sorted(f["match"] for f in c["native_refusals"]), ['<vellum/view/', 'vellum::view'])
        self.assertEqual([q["native"] for q in c["blob_quarantine"]], ["graphics/src/copied.cpp"])

    def test_objc_class_collision_with_native_fails(self) -> None:
        build_native(self.tmp, {"graphics/src/view.mm": "@interface VellumMirrorView : NSView\n@end\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(report["collisions"]["objc_class_collisions"],
                         [{"class": "VellumMirrorView", "from": "PulpView", "native": "graphics/src/view.mm"}])

    def test_third_party_shared_blobs_are_listed_not_refused(self) -> None:
        build_native(self.tmp, {"assets/fonts/miniz.c": FIXTURE["external/miniz/miniz.c"]})
        code, report = run(str(self.root))
        self.assertEqual(code, 0, report.get("errors"))
        self.assertEqual(report["collisions"]["third_party_shared_blobs"],
                         [{"mirror": "external/miniz/miniz.c", "native": "assets/fonts/miniz.c"}])

    def test_receipt_verifies_tool_free(self) -> None:
        raw_parent = Path(tempfile.mkdtemp())  # outside the native tree
        self.addCleanup(shutil.rmtree, raw_parent)
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        self.assertEqual(identity_verify(raw, self.root, report["receipt"]), [])
        # A non-rename edit is caught even though every spelling check would pass.
        log = self.root / "core/runtime/src/log.cpp"
        log.write_text(log.read_text().replace("(void)s;", "(void)s; (void)0;"))
        self.assertTrue(any("not a pure rename" in e for e in identity_verify(raw, self.root, report["receipt"])))

    def renamed_with_raw(self) -> tuple[Path, dict]:
        raw_parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raw_parent)
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        return raw, report["receipt"]

    def test_identity_checks_each_receipt_claim(self) -> None:
        from verify_mirror_rename_identity import table_sha256
        raw, receipt = self.renamed_with_raw()
        self.assertEqual(identity_verify(raw, self.root, receipt), [])
        # The table must hash to its recorded digest.
        tampered = {**receipt, "include_units": ["runtime"]}
        self.assertTrue(any("table_sha256" in e for e in identity_verify(raw, self.root, tampered)))
        # Discovery over the raw export must reproduce the recorded table.
        fewer = {**receipt, "objc_classes": ["PulpView"]}
        fewer["table_sha256"] = table_sha256(fewer)
        self.assertTrue(any("discovered classes" in e for e in identity_verify(raw, self.root, fewer)))
        # The mirror holds exactly the renamed raw paths.
        (self.root / "core/view/src/extra.cpp").write_text("int x;\n")
        self.assertTrue(any("no raw path renames to" in e for e in identity_verify(raw, self.root, receipt)))
        (self.root / "core/view/src/extra.cpp").unlink()
        # A raw export that already spells the target is refused.
        (raw / "core/view/src/early.cpp").write_text("int vellum::x;\n")
        (self.root / "core/view/src/early.cpp").write_text("int vellum::x;\n")
        self.assertTrue(any("already contains the target spelling" in e
                            for e in identity_verify(raw, self.root, receipt)))

    def test_identity_fails_a_mirror_made_by_a_modified_tool(self) -> None:
        # The HIGH-1 scenario: a Vellum PR edits the tool (an extra non-rename
        # rule, its own self-check removed) and regenerates the mirror. The
        # tool-free verifier must still refuse it.
        import subprocess
        raw_parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raw_parent)
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        here = Path(rename.__file__).resolve().parent
        tools = raw_parent / "tools"
        tools.mkdir()
        for name in ("mirror_collision_census.py", "verify_mirror_rename_identity.py"):
            shutil.copy(here / name, tools / name)
        source = (here / "rename_mirror_namespace.py").read_text()
        hostile = source.replace('    rules.append(("include-dir",',
                                 '    rules.append(("evil", re.compile(r"\\(void\\)s;"), "(void)s; (void)0;"))\n'
                                 '    rules.append(("include-dir",', 1)
        hostile = hostile.replace('    for problem in report.get("inverse_errors", []):\n        errors.append(problem)\n', "", 1)
        self.assertNotEqual(hostile, source)
        (tools / "rename_mirror_namespace.py").write_text(hostile)
        result = subprocess.run([sys.executable, "-I", "-B", str(tools / "rename_mirror_namespace.py"), str(self.root),
                                 "--to", "vellum", "--literal-exclusions", str(EMPTY_EXCLUSIONS), "--apply"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        receipt = json.loads(result.stdout)["receipt"]
        self.assertIn("(void)s; (void)0;", (self.root / "core/runtime/src/log.cpp").read_text())
        errors = identity_verify(raw, self.root, receipt)
        self.assertTrue(any("not a pure rename" in e for e in errors), errors)

    def test_identity_reads_git_objects(self) -> None:
        import subprocess
        from verify_mirror_rename_identity import git_tree
        raw, receipt = self.renamed_with_raw()
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        shutil.copytree(raw, repo / "raw")
        shutil.copytree(self.root, repo / "mirror")
        for args in (["init", "-q"], ["-c", "core.autocrlf=false", "add", "."],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "trees"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        self.assertEqual(identity_verify(git_tree(repo, "HEAD:raw"), git_tree(repo, "HEAD:mirror"), receipt), [])

    def test_identity_refuses_swaps_the_rules_never_make(self) -> None:
        # boundary-review NEW-A: each of these inverts cleanly to the raw bytes.
        swaps = {
            "core/view/src/react.cpp": ('const char* pkg = "@pulp/react";\n', '"@pulp/react"', '"@vellum/react"'),
            "core/view/src/chart.mm": ("void draw(PulpChart* c);\n", "PulpChart", "VellumMirrorChart"),
            "core/view/src/url.cpp": ('// https://github.com/Generous-Corp/pulp/blob/main/x\n',
                                      "Generous-Corp/pulp/blob", "Generous-Corp/vellum/blob"),
        }
        build(self.root, {path: text for path, (text, _, _) in swaps.items()})
        raw, receipt = self.renamed_with_raw()
        self.assertEqual(identity_verify(raw, self.root, receipt), [])
        from verify_mirror_rename_identity import inverse_bytes
        for path, (_, before, after) in swaps.items():
            with self.subTest(path):
                target = self.root / path
                original = target.read_text()
                target.write_text(original.replace(before, after))
                self.assertEqual(inverse_bytes(target.read_bytes(), "pulp", "vellum", "VellumMirror"),
                                 (raw / path).read_bytes())  # the inverse alone is fooled
                errors = identity_verify(raw, self.root, receipt)
                self.assertTrue(any("differs from the forward rename" in e for e in errors), errors)
                target.write_text(original)

    def test_identity_refuses_non_regular_entries(self) -> None:
        raw, receipt = self.renamed_with_raw()
        link = self.root / "core/view/include/vellum/view/evil.hpp"
        os.symlink("/etc/hosts", link)
        (raw / "core/view/include/pulp/view").mkdir(parents=True, exist_ok=True)
        os.symlink("/etc/hosts", raw / "core/view/include/pulp/view/evil.hpp")
        errors = identity_verify(raw, self.root, receipt)
        self.assertTrue(any("evil.hpp is not a regular file (mode 120000)" in e for e in errors), errors)
        link.unlink()
        (raw / "core/view/include/pulp/view/evil.hpp").unlink()
        script = self.root / "core/runtime/src/log.cpp"
        os.chmod(script, 0o755)
        errors = identity_verify(raw, self.root, receipt)
        self.assertTrue(any("has mode 100755" in e for e in errors), errors)

    def test_identity_refuses_raw_paths_with_target_spellings_and_duplicate_targets(self) -> None:
        raw, receipt = self.renamed_with_raw()
        twin = raw / "core/view/include/vellum/view/view.hpp"
        twin.parent.mkdir(parents=True)
        twin.write_bytes((self.root / "core/view/include/vellum/view/view.hpp").read_bytes())
        errors = identity_verify(raw, self.root, receipt)
        self.assertTrue(any("raw export path core/view/include/vellum/view/view.hpp" in e for e in errors), errors)
        self.assertTrue(any("both rename to core/view/include/vellum/view/view.hpp" in e for e in errors), errors)

    def test_identity_refuses_duplicate_exclusions_and_counts_per_file(self) -> None:
        from verify_mirror_rename_identity import table_sha256
        build(self.root, {"core/runtime/src/license.cpp": 'seed(ctx, "pulp::license::v2", 17);\n'})
        exclusions = self.tmp / "exclusions.json"
        exclusions.write_text(json.dumps([{"path": "core/runtime/src/license.cpp", "literal": '"pulp::license::v2"',
                                           "reason": "length-coupled"}]))
        raw_parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raw_parent)
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        code, report = run(str(self.root), "--literal-exclusions", str(exclusions), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        receipt = report["receipt"]
        self.assertEqual(identity_verify(raw, self.root, receipt), [])
        doubled = {**receipt, "literal_exclusions": receipt["literal_exclusions"] * 2}
        doubled["table_sha256"] = table_sha256(doubled)
        lic = self.root / "core/runtime/src/license.cpp"
        lic.write_text(lic.read_text() + "// pulp::left\n")
        errors = identity_verify(raw, self.root, doubled)
        self.assertTrue(any("duplicate literal exclusion" in e for e in errors), errors)
        self.assertTrue(any("license.cpp: 2 `pulp::`" in e for e in errors), errors)
        # Another file's allowance cannot cover a leftover elsewhere.
        lic.write_text(lic.read_text().replace("// pulp::left\n", ""))
        log = self.root / "core/runtime/src/log.cpp"
        log.write_text(log.read_text() + "// pulp::left\n")
        self.assertTrue(any("log.cpp: 1 `pulp::`" in e for e in identity_verify(raw, self.root, receipt)))

    def test_identity_pins_its_spellings(self) -> None:
        from verify_mirror_rename_identity import table_sha256
        raw, receipt = self.renamed_with_raw()
        other = {**receipt, "objc_class_prefix": "Pulp"}
        other["table_sha256"] = table_sha256(other)
        self.assertTrue(any("pinned to 'VellumMirror'" in e for e in identity_verify(raw, self.root, other)))

    def test_secondary_refuses_a_helper_path(self) -> None:
        from verify_mirror_rename_identity import secondary
        raw, receipt = self.renamed_with_raw()
        hostile = {**receipt, "tool_git_blob": "x", "helper_git_blobs": {}}
        hostile["python"] = f"{sys.version_info.major}.{sys.version_info.minor}"
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        import subprocess
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=b"print(1)\n",
                              capture_output=True).stdout.decode().strip()
        hostile["tool_git_blob"] = blob
        escape = repo / "escaped" / "escape.py"  # an absolute name outside the verifier's scratch dir
        escape.parent.mkdir()
        hostile["helper_git_blobs"] = {str(escape): blob}
        errors = secondary(raw, self.root, hostile, repo)
        self.assertTrue(any("not a bare *.py basename" in e for e in errors), errors)
        self.assertFalse(escape.exists())

    def pulp_repo_from(self, raw: Path) -> tuple[Path, str, list[dict]]:
        import subprocess
        pulp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pulp)
        shutil.copytree(raw, pulp, dirs_exist_ok=True)
        git = lambda *a: subprocess.run(["git", "-C", str(pulp), *a], check=True, capture_output=True,
                                        text=True).stdout.strip()
        git("init", "-q", "-b", "main")
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pulp")
        head = git("rev-parse", "HEAD")
        records = [{"pulp_commit": head, "pulp_path": f, "pulp_git_blob_sha": git("rev-parse", f"HEAD:{f}")}
                   for f in git("ls-files").split()]
        return pulp, head, records

    def test_raw_is_rebuilt_from_pulp_records(self) -> None:
        import subprocess
        from verify_mirror_rename_identity import raw_from_records
        raw, receipt = self.renamed_with_raw()
        pulp, head, records = self.pulp_repo_from(raw)
        rebuilt, problems = raw_from_records(pulp, records, head, "main")
        self.assertEqual(problems, [])
        self.assertEqual(identity_verify(rebuilt, self.root, receipt), [])
        # boundary-review NEW-G, each of which must fail:
        bad_blob = [dict(r) for r in records]
        bad_blob[0]["pulp_git_blob_sha"] = "0" * 40
        self.assertTrue(raw_from_records(pulp, bad_blob, head, "main")[1])
        missing = [dict(r) for r in records]
        del missing[0]["pulp_git_blob_sha"]
        self.assertTrue(any("missing" in e for e in raw_from_records(pulp, missing, head, "main")[1]))
        other = "1" * 40
        self.assertTrue(any("not the receipt's pulp_sha" in e for e in raw_from_records(pulp, records, other, "main")[1]))
        magic = [dict(records[0], pulp_path=":(glob)core/**")]
        self.assertTrue(any("not a safe relative path" in e for e in raw_from_records(pulp, magic, head, "main")[1]))
        tree = [dict(records[0], pulp_path="core/runtime")]
        self.assertTrue(any("is a tree" in e for e in raw_from_records(pulp, tree, head, "main")[1]))
        glob = [dict(records[0], pulp_path="core/*/CMakeLists.txt")]
        self.assertTrue(any("not a safe relative path" in e for e in raw_from_records(pulp, glob, head, "main")[1]))
        # An off-main commit (a side branch, then deleted) is not authoritative.
        g = lambda *a: subprocess.run(["git", "-C", str(pulp), *a], check=True, capture_output=True,
                                      text=True).stdout.strip()
        g("checkout", "-q", "-b", "side")
        (pulp / "core/runtime/src/log.cpp").write_text((pulp / "core/runtime/src/log.cpp").read_text() + "int injected;\n")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "side")
        side = g("rev-parse", "HEAD")
        g("checkout", "-q", "main")
        g("branch", "-q", "-D", "side")
        side_records = [dict(r, pulp_commit=side,
                             pulp_git_blob_sha=g("rev-parse", f"{side}:{r['pulp_path']}")) for r in records]
        self.assertTrue(any("not an ancestor" in e for e in raw_from_records(pulp, side_records, side, "main")[1]))

    def test_records_mode_from_the_command_line(self) -> None:
        import subprocess
        raw, receipt = self.renamed_with_raw()
        pulp, head, records = self.pulp_repo_from(raw)
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        (work / "records.json").write_text(json.dumps({"records": records}))
        (work / "export.json").write_text(json.dumps({"pulp_sha": head}))
        (work / "rename.json").write_text(json.dumps(receipt))
        verifier = Path(rename.__file__).resolve().parent / "verify_mirror_rename_identity.py"
        run_ = lambda *extra: subprocess.run(
            [sys.executable, "-I", "-B", str(verifier), "--records", str(work / "records.json"), "--pulp-repo", str(pulp),
             "--export-receipt", str(work / "export.json"), "--pulp-main", "main", "--mirror", str(self.root),
             "--receipt", str(work / "rename.json"), *extra], capture_output=True, text=True)
        result = run_()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["authoritative_raw"])
        # A complete, matching raw export whose commit is not on Pulp's main
        # still fails: the source check reaches the command line's verdict.
        g = lambda *a: subprocess.run(["git", "-C", str(pulp), *a], check=True, capture_output=True, text=True)
        g("checkout", "-q", "--orphan", "elsewhere")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "unrelated")
        unrelated = run_("--pulp-main", "elsewhere")
        self.assertEqual(unrelated.returncode, 1)
        self.assertTrue(any("not an ancestor" in e for e in json.loads(unrelated.stdout)["errors"]))

    def test_materialize_refuses_unsafe_paths(self) -> None:
        from verify_mirror_rename_identity import materialize, secondary, GitBlob
        dest = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dest)
        outside = dest.parent / f"escaped-{dest.name}.txt"
        for bad in ("../" + outside.name, str(outside), "a//b.txt", "a/./b.txt"):
            with self.subTest(bad), self.assertRaises(ValueError):
                materialize({bad: GitBlob(dest, "0" * 40)}, dest / "out")
        self.assertFalse(outside.exists())
        raw, receipt = self.renamed_with_raw()
        errors = secondary({"../escape.cpp": GitBlob(dest, "0" * 40)}, self.root, receipt, dest)
        self.assertTrue(any("unsafe path" in e for e in errors), errors)

    def test_scratch_inputs_need_the_scratch_flag(self) -> None:
        import subprocess
        raw, receipt = self.renamed_with_raw()
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        (work / "rename.json").write_text(json.dumps(receipt))
        verifier = Path(rename.__file__).resolve().parent / "verify_mirror_rename_identity.py"
        base = [sys.executable, "-I", "-B", str(verifier), "--raw", str(raw), "--mirror", str(self.root),
                "--receipt", str(work / "rename.json")]
        self.assertEqual(subprocess.run(base, capture_output=True).returncode, 2)
        ok = subprocess.run(base + ["--scratch"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertFalse(json.loads(ok.stdout)["authoritative_raw"])

    def test_exclusions_must_be_quoted_literals_and_the_edge_table_is_hashed(self) -> None:
        from verify_mirror_rename_identity import table_sha256
        exclusions = self.tmp / "exclusions.json"
        exclusions.write_text(json.dumps([{"path": "core/runtime/src/log.cpp", "literal": "namespace pulp {",
                                           "reason": "code, not a string"}]))
        code, _ = run(str(self.root), "--literal-exclusions", str(exclusions))
        self.assertEqual(code, 2)
        raw, receipt = self.renamed_with_raw()
        coded = {**receipt, "literal_exclusions": [{"path": "core/runtime/src/log.cpp", "literal": "namespace pulp {"}]}
        coded["table_sha256"] = table_sha256(coded)
        self.assertTrue(any("not a quoted string literal" in e for e in identity_verify(raw, self.root, coded)))
        widened = {**receipt, "edge_table": {**receipt["edge_table"], "suffixes": receipt["edge_table"]["suffixes"] + [".md"]}}
        self.assertTrue(any("table_sha256" in e for e in identity_verify(raw, self.root, widened)))

    def test_inverse_self_check_catches_a_non_invertible_rule(self) -> None:
        original = rename.build_rules
        def with_extra_rule(*a, **k):
            return [*original(*a, **k), ("bad", re.compile(r"\blog\b"), "LOG")]
        rename.build_rules = with_extra_rule
        try:
            code, report = run(str(self.root))
        finally:
            rename.build_rules = original
        self.assertEqual(code, 1)
        self.assertTrue(report["inverse_errors"], report.get("errors"))

    def test_secondary_runs_the_recorded_tool_blob(self) -> None:
        import subprocess
        from verify_mirror_rename_identity import secondary
        raw_parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raw_parent)
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        repo = raw_parent / "tools-repo"
        repo.mkdir()
        here = Path(rename.__file__).resolve().parent
        names = ["rename_mirror_namespace.py", "mirror_collision_census.py", "verify_mirror_rename_identity.py"]
        for name in names:
            shutil.copy(here / name, repo / name)
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "tools"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        blob = lambda n: subprocess.run(["git", "-C", str(repo), "rev-parse", f"HEAD:{n}"],
                                        capture_output=True, text=True).stdout.strip()
        receipt = {**report["receipt"], "tool_git_blob": blob(names[0]),
                   "helper_git_blobs": {n: blob(n) for n in names[1:]}}
        self.assertEqual(secondary(raw, self.root, receipt, repo), [])
        # The same secondary check from authoritative git input (no directory trees).
        from verify_mirror_rename_identity import git_tree
        trees = raw_parent / "trees"
        shutil.copytree(raw, trees / "raw")
        shutil.copytree(self.root, trees / "mirror")
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "trees"]):
            subprocess.run(["git", "-C", str(trees), *args], check=True, capture_output=True)
        self.assertEqual(secondary(git_tree(trees, "HEAD:raw"), git_tree(trees, "HEAD:mirror"), receipt, repo), [])
        # The recorded blob runs, not whatever the checkout has now.
        (repo / names[0]).write_text("raise SystemExit(9)\n")
        self.assertEqual(secondary(raw, self.root, receipt, repo), [])
        self.assertTrue(secondary(raw, self.root, {**receipt, "python": "2.7"}, repo))
        self.assertTrue(secondary(raw, self.root, {**receipt, "tool_rule_digest": "0" * 64}, repo))

    def test_identity_catches_a_renamed_excluded_literal(self) -> None:
        raw_parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raw_parent)
        build(self.root, {"core/runtime/src/license.cpp": 'seed(ctx, "pulp::license::v2", 17);\n'})
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        exclusions = self.tmp / "exclusions.json"
        exclusions.write_text(json.dumps([{"path": "core/runtime/src/license.cpp", "literal": '"pulp::license::v2"',
                                           "reason": "length-coupled"}]))
        code, report = run(str(self.root), "--literal-exclusions", str(exclusions), "--apply")
        self.assertEqual(code, 0, report.get("errors"))
        self.assertEqual(identity_verify(raw, self.root, report["receipt"]), [])
        lic = self.root / "core/runtime/src/license.cpp"
        lic.write_text(lic.read_text().replace('"pulp::license::v2"', '"vellum::license::v2"'))
        self.assertTrue(any("it was changed" in e for e in identity_verify(raw, self.root, report["receipt"])))

    def test_apply_moves_the_renamed_tree_to_a_native_root(self) -> None:
        raw_parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, raw_parent)
        raw = raw_parent / "raw"
        shutil.copytree(self.root, raw)
        code, report = run(str(self.root), "--apply", "--move-to", "framework")
        self.assertEqual(code, 0, report.get("errors"))
        native = self.tmp / "framework"
        self.assertFalse(self.root.exists())  # the mirror root never holds renamed files
        self.assertTrue((native / "core/runtime/include/vellum/runtime/log.hpp").is_file())
        self.assertEqual(report["moved_to"], "framework")
        # The identity proof still maps raw paths onto the native root.
        self.assertEqual(identity_verify(raw, native, report["receipt"]), [])

    def test_move_refuses_a_non_empty_destination(self) -> None:
        (self.tmp / "framework").mkdir()
        (self.tmp / "framework/native.cpp").write_text("int x;\n")
        before = tree_digest(self.root)
        code, report = run(str(self.root), "--apply", "--move-to", "framework")
        self.assertEqual((code, report["status"]), (1, "refused"))
        self.assertEqual(tree_digest(self.root), before)

    def test_apply_is_idempotent(self) -> None:
        run(str(self.root), "--apply")
        once = tree_digest(self.root)
        code, report = run(str(self.root), "--apply")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "already-renamed")
        self.assertEqual(report["files_changed"], 0)
        self.assertEqual(report["paths_moved"], 0)
        self.assertEqual(tree_digest(self.root), once)

    def test_output_is_deterministic(self) -> None:
        other_repo = Path(tempfile.mkdtemp())  # its own repo, outside this one's native tree
        self.addCleanup(shutil.rmtree, other_repo)
        other = other_repo / "mirror"
        build(other)
        build_native(other_repo)
        write_table(other_repo / "tools/provenance/mirror_edge_patterns.json", ["pulp", "vellum"])
        code_a, a = run(str(self.root), "--apply")
        code_b, b = run(str(other), "--apply")
        self.assertEqual((code_a, code_b), (0, 0), (a.get("errors"), b.get("errors")))
        self.assertEqual(tree_digest(self.root), tree_digest(other))
        self.assertEqual(a["rule_digest"], b["rule_digest"])
        self.assertEqual(a["replacements"], b["replacements"])

    def test_dry_run_predicts_apply(self) -> None:
        _, dry = run(str(self.root))
        _, applied = run(str(self.root), "--apply")
        self.assertEqual(dry["after"], applied["after"])
        self.assertEqual(dry["replacements"], applied["replacements"])

    def test_zero_control_is_instrument_blind(self) -> None:
        empty = self.tmp / "empty"
        (empty / "core").mkdir(parents=True)
        (empty / "core/a.cpp").write_text("int a;\n")
        code, report = run(str(empty))
        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "instrument-blind")

    def test_expected_count_mismatch_fails(self) -> None:
        code, report = run(str(self.root), "--expect-namespace-count", "6")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "control-mismatch")
        code, _ = run(str(self.root), "--expect-namespace-count", "5")
        self.assertEqual(code, 0)

    def test_pulp_names_in_excluded_dirs_fail(self) -> None:
        build(self.root, {"external/lib/x.hpp": "namespace pulp { int x; }\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertTrue(any("excluded" in e for e in report["errors"]), report["errors"])

    def test_move_collision_fails(self) -> None:
        build(self.root, {"core/view/include/vellum/view/view.hpp": "// already here\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(report["move_collisions"], ["core/view/include/vellum/view/view.hpp"])

    def test_unknown_include_unit_is_left_and_flagged(self) -> None:
        build(self.root, {"core/view/CMakeLists.txt": "install(DIRECTORY include/pulp/missing/ DESTINATION x)\n"})
        code, report = run(str(self.root))
        self.assertEqual(code, 1)
        self.assertTrue(any("dir include/pulp" in e for e in report["errors"]), report["errors"])


if __name__ == "__main__":
    unittest.main()
