#!/usr/bin/env python3
"""The port respell's recognisers, its respell and its table, each with a control."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
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

    def test_prefixed_literals_are_never_respelled(self) -> None:
        m = {"pulp_name": "vellum_name", "PULP_NAME": "VELLUM_NAME",
             "pulp_x": "vellum_x", "pulp_y": "vellum_y"}
        for lit in ("u8'pulp_name'", "L'PULP_NAME'", 'u"pulp_x"', 'U"pulp_x"',
                    'L"pulp_x"', 'u8"pulp_x"', 'R"(pulp_y)"', 'u8R"(pulp_y)"',
                    'LR"d(pulp_y)d"', "u'pulp_x'", "U'pulp_x'"):
            text = f"auto v = {lit}; pulp_x();"
            self.assertEqual(pr.apply(text, m), f"auto v = {lit}; vellum_x();", lit)

    def test_digit_separators_stay_numbers(self) -> None:
        m = {"pulp_f": "vellum_f"}
        text = "int a = 1'000'000; int b = 0xFF'FF; int c = 0xAu'1; pulp_f();"
        self.assertEqual(pr.apply(text, m), text.replace("pulp_f", "vellum_f"))
        self.assertEqual([k for k, *_ in pr.segments(text)], ["code"])

    def test_identifier_before_quote_is_not_a_prefix(self) -> None:
        m = {"pulp_f": "vellum_f"}
        # myL / fooU are identifiers, so a spaced string is an ordinary literal
        self.assertIn('myL "pulp_f"', pr.apply('myL "pulp_f"; pulp_f();', m))
        # an identifier that merely ends in R is not a raw-string prefix
        kinds = [k for k, *_ in pr.segments('fooR"(a)"')]
        self.assertEqual(kinds, ["code", "literal"])
        self.assertEqual(pr.segments('fooR"(a)"')[1][1:], (4, 9))
        # identifier ending in u/L glued to a quote is not a char literal
        self.assertEqual([k for k, *_ in pr.segments("valu'x")], ["code"])

    def test_only_whole_tokens_change(self) -> None:
        out = pr.apply("pulp_fx(); xpulp_f(); pulp_f();", {"pulp_f": "vellum_f"})
        self.assertEqual(out, "pulp_fx(); xpulp_f(); vellum_f();")

    def test_respelled_changes_only_the_prefix(self) -> None:
        self.assertEqual(pr.respelled("PULP_LOG_TAG"), "VELLUM_LOG_TAG")
        self.assertEqual(pr.respelled("pulp_uia_handle"), "vellum_uia_handle")
        with self.assertRaises(pr.RespellError):
            pr.respelled("PulpThing")


class CommentPrefixTest(unittest.TestCase):
    MAPPING = {"pulp_motion_publish": "vellum_motion_publish", "pulp_motion_enabled": "vellum_motion_enabled",
               "pulp_role_of": "vellum_role_of"}
    SOURCE = ('// A C ABI (`pulp_motion_*`) for Kotlin; see pulp_role_ and pulp_other_.\n'
              'extern "C" void pulp_motion_publish() {}\nextern "C" bool pulp_motion_enabled() { return true; }\n'
              'extern "C" int pulp_role_of() { return 0; }\nconst char* s = "pulp_motion_ in a string";\n'
              'int pulp_motion_ = 0;  // a code token is never a prefix\n')

    def test_a_shared_prefix_in_a_comment_is_respelled(self) -> None:
        out = pr.apply(self.SOURCE, self.MAPPING)
        self.assertIn("A C ABI (`vellum_motion_*`)", out)
        self.assertIn("vellum_motion_publish()", out)

    def test_other_comment_tokens_and_literals_and_code_are_not(self) -> None:
        out = pr.apply(self.SOURCE, self.MAPPING)
        self.assertIn("pulp_other_.", out)  # an unrelated pulp_ token
        self.assertIn("see pulp_role_ and", out)  # a prefix of only one respelled name
        self.assertIn('"pulp_motion_ in a string"', out)  # literal untouched
        self.assertIn("int pulp_motion_ = 0;", out)  # code token, not a prefix rule

    def test_reapply_is_idempotent_and_apply_is_not_meant_to_be(self) -> None:
        once = pr.apply(self.SOURCE, self.MAPPING)
        self.assertEqual(pr.reapply(once, self.MAPPING), once)
        self.assertEqual(pr.reapply(self.SOURCE, self.MAPPING), once)
        # apply() on respelled bytes would see no Pulp names and leave a stale
        # prefix, so it refuses and names reapply().
        half = once.replace("vellum_motion_*", "pulp_motion_*")
        with self.assertRaises(pr.RespellError) as caught:
            pr.apply(half, self.MAPPING)
        self.assertIn("use reapply()", str(caught.exception))
        self.assertIn("vellum_motion_*", pr.reapply(half, self.MAPPING))
        # Undoing is never refused: its targets are Pulp names.
        self.assertIn("pulp_motion_publish", pr.apply(once, {v: k for k, v in self.MAPPING.items()}))

    def test_a_prefix_needs_two_names_present_in_the_file(self) -> None:
        one = '// the pulp_motion_* calls\nextern "C" void pulp_motion_publish() {}\n'
        self.assertIn("pulp_motion_*", pr.apply(one, self.MAPPING))


class RecogniserTest(unittest.TestCase):
    SOURCE = """#define PULP_LOG_TAG "Pulp"
#define PULP_LOGI(...) log(PULP_LOG_TAG, __VA_ARGS__)
#if defined(__ANDROID__) && !defined(PULP_TEST_ONLY_SWITCH)
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
        self.assertEqual(switches, {"PULP_TEST_ONLY_SWITCH", "PULP_ONLY_TESTED"})
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


if __name__ == "__main__":
    unittest.main()
