#!/usr/bin/env python3
"""Negative controls for the Phase-6 debt inertness proof."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("verify_mirror_inertness", HERE / "verify_mirror_inertness.py")
assert spec and spec.loader
vi = importlib.util.module_from_spec(spec)
sys.modules["verify_mirror_inertness"] = vi
spec.loader.exec_module(vi)

GUARD = vi.GUARD
CLEAN_SYMBOLS = {
    "consumer": ["pulp::canvas::BidiAnalyzer::analyze(std::string_view)", "_main"],
    "libvellum-mirror-canvas.dylib": ["pulp::canvas::TextShaper::TextShaper()"],
}


class FindingTest(unittest.TestCase):
    def test_guard_in_a_compile_command_is_found(self) -> None:
        commands = [{"file": "a.cpp", "command": f"c++ -D{GUARD}=1 -c a.cpp"}]
        self.assertTrue(vi.compile_command_findings(commands))
        self.assertFalse(vi.compile_command_findings([{"file": "a.cpp", "command": "c++ -DOTHER=1 -c a.cpp"}]))

    def test_guard_exported_or_defined_by_the_package_is_found(self) -> None:
        exported = ("mirror/lib/cmake/VellumMirror/VellumMirrorTargets.cmake",
                    f'INTERFACE_COMPILE_DEFINITIONS "{GUARD}=1"')
        defined = ("mirror/include/pulp/view/config.hpp", f"#define {GUARD} 1\n")
        guarded = ("mirror/include/pulp/view/widgets.hpp", f"#if defined({GUARD})\n#endif\n")
        self.assertEqual(len(vi.package_definition_findings([exported, defined])), 2)
        # A header that only tests the guard is the debt itself, not a definition.
        self.assertFalse(vi.package_definition_findings([guarded]))

    def test_view_audio_header_in_a_consumer_or_the_package_is_found(self) -> None:
        for header in ("/p/mirror/include/pulp/view/audio_widgets.hpp",
                       "/p/mirror/include/pulp/view/meter.hpp",
                       "/p/mirror/include/pulp/view/audio/level.hpp",
                       "/p/mirror/include/pulp/view/widgets/graph_editor_view.hpp"):
            with self.subTest(header=header):
                self.assertTrue(vi.header_findings({"main.o": [header]}, []))
                self.assertTrue(vi.header_findings({}, [header]))
        self.assertFalse(vi.header_findings({"main.o": ["/p/mirror/include/pulp/canvas/canvas.hpp"]},
                                            ["mirror/include/pulp/view/widgets.hpp"]))

    def test_view_audio_symbols_are_found(self) -> None:
        planted = {"consumer": ["pulp::view::Meter::~Meter()"]}
        audio, forward = vi.symbol_findings(planted, view_unit_packaged=True,
                                            floor={"pulp::view::Meter::~Meter()"})
        self.assertTrue(audio)
        self.assertTrue(forward)
        registration = {"consumer": ["pulp::view::register_audio_widgets()"]}
        self.assertTrue(vi.symbol_findings(registration, view_unit_packaged=False)[0])
        product = {"consumer": ["pulp::signal::ColorMapper::interpolate(float)"]}
        self.assertTrue(vi.symbol_findings(product, view_unit_packaged=False)[0])

    def test_any_view_symbol_fails_while_no_view_unit_is_packaged(self) -> None:
        planted = {"consumer": ["pulp::view::Label::Label()"]}
        self.assertTrue(vi.symbol_findings(planted, view_unit_packaged=False)[0])
        self.assertFalse(vi.symbol_findings(planted, view_unit_packaged=True, floor={"x"})[0])
        self.assertFalse(vi.symbol_findings(CLEAN_SYMBOLS, view_unit_packaged=False)[0])

    def test_a_packaged_view_unit_needs_the_floor(self) -> None:
        with self.assertRaises(vi.EvidenceError):
            vi.symbol_findings(CLEAN_SYMBOLS, view_unit_packaged=True)

    def test_bind_virtuals_count_as_forward_declaration_references(self) -> None:
        planted = {"consumer": ["pulp::view::DesignHost::bind_meter(pulp::view::Meter&)"]}
        self.assertTrue(vi.symbol_findings(planted, view_unit_packaged=False)[1])

    def test_floor_splits_weak_definitions_out_and_counts_no_references(self) -> None:
        # A weak-only (W/V) definition is header-inline code, not view-audio
        # code of its own; U, w and v are references, never definitions.
        listing = (
            "lib.a[a.o]: __ZN4pulp4view5MeterD1Ev T 0 0\n"
            "lib.a[a.o]: __ZN4pulp4view5Meter5paintEv W 10 4\n"
            "lib.a[b.o]: __ZN4pulp4view5Meter6layoutEv W 20 8\n"
        )
        self.assertEqual(vi.floor_strength(listing), (
            {"__ZN4pulp4view5MeterD1Ev"},
            {"__ZN4pulp4view5Meter5paintEv", "__ZN4pulp4view5Meter6layoutEv"}))
        references = (
            "lib.a[a.o]: __ZN4pulp6canvas4PathD1Ev U\n"
            "lib.a[a.o]: __ZN4pulp4view4WeakEv w\n"
            "lib.a[b.o]: __ZTIN4pulp4view4WeakE v\n"
        )
        self.assertEqual(vi.floor_strength(references), (set(), set()))

    def test_floor_reads_nm_posix_member_lines(self) -> None:
        listing = (
            "lib.a[a.o]: __ZN4pulp4view5MeterD1Ev T 0 0\n"
            "lib.a[a.o]: __ZN4pulp6canvas4PathD1Ev U\n"
            "lib.a[a.o]: __ZN4pulp4view4WeakEv w\n"
            "lib.a[b.o]: __ZTVN4pulp4view5MeterE S 10 8\n"
            "not a symbol line\n"
        )
        self.assertEqual(vi.floor_strength(listing)[0],
                         {"__ZN4pulp4view5MeterD1Ev", "__ZTVN4pulp4view5MeterE"})


class VendoredNmSymbolsTest(unittest.TestCase):
    """nm_symbols.py is Pulp's file byte for byte, and the floor uses its contamination rule."""

    def setUp(self) -> None:
        self.pin = json.loads((HERE / "nm_symbols.pin.json").read_text(encoding="utf-8"))
        self.vendored = HERE / "nm_symbols.py"

    def test_the_vendored_file_matches_its_pin(self) -> None:
        data = self.vendored.read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        self.assertEqual(blob, self.pin["git_blob"])
        self.assertEqual(self.pin["vendored_path"], "tools/mirror/nm_symbols.py")
        self.assertEqual(self.pin["pulp_path"], "tools/scripts/nm_symbols.py")
        self.assertRegex(self.pin["pulp_commit"], r"^[0-9a-f]{40}$")

    def test_the_vendored_file_matches_pulp_when_a_checkout_is_given(self) -> None:
        root = os.environ.get("VELLUM_PULP_ROOT")
        if not root:
            self.skipTest("VELLUM_PULP_ROOT not set")
        pulp = subprocess.run(["git", "-C", root, "show",
                               f"{self.pin['pulp_commit']}:{self.pin['pulp_path']}"],
                              capture_output=True, check=True).stdout
        self.assertEqual(pulp, self.vendored.read_bytes())

    def test_one_strong_and_two_weak_definitions_split_by_strength(self) -> None:
        listing = (
            "lib.a[m.o]: __ZN4pulp4view5Meter9set_levelEff T 20 0\n"
            "lib.a[m.o]: __ZTIN4pulp4view5MeterE W 0 0\n"
            "lib.a[m.o]: __ZTVN4pulp4view5MeterE W 0 0\n"
            "lib.a[m.o]: __ZN4pulp4view4View5paintEv U 0 0\n"
            "lib.a[m.o]: __ZN4pulp4view4WeakEv w 0 0\n"
        )
        strong, weak = vi.floor_strength(listing)
        self.assertEqual(strong, {"__ZN4pulp4view5Meter9set_levelEff"})
        self.assertEqual(weak, {"__ZTIN4pulp4view5MeterE", "__ZTVN4pulp4view5MeterE"})


# GNU nm on an ELF archive: no leading underscore, `u` for a unique global
# (GNU extension, a definition the dynamic linker keeps one copy of) and `i`
# for an indirect function, both definitions; `w`/`v` and `U` are references.
ELF_FLOOR_LISTING = (
    "libpulp-view-audio.a[meter.cpp.o]: _ZN4pulp4view5Meter9set_levelEff T 0000000000000040 0000000000000010\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZTIN4pulp4view5MeterE V 0000000000000000 0000000000000018\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZTVN4pulp4view5MeterE W 0000000000000000 0000000000000030\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZZN4pulp4view5Meter8instanceEvE1s u 0000000000000000 0000000000000008\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZN4pulp4view13resolve_simdEv i 0000000000000080 0000000000000004\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZN4pulp4view4View5paintEv U\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZN4pulp4view4WeakEv w\n"
    "libpulp-view-audio.a[meter.cpp.o]: _ZN4pulp4view6WeakObjEv v\n"
)
ELF_FLOOR_STRONG = {
    "_ZN4pulp4view5Meter9set_levelEff",
    "_ZZN4pulp4view5Meter8instanceEvE1s", "_ZN4pulp4view13resolve_simdEv",
}
ELF_FLOOR_WEAK = {"_ZTIN4pulp4view5MeterE", "_ZTVN4pulp4view5MeterE"}


class ElfFloorTest(unittest.TestCase):
    """GNU unique (`u`) and indirect (`i`) definitions are strong; W/V are weak."""

    def test_vellum_counts_unique_and_indirect_definitions(self) -> None:
        self.assertEqual(vi.floor_strength(ELF_FLOOR_LISTING), (ELF_FLOOR_STRONG, ELF_FLOOR_WEAK))

    def test_pulp_reads_the_same_floor_when_a_checkout_is_given(self) -> None:
        root = os.environ.get("VELLUM_PULP_ROOT")
        if not root:
            self.skipTest("VELLUM_PULP_ROOT not set")
        pin = json.loads((HERE / "nm_symbols.pin.json").read_text(encoding="utf-8"))
        source = subprocess.run(["git", "-C", root, "show", f"{pin['pulp_commit']}:{pin['pulp_path']}"],
                                capture_output=True, check=True, text=True).stdout
        pulp: dict = {}
        exec(compile(source, pin["pulp_path"], "exec"), pulp)
        _, weak = pulp["strength"](ELF_FLOOR_LISTING)
        self.assertEqual(weak, vi.floor_strength(ELF_FLOOR_LISTING)[1])
        self.assertEqual(pulp["defined_any"](ELF_FLOOR_LISTING) - weak, ELF_FLOOR_STRONG)


# `nm -g -P` and `nm -g -m` of one Mach-O archive. Apple's nm prints the weak
# inline definition and the weak vtable with strong letters; only -m marks them.
MACHO_P = (
    "lib.a[engine.o]: __ZN4pulp5audio6Engine5startEv T 0 0\n"
    "lib.a[engine.o]: __ZN4pulp4view4View11on_attachedEv T 10 0\n"
    "lib.a[engine.o]: __ZTVN4pulp5audio6EngineE S 20 0\n"
    "lib.a[engine.o]: __ZTIN4pulp6canvas4PathE S 30 0\n"
    "lib.a[engine.o]: __ZN4pulp6canvas4PathD1Ev U\n"
)
MACHO_M = (
    "lib.a(engine.o):\n"
    "0000000000000000 (__TEXT,__text) external __ZN4pulp5audio6Engine5startEv\n"
    "0000000000000010 (__TEXT,__text) weak external automatically hidden __ZN4pulp4view4View11on_attachedEv\n"
    "0000000000000020 (__DATA,__const) weak external __ZTVN4pulp5audio6EngineE\n"
    "0000000000000030 (__DATA,__const) weak external __ZTIN4pulp6canvas4PathE\n"
    "                 (undefined) external __ZN4pulp6canvas4PathD1Ev\n"
)


class StrengthAwareFloorTest(unittest.TestCase):
    """Only strong floor symbols and RTTI of an owned class are view-audio code."""

    def setUp(self) -> None:
        self.floor = vi.build_floor(MACHO_P, MACHO_M)

    def findings(self, *names: str) -> list[str]:
        return vi.symbol_findings({"provider": list(names)}, view_unit_packaged=True, floor=self.floor)[0]

    def test_floor_reads_weakness_from_nm_m(self) -> None:
        self.assertEqual(set(self.floor), {"pulp::audio::Engine::start()"})
        self.assertEqual(self.floor.owned_classes, {"pulp::audio::Engine"})

    def test_weak_shared_inline_passes(self) -> None:
        self.assertEqual(self.findings("pulp::view::View::on_attached()"), [])

    def test_strong_copy_fails(self) -> None:
        self.assertEqual(self.findings("pulp::audio::Engine::start()"),
                         ["provider: pulp::audio::Engine::start()"])

    def test_weak_owned_rtti_fails(self) -> None:
        self.assertEqual(self.findings("vtable for pulp::audio::Engine", "typeinfo for pulp::canvas::Path"),
                         ["provider: vtable for pulp::audio::Engine"])


class NameDebtTest(unittest.TestCase):
    """Audio-named view-core symbols pass only under a declared Phase-6 debt."""

    def setUp(self) -> None:
        self.floor = vi.build_floor(MACHO_P, MACHO_M)

    def report(self, *names: str, floor: set[str] | None = None) -> dict:
        return vi.symbol_report({"provider": list(names)}, view_unit_packaged=True,
                                floor=self.floor if floor is None else floor, name_debts=vi.NAME_DEBTS)

    def test_declared_debt_hit_passes_and_is_listed(self) -> None:
        report = self.report("pulp::view::LevelDisplay::~LevelDisplay()",
                             "vtable for pulp::view::WaveformRecorder")
        self.assertEqual(report["audio"], [])
        self.assertEqual(report["debts"]["view-core-level-display"],
                         ["provider: pulp::view::LevelDisplay::~LevelDisplay()"])
        self.assertEqual(report["debts"]["view-core-waveform-recorder"],
                         ["provider: vtable for pulp::view::WaveformRecorder"])

    def test_undeclared_audio_named_symbol_fails(self) -> None:
        report = self.report("pulp::view::SpectrumAnalyzer::paint()",
                             "pulp::view::WaveformRecorderPanel::paint()")
        self.assertEqual(report["audio"], ["provider: pulp::view::SpectrumAnalyzer::paint()",
                                           "provider: pulp::view::WaveformRecorderPanel::paint()"])
        self.assertEqual(sum(len(hits) for hits in report["debts"].values()), 0)

    def test_floor_symbol_fails_even_when_a_debt_names_it(self) -> None:
        floor = vi.ViewAudioFloor({"pulp::view::LevelDisplay::~LevelDisplay()"})
        report = self.report("pulp::view::LevelDisplay::~LevelDisplay()", floor=floor)
        self.assertEqual(report["audio"], ["provider: pulp::view::LevelDisplay::~LevelDisplay()"])
        self.assertEqual(report["debts"]["view-core-level-display"], [])

    def test_forward_declaration_passes_only_under_its_debt(self) -> None:
        bind = ("pulp::view::NativeImportBindingContext::bind_meter(pulp::view::Meter&, "
                "pulp::view::NativeImportMeterBindingDescriptor const&)")
        report = self.report(bind, "pulp::view::Meter::~Meter()")
        self.assertEqual(report["forward"], ["provider: pulp::view::Meter::~Meter()"])
        self.assertEqual(report["debts"]["design-import-deprecated-bind-virtuals"], [f"provider: {bind}"])

    def test_every_debt_is_declared_for_phase_6_against_a_landed_file(self) -> None:
        repo = HERE.parents[1]
        for debt, entry in vi.NAME_DEBTS.items():
            with self.subTest(debt=debt):
                self.assertEqual(set(entry), {"rules", "pattern", "file", "owner"})
                self.assertEqual(entry["owner"], "Phase 6")
                self.assertTrue(set(entry["rules"]) <= {"no-audio-symbols", "name-only-forward-declarations"})
                self.assertTrue((repo / "mirror" / entry["file"]).is_file(), entry["file"])


class VendoredRttiOwnershipTest(unittest.TestCase):
    """rtti_ownership.py is Pulp's file byte for byte."""

    def setUp(self) -> None:
        self.pin = json.loads((HERE / "rtti_ownership.pin.json").read_text(encoding="utf-8"))
        self.vendored = HERE / "rtti_ownership.py"

    def test_the_vendored_file_matches_its_pin(self) -> None:
        data = self.vendored.read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        self.assertEqual(blob, self.pin["git_blob"])
        self.assertEqual(self.pin["vendored_path"], "tools/mirror/rtti_ownership.py")
        self.assertEqual(self.pin["pulp_path"], "tools/scripts/rtti_ownership.py")
        self.assertRegex(self.pin["pulp_commit"], r"^[0-9a-f]{40}$")

    def test_the_vendored_file_matches_pulp_when_a_checkout_is_given(self) -> None:
        root = os.environ.get("VELLUM_PULP_ROOT")
        if not root:
            self.skipTest("VELLUM_PULP_ROOT not set")
        pulp = subprocess.run(["git", "-C", root, "show",
                               f"{self.pin['pulp_commit']}:{self.pin['pulp_path']}"],
                              capture_output=True, check=True).stdout
        self.assertEqual(pulp, self.vendored.read_bytes())


class FloorAgreementTest(unittest.TestCase):
    """The stand-in rule covers every symbol Pulp's floor attributes to view-audio.

    The fixture is the floor's defined set for a real pulp-view-audio archive.
    """

    def setUp(self) -> None:
        fixture = HERE / "fixtures/view-audio-floor.txt"
        self.floor = [line for line in fixture.read_text(encoding="utf-8").splitlines()
                      if line and not line.startswith("#")]

    def test_fixture_is_a_real_floor(self) -> None:
        owned = [name for name in self.floor if vi.VIEW_AUDIO_OWNED.search(name)]
        self.assertGreater(len(owned), 300)
        self.assertTrue(any("register_audio_widgets" in name for name in owned))

    def test_stand_in_flags_every_owned_floor_symbol(self) -> None:
        owned = [name for name in self.floor if vi.VIEW_AUDIO_OWNED.search(name)]
        flagged, _ = vi.symbol_findings({"image": owned}, view_unit_packaged=False)
        self.assertEqual(len(flagged), len(owned))

    def test_floor_rule_ignores_shared_inline_definitions(self) -> None:
        shared = [name for name in self.floor if not vi.VIEW_AUDIO_OWNED.search(name)]
        self.assertTrue(any("pulp::canvas::" in name for name in shared))
        floor = {name for name in self.floor if vi.VIEW_AUDIO_OWNED.search(name)}
        flagged, _ = vi.symbol_findings({"image": shared}, view_unit_packaged=True, floor=floor)
        self.assertEqual(flagged, [f"image: {name}" for name in shared if vi.AUDIO_SYMBOL.search(name)])


    def test_depfile_and_ninja_listing_parse(self) -> None:
        self.assertEqual(vi.depfile_includes("a.o: a.cpp \\\n /x/b.hpp /x/c\\ d.hpp\n"),
                         ["a.cpp", "/x/b.hpp", "/x/c d.hpp"])
        listing = "a.o: #deps 2, deps mtime 1 (VALID)\n    a.cpp\n    /x/b.hpp\n\n"
        self.assertEqual(vi.ninja_deps(listing), {"a.o": ["a.cpp", "/x/b.hpp"]})
        with self.assertRaises(vi.EvidenceError):
            vi.ninja_deps("a.o: #deps 2, deps mtime 1 (STALE)\n    a.cpp\n")


class DemanglerTest(unittest.TestCase):
    """Image symbols go through the vendored rtti_ownership demangler."""

    def test_mach_o_and_elf_spellings_both_demangle(self) -> None:
        if not (vi.shutil.which("c++filt") or vi.shutil.which("llvm-cxxfilt")):
            self.skipTest("needs c++filt")
        mach_o, elf = "__ZN4pulp6canvas5Color8from_hsvENS0_3HSVEf", "_ZN4pulp6canvas5Color8from_hsvENS0_3HSVEf"
        out = vi.demangle([mach_o, elf, "_main"])
        self.assertTrue(out[0].startswith("pulp::canvas::Color::from_hsv("), out)
        self.assertTrue(out[1].startswith("pulp::canvas::Color::from_hsv("), out)
        self.assertEqual(len(out), 3)

    def test_a_demangler_that_fails_its_control_is_refused(self) -> None:
        with mock.patch.object(vi.rtti_ownership, "demangle",
                               side_effect=vi.rtti_ownership.DemangleError("control not demangled")):
            with self.assertRaisesRegex(vi.EvidenceError, "cannot demangle"):
                vi.demangle(["__ZN4pulp1xEv"])


class VerifyTest(unittest.TestCase):
    """End to end over a planted package and consumer, with symbols injected."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.prefix = root / "prefix"
        self.build = root / "build"
        mirror = self.prefix / "mirror"
        (mirror / "lib/cmake/VellumMirror").mkdir(parents=True)
        (mirror / "lib/cmake/VellumMirror/VellumMirrorTargets.cmake").write_text(
            'INTERFACE_COMPILE_DEFINITIONS "PULP_HAS_SKIA=1"\n')
        (mirror / "lib/libvellum-mirror-canvas.dylib").write_bytes(b"\0")
        self.provider = self.prefix / "lib/libvellum-skia-dawn-provider.dylib"
        self.provider.parent.mkdir(parents=True, exist_ok=True)
        self.provider.write_bytes(b"\0")
        (mirror / "include/pulp/canvas").mkdir(parents=True)
        (mirror / "include/pulp/canvas/canvas.hpp").write_text("#pragma once\n")
        (mirror / "share/provenance").mkdir(parents=True)
        (mirror / "share/provenance/receipt.json").write_text(json.dumps({
            "export_id": "x", "units": [{"name": "foundation"}, {"name": "canvas"}],
            "files": [{"pulp_path": "core/canvas/src/canvas.cpp"}, {"pulp_path": "core/runtime/src/log.cpp"}]}))
        self.build.mkdir()
        (self.build / "compile_commands.json").write_text(json.dumps([
            {"file": "main.cpp", "command": "c++ -DPULP_HAS_SKIA=1 -c main.cpp"}]))
        (self.build / "main.cpp.o.d").write_text(
            f"main.cpp.o: main.cpp {mirror}/include/pulp/canvas/canvas.hpp\n")
        self.image = self.build / "consumer"
        self.image.write_bytes(b"\0")
        self.symbols = {self.image: CLEAN_SYMBOLS["consumer"],
                        mirror / "lib/libvellum-mirror-canvas.dylib": CLEAN_SYMBOLS["libvellum-mirror-canvas.dylib"],
                        self.provider: ["SkFont::setSize(float)"]}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_verify(self, floor: set[str] | None = None,
                   install_libdir: str = "lib") -> dict:
        with mock.patch.object(vi, "image_symbols", side_effect=lambda path: self.symbols[path]), \
                mock.patch.object(vi, "view_audio_floor", side_effect=lambda archive: floor):
            return vi.verify(self.prefix, self.build, self.image,
                             Path("libpulp-view-audio.a") if floor is not None else None,
                             install_libdir)

    def test_a_packaged_plugin_view_host_reference_is_not_inert(self) -> None:
        clean = self.run_verify()
        self.assertTrue(clean["checks"]["no-pulp-kept-references"])
        self.assertEqual(clean["name_debts"]["view-core-plugin-view-host-declarations"]["matched"], 0)
        reference = "pulp::view::PluginViewHost::create(pulp::view::View&, pulp::view::Size)"
        self.symbols[self.provider] = [*self.symbols[self.provider], reference]
        proof = self.run_verify()
        self.assertFalse(proof["inert"])
        self.assertFalse(proof["checks"]["no-pulp-kept-references"])
        self.assertEqual(proof["name_debts"]["view-core-plugin-view-host-declarations"]["matched"], 1)

    def test_the_consumer_may_reference_plugin_view_host(self) -> None:
        reference = "pulp::view::register_platform_plugin_view_host()"
        self.symbols[self.image] = [*self.symbols[self.image], reference]
        self.assertTrue(self.run_verify()["checks"]["no-pulp-kept-references"])

    def test_clean_package_is_inert_and_records_debt_absence(self) -> None:
        proof = self.run_verify()
        self.assertTrue(proof["inert"], proof["findings"])
        self.assertFalse(proof["debts"]["vellum-header-audio-widgets-include"]["header_in_package"])

    def test_planted_guard_definition_is_not_inert(self) -> None:
        (self.build / "compile_commands.json").write_text(json.dumps([
            {"file": "main.cpp", "command": f"c++ -D{GUARD}=1 -c main.cpp"}]))
        self.assertFalse(self.run_verify()["checks"]["no-widgets-gate-definition"])

    def test_planted_audio_include_is_not_inert(self) -> None:
        depfile = self.build / "main.cpp.o.d"
        depfile.write_text(depfile.read_text().rstrip() + " /x/pulp/view/audio_widgets.hpp\n")
        self.assertFalse(self.run_verify()["checks"]["no-audio-headers"])

    def test_planted_audio_symbol_is_not_inert(self) -> None:
        self.symbols[self.image] = self.symbols[self.image] + ["pulp::view::WaveformView::paint()"]
        proof = self.run_verify()
        self.assertFalse(proof["checks"]["no-audio-symbols"])
        self.assertFalse(proof["checks"]["name-only-forward-declarations"])

    def test_provider_owned_audio_symbol_is_not_inert(self) -> None:
        self.symbols[self.provider].append("pulp::view::WaveformView::paint()")
        proof = self.run_verify()
        self.assertFalse(proof["checks"]["no-audio-symbols"])
        self.assertFalse(proof["checks"]["name-only-forward-declarations"])

    def test_nested_install_library_directory_is_scanned(self) -> None:
        nested_provider = self.prefix / "lib/vellum/libvellum-skia-dawn-provider.dylib"
        nested_provider.parent.mkdir(parents=True)
        nested_provider.write_bytes(b"\0")
        self.provider.unlink()
        self.symbols[nested_provider] = ["pulp::view::WaveformView::paint()"]
        proof = self.run_verify(install_libdir="lib/vellum")
        self.assertFalse(proof["checks"]["no-audio-symbols"])

    def test_a_so_provider_is_scanned(self) -> None:
        so_provider = self.provider.with_suffix(".so")
        so_provider.write_bytes(b"\0")
        self.provider.unlink()
        self.symbols[so_provider] = ["pulp::view::WaveformView::paint()"]
        proof = self.run_verify()
        self.assertFalse(proof["checks"]["no-audio-symbols"])

    def plant_debt_headers(self) -> list[Path]:
        include = self.prefix / "mirror/include/pulp/view"
        include.mkdir(parents=True)
        widgets = include / "widgets.hpp"
        widgets.write_text(
            f"#pragma once\n#if defined({GUARD})\n#include <pulp/view/audio_widgets.hpp>\n#endif\n")
        design = include / "design_import.hpp"
        design.write_text("#pragma once\nnamespace pulp::view {\nclass Meter;\nclass WaveformView;\n}\n")
        receipt = self.prefix / "mirror/share/provenance/receipt.json"
        data = json.loads(receipt.read_text())
        data["units"].append({"name": "view-core"})
        data["files"].append({"pulp_path": "core/view/include/pulp/view/widgets.hpp"})
        receipt.write_text(json.dumps(data))
        return [widgets, design]

    def test_shipped_debt_header_no_consumer_compiled_is_refused(self) -> None:
        self.plant_debt_headers()
        with self.assertRaises(vi.EvidenceError) as caught:
            self.run_verify()
        self.assertIn("no consumer translation unit compiled it", str(caught.exception))

    def test_shipped_debt_headers_compiled_by_the_consumer_are_judged(self) -> None:
        headers = self.plant_debt_headers()
        depfile = self.build / "probe.cpp.o.d"
        depfile.write_text("probe.cpp.o: probe.cpp " + " ".join(str(h) for h in headers) + "\n")
        commands = json.loads((self.build / "compile_commands.json").read_text())
        commands.append({"file": "probe.cpp", "command": "c++ -c probe.cpp"})
        (self.build / "compile_commands.json").write_text(json.dumps(commands))
        proof = self.run_verify(floor={"pulp::view::Meter::~Meter()"})
        self.assertTrue(proof["inert"], proof["findings"])
        self.assertEqual(proof["symbol_rule"], "view_audio_symbol_floor")
        self.assertEqual(proof["debts"]["vellum-header-audio-widgets-include"]["compiled_by"], ["probe.cpp.o.d"])

    def test_debt_probe_includes_every_shipped_debt_header(self) -> None:
        output = Path(self.temporary.name) / "probe.cpp"
        self.assertEqual(vi.write_debt_probe(self.prefix, output), [])
        self.assertNotIn("#include", output.read_text())
        self.plant_debt_headers()
        included = vi.write_debt_probe(self.prefix, output)
        self.assertEqual(included, sorted(vi.DEBTS.values()))
        for header in vi.DEBTS.values():
            self.assertIn(f"#include <{header}>", output.read_text())

    def set_receipt(self, **fields) -> None:
        receipt = self.prefix / "mirror/share/provenance/receipt.json"
        data = json.loads(receipt.read_text())
        data.update(fields)
        receipt.write_text(json.dumps(data))

    def test_view_source_is_read_from_file_rows_not_unit_names(self) -> None:
        # A unit that ships core/view source needs the floor whatever it is called.
        self.set_receipt(units=[{"name": "foundation"}, {"name": "ui-kit"}],
                         files=[{"pulp_path": "core/canvas/src/canvas.cpp"},
                                {"pulp_path": "core/view/src/view.cpp"}])
        with self.assertRaises(vi.EvidenceError) as caught:
            self.run_verify()
        self.assertIn("needs the pulp-view-audio archive", str(caught.exception))
        # A unit merely named for views, shipping no view source, does not.
        self.set_receipt(units=[{"name": "view-free-canvas"}],
                         files=[{"pulp_path": "core/canvas/src/canvas.cpp"}])
        self.assertTrue(self.run_verify()["inert"])

    def test_receipt_without_file_rows_is_refused(self) -> None:
        self.set_receipt(files=[])
        with self.assertRaises(vi.EvidenceError):
            self.run_verify()

    def test_shared_and_static_libraries_on_every_platform_are_read(self) -> None:
        lib = self.prefix / "mirror/lib"
        (lib / "libvellum-mirror-canvas.dylib").unlink()
        del self.symbols[lib / "libvellum-mirror-canvas.dylib"]
        for name in ("libvellum-mirror-canvas.so", "libvellum-mirror-runtime.a",
                     "vellum-mirror-events.dll", "libvellum-mirror-text.so.1"):
            (lib / name).write_bytes(b"\0")
            self.symbols[lib / name] = CLEAN_SYMBOLS["libvellum-mirror-canvas.dylib"]
        self.assertTrue(self.run_verify()["inert"])
        for name in ("libvellum-mirror-runtime.a", "vellum-mirror-events.dll", "libvellum-mirror-text.so.1"):
            with self.subTest(library=name):
                saved = self.symbols[lib / name]
                self.symbols[lib / name] = saved + ["pulp::view::Meter::paint()"]
                self.assertFalse(self.run_verify()["checks"]["no-audio-symbols"])
                self.symbols[lib / name] = saved

    def test_evidence_that_read_nothing_is_refused(self) -> None:
        (self.build / "main.cpp.o.d").unlink()
        with self.assertRaises(vi.EvidenceError):
            self.run_verify()

    def test_consumer_that_never_used_the_package_is_refused(self) -> None:
        (self.build / "main.cpp.o.d").write_text("main.cpp.o: main.cpp /usr/include/stdio.h\n")
        with self.assertRaises(vi.EvidenceError):
            self.run_verify()

    def test_image_without_packaged_symbols_is_refused(self) -> None:
        self.symbols[self.image] = ["_main"]
        with self.assertRaises(vi.EvidenceError):
            self.run_verify()


if __name__ == "__main__":
    unittest.main()
