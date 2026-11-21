"""Harness bundles ship as SDK resources and never replace native preludes."""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "framework/core/view"
BUNDLES = ("web-compat", "web-compat-selector", "web-compat-runtime-apis",
           "web-compat-three-shim")


class WebCompatPackagingTests(unittest.TestCase):
    def test_harness_bundles_are_not_native_preludes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "dummy.cpp").write_text("int dummy;\n")
            (source / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.20)\n'
                'project(PreludeBoundary LANGUAGES CXX)\n'
                'add_library(pulp-view-script OBJECT dummy.cpp)\n'
                f'set(CMAKE_CURRENT_SOURCE_DIR "{VIEW}")\n'
                f'include("{VIEW}/cmake/PulpViewJsPreludes.cmake")\n')
            build = source / "build"
            result = subprocess.run(["cmake", "-S", str(source), "-B", str(build)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            declarations = (build / "web_compat_preludes_gen.hpp").read_text()
        embedded = set(re.findall(r"extern const EmbeddedPrelude (\w+);", declarations))
        bridge = (VIEW / "src/widget_bridge.cpp").read_text()
        evaluated = set(re.findall(r'eval_prelude\(\s*"([^"\n]+)"', bridge))
        # Positive controls ensure both production registries were inspected.
        for native in ("web_compat_element", "web_compat_document_selectors"):
            self.assertIn(native, embedded)
            self.assertIn(native, evaluated)
        for bundle in BUNDLES:
            symbol = bundle.replace("-", "_")
            with self.subTest(bundle=bundle, registry="embedded"):
                self.assertNotIn(symbol, embedded)
            with self.subTest(bundle=bundle, registry="evaluated"):
                self.assertNotIn(symbol, evaluated)

    def test_sdk_installs_unchanged_harness_bundles(self):
        module = ROOT / "cmake/VellumWebCompat.cmake"
        self.assertIn("include(cmake/VellumWebCompat.cmake)",
                      (ROOT / "CMakeLists.txt").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            build, prefix = source / "build", source / "sdk"
            (source / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.20)\n'
                'project(WebCompatInstall LANGUAGES NONE)\n'
                'set(CMAKE_INSTALL_LIBDIR lib)\n'
                f'include("{module}")\n')
            for cmd in (["cmake", "-S", str(source), "-B", str(build)],
                        ["cmake", "--install", str(build), "--prefix", str(prefix)]):
                result = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for bundle in BUNDLES:
                with self.subTest(bundle=bundle):
                    relative = f"lib/cmake/Vellum/scripts/{bundle}.js"
                    self.assertEqual((prefix / relative).read_bytes(),
                                     (VIEW / f"js/{bundle}.js").read_bytes())


if __name__ == "__main__":
    unittest.main()
