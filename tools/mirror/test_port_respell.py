#!/usr/bin/env python3
"""The port respell's recognisers, its respell and its table, each with a control."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("port_respell", HERE / "port_respell.py")
assert spec and spec.loader
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


def entry(old: str, kind: str, evidence: str | None = None) -> dict:
    return {"old": old, "new": pr.respelled(old), "kind": kind, "evidence": evidence}


class LexingTest(unittest.TestCase):
    def test_literals_are_never_respelled(self) -> None:
        text = ('const char* a = "pulp_f PULP_X";\nchar b = \'p\';\n'
                'auto c = R"x(pulp_f "quoted" PULP_X)x";\nint d = 1\'000; pulp_f(); // pulp_f in a comment\n')
        out = pr.apply(text, {"pulp_f": "vellum_f", "PULP_X": "VELLUM_X"})
        self.assertIn('"pulp_f PULP_X"', out)
        self.assertIn('R"x(pulp_f "quoted" PULP_X)x"', out)
        self.assertIn("vellum_f(); // vellum_f in a comment", out)  # code and comments are
        self.assertNotIn("pulp_f()", out)

    def test_only_whole_tokens_change(self) -> None:
        out = pr.apply("pulp_fx(); xpulp_f(); pulp_f();", {"pulp_f": "vellum_f"})
        self.assertEqual(out, "pulp_fx(); xpulp_f(); vellum_f();")

    def test_respelled_changes_only_the_prefix(self) -> None:
        self.assertEqual(pr.respelled("PULP_LOG_TAG"), "VELLUM_LOG_TAG")
        self.assertEqual(pr.respelled("pulp_uia_handle"), "vellum_uia_handle")
        with self.assertRaises(pr.RespellError):
            pr.respelled("PulpThing")


class RecogniserTest(unittest.TestCase):
    SOURCE = """#define PULP_LOG_TAG "Pulp"
#define PULP_LOGI(...) log(PULP_LOG_TAG, __VA_ARGS__)
#if defined(__ANDROID__) && !defined(PULP_RENDER_LOOP_FORCE_TIMER)
#endif
#ifndef PULP_ONLY_TESTED
#endif
#if PULP_VALUE_USED
#endif
extern "C" LRESULT pulp_uia_handle(void* handle,
                                   int pulp_role) {
    return pulp_helper(pulp_role);
}
extern "C" PulpProvider*
pulp_lookup_windows(const char* id) { return nullptr; }
extern "C" void pulp_declared_only(int);
extern "C" KEEP const char pulp_sentinel[] = "PULP_TRACING_COMPILED_IN__DO_NOT_SHIP";
extern "C" {
bool pulp_motion_enabled(void) { return true; }
void pulp_motion_decl(int);
JNIEXPORT jint JNICALL Java_com_pulp_x(JNIEnv*) { return 0; }
}
"""

    def test_file_macros_are_the_ones_the_file_defines(self) -> None:
        self.assertEqual(pr.self_defined_macros(self.SOURCE), {"PULP_LOG_TAG", "PULP_LOGI"})

    def test_extern_c_definitions_cover_every_form_and_only_definitions(self) -> None:
        self.assertEqual(pr.extern_c_definitions(self.SOURCE),
                         {"pulp_uia_handle", "pulp_lookup_windows", "pulp_sentinel", "pulp_motion_enabled"})

    def test_a_non_extern_c_pulp_name_is_not_a_definition(self) -> None:
        names = pr.extern_c_definitions(self.SOURCE)
        for name in ("pulp_role", "pulp_helper", "pulp_declared_only", "pulp_motion_decl", "Java_com_pulp_x"):
            self.assertNotIn(name, names)

    def test_a_switch_is_only_tested_and_a_value_use_is_not_one(self) -> None:
        switches = pr.opt_in_switches(self.SOURCE)
        self.assertEqual(switches, {"PULP_RENDER_LOOP_FORCE_TIMER", "PULP_ONLY_TESTED"})
        self.assertEqual(pr.macro_uses(self.SOURCE, "PULP_VALUE_USED"), (0, 1))
        self.assertNotIn("PULP_VALUE_USED", switches)
        # A defined macro is not a switch either.
        self.assertNotIn("PULP_LOG_TAG", switches)
        # Tested and also used for its value: not a switch.
        mixed = "#ifdef PULP_MIXED\n#endif\nint x = PULP_MIXED;\n"
        self.assertEqual(pr.macro_uses(mixed, "PULP_MIXED"), (1, 1))
        self.assertEqual(pr.opt_in_switches(mixed), set())


class DeriveTest(unittest.TestCase):
    PORTED = "framework/core/view/platform/x/host.cpp"
    CALLER = "framework/core/view/platform/x/caller.cpp"
    HEADER = "framework/core/view/include/vellum/view/bridge.h"

    def ported(self) -> dict:
        return {
            self.PORTED: ('#define PULP_TAG "x"\nextern "C" void pulp_bridge(int pulp_role) {}\n'
                          '#ifdef PULP_FORCE\n#endif\nint n = PULP_HAS_SKIA;\n'),
            self.CALLER: 'void f() { pulp_bridge(1); }\n',
        }

    def test_each_kind_is_derived_from_its_evidence(self) -> None:
        transferred = {self.HEADER: "void pulp_bridge(int);\n"}
        table = pr.derive(self.ported(), transferred, {"PULP_HAS_SKIA"}, set(), {**self.ported(), **transferred})
        self.assertEqual(table, [
            entry("PULP_FORCE", "opt-in-switch", self.PORTED),
            entry("PULP_HAS_SKIA", "closure-macro"),
            entry("PULP_TAG", "file-macro", self.PORTED),
            entry("pulp_bridge", "extern-c", self.PORTED),
        ])
        # pulp_role is a parameter, not a C-linkage definition: never respelled.
        self.assertNotIn("pulp_role", [e["old"] for e in table])

    def test_a_switch_something_defines_is_not_derived(self) -> None:
        defines = {"framework/core/a.hpp": "#define PULP_FORCE 1\n"}
        table = pr.derive(self.ported(), defines, set(), set(), {**self.ported(), **defines})
        self.assertNotIn("PULP_FORCE", [e["old"] for e in table])
        table = pr.derive(self.ported(), {}, {"PULP_FORCE"}, set(), self.ported())
        self.assertNotIn(("PULP_FORCE", "opt-in-switch"), [(e["old"], e["kind"]) for e in table])

    def test_a_caller_and_its_definition_stay_consistent(self) -> None:
        transferred = {self.HEADER: "void pulp_bridge(int);\n"}
        files = {**self.ported(), **transferred}
        table = pr.derive(self.ported(), transferred, {"PULP_HAS_SKIA"}, set(), files)
        respelled = {path: pr.apply(text, pr.mapping_for(table, path, path in self.ported()))
                     for path, text in files.items()}
        self.assertEqual(pr.consistency_errors(table, respelled), [])
        self.assertIn("vellum_bridge(1)", respelled[self.CALLER])
        self.assertIn("void vellum_bridge(int);", respelled[self.HEADER])
        self.assertIn("int pulp_role", respelled[self.PORTED])  # untouched
        # A transferred file keeps its closure-macro spelling; a ported one is respelled.
        self.assertIn("VELLUM_HAS_SKIA", respelled[self.PORTED])
        # Control: a caller left in the Pulp spelling is refused.
        stale = {**respelled, self.CALLER: self.ported()[self.CALLER]}
        errors = pr.consistency_errors(table, stale)
        self.assertTrue(any(self.CALLER in e and "pulp_bridge" in e for e in errors), errors)

    def test_a_file_macro_is_respelled_in_its_own_file_only(self) -> None:
        other = "framework/core/other.cpp"
        files = {**self.ported(), other: "#ifdef PULP_TAG\n#endif\n"}
        table = pr.derive(self.ported(), {other: files[other]}, set(), set(), files)
        mapping = pr.mapping_for(table, other, ported=False)
        self.assertNotIn("PULP_TAG", mapping)
        self.assertEqual(pr.consistency_errors([entry("PULP_TAG", "file-macro", self.PORTED)],
                                               {other: files[other]}), [])


class LinkExportAndSwitchTest(unittest.TestCase):
    PORTED = "framework/core/view/platform/web/input.cpp"

    def test_a_link_export_is_derived_only_from_the_closures_list(self) -> None:
        ported = {self.PORTED: "EMSCRIPTEN_KEEPALIVE void pulp_web_on_key(int k) {}\n"}
        self.assertEqual(pr.derive(ported, {}, set(), {"pulp_web_on_key"}, ported),
                         [entry("pulp_web_on_key", "link-export")])
        # Control: a name the closures do not list is not respelled.
        self.assertEqual(pr.derive(ported, {}, set(), set(), ported), [])

    def test_a_switch_set_outside_framework_is_refused(self) -> None:
        table = [entry("PULP_FORCE", "opt-in-switch", self.PORTED)]
        current = {self.PORTED: "#ifdef VELLUM_FORCE\n#endif\n"}
        self.assertEqual(pr.switch_errors(table, current, set(), {"CMakeLists.txt": "add_definitions()\n"}), [])
        errors = pr.switch_errors(table, current, set(), {"tests/CMakeLists.txt": "target_compile_definitions(t PRIVATE PULP_FORCE=1)\n"})
        self.assertTrue(any("still sets or names PULP_FORCE" in e for e in errors), errors)
        errors = pr.switch_errors(table, {**current, "framework/a.hpp": "#define VELLUM_FORCE 1\n"}, set(), {})
        self.assertTrue(any("defines PULP_FORCE or VELLUM_FORCE" in e for e in errors), errors)
        errors = pr.switch_errors(table, {self.PORTED: "#if VELLUM_FORCE\n#endif\n"}, set(), {})
        self.assertTrue(any("for its value" in e for e in errors), errors)
        self.assertTrue(pr.switch_errors(table, current, {"PULP_FORCE"}, {}))


class TableTest(unittest.TestCase):
    def errors(self, rows: list) -> list:
        return pr.parse(json.dumps({"schema": pr.SCHEMA, "entries": rows}))[1]

    def test_entries_are_checked(self) -> None:
        ok = entry("pulp_f", "extern-c", "framework/a.cpp")
        self.assertEqual(self.errors([ok]), [])
        for bad, fragment in (
            ({**ok, "new": "vellum_g"}, "respells to vellum_f"),
            ({**ok, "kind": "renamed"}, "kind must be one of"),
            ({**ok, "extra": 1}, "must have exactly"),
            ({**ok, "evidence": None}, "names the framework/ source"),
            ({**ok, "evidence": "framework/a.json"}, "names the framework/ source"),
            (entry("PULP_X", "extern-c", "framework/a.cpp"), "respells a pulp_ name"),
            (entry("pulp_x", "file-macro", "framework/a.cpp"), "respells a PULP_ name"),
            ({**entry("PULP_X", "closure-macro"), "evidence": "framework/a.cpp"}, "evidence is null"),
        ):
            self.assertTrue(any(fragment in e for e in self.errors([bad])), (bad, self.errors([bad])))

    def test_order_and_duplicates(self) -> None:
        a, b = entry("pulp_a", "extern-c", "framework/a.cpp"), entry("pulp_b", "extern-c", "framework/b.cpp")
        self.assertTrue(any("sorted" in e for e in self.errors([b, a])))
        self.assertTrue(any("listed twice" in e for e in self.errors([a, {**a, "evidence": "framework/z.cpp"}])))
        # A file macro may be defined in several files.
        m1, m2 = entry("PULP_TAG", "file-macro", "framework/a.cpp"), entry("PULP_TAG", "file-macro", "framework/b.cpp")
        self.assertEqual(self.errors([m1, m2]), [])


class LandedTableTest(unittest.TestCase):
    """The landed table and framework/ as they are in this checkout."""

    def test_the_landed_table_is_the_transferred_c_linkage_definitions(self) -> None:
        table, errors = pr.load(REPO)
        self.assertEqual(errors, [])
        self.assertEqual(
            {(e["old"], e["kind"]) for e in table if e["evidence"] and e["kind"] == "extern-c"},
            {("pulp_gpu_runtime_hooks_anchor", "extern-c"),
             ("pulp_mac_text_input_client_category_anchor", "extern-c"),
             ("pulp_text_accessibility_lookup_macos", "extern-c"),
             ("pulp_tracing_ship_sentinel", "extern-c")})

    def test_the_ship_sentinel_string_is_unchanged(self) -> None:
        # pulp ship's scanner looks for the string's bytes, not the symbol.
        text = (REPO / "framework/core/runtime/src/trace.cpp").read_text(encoding="utf-8")
        self.assertIn('vellum_tracing_ship_sentinel[] = "PULP_TRACING_COMPILED_IN__DO_NOT_SHIP";', text)
        self.assertNotIn("pulp_tracing_ship_sentinel", text)

    def test_the_transferred_bytes_imply_exactly_the_landed_extern_c_entries(self) -> None:
        # Read the transfer record's blobs: the evidence as transferred, before any respell.
        records = json.loads((REPO / "provenance/framework/transfer.json").read_text(encoding="utf-8"))["records"]
        wanted = [(r["framework_path"], r["framework_git_blob_sha"]) for r in records
                  if pr.respells(r["framework_path"]) and not r["framework_path"].startswith("framework/external/")]
        batch = subprocess.run(["git", "-C", str(REPO), "cat-file", "--batch"],
                               input="".join(f"{blob}\n" for _, blob in wanted).encode(), capture_output=True)
        if batch.returncode != 0 or b" missing" in batch.stdout[:200]:
            self.skipTest("the transfer's objects are not in this clone")
        texts, data, i = {}, batch.stdout, 0
        for path, _ in wanted:
            end = data.index(b"\n", i)
            size = int(data[i:end].split()[2])
            texts[path] = data[end + 1:end + 1 + size].decode("utf-8", errors="replace")
            i = end + 1 + size + 1
        self.assertGreater(len(texts), 800)  # control: the instrument read the tree
        derived = pr.derive({}, texts, set(), set(), texts)
        table, _ = pr.load(REPO)
        self.assertEqual(derived, [e for e in table if e["kind"] == "extern-c" and e["evidence"] in texts])


if __name__ == "__main__":
    unittest.main()
