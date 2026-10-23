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

    def test_floor_reads_archives_like_view_audio_symbol_floor(self) -> None:
        listing = (
            "lib.a[a.o]: __ZN4pulp4view5MeterD1Ev T 0 0\n"
            "lib.a[a.o]: __ZN4pulp6canvas4PathD1Ev U\n"
            "lib.a[a.o]: __ZN4pulp4view4WeakEv w\n"
            "lib.a[b.o]: __ZTVN4pulp4view5MeterE S 10 8\n"
            "not a symbol line\n"
        )
        self.assertEqual(vi.floor_defined(listing),
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

    def test_one_strong_and_two_weak_definitions_count_three(self) -> None:
        listing = (
            "lib.a[m.o]: __ZN4pulp4view5Meter9set_levelEff T 20 0\n"
            "lib.a[m.o]: __ZTIN4pulp4view5MeterE W 0 0\n"
            "lib.a[m.o]: __ZTVN4pulp4view5MeterE W 0 0\n"
            "lib.a[m.o]: __ZN4pulp4view4View5paintEv U 0 0\n"
            "lib.a[m.o]: __ZN4pulp4view4WeakEv w 0 0\n"
        )
        self.assertEqual(len(vi.floor_defined(listing)), 3)
        self.assertEqual(vi.floor_defined(listing), vi.nm_symbols.defined_any(listing))


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
ELF_FLOOR_DEFINED = {
    "_ZN4pulp4view5Meter9set_levelEff", "_ZTIN4pulp4view5MeterE", "_ZTVN4pulp4view5MeterE",
    "_ZZN4pulp4view5Meter8instanceEvE1s", "_ZN4pulp4view13resolve_simdEv",
}


class ElfFloorTest(unittest.TestCase):
    """GNU unique (`u`) and indirect (`i`) definitions belong to the floor."""

    def test_vellum_counts_unique_and_indirect_definitions(self) -> None:
        self.assertEqual(vi.floor_defined(ELF_FLOOR_LISTING), ELF_FLOOR_DEFINED)

    def test_pulp_reads_the_same_floor_when_a_checkout_is_given(self) -> None:
        root = os.environ.get("VELLUM_PULP_ROOT")
        if not root:
            self.skipTest("VELLUM_PULP_ROOT not set")
        pin = json.loads((HERE / "nm_symbols.pin.json").read_text(encoding="utf-8"))
        source = subprocess.run(["git", "-C", root, "show", f"{pin['pulp_commit']}:{pin['pulp_path']}"],
                                capture_output=True, check=True, text=True).stdout
        pulp: dict = {}
        exec(compile(source, pin["pulp_path"], "exec"), pulp)
        self.assertEqual(pulp["defined_any"](ELF_FLOOR_LISTING), vi.floor_defined(ELF_FLOOR_LISTING))
        self.assertEqual(pulp["defined_any"](ELF_FLOOR_LISTING), ELF_FLOOR_DEFINED)


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
    def fake_filt(self, working_flags):
        def run(arguments, **kwargs):
            flags = arguments[1:]
            mangled, expected = vi.DEMANGLE_CONTROL
            output = expected + "pulp::canvas::HSV, float)\n" if flags == working_flags else mangled + "\n"
            return mock.Mock(returncode=0, stdout=output)
        return run

    def test_only_an_invocation_that_demangles_the_control_is_used(self) -> None:
        with mock.patch.object(vi.shutil, "which", return_value="/usr/bin/c++filt"), \
                mock.patch.object(vi.subprocess, "run", side_effect=self.fake_filt(["-_"])):
            self.assertEqual(vi.demangler(), ["/usr/bin/c++filt", "-_"])

    def test_a_demangler_that_demangles_nothing_is_refused(self) -> None:
        with mock.patch.object(vi.shutil, "which", return_value="/usr/bin/c++filt"), \
                mock.patch.object(vi.subprocess, "run", side_effect=self.fake_filt(["--never"])):
            with self.assertRaises(vi.EvidenceError):
                vi.demangler()


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
            "export_id": "x", "units": [{"name": "foundation"}, {"name": "canvas"}]}))
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
