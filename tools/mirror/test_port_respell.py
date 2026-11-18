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


def entry(old: str, evidence: str | None = "framework/a.cpp", kind: str = "extern-c") -> dict:
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

    def test_header_names_in_angle_brackets_are_literals(self) -> None:
        m = {"pulp_f": "vellum_f"}
        text = ('#include <pulp_f.h>\n#  import <x/pulp_f.h>\n#include_next <pulp_f>\n'
                '#if __has_include(<pulp_f.h>)\n#endif\nbool b = a < pulp_f;\n#include "pulp_f.h"\n')
        out = pr.apply(text, m)
        for header in ("<pulp_f.h>", "<x/pulp_f.h>", "<pulp_f>", '"pulp_f.h"'):
            self.assertIn(header, out)
        self.assertIn("a < vellum_f;", out)  # control: a comparison is code
        self.assertEqual(pr.pulp_tokens(text), {"pulp_f"})

    def test_a_pasted_pulp_fragment_is_found(self) -> None:
        text = "#define A(x) pulp_##x\n#define B(x) x ## PULP_TAIL\n#define C(x) PULP_ ## x\nint pulp_ok;\n"
        self.assertEqual(pr.pasted_pulp_fragments(text), {"pulp_", "PULP_TAIL", "PULP_"})
        # Control: pasting that involves no Pulp prefix, and a ## inside a literal, are not.
        self.assertEqual(pr.pasted_pulp_fragments('#define D(x) x##_suffix\nconst char* s = "pulp_##";\n'), set())
        self.assertTrue(pr.paste_errors([entry("pulp_f")], {"framework/a.cpp": text}))
        self.assertEqual(pr.paste_errors([], {"framework/a.cpp": text}), [])

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
extern "C" LRESULT pulp_uia_handle(void* handle,
                                   int pulp_role) {
    return pulp_helper(pulp_role);
}
extern "C" PulpProvider*
pulp_lookup_windows(const char* id) { return nullptr; }
extern "C" void pulp_declared_only(int);
extern "C" int pulp_declared_object;
extern "C" KEEP const char pulp_sentinel[] = "PULP_TRACING_COMPILED_IN__DO_NOT_SHIP";
extern "C" {
bool pulp_motion_enabled(void) { return true; }
void pulp_motion_decl(int);
JNIEXPORT jint JNICALL Java_com_pulp_x(JNIEnv*) { return 0; }
}
"""

    def test_extern_c_definitions_cover_every_form_and_only_definitions(self) -> None:
        self.assertEqual(pr.extern_c_definitions(self.SOURCE),
                         {"pulp_uia_handle", "pulp_lookup_windows", "pulp_sentinel", "pulp_motion_enabled"})

    def test_a_non_extern_c_pulp_name_is_not_a_definition(self) -> None:
        names = pr.extern_c_definitions(self.SOURCE)
        for name in ("pulp_role", "pulp_helper", "pulp_declared_only", "pulp_declared_object", "pulp_motion_decl",
                     "Java_com_pulp_x", "PULP_LOG_TAG"):
            self.assertNotIn(name, names)

    DEFINED = (
        'extern "C" __attribute__((visibility("default"))) void pulp_f() {}\n',
        'extern "C" __attribute__ ((used)) int pulp_f = 1;\n',
        'extern "C" __declspec(dllexport) void pulp_f() {}\n',
        'extern "C" [[gnu::used]] void pulp_f() {}\n',
        'extern "C" void pulp_f() __attribute__((cold)) {}\n',
        'extern "C" void pulp_f() noexcept {}\n',
        'extern "C" void pulp_f() noexcept(true) {}\n',
        'extern "C" auto pulp_f() -> int { return 1; }\n',
        'extern "C" std::array<int, 2> pulp_f = {1, 2};\n',
        'extern "C" void (*pulp_f)(int) = nullptr;\n',
        'extern "C" { int pulp_f; }\n',
        'extern "C" { int pulp_f[4]; }\n',
        'extern "C" { void (*pulp_f)(int); }\n',
        'extern "C" {\n#if X\nint pulp_f;\n#endif\n}\n',
    )
    DECLARED = (
        'extern "C" int pulp_f;\n',
        'extern "C" void pulp_f(int);\n',
        'extern "C" void pulp_f() __attribute__((used));\n',
        'extern "C" { void pulp_f(int); }\n',
        'extern "C" { extern int pulp_f; }\n',
        'extern "C" { typedef void (*pulp_f)(int); }\n',
        'extern "C" { static int pulp_f; }\n',
        'extern "C" {\n#include <pulp_f.h>\n}\n',
        'int pulp_f() { return 0; }\n',
    )

    def test_attributes_block_definitions_and_function_pointers_are_recognised(self) -> None:
        for text in self.DEFINED:
            self.assertEqual(pr.extern_c_definitions(text), {"pulp_f"}, text)
        # Control: declarations, typedefs, internal linkage and C++ linkage define nothing.
        for text in self.DECLARED:
            self.assertEqual(pr.extern_c_definitions(text), set(), text)


class DeriveTest(unittest.TestCase):
    DEFINES = "framework/core/view/platform/x/host.cpp"
    CALLER = "framework/core/view/platform/x/caller.cpp"
    HEADER = "framework/core/view/include/vellum/view/bridge.h"

    def evidence(self) -> dict:
        return {
            self.DEFINES: '#define PULP_TAG "x"\nextern "C" void pulp_bridge(int pulp_role) {}\nint n = PULP_HAS_SKIA;\n',
            self.CALLER: 'void f() { pulp_bridge(1); }\n',
            self.HEADER: 'extern "C" void pulp_bridge(int);\n',
        }

    def test_only_c_linkage_definitions_are_derived(self) -> None:
        self.assertEqual(pr.derive(self.evidence()), [entry("pulp_bridge", self.DEFINES)])
        # A name defined in two files keeps the first path.
        two = {**self.evidence(), "framework/a/first.cpp": 'extern "C" void pulp_bridge(int) {}\n'}
        self.assertEqual(pr.derive(two), [entry("pulp_bridge", "framework/a/first.cpp")])

    def test_a_caller_and_its_definition_stay_consistent(self) -> None:
        table = pr.derive(self.evidence())
        respelled = {path: pr.apply(text, pr.mapping_for(table, path)) for path, text in self.evidence().items()}
        self.assertEqual(pr.consistency_errors(table, respelled), [])
        self.assertIn("vellum_bridge(1)", respelled[self.CALLER])
        self.assertIn("void vellum_bridge(int);", respelled[self.HEADER])
        self.assertIn("int pulp_role", respelled[self.DEFINES])  # a parameter: untouched
        self.assertIn("PULP_TAG", respelled[self.DEFINES])  # macros are name debt: untouched
        # Control: a caller left in the Pulp spelling is refused.
        stale = {**respelled, self.CALLER: self.evidence()[self.CALLER]}
        errors = pr.consistency_errors(table, stale)
        self.assertTrue(any(self.CALLER in e and "pulp_bridge" in e for e in errors), errors)

    def test_a_hand_respell_is_a_name_the_table_does_not_give(self) -> None:
        origin = self.evidence()[self.DEFINES]
        table = pr.derive(self.evidence())
        right = pr.apply(origin, pr.mapping_for(table, self.DEFINES))
        self.assertEqual(pr.hand_respells(origin, right, table), [])
        self.assertEqual(pr.hand_respells(origin, right.replace("PULP_TAG", "VELLUM_TAG"), table), ["PULP_TAG"])
        self.assertEqual(pr.hand_respells(origin, right, []), ["pulp_bridge"])
        # Control: a vellum_ name the origin already spelled is not a hand respell.
        self.assertEqual(pr.hand_respells(origin + "int vellum_bridge2;\n", right + "int vellum_bridge2;\n", table), [])


class TableTest(unittest.TestCase):
    def errors(self, rows: list) -> list:
        return pr.parse(json.dumps({"schema": pr.SCHEMA, "entries": rows}))[1]

    def test_entries_are_checked(self) -> None:
        ok = entry("pulp_f", "framework/a.cpp")
        self.assertEqual(self.errors([ok]), [])
        for bad, fragment in (
            ({**ok, "new": "vellum_g"}, "respells to vellum_f"),
            ({**ok, "kind": "renamed"}, "kind must be extern-c"),
            ({**ok, "extra": 1}, "must have exactly"),
            ({**ok, "evidence": None}, "names the framework/ source"),
            ({**ok, "evidence": "framework/a.json"}, "names the framework/ source"),
            (entry("PULP_X", "framework/a.cpp"), "respells a pulp_ name"),
        ):
            self.assertTrue(any(fragment in e for e in self.errors([bad])), (bad, self.errors([bad])))

    def test_the_macro_kinds_are_refused(self) -> None:
        for kind, old in (("closure-macro", "PULP_X"), ("link-export", "pulp_x"), ("file-macro", "PULP_X"),
                          ("opt-in-switch", "PULP_X")):
            errors = self.errors([entry(old, "framework/a.cpp", kind)])
            self.assertTrue(any(f"kind must be extern-c, not '{kind}'" in e for e in errors), (kind, errors))
            self.assertEqual(pr.parse(json.dumps({"schema": pr.SCHEMA, "entries": [entry(old, None, kind)]}))[0], [])

    def test_order_and_duplicates(self) -> None:
        a, b = entry("pulp_a", "framework/a.cpp"), entry("pulp_b", "framework/b.cpp")
        self.assertTrue(any("sorted" in e for e in self.errors([b, a])))
        self.assertTrue(any("listed twice" in e for e in self.errors([a, {**a, "evidence": "framework/z.cpp"}])))

    def test_non_utf8_bytes_are_a_value_error_the_verifier_records(self) -> None:
        with self.assertRaises(ValueError):
            pr.apply_bytes(b"\xff", [entry("pulp_a")], "framework/a.cpp")
        # A file the respell does not read is returned unchanged.
        self.assertEqual(pr.apply_bytes(b"\xff", [entry("pulp_a")], "framework/a.json"), b"\xff")


if __name__ == "__main__":
    unittest.main()
