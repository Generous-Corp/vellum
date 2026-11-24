"""Harness bundles ship as SDK resources and never replace native preludes."""

import re
import json
import hashlib
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
            globalThis.registerFocus = function() {};
            globalThis.on = function(id, type, fn) {
                globalThis.__callbacks__[id + ":" + type] = fn;
            };
            globalThis.__dispatch__ = function(id, type, data) {
                const el = __nativeElements__[id];
                if (el && (type === "focus" || type === "blur"))
                    __pulpApplyNativeFocus__(el, type === "focus", data || {});
                const cb = __callbacks__[id + ":" + type];
                if (cb) cb(data || {});
            };
            function Element() { this._id = "focus-target"; }
            globalThis.Element = Element;
            vm.runInThisContext(fs.readFileSync(
                "framework/core/view/js/web-compat-element-events.js", "utf8"));
            const target = new Element();
            __nativeElements__[target._id] = target;
            const events = [];
            target.addEventListener("focus", () => events.push("focus"));
            target.addEventListener("focusin", () => events.push("focusin"));
            target._registerNativeEvent("focus");
            __dispatch__(target._id, "focus",
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

    def test_installed_sdk_helper_embeds_manifest_and_rejects_missing_prelude(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            producer = source / "producer"
            producer.mkdir()
            (producer / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.24)\n'
                'project(WebCompatInstall VERSION 0.2.3 LANGUAGES NONE)\n'
                'include(GNUInstallDirs)\n'
                f'include("{ROOT / "cmake/VellumWebCompat.cmake"}")\n'
                'file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/VellumConfig.cmake"\n'
                '  "include(\\\"\\${CMAKE_CURRENT_LIST_DIR}/web-compat/VellumWebCompatPackage.cmake\\\")\\n")\n'
                'install(FILES "${CMAKE_CURRENT_BINARY_DIR}/VellumConfig.cmake"\n'
                '  DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum")\n')
            build, prefix = source / "build", source / "sdk"
            for cmd in (["cmake", "-S", str(producer), "-B", str(build)],
                        ["cmake", "--install", str(build), "--prefix", str(prefix)]):
                result = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            consumer = source / "consumer"
            consumer.mkdir()
            (consumer / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.24)\n'
                'project(WebCompatConsumer LANGUAGES NONE)\n'
                'find_package(Vellum CONFIG REQUIRED)\n'
                'vellum_embed_web_compat_preludes("${CMAKE_CURRENT_BINARY_DIR}/embedded.cpp"\n'
                '  "${CMAKE_CURRENT_BINARY_DIR}/web_compat_preludes_gen.hpp")\n'
                'add_custom_target(embed ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/embedded.cpp")\n')
            consumer_build = source / "consumer-build"
            result = subprocess.run([
                "cmake", "-S", str(consumer), "-B", str(consumer_build),
                f"-DCMAKE_PREFIX_PATH={prefix}",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["cmake", "--build", str(consumer_build), "--target", "embed"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((prefix / "lib/cmake/Vellum/web-compat/vellum-web-compat-manifest.json").read_text())
            generated = (consumer_build / "embedded.cpp").read_text()
            self.assertEqual({entry["name"] for entry in manifest["preludes"]},
                             {path.name for path in (prefix / "lib/cmake/Vellum/web-compat/preludes").glob("*.js")})
            for entry in manifest["preludes"]:
                installed = prefix / "lib/cmake/Vellum/web-compat/preludes" / entry["name"]
                self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), entry["sha256"])
                self.assertIn(entry["name"], generated)

            missing = prefix / "lib/cmake/Vellum/web-compat/preludes" / manifest["preludes"][0]["name"]
            missing.unlink()
            negative_build = source / "negative-build"
            result = subprocess.run([
                "cmake", "-S", str(consumer), "-B", str(negative_build),
                f"-DCMAKE_PREFIX_PATH={prefix}",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
