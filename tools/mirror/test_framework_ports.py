#!/usr/bin/env python3
"""The ports ledger's reading and replay, each rule with a passing case and a control."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("framework_ports", HERE / "framework_ports.py")
assert spec and spec.loader
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

COMMIT = "a" * 40


def row(path: str = "framework/core/a/src/new.cpp", change: str = "added", **overrides) -> dict:
    base = {"framework_path": path, "change": change,
            "target": "a-target" if change == "added" and fp.compiled(path) else None,
            "origin": "pulp", "pulp_path": path.removeprefix("framework/"), "pulp_commits": [COMMIT],
            "framework_blob": "b" * 40 if change == "added" else None, "reason": "port"}
    return {**base, **overrides}


def ledger(rows: list) -> str:
    return json.dumps({"schema": fp.SCHEMA, "rows": rows})


class RowShapeTest(unittest.TestCase):
    def assertRowRefused(self, candidate, fragment: str) -> None:
        errors = fp.row_errors(candidate, "row")
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_well_formed_rows_pass(self) -> None:
        for candidate in (
            row(),
            row("framework/core/a/include/vellum/a/new.hpp"),
            row("framework/core/a/src/old.cpp", "removed"),
            row(origin="native", pulp_path=None, pulp_commits=[], framework_blob=None),
            row(pulp_commits=[COMMIT, "b" * 40]),
        ):
            self.assertEqual(fp.row_errors(candidate, "row"), [], candidate)

    def test_keys_are_exact(self) -> None:
        self.assertRowRefused({**row(), "extra": 1}, "must have exactly")
        missing = row()
        del missing["reason"]
        self.assertRowRefused(missing, "must have exactly")
        self.assertRowRefused("framework/a.cpp", "must have exactly")

    def test_paths_stay_inside_framework(self) -> None:
        for path in ("graphics/a.cpp", "framework/../graphics/a.cpp", "/framework/a.cpp", "framework/./a.cpp",
                     "framework//a.cpp", "framework/a b.cpp", "framework/", "frameworkx/a.cpp", 7):
            self.assertRowRefused(row(target="t") | {"framework_path": path}, "not a safe path inside framework/")

    def test_third_party_source_is_not_ported_through_the_ledger(self) -> None:
        self.assertRowRefused(row("framework/external/x/x.c"), "third-party source")
        self.assertRowRefused(row("framework/external/x/x.c", "removed"), "third-party source")

    def test_change_origin_and_reason_are_closed_sets(self) -> None:
        self.assertRowRefused(row(change="edited"), "change must be one of")
        self.assertRowRefused(row(origin="upstream"), "origin must be one of")
        self.assertRowRefused(row(reason="  "), "reason must be a non-empty string")

    def test_a_pulp_row_names_full_distinct_commits(self) -> None:
        for commits in ([], [COMMIT[:12]], [COMMIT, COMMIT], COMMIT, ["A" * 40], [None]):
            self.assertRowRefused(row(pulp_commits=commits), "distinct full Pulp commit ids")
        self.assertRowRefused(row(pulp_path="../core/a.cpp"), "not a safe relative path")
        self.assertRowRefused(row(pulp_path=None), "not a safe relative path")

    def test_only_an_added_pulp_row_records_the_joining_blob(self) -> None:
        for blob in (None, "b" * 39, "B" * 40, 7):
            self.assertRowRefused(row(framework_blob=blob), "records the file's git blob id")
        self.assertRowRefused(row("framework/core/a/src/old.cpp", "removed", framework_blob="b" * 40),
                              "framework_blob is null unless")
        self.assertRowRefused(row(origin="native", pulp_path=None, pulp_commits=[]), "framework_blob is null unless")

    def test_a_native_row_names_no_pulp_source(self) -> None:
        self.assertRowRefused(row(origin="native"), "a native row has pulp_path null")
        self.assertRowRefused(row(origin="native", pulp_path=None), "a native row has pulp_path null")

    def test_only_an_added_compilable_source_names_a_target(self) -> None:
        for suffix in sorted(fp.SOURCE_SUFFIXES):
            self.assertRowRefused(row(f"framework/core/a/src/new{suffix}", target=None), "target must name")
        self.assertRowRefused(row(target="bad target"), "target must name")
        self.assertRowRefused(row("framework/core/a/include/vellum/a/new.hpp", target="a-target"),
                              "target is null unless")
        self.assertRowRefused(row("framework/core/a/src/old.cpp", "removed", target="a-target"),
                              "target is null unless")


class LedgerReadTest(unittest.TestCase):
    def test_document_shape(self) -> None:
        self.assertEqual(fp.parse(ledger([row()])), ([row()], []))
        for text in ("[", "[]", json.dumps({"schema": "other", "rows": []}),
                     json.dumps({"schema": fp.SCHEMA, "rows": {}}),
                     json.dumps({"schema": fp.SCHEMA, "rows": [], "note": ""})):
            rows, errors = fp.parse(text)
            self.assertEqual(rows, [])
            self.assertTrue(errors, text)

    def test_one_bad_row_withholds_every_row(self) -> None:
        rows, errors = fp.parse(ledger([row(), row(change="edited")]))
        self.assertEqual(rows, [])
        self.assertIn("rows[1]", errors[0])

    def test_a_missing_ledger_is_empty_and_a_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            self.assertEqual(fp.load(repo), ([], []))
            (repo / fp.LEDGER).parent.mkdir(parents=True)
            (repo / "elsewhere.json").write_text(ledger([row()]), encoding="utf-8")
            (repo / fp.LEDGER).symlink_to(repo / "elsewhere.json")
            self.assertEqual(fp.load(repo), ([], [f"{fp.LEDGER}: is a symlink"]))
            (repo / fp.LEDGER).unlink()
            (repo / fp.LEDGER).write_text(ledger([row()]), encoding="utf-8")
            self.assertEqual(fp.load(repo), ([row()], []))


class ReplayTest(unittest.TestCase):
    TRANSFERRED = {"framework/core/a/src/a.cpp", "framework/core/a/src/b.cpp"}

    def replay(self, rows: list) -> tuple:
        return fp.replay(rows, set(self.TRANSFERRED))

    def test_additions_and_removals_make_the_file_set(self) -> None:
        new, gone = "framework/core/a/src/new.cpp", "framework/core/a/src/a.cpp"
        added, removed, errors = self.replay([row(new), row(gone, "removed")])
        self.assertEqual((sorted(added), removed, errors), ([new], {gone}, []))
        self.assertEqual(added[new]["target"], "a-target")

    def test_a_transferred_file_may_only_be_removed_and_only_once(self) -> None:
        gone = "framework/core/a/src/a.cpp"
        _, _, errors = self.replay([row(gone)])
        self.assertIn("a ledger row may only remove it", errors[0])
        _, _, errors = self.replay([row(gone, "removed"), row(gone)])
        self.assertIn("a ledger row may only remove it", errors[0])
        _, _, errors = self.replay([row(gone, "removed"), row(gone, "removed")])
        self.assertIn("cannot be removed", errors[0])

    def test_a_path_is_added_only_while_absent_and_removed_only_while_present(self) -> None:
        new = "framework/core/a/src/new.cpp"
        _, _, errors = self.replay([row(new), row(new)])
        self.assertIn("already added", errors[0])
        _, _, errors = self.replay([row(new, "removed")])
        self.assertIn("cannot be removed", errors[0])
        # Control: add, remove, add again replays, ending added.
        added, removed, errors = self.replay([row(new), row(new, "removed"), row(new)])
        self.assertEqual((sorted(added), removed, errors), ([new], set(), []))

    def test_a_broken_ledger_never_widens_the_file_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / fp.LEDGER).parent.mkdir(parents=True)
            (repo / fp.LEDGER).write_text(ledger([row(), row("framework/core/a/src/a.cpp", "removed")]),
                                          encoding="utf-8")
            expected, added, removed, errors = fp.file_set(repo, set(self.TRANSFERRED))
            self.assertEqual(errors, [])
            self.assertEqual(expected, {"framework/core/a/src/b.cpp", "framework/core/a/src/new.cpp"})
            (repo / fp.LEDGER).write_text(ledger([row(), row()]), encoding="utf-8")
            expected, added, removed, errors = fp.file_set(repo, set(self.TRANSFERRED))
            self.assertTrue(errors)
            self.assertEqual((expected, added, removed), (self.TRANSFERRED, {}, set()))


LIB_SOURCE = "framework/core/example/src/example.cpp"
TOOL_SOURCE = "framework/core/example/src/example_tool.cpp"


def unit_target(name: str = "vellum-example", **overrides) -> dict:
    base = {"name": name, "type": "STATIC_LIBRARY", "unit": "example", "sources": [LIB_SOURCE],
            "include_dirs": ["core/example/include"], "compile_definitions": ["VELLUM_EXAMPLE=1"],
            "link_targets": ["pulp-runtime"], "reason": "a unit no closure builds"}
    return {**base, **overrides}


def tool_target(**overrides) -> dict:
    fields = {"type": "EXECUTABLE", "unit": None, "sources": [TOOL_SOURCE], "include_dirs": [],
              "compile_definitions": [], "link_targets": ["vellum-example"], **overrides}
    return unit_target("vellum-example-tool", **fields)


def manifest(targets: list) -> str:
    return json.dumps({"schema": fp.UNITS_SCHEMA, "targets": targets})


class UnitTargetShapeTest(unittest.TestCase):
    def assertTargetRefused(self, candidate, fragment: str) -> None:
        errors = fp.unit_target_errors(candidate, "target")
        self.assertTrue(any(fragment in error for error in errors), (candidate, errors))

    def test_well_formed_targets_pass(self) -> None:
        for candidate in (unit_target(), tool_target(), unit_target(compile_definitions=["A", "B=x.y"])):
            self.assertEqual(fp.unit_target_errors(candidate, "target"), [], candidate)

    def test_keys_are_exact(self) -> None:
        self.assertTargetRefused({**unit_target(), "extra": 1}, "must have exactly")
        missing = unit_target()
        del missing["link_targets"]
        self.assertTargetRefused(missing, "must have exactly")

    def test_name_type_and_unit(self) -> None:
        self.assertTargetRefused(unit_target("bad name"), "is not a target name")
        self.assertTargetRefused(unit_target(type="SHARED_LIBRARY"), "type must be one of")
        self.assertTargetRefused(unit_target(unit=None), "names the new unit")
        self.assertTargetRefused(tool_target(unit="example"), "an executable belongs to no unit")

    def test_a_new_target_carries_no_pulp_name(self) -> None:
        self.assertTargetRefused(unit_target("pulp-example"), "may not carry a Pulp name")
        self.assertTargetRefused(unit_target(unit="pulp-scene"), "may not carry a Pulp name")

    def test_sources_are_compilable_framework_paths(self) -> None:
        for sources in ([], [LIB_SOURCE, LIB_SOURCE], "x.cpp", None):
            self.assertTargetRefused(unit_target(sources=sources), "sources must list")
        for source in ("graphics/a.cpp", "framework/../a.cpp", "framework/core/example/include/e.hpp"):
            self.assertTargetRefused(unit_target(sources=[source]), "is not a compilable path inside")

    def test_include_dirs_definitions_links_and_reason(self) -> None:
        self.assertTargetRefused(unit_target(include_dirs=["../graphics"]), "is not a safe path")
        self.assertTargetRefused(unit_target(include_dirs="core"), "include_dirs must be a list")
        self.assertTargetRefused(unit_target(compile_definitions=["-DX"]), "compile_definitions must be")
        self.assertTargetRefused(unit_target(compile_definitions=["X=a b"]), "compile_definitions must be")
        self.assertTargetRefused(unit_target(link_targets=["a", "a"]), "link_targets must be")
        self.assertTargetRefused(unit_target(link_targets=["vellum-example"]), "links itself")
        self.assertTargetRefused(unit_target(reason=" "), "reason must be")

    def test_reserved_definitions_are_refused(self) -> None:
        for item in ("VELLUM_MIRROR_BUILD", "VELLUM_MIRROR_BUILD=1", "GPU_RUNTIME_BENCHMARK=1", "GPU_RUNTIME_X"):
            self.assertTargetRefused(unit_target(compile_definitions=[item]), "is reserved")
        self.assertEqual(fp.unit_target_errors(unit_target(compile_definitions=["VELLUM_MIRROR_BUILDER=1"]), "t"), [])

    def test_document_shape_and_one_bad_target_withholds_all(self) -> None:
        for text in ("[", json.dumps({"schema": "other", "targets": []}),
                     json.dumps({"schema": fp.UNITS_SCHEMA, "targets": {}}),
                     json.dumps({"schema": fp.UNITS_SCHEMA, "targets": [], "note": ""})):
            self.assertEqual(fp.parse_units(text)[0], [])
            self.assertTrue(fp.parse_units(text)[1], text)
        targets, errors = fp.parse_units(manifest([unit_target(), unit_target(type="X")]))
        self.assertEqual(targets, [])
        self.assertIn("targets[1]", errors[0])
        self.assertEqual(fp.parse_units(manifest([unit_target()])), ([unit_target()], []))


class DeclaredUnitsTest(unittest.TestCase):
    """A declared target agrees with the ledger, source by source, and links only what is declared before it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        (self.repo / "framework/core/example/include").mkdir(parents=True)
        self.added = {LIB_SOURCE: row(LIB_SOURCE, target="vellum-example"),
                      TOOL_SOURCE: row(TOOL_SOURCE, target="vellum-example-tool")}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, targets: list) -> None:
        (self.repo / fp.UNITS).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / fp.UNITS).write_text(manifest(targets), encoding="utf-8")

    def assertDeclaredRefused(self, targets: list, fragment: str) -> None:
        self.write(targets)
        declared, errors = fp.declared_units(self.repo, self.added)
        self.assertEqual(declared, [])
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_a_missing_manifest_declares_nothing_and_a_symlink_is_refused(self) -> None:
        self.assertEqual(fp.declared_units(self.repo, {}), ([], []))
        (self.repo / fp.UNITS).parent.mkdir(parents=True)
        (self.repo / "elsewhere.json").write_text(manifest([]), encoding="utf-8")
        (self.repo / fp.UNITS).symlink_to(self.repo / "elsewhere.json")
        self.assertEqual(fp.declared_units(self.repo, {}), ([], [f"{fp.UNITS}: is a symlink"]))

    def test_matching_targets_are_declared(self) -> None:
        self.write([unit_target(), tool_target()])
        self.assertEqual(fp.declared_units(self.repo, self.added), ([unit_target(), tool_target()], []))

    def test_a_source_needs_its_ledger_row_with_this_target(self) -> None:
        del self.added[TOOL_SOURCE]
        self.assertDeclaredRefused([unit_target(), tool_target()], "has no ports ledger row adding it")
        self.added[TOOL_SOURCE] = row(TOOL_SOURCE, target="vellum-example")
        self.assertDeclaredRefused([unit_target(), tool_target()], "is added by its ledger row to vellum-example")

    def test_a_ledger_row_for_a_declared_target_must_be_listed(self) -> None:
        self.assertDeclaredRefused([unit_target(), tool_target(sources=[LIB_SOURCE])],
                                   "does not list it in")

    def test_a_declared_link_names_an_earlier_library(self) -> None:
        self.assertDeclaredRefused([tool_target(), unit_target()], "which is declared after it")
        tool = tool_target()
        self.assertDeclaredRefused([unit_target(), tool, unit_target("vellum-other", sources=[TOOL_SOURCE],
                                                                     link_targets=["vellum-example-tool"])],
                                   "links the executable")
        self.assertDeclaredRefused([unit_target(), unit_target()], "is declared twice")

    def test_an_include_directory_must_exist_under_framework(self) -> None:
        self.assertDeclaredRefused([unit_target(include_dirs=["core/missing/include"]), tool_target()],
                                   "is not a directory framework/ holds")



class PlatformScopeTest(unittest.TestCase):
    """Schema v2: a compiled source carries the platforms it is compiled on."""

    SRC = "framework/core/a/src/new.cpp"

    def scoped(self, platforms, path: str = SRC, **overrides) -> dict:
        fields = {"target": "a-target", "framework_blob": None, "platforms": platforms, **overrides}
        return row(path, "scoped", **fields)

    def errors(self, candidate) -> list:
        return fp.row_errors(candidate, "row")

    def assertRefused(self, candidate, fragment: str) -> None:
        errors = self.errors(candidate)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_a_compiled_row_may_name_known_platforms(self) -> None:
        self.assertEqual(self.errors(row(platforms=["linux-x64", "macos-arm64"])), [])
        self.assertEqual(self.errors(self.scoped(["windows-x64"])), [])

    def test_platform_lists_are_checked(self) -> None:
        self.assertRefused(row(platforms=["beos-ppc"]), "unknown platform")
        self.assertRefused(row(platforms=[]), "non-empty list")
        self.assertRefused(row(platforms=["linux-x64", "linux-x64"]), "non-empty list")
        self.assertRefused(row(platforms="linux-x64"), "non-empty list")

    def test_only_compiled_sources_carry_a_scope(self) -> None:
        self.assertRefused(row("framework/core/a/include/vellum/a/new.hpp", platforms=["linux-x64"]),
                           "platforms scope only a compilable source")
        self.assertRefused(row("framework/core/a/src/old.cpp", "removed", platforms=["linux-x64"]),
                           "platforms scope only a compilable source")
        self.assertRefused(self.scoped(["linux-x64"], path="framework/core/a/include/a.hpp", target=None),
                           "only a compilable source can be scoped")

    def test_a_scoped_row_names_its_platforms_and_target(self) -> None:
        missing = self.scoped(["linux-x64"])
        del missing["platforms"]
        self.assertRefused(missing, "names the platforms")
        self.assertRefused(self.scoped(["linux-x64"], target=None), "target must name")
        self.assertRefused(self.scoped(["linux-x64"], framework_blob="b" * 40), "framework_blob is null")

    def test_a_v1_document_holds_no_scope(self) -> None:
        document = json.dumps({"schema": fp.SCHEMA_V1, "rows": [row(platforms=["linux-x64"])]})
        rows, errors = fp.parse(document)
        self.assertEqual(rows, [])
        self.assertTrue(any("needs schema" in error for error in errors), errors)
        document = json.dumps({"schema": fp.SCHEMA_V1, "rows": [row(), self.scoped(["linux-x64"])]})
        self.assertTrue(any("needs schema" in error for error in fp.parse(document)[1]))
        # Control: the landed v1 shape still reads, and v2 reads the same rows with a scope.
        self.assertEqual(fp.parse(json.dumps({"schema": fp.SCHEMA_V1, "rows": [row()]}))[1], [])
        self.assertEqual(fp.parse(ledger([row(platforms=["linux-x64"])]))[1], [])

    def test_the_committed_ledger_reads_the_same_as_v1_and_v2(self) -> None:
        # The ledger moved from v1 to v2 with its rows unchanged: every
        # unscoped row reads identically under both schemas, and a scope is
        # refused only under v1.
        repo = HERE.parent.parent
        committed = json.loads((repo / fp.LEDGER).read_text(encoding="utf-8"))
        self.assertEqual(committed["schema"], fp.SCHEMA)
        unscoped = [r for r in committed["rows"] if "platforms" not in r and r["change"] != "scoped"]
        self.assertGreaterEqual(len(unscoped), 29)
        as_v1 = fp.parse(json.dumps({"schema": fp.SCHEMA_V1, "rows": unscoped}))
        as_v2 = fp.parse(json.dumps({"schema": fp.SCHEMA, "rows": unscoped}))
        self.assertEqual(as_v1[1], [])
        self.assertEqual(as_v1, as_v2)
        self.assertEqual(as_v2[0], unscoped)
        # Control: a v1 document carrying a scoped row is refused, so the equality
        # above is not the parser accepting anything.
        scoped = [dict(unscoped[0], platforms=["linux-x64"])] if fp.compiled(unscoped[0]["framework_path"]) else \
            [row(platforms=["linux-x64"])]
        self.assertTrue(fp.parse(json.dumps({"schema": fp.SCHEMA_V1, "rows": scoped}))[1])

    def test_replay_scopes_each_added_source(self) -> None:
        header = row("framework/core/a/include/vellum/a/new.hpp")
        added, _, scopes, errors = fp.replay_scoped([row(), header], set())
        self.assertEqual(errors, [])
        self.assertEqual(scopes, {self.SRC: frozenset({"macos-arm64"})})
        self.assertIn(header["framework_path"], added)
        _, _, scopes, _ = fp.replay_scoped([row(platforms=["linux-x64"])], set())
        self.assertEqual(scopes[self.SRC], frozenset({"linux-x64"}))

    def test_a_scoped_row_replaces_the_scope_and_keeps_the_adding_row(self) -> None:
        adding = row(platforms=["linux-x64"])
        added, _, scopes, errors = fp.replay_scoped([adding, self.scoped(["linux-x64", "windows-x64"])], set())
        self.assertEqual(errors, [])
        self.assertEqual(scopes[self.SRC], frozenset({"linux-x64", "windows-x64"}))
        # The joining-blob check keys on the adding row; a scope change never replaces it.
        self.assertIs(added[self.SRC], adding)

    def test_a_scoped_row_needs_a_source_the_ledger_adds_to_the_same_target(self) -> None:
        _, _, scopes, errors = fp.replay_scoped([self.scoped(["linux-x64"])], set())
        self.assertTrue(any("cannot be scoped" in error for error in errors), errors)
        _, _, _, errors = fp.replay_scoped([self.scoped(["linux-x64"])], {self.SRC})
        self.assertTrue(any("cannot be scoped" in error for error in errors), errors)  # a transferred file
        _, _, _, errors = fp.replay_scoped([row(), self.scoped(["linux-x64"], target="b-target")], set())
        self.assertTrue(any("cannot move it" in error for error in errors), errors)

    def test_removing_and_re_adding_resets_the_scope(self) -> None:
        rows = [row(platforms=["linux-x64"]), self.scoped(["windows-x64"]), row(change="removed"), row()]
        _, _, scopes, errors = fp.replay_scoped(rows, set())
        self.assertEqual(errors, [])
        self.assertEqual(scopes[self.SRC], frozenset({"macos-arm64"}))
        _, removed, scopes, _ = fp.replay_scoped(rows[:3], set())
        self.assertNotIn(self.SRC, scopes)
        self.assertIn(self.SRC, removed)

    def test_a_scope_change_cannot_leave_the_macos_build(self) -> None:
        # The macOS closure is the only one landed: leaving it would leave the
        # source compiled nowhere a lane checks.
        for adding in (row(), row(platforms=["macos-arm64", "linux-x64"])):
            _, _, scopes, errors = fp.replay_scoped([adding, self.scoped(["linux-x64"])], set())
            self.assertTrue(any("cannot take it out of the macos-arm64 build" in e for e in errors), errors)
            # Control: a scope change that keeps macOS is accepted.
            _, _, scopes, errors = fp.replay_scoped([adding, self.scoped(["macos-arm64", "wasm32"])], set())
            self.assertEqual(errors, [])
            self.assertEqual(scopes[self.SRC], frozenset({"macos-arm64", "wasm32"}))
        # A source never compiled on macOS may gain it, then cannot lose it again.
        rows = [row(platforms=["linux-x64"]), self.scoped(["linux-x64", "macos-arm64"])]
        self.assertEqual(fp.replay_scoped(rows, set())[3], [])
        errors = fp.replay_scoped(rows + [self.scoped(["linux-x64"])], set())[3]
        self.assertTrue(any("cannot take it out" in e for e in errors), errors)

    def test_a_removal_cannot_carry_a_source_out_of_the_macos_build(self) -> None:
        removed = row(change="removed")
        for readd in (row(platforms=["wasm32"]), row(platforms=["linux-x64"])):
            errors = fp.replay_scoped([row(), removed, readd], set())[3]
            self.assertTrue(any("comes back only with macos-arm64" in e for e in errors), errors)
        # Also after a scope change brought it into macOS.
        rows = [row(platforms=["linux-x64"]), self.scoped(["linux-x64", "macos-arm64"]), removed,
                row(platforms=["linux-x64"])]
        self.assertTrue(any("comes back only" in e for e in fp.replay_scoped(rows, set())[3]))
        # Controls: back with macOS is fine; a source never on macOS may come back off it.
        self.assertEqual(fp.replay_scoped([row(), removed, row(platforms=["macos-arm64", "wasm32"])],
                                          set())[3], [])
        self.assertEqual(fp.replay_scoped([row(platforms=["linux-x64"]), removed, row(platforms=["wasm32"])],
                                          set())[3], [])

    def test_a_scoped_row_makes_no_provenance_claim_of_its_own(self) -> None:
        adding = row(platforms=["linux-x64"])
        for other, fragment in (({"pulp_path": "core/unrelated/x.cpp"}, "a scope change names the same"),
                                ({"origin": "native", "pulp_path": None, "pulp_commits": []},
                                 "a scope change names the same")):
            errors = fp.replay_scoped([adding, self.scoped(["windows-x64"], **other)], set())[3]
            self.assertTrue(any(fragment in e for e in errors), (other, errors))
        errors = fp.replay_scoped([adding, self.scoped(["windows-x64"], pulp_commits=["c" * 40])], set())[3]
        self.assertTrue(any("a scope change names the same" in e for e in errors), errors)
        # Control: the adding row's origin, Pulp path and commits.
        self.assertEqual(fp.replay_scoped([adding, self.scoped(["windows-x64"])], set())[3], [])

    def test_the_landed_ledger_scopes_only_rows_that_name_a_platform(self) -> None:
        # A row that names no scope keeps compiling exactly where it did, on
        # macOS; a source compiles elsewhere only when its adding row says so.
        repo = HERE.parents[1]
        rows, errors = fp.load(repo)
        self.assertEqual(errors, [])
        added = {r["framework_path"]: r for r in rows
                 if r["change"] == "added" and fp.compiled(r["framework_path"])}
        self.assertTrue(added, "the landed ledger adds no compiled source; this check sees nothing")
        _, _, scopes, replay_errors = fp.replay_scoped(rows, set())
        self.assertEqual(replay_errors, [])
        unscoped = [path for path, r in added.items() if "platforms" not in r]
        self.assertTrue(unscoped, "no unscoped compiled row; this check sees nothing")
        self.assertEqual({scopes[path] for path in unscoped}, {frozenset({"macos-arm64"})})
        for path, r in added.items():
            if "platforms" in r:
                self.assertEqual(scopes[path], frozenset(r["platforms"]))
                self.assertNotIn("macos-arm64", scopes[path], path)

    def test_scopes_reads_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / fp.LEDGER).parent.mkdir(parents=True)
            (repo / fp.LEDGER).write_text(ledger([row(platforms=["linux-x64"])]), encoding="utf-8")
            scopes, errors = fp.scopes(repo, set())
            self.assertEqual(errors, [])
            self.assertTrue(fp.compiled_on(scopes, self.SRC, "linux-x64"))
            self.assertFalse(fp.compiled_on(scopes, self.SRC, "macos-arm64"))
            # A broken ledger scopes nothing.
            (repo / fp.LEDGER).write_text(ledger([row(platforms=["nope"])]), encoding="utf-8")
            self.assertEqual(fp.scopes(repo, set())[0], {})

    def test_declared_targets_carry_a_covered_scope(self) -> None:
        target = {"name": "vellum-a", "type": "STATIC_LIBRARY", "unit": "a", "sources": [self.SRC],
                  "include_dirs": [], "compile_definitions": [], "link_targets": [], "reason": "r",
                  "platforms": ["linux-x64"]}
        self.assertEqual(fp.unit_target_errors(target, "t"), [])
        self.assertTrue(fp.unit_target_errors({**target, "platforms": ["nope"]}, "t"))
        added = {self.SRC: row(target="vellum-a", platforms=["linux-x64"])}
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.assertEqual(fp.units_errors(repo, [target], added), [])
            wider = {**target, "platforms": ["linux-x64", "macos-arm64"]}
            self.assertTrue(any("is not scoped" in e for e in fp.units_errors(repo, [wider], added)))
            # An unscoped target means macOS, which this source is not scoped to.
            plain = {k: v for k, v in target.items() if k != "platforms"}
            self.assertTrue(any("is not scoped" in e for e in fp.units_errors(repo, [plain], added)))
            # A later scoped row widens the source, so the wider target is covered.
            widened = {self.SRC: frozenset({"linux-x64", "macos-arm64"})}
            self.assertEqual(fp.units_errors(repo, [wider], added, widened), [])
        # A declared target links only declared targets that cover its platforms.
        lib_b = {**target, "name": "vellum-b", "unit": "b", "sources": [], "link_targets": ["vellum-a"],
                 "platforms": ["linux-x64", "macos-arm64"]}
        with tempfile.TemporaryDirectory() as directory:
            errors = fp.units_errors(Path(directory), [target, lib_b], added)
            self.assertTrue(any("where the target it links, vellum-a, is not" in e for e in errors), errors)
            lib_b["platforms"] = ["linux-x64"]
            self.assertFalse(any("target it links" in e for e in fp.units_errors(Path(directory),
                                                                                    [target, lib_b], added)))
        v1 = json.dumps({"schema": fp.UNITS_SCHEMA_V1, "targets": [target]})
        self.assertTrue(any("needs schema" in e for e in fp.parse_units(v1)[1]))


if __name__ == "__main__":
    unittest.main()
