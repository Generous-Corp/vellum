#!/usr/bin/env python3
"""Tests for the installed-dylib symbol census and runtime identity."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("symbol_census", HERE / "symbol_census.py")
assert spec and spec.loader
sc = importlib.util.module_from_spec(spec)
sys.modules["symbol_census"] = sc
spec.loader.exec_module(sc)

ANCHOR = "_vellum_mirror_forwarding_anchor"
FN = "__ZN6vellum8graphics11clip_extentEv"
PULP_FN = "__ZN4pulp4view4drawEv"


def listing(*rows: str) -> str:
    return "\n".join(rows) + "\n"


def sym(name: str, attrs: str = "external") -> str:
    return f"0000000000001000 (__TEXT,__text) {attrs} {name}"


def demangle(names: list[str]) -> dict[str, str]:
    table = {FN: "vellum::graphics::clip_extent()", PULP_FN: "pulp::view::draw()", ANCHOR: ANCHOR}
    return {n: table.get(n, n) for n in names}


def run(nm_by_image: dict[str, str], otool_by_image: dict[str, str] | None = None, allow=frozenset()):
    images = [Path(name) for name in nm_by_image]
    otool_by_image = otool_by_image or {}
    return sc.census(images, allow, nm=lambda p: nm_by_image[str(p)], otool=lambda p: otool_by_image.get(str(p), ""),
                     demangle=demangle)


def stripped_classes(*names: str) -> str:
    """Entries with no _OBJC_CLASS_$_ symbol: the name survives only in class_ro_t."""
    rows = ["Contents of (__DATA_CONST,__objc_classlist) section"]
    for n in names:
        rows += ["0000000000004000 0x5000", "    isa        0x5028", "    data       0x4000",
                 "        flags          0x0", f"        name           0x9000 {n}", "        baseMethods    0x0",
                 "            name    0x55df8 (0x1f0120)"]
    return "\n".join(rows) + "\n"


def classes(*names: str) -> str:
    rows = ["Contents of (__DATA,__objc_classlist) section"]
    rows += [f"0000000000004000 0x5000 _OBJC_CLASS_$_{n}" for n in names]
    return "\n".join(rows) + "\n"


class MirrorRuntimeIdentityTest(unittest.TestCase):
    def test_two_runtime_identities_use_disjoint_macho_and_objc_names(self) -> None:
        cmake = (HERE / "VellumMirror.cmake").read_text(encoding="utf-8")
        self.assertIn('OUTPUT_NAME "vellum-mirror-${_vellum_mirror_unit}${VELLUM_RUNTIME_IDENTITY_SUFFIX}"', cmake)
        self.assertIn('OUTPUT_NAME "vellum-skia-dawn-provider${VELLUM_RUNTIME_IDENTITY_SUFFIX}"', cmake)
        self.assertIn("PULP_VIEW_OBJC_SUFFIX=${VELLUM_RUNTIME_OBJC_SUFFIX}", cmake)
        self.assertIn("string(SHA256 _vellum_runtime_identity_hash", cmake)

    @unittest.skipUnless(os.environ.get("VELLUM_RUNTIME_IDENTITY_BUILD"),
                         "requires a built macOS mirror runtime")
    def test_dylib_and_objc_names_include_build_identity(self) -> None:
        build = Path(os.environ["VELLUM_RUNTIME_IDENTITY_BUILD"])
        identity = os.environ["VELLUM_RUNTIME_IDENTITY_TAG"]
        objc_suffix = "_" + identity.replace("-", "_").replace(".", "_")
        images = sorted(build.glob("libvellum-mirror-*.dylib"))
        self.assertTrue(images, f"no mirror dylibs in {build}")
        providers = sorted(build.glob("libvellum-skia-dawn-provider*.dylib"))
        self.assertEqual(len(providers), 1, f"expected one provider in {build}")

        findings = []
        classes_seen = set()
        for image in [*images, *providers]:
            result = subprocess.run(["otool", "-D", str(image)], check=True,
                                    capture_output=True, text=True)
            _, install_name = result.stdout.splitlines()
            if install_name != f"@rpath/{image.name}" or not image.name.endswith(f"-{identity}.dylib"):
                findings.append(f"{image.name}: {install_name}")
            for name in sorted(sc.objc_classes(sc.run_otool(image))):
                classes_seen.add(name)
                if not name.endswith(objc_suffix):
                    findings.append(f"{image.name}: Objective-C class {name}")

        self.assertTrue(any(name.startswith("VellumMirrorMetalView") for name in classes_seen),
                        "the selected dylibs do not define the plug-in's Metal view base class")
        if findings:
            self.fail("\n".join(findings))


class SymbolCensusTest(unittest.TestCase):
    def test_clean_images_pass(self) -> None:
        report, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(sym(ANCHOR.replace("anchor", "other")))})
        self.assertEqual(findings, [])
        self.assertEqual(report["vellum_definitions"], 1)

    def test_pulp_definition_fails(self) -> None:
        _, findings = run({"a.dylib": listing(sym(FN), sym(PULP_FN))})
        self.assertEqual(len(findings), 1)
        self.assertIn("pulp::", findings[0])

    def test_hidden_pulp_definition_still_fails(self) -> None:
        # A private-external pulp:: symbol is still left over from the rename.
        _, findings = run({"a.dylib": listing(sym(FN), sym(PULP_FN, "private external"))})
        self.assertEqual(len(findings), 1)

    def test_local_pulp_definition_fails(self) -> None:
        # `nm -g` omits local symbols; the census reads the full table, so a local
        # pulp:: function beside an exported vellum:: one is still caught.
        _, findings = run({"a.dylib": listing(sym(FN), sym(PULP_FN, ""))})
        self.assertEqual(len(findings), 1)
        self.assertIn("pulp::", findings[0])

    def test_nm_reads_the_full_symbol_table(self) -> None:
        calls: list[list[str]] = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(sc.subprocess, "run", fake):
            sc.run_nm(Path("a.dylib"))
        self.assertNotIn("-g", calls[0])

    def test_failed_otool_cannot_pass(self) -> None:
        failed = subprocess.CompletedProcess(["otool"], 1, stdout=classes("Menu"), stderr="truncated file")
        with mock.patch.object(sc.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "otool failed for a.dylib: truncated file"):
                sc.run_otool(Path("a.dylib"))

    def _fake_run(self, otool_rc: int = 0, filt_rc: int = 0, filt_lines: int | None = None, calls: list | None = None):
        nm_out = listing(sym(FN))

        def fake(cmd, **kw):
            if calls is not None:
                calls.append(cmd[0])
            if cmd[0] == "nm":
                return mock.Mock(returncode=0, stdout=nm_out, stderr="")
            if cmd[0] == "otool":
                return mock.Mock(returncode=otool_rc, stdout="", stderr="otool: bad file")
            names = kw["input"].splitlines()
            keep = names if filt_lines is None else names[:filt_lines]
            return mock.Mock(returncode=filt_rc, stdout="\n".join("vellum::" + n for n in keep), stderr="c++filt: bad")
        return fake

    def _main(self, fake) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "a.dylib"
            image.write_bytes(b"x")
            with mock.patch.object(sc.subprocess, "run", fake), contextlib.redirect_stderr(io.StringIO()):
                return sc.main([str(image)])

    def test_main_succeeds_when_every_tool_succeeds(self) -> None:
        calls: list = []
        self.assertEqual(self._main(self._fake_run(calls=calls)), 0)
        self.assertIn("otool", calls)

    def test_main_is_blind_when_otool_fails(self) -> None:
        calls: list = []
        self.assertEqual(self._main(self._fake_run(otool_rc=1, calls=calls)), 3)
        self.assertIn("otool", calls)

    def test_main_is_blind_when_demangler_fails(self) -> None:
        self.assertEqual(self._main(self._fake_run(filt_rc=1)), 3)

    def test_truncated_demangler_output_is_rejected(self) -> None:
        # Two names in, one out: zip would silently leave one mangled and unsearched.
        fake = self._fake_run(filt_lines=1)
        with mock.patch.object(sc.subprocess, "run", fake):
            with self.assertRaises(RuntimeError):
                sc.run_demangle([FN, PULP_FN])

    def test_failed_demangler_on_pulp_image_cannot_hide_it_behind_a_good_image(self) -> None:
        good, bad = Path("good.dylib"), Path("bad.dylib")
        nm = {str(good): listing(sym(FN)), str(bad): listing(sym(PULP_FN))}

        def flaky(names):
            if PULP_FN in names:
                raise RuntimeError("c++filt failed")
            return demangle(names)

        with self.assertRaises(RuntimeError):
            sc.census([good, bad], nm=lambda p: nm[str(p)], otool=lambda p: "", demangle=flaky)

    def test_annotated_strong_duplicate_is_still_a_duplicate(self) -> None:
        annotated = sym(FN, "external [referenced dynamically]")
        nm = {"a.dylib": listing(annotated, sym("_a")), "b.dylib": listing(annotated, sym("_b"))}
        _, findings = run(nm)
        self.assertEqual(len(findings), 1)
        self.assertIn("exported by 2 images", findings[0])

    def test_annotated_weak_or_private_copy_is_still_not_a_duplicate(self) -> None:
        nm = {"a.dylib": listing(sym(FN)),
              "b.dylib": listing(sym(FN, "weak external [referenced dynamically]")),
              "c.dylib": listing(sym(FN, "private external [no dead-strip]"))}
        self.assertEqual(run(nm)[1], [])

    def test_name_a_pulp_library_also_defines_fails(self) -> None:
        nm = {"a.dylib": listing(sym(FN), sym("_mbedtls_ssl_init"))}
        images = [Path("a.dylib")]
        report, findings = sc.census(images, nm=lambda p: nm[str(p)], otool=lambda p: "", demangle=demangle,
                                     pulp_names=frozenset({"_mbedtls_ssl_init"}))
        self.assertEqual(report["exported_names_pulp_also_defines"], 1)
        self.assertEqual(len(findings), 1)
        self.assertIn("a Pulp build also defines", findings[0])

    def test_private_overlap_with_pulp_is_not_a_leak(self) -> None:
        nm = {"a.dylib": listing(sym(FN), sym("_sdl", "private external"))}
        _, findings = sc.census([Path("a.dylib")], nm=lambda p: nm[str(p)], otool=lambda p: "", demangle=demangle,
                                pulp_names=frozenset({"_sdl"}))
        self.assertEqual(findings, [])

    def test_weak_pulp_overlaps_fail_in_both_directions(self) -> None:
        for image_attrs, pulp_attrs in (("weak external", "weak external"),
                                        ("external", "weak external"),
                                        ("weak external", "external")):
            with self.subTest(image_attrs=image_attrs, pulp_attrs=pulp_attrs):
                image = listing(sym(FN), sym("_shared", image_attrs))
                pulp = listing(sym("_shared", pulp_attrs))
                pulp_names = frozenset(sc.pulp_defined([Path("libp.a")], nm=lambda _: pulp))
                _, findings = sc.census([Path("a.dylib")], nm=lambda _: image, otool=lambda _: "",
                                        demangle=demangle, pulp_names=pulp_names)
                self.assertEqual(len(findings), 1)
                self.assertIn("a Pulp build also defines", findings[0])

    def test_pulp_overlap_check_off_without_a_pulp_set(self) -> None:
        nm = {"a.dylib": listing(sym(FN), sym("_mbedtls_ssl_init"))}
        report, findings = sc.census([Path("a.dylib")], nm=lambda p: nm[str(p)], otool=lambda p: "", demangle=demangle)
        self.assertEqual(findings, [])
        self.assertNotIn("pulp_library_symbols", report)

    def test_pulp_defined_reads_exported_strong_names_across_libraries(self) -> None:
        lib = {"a.a": listing(sym("_x"), sym("_hidden", "private external")), "b.a": listing(sym("_y", "weak external"), sym("_z"))}
        self.assertEqual(sc.pulp_defined([Path("a.a"), Path("b.a")], nm=lambda p: lib[str(p)]), {"_x", "_y", "_z"})

    def _run_main(self, pulp_listing: str, *extra: str) -> int:
        image_listing = listing(sym(FN), sym("_shared_c"))

        def fake(cmd, **kw):
            if cmd[0] == "nm":
                return mock.Mock(returncode=0, stdout=pulp_listing if cmd[-1].endswith(".a") else image_listing, stderr="")
            if cmd[0] == "otool":
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="\n".join("vellum::" + n for n in kw["input"].splitlines()), stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "a.dylib"
            image.write_bytes(b"x")
            archive = Path(tmp) / "libp.a"
            archive.write_bytes(b"y")
            with mock.patch.object(sc.subprocess, "run", fake), contextlib.redirect_stderr(io.StringIO()):
                return sc.main([str(image), "--pulp-library", str(archive), *extra])

    def test_main_clean_when_pulp_defines_other_names(self) -> None:
        self.assertEqual(self._run_main(listing(sym("_only_in_pulp"))), 0)

    def test_main_fails_when_pulp_defines_an_exported_name(self) -> None:
        self.assertEqual(self._run_main(listing(sym("_shared_c"))), 1)

    def test_main_is_blind_when_the_pulp_side_defines_nothing(self) -> None:
        self.assertEqual(self._run_main(listing()), 3)

    def test_plant_pulp_control_makes_a_clean_run_fail(self) -> None:
        self.assertEqual(self._run_main(listing(sym("_only_in_pulp"))), 0)
        self.assertEqual(self._run_main(listing(sym("_only_in_pulp")), "--plant-pulp"), 1)

    def test_same_export_in_two_images_fails(self) -> None:
        _, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(sym(FN))})
        self.assertEqual(len(findings), 1)
        self.assertIn("exported by 2 images", findings[0])

    def test_hidden_or_weak_copy_is_not_a_duplicate(self) -> None:
        _, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(sym(FN, "private external")),
                           "c.dylib": listing(sym(FN, "weak external"))})
        self.assertEqual(findings, [])

    def test_allowlisted_anchor_passes_and_other_duplicates_still_fail(self) -> None:
        nm = {"a.dylib": listing(sym(ANCHOR), sym(FN)), "b.dylib": listing(sym(ANCHOR), sym(FN))}
        _, findings = run(nm, allow=frozenset({ANCHOR}))
        self.assertEqual(len(findings), 1)
        self.assertIn("clip_extent", findings[0])
        nm = {"a.dylib": listing(sym(ANCHOR)), "b.dylib": listing(sym(ANCHOR))}
        self.assertEqual(run(nm, allow=frozenset({ANCHOR}))[1], [])
        self.assertEqual(len(run(nm)[1]), 1)

    def test_objc_class_in_two_images_fails(self) -> None:
        nm = {"a.dylib": listing(sym(FN)), "b.dylib": listing(sym("_other"))}
        _, findings = run(nm, {"a.dylib": classes("Menu"), "b.dylib": classes("Menu", "Other")})
        self.assertEqual(len(findings), 1)
        self.assertIn("Objective-C class Menu", findings[0])

    def test_stripped_class_is_read_from_its_metadata(self) -> None:
        self.assertEqual(sc.objc_classes(stripped_classes("Menu", "Other")), {"Menu", "Other"})
        # a method's own `name` field, one indent deeper, is not the class name
        self.assertNotIn("0x55df8", sc.objc_classes(stripped_classes("Menu")))

    def test_same_stripped_class_in_two_images_fails(self) -> None:
        nm = {"a.dylib": listing(sym(FN)), "b.dylib": listing(sym("_other"))}
        _, findings = run(nm, {"a.dylib": stripped_classes("Menu"), "b.dylib": stripped_classes("Menu")})
        self.assertEqual(len(findings), 1)
        self.assertIn("Objective-C class Menu", findings[0])

    def test_symbol_and_metadata_names_agree_for_a_symbolled_class(self) -> None:
        rows = ["Contents of (__DATA_CONST,__objc_classlist) section",
                "0000000000004000 0x5000 _OBJC_CLASS_$_Menu", "    data       0x4000",
                "        name           0x9000 Menu"]
        self.assertEqual(sc.objc_classes("\n".join(rows) + "\n"), {"Menu"})

    def test_class_entry_with_no_name_is_an_error_not_a_skipped_class(self) -> None:
        rows = ["Contents of (__DATA_CONST,__objc_classlist) section", "0000000000004000 0x5000",
                "    isa        0x5028"]
        with self.assertRaises(RuntimeError):
            sc.objc_classes("\n".join(rows) + "\n")

    def test_listing_an_image_twice_is_caught(self) -> None:
        # The census's own plant: the same image seen twice.
        images = [Path("a.dylib"), Path("a.dylib")]
        _, findings = sc.census(images, nm=lambda p: listing(sym(FN)), otool=lambda p: "", demangle=demangle)
        self.assertEqual(len(findings), 1)

    def test_undefined_references_are_not_definitions(self) -> None:
        undefined = "                 (undefined) external " + FN
        _, findings = run({"a.dylib": listing(sym(FN)), "b.dylib": listing(undefined)})
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
