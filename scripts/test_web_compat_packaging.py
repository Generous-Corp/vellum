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
    def _run_node(self, source):
        result = subprocess.run(["node", "-e", source], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_focus_pseudos_match_focus_state_and_ancestors(self):
        self._run_node(r'''
            const fs = require("fs");
            const vm = require("vm");
            globalThis.document = {activeElement: null};
            vm.runInThisContext(fs.readFileSync(
                "framework/core/view/js/web-compat-document-selectors.js", "utf8"));
            const child = {_hasFocus: true, _focusVisible: true};
            const parent = {contains: value => value === child};
            document.activeElement = child;
            if (!_matchesPseudoClass(child, "focus-visible")) process.exit(1);
            if (!_matchesPseudoClass(parent, "focus-within")) process.exit(1);
            child._focusVisible = false;
            if (_matchesPseudoClass(child, "focus-visible")) process.exit(1);
        ''')

    def test_focus_pseudo_styles_follow_focusin_and_focusout(self):
        self._run_node(r'''
            const fs = require("fs");
            const vm = require("vm");
            globalThis.__stylesheets__ = [];
            globalThis.__elements__ = {};
            globalThis.Element = function(tag, id) {
                this.tagName = String(tag || "div").toUpperCase();
                this._id = id || "test";
                this.style = {};
                this.addEventListener = function() {};
            };
            globalThis.CSSStyleDeclaration = function() {};
            function parse(selector) {
                return {tag: selector.split(":")[0], id: null, classes: [], attrs: [],
                        pseudo: selector.split(":")[1], parent: null, direct: false};
            }
            globalThis._parseSelector = parse;
            globalThis._matchesSelector = () => true;
            vm.runInThisContext(fs.readFileSync(
                "framework/core/view/js/web-compat-document.js", "utf8"));
            const listeners = {};
            const el = {style: {outline: "none"}, addEventListener(type, fn) {
                listeners[type] = fn;
            }};
            _setupPseudoFocusVisible(el, {outline: "2px solid"});
            el._focusVisible = true;
            listeners.focus();
            if (el.style.outline !== "2px solid") process.exit(1);
            listeners.blur();
            if (el.style.outline !== "none") process.exit(1);
            _setupPseudoFocusWithin(el, {border: "1px solid"});
            listeners.focusin();
            if (el.style.border !== "1px solid") process.exit(1);
            listeners.focusout();
            if (el.style.border !== undefined) process.exit(1);
        ''')

    def test_native_focus_fanout_updates_dom_state_once(self):
        self._run_node(r'''
            const fs = require("fs");
            const vm = require("vm");
            globalThis.document = {activeElement: null};
            globalThis.__nativeElements__ = {};
            globalThis.__nativeRegistered__ = {};
            globalThis.__callbacks__ = {};
            globalThis.__eventListeners__ = {};
            function Element() { this._id = "focus-target"; }
            globalThis.Element = Element;
            vm.runInThisContext(fs.readFileSync(
                "framework/core/view/js/web-compat-element-events.js", "utf8"));
            const target = new Element();
            const events = [];
            target.addEventListener("focus", () => events.push("focus"));
            target.addEventListener("focusin", () => events.push("focusin"));
            __pulpApplyNativeFocus__(target, true,
                {cause: "keyboard", focusVisible: true});
            if (events.join(",") !== "focus,focusin" || !target._hasFocus ||
                !target._focusVisible || document.activeElement !== target)
                process.exit(1);
            __pulpApplyNativeFocus__(target, false, {cause: "keyboard"});
            if (target._hasFocus || target._focusVisible ||
                document.activeElement !== null) process.exit(1);
        ''')

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
