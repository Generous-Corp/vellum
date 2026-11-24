"""Harness bundles ship as SDK resources and never replace native preludes."""

import hashlib
import json
import re
import shutil
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

    # -- installed package ---------------------------------------------------

    def _cmake(self, *args):
        return subprocess.run(["cmake", *args], capture_output=True, text=True)

    def _install_package(self, root):
        """Copies the producer inputs into `root`, installs them, returns the prefix.

        The copy lets a test edit a prelude without touching the repository.
        """
        framework_js = root / "src/framework/core/view/js"
        framework_js.mkdir(parents=True)
        for path in (VIEW / "js").glob("*"):
            if path.is_file():
                shutil.copy2(path, framework_js / path.name)
        cmake_dir = root / "src/cmake"
        cmake_dir.mkdir()
        for name in ("VellumWebCompat.cmake", "VellumWebCompatPackage.cmake",
                     "vellum_embed_preludes.py"):
            shutil.copy2(ROOT / "cmake" / name, cmake_dir / name)
        producer = root / "src"
        (producer / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.24)\n'
            'project(WebCompatInstall VERSION 0.2.3 LANGUAGES NONE)\n'
            'include(GNUInstallDirs)\n'
            'include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/VellumWebCompat.cmake")\n'
            'add_custom_target(reconfigure_probe)\n'
            'file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/VellumConfig.cmake"\n'
            '  "include(\\"\\${CMAKE_CURRENT_LIST_DIR}/web-compat/VellumWebCompatPackage.cmake\\")\\n")\n'
            'install(FILES "${CMAKE_CURRENT_BINARY_DIR}/VellumConfig.cmake"\n'
            '  DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum")\n')
        self.producer_build = root / "build"
        prefix = root / "sdk"
        for cmd in (["-S", str(producer), "-B", str(self.producer_build)],
                    ["--install", str(self.producer_build), "--prefix", str(prefix)]):
            result = self._cmake(*cmd)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.framework_js = framework_js
        return prefix

    def _manifest(self, prefix):
        return json.loads(
            (prefix / "lib/cmake/Vellum/web-compat/vellum-web-compat-manifest.json").read_text())

    def _consumer(self, root, prefix, body, languages="NONE", sources=None, name="consumer"):
        consumer = root / name
        consumer.mkdir()
        for filename, text in (sources or {}).items():
            (consumer / filename).write_text(text)
        (consumer / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.24)\n'
            f'project(WebCompatConsumer LANGUAGES {languages})\n'
            'find_package(Vellum CONFIG REQUIRED)\n' + body)
        return consumer, self._cmake("-S", str(consumer), "-B", str(root / f"{name}-build"),
                                     f"-DCMAKE_PREFIX_PATH={prefix}")

    def test_manifest_matches_the_source_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._install_package(Path(tmp))
            manifest = self._manifest(prefix)
        module = (ROOT / "cmake/VellumWebCompat.cmake").read_text()
        registry = re.search(r"set\(_vellum_web_compat_preludes\n(.*?)\)", module, re.S)
        self.assertIsNotNone(registry)
        self.assertEqual([entry["name"] for entry in manifest["preludes"]],
                         registry.group(1).split())
        for entry in manifest["preludes"] + manifest["scripts"]:
            source = (VIEW / "js" / entry["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), entry["sha256"], entry["name"])

    def test_prelude_registry_matches_pulp_embed_list(self):
        """The installed prelude list is the framework's embedded list, in order."""
        module = (ROOT / "cmake/VellumWebCompat.cmake").read_text()
        ours = re.search(r"set\(_vellum_web_compat_preludes\n(.*?)\)", module, re.S)
        self.assertIsNotNone(ours)
        embedded = (VIEW / "cmake/PulpViewJsPreludes.cmake").read_text()
        listing = re.search(r"set\(PULP_JS_PRELUDES\n(.*?)\n\)", embedded, re.S)
        self.assertIsNotNone(listing)
        lines = [line for line in listing.group(1).splitlines()
                 if not line.strip().startswith("#")]
        theirs = [Path(line.strip()).name for line in lines if line.strip()]
        self.assertGreater(len(theirs), 20)
        self.assertEqual(ours.group(1).split(), theirs)

    def test_installed_files_pass_the_sdk_payload_census(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import install_core
        with tempfile.TemporaryDirectory() as tmp:
            prefix = self._install_package(Path(tmp))
            installed = [path for path in prefix.rglob("*") if path.is_file()]
            names = {path.relative_to(prefix).as_posix() for path in installed}
            for expected in ("lib/cmake/Vellum/web-compat/vellum_embed_preludes.py",
                             "lib/cmake/Vellum/web-compat/VellumWebCompatPackage.cmake",
                             "lib/cmake/Vellum/web-compat/vellum-web-compat-manifest.json",
                             "mirror/share/web-compat/preludes/css-colors.js",
                             "lib/cmake/Vellum/scripts/web-compat-three-shim.js"):
                self.assertIn(expected, names)
            findings = []
            for path in installed:
                relative = path.relative_to(prefix).as_posix()
                # The archive names members under sdk/; an installed prefix
                # is judged by its prefix-relative path.
                for name in ("sdk/" + relative, relative):
                    findings.extend(install_core.payload_contamination_findings(
                        name, path.read_bytes()))
            self.assertEqual(findings, [])

    @unittest.skipUnless(shutil.which("c++") or shutil.which("clang++"),
                         "the consumer compiles and links a C++ target")
    def test_consumer_compiles_links_and_inflates_a_prelude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = self._install_package(root)
            sources = {
                "inflate.hpp": (
                    '#pragma once\n#include <cstddef>\n#include <cstdint>\n'
                    '#include <optional>\n#include <string>\n'
                    'namespace consumer { std::optional<std::string> inflate('
                    'const std::uint8_t*, std::size_t, std::size_t); }\n'),
                "inflate.cpp": (
                    '#include "inflate.hpp"\n#include <zlib.h>\n'
                    'namespace consumer {\n'
                    'std::optional<std::string> inflate(const std::uint8_t* data, std::size_t size,'
                    ' std::size_t expected) {\n'
                    '    std::string out(expected, \'\\0\');\n'
                    '    uLongf length = static_cast<uLongf>(expected);\n'
                    '    if (uncompress(reinterpret_cast<Bytef*>(out.data()), &length, data, size)'
                    ' != Z_OK || length != expected) return std::nullopt;\n'
                    '    return out;\n}\n}\n'),
                "main.cpp": (
                    '#include "prelude_decls.hpp"\n#include <cstdio>\n#include <fstream>\n'
                    'int main(int argc, char** argv) {\n'
                    '    const auto text = testns::preludes::source(testns::preludes::css_colors);\n'
                    '    std::ofstream(argv[1], std::ios::binary) << text;\n'
                    '    std::printf("%zu %zu\\n", testns::preludes::kAllCount, text.size());\n'
                    '    return 0;\n}\n'),
            }
            body = (
                'find_package(ZLIB REQUIRED)\n'
                'vellum_embed_web_compat_preludes(\n'
                '  "${CMAKE_CURRENT_BINARY_DIR}/gen/preludes.cpp"\n'
                '  "${CMAKE_CURRENT_BINARY_DIR}/gen/prelude_decls.hpp"\n'
                '  NAMESPACE testns::preludes\n'
                '  INFLATE_HEADER "\\"inflate.hpp\\""\n'
                '  INFLATE_FUNCTION consumer::inflate)\n'
                'add_executable(prelude_consumer main.cpp inflate.cpp '
                '"${CMAKE_CURRENT_BINARY_DIR}/gen/preludes.cpp")\n'
                'target_include_directories(prelude_consumer PRIVATE '
                '"${CMAKE_CURRENT_BINARY_DIR}/gen" "${CMAKE_CURRENT_SOURCE_DIR}")\n'
                'target_compile_features(prelude_consumer PRIVATE cxx_std_17)\n'
                'target_link_libraries(prelude_consumer PRIVATE ZLIB::ZLIB)\n')
            _, configured = self._consumer(root, prefix, body, "CXX", sources)
            self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
            build = root / "consumer-build"
            built = self._cmake("--build", str(build))
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            produced = root / "css-colors.out"
            ran = subprocess.run([str(build / "prelude_consumer"), str(produced)],
                                 capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertEqual(produced.read_bytes(), (VIEW / "js/css-colors.js").read_bytes())
            count = len(self._manifest(prefix)["preludes"])
            self.assertEqual(ran.stdout.split()[0], str(count))

            # Reconfiguring leaves the generated header's timestamp alone, so
            # translation units that include it do not rebuild.
            header = build / "gen/prelude_decls.hpp"
            before = header.stat().st_mtime_ns
            again = self._cmake("-S", str(root / "consumer"), "-B", str(build))
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertEqual(header.stat().st_mtime_ns, before)

    def test_helper_requires_namespace_and_inflate_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = self._install_package(root)
            body = ('vellum_embed_web_compat_preludes("${CMAKE_CURRENT_BINARY_DIR}/embedded.cpp"\n'
                    '  "${CMAKE_CURRENT_BINARY_DIR}/gen.hpp" NAMESPACE a::b)\n')
            _, result = self._consumer(root, prefix, body)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires INFLATE_HEADER", result.stdout + result.stderr)

    def test_generated_definitions_match_the_framework_encoder(self):
        """The installed encoder emits the same byte arrays embed_js.py does."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            inputs = [str(VIEW / "js/css-colors.js"), str(VIEW / "js/web-compat-element.js")]
            framework = out / "framework.cpp"
            installed = out / "installed.cpp"
            for command in (
                    ["python3", str(VIEW / "js/embed_js.py"), str(framework), *inputs],
                    ["python3", str(ROOT / "cmake/vellum_embed_preludes.py"), str(installed),
                     *inputs, "--namespace", "x::y", "--header", "decls.hpp",
                     "--inflate-header", "<z.hpp>", "--inflate-function", "z::inflate"]):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            def arrays(text):
                return re.findall(r"static const unsigned char \w+\[\] = \{.*?\};\n"
                                  r"const EmbeddedPrelude .*?;\n", text, re.S)
            self.assertEqual(len(arrays(framework.read_text())), 2)
            self.assertEqual(arrays(framework.read_text()), arrays(installed.read_text()))
            text = installed.read_text()
            self.assertIn("namespace x::y {", text)
            self.assertIn('#include "decls.hpp"', text)
            self.assertIn("z::inflate(prelude.deflated", text)

    def test_installed_scripts_resolve_for_the_generated_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = self._install_package(root)
            body = ('vellum_embed_web_compat_preludes("${CMAKE_CURRENT_BINARY_DIR}/embedded.cpp"\n'
                    '  "${CMAKE_CURRENT_BINARY_DIR}/web_compat_preludes_gen.hpp"\n'
                    '  NAMESPACE app::preludes INFLATE_HEADER "<app/zip.hpp>"\n'
                    '  INFLATE_FUNCTION app::inflate)\n'
                    'add_custom_target(embed ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/embedded.cpp")\n')
            _, configured = self._consumer(root, prefix, body)
            self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
            built = self._cmake("--build", str(root / "consumer-build"))
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            generated = (root / "consumer-build/embedded.cpp").read_text()
            self.assertIn("namespace app::preludes {", generated)
            self.assertIn("std::string source(const EmbeddedPrelude& prelude)", generated)
            for entry in self._manifest(prefix)["preludes"]:
                self.assertIn(entry["sha256"], generated)

    def test_consumer_refuses_missing_or_altered_files(self):
        body = ('vellum_embed_web_compat_preludes("${CMAKE_CURRENT_BINARY_DIR}/embedded.cpp"\n'
                '  "${CMAKE_CURRENT_BINARY_DIR}/gen.hpp"\n'
                '  NAMESPACE app::preludes INFLATE_HEADER "<app/zip.hpp>"\n'
                '  INFLATE_FUNCTION app::inflate)\n')
        cases = {
            "missing prelude": (lambda p: (p / "mirror/share/web-compat/preludes/css-colors.js").unlink(),
                                "is missing"),
            "altered prelude": (lambda p: (p / "mirror/share/web-compat/preludes/css-colors.js")
                                .write_text("// drift\n"), "does not match the manifest"),
            "altered script": (lambda p: (p / "lib/cmake/Vellum/scripts/web-compat-three-shim.js")
                               .write_text("// drift\n"), "does not match the manifest"),
            "missing script": (lambda p: (p / "lib/cmake/Vellum/scripts/web-compat.js").unlink(),
                               "is missing"),
        }
        for label, (damage, message) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prefix = self._install_package(root)
                _, control = self._consumer(root, prefix, body, name="control")
                self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
                damage(prefix)
                _, result = self._consumer(root, prefix, body, name="damaged")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, " ".join((result.stdout + result.stderr).split()))

    def test_a_changed_prelude_refreshes_the_manifest_on_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = self._install_package(root)
            before = {e["name"]: e["sha256"] for e in self._manifest(prefix)["preludes"]}
            manifest_file = self.producer_build / "vellum-web-compat-manifest.json"
            unchanged = manifest_file.stat().st_mtime_ns
            again = self._cmake("-S", str(root / "src"), "-B", str(self.producer_build))
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertEqual(manifest_file.stat().st_mtime_ns, unchanged)
            with open(self.framework_js / "css-colors.js", "a") as handle:
                handle.write("\n// edited\n")
            built = self._cmake("--build", str(self.producer_build))
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            manifest = json.loads(manifest_file.read_text())
            after = {e["name"]: e["sha256"] for e in manifest["preludes"]}
            self.assertNotEqual(after["css-colors.js"], before["css-colors.js"])
            self.assertEqual(after["css-parser.js"], before["css-parser.js"])


if __name__ == "__main__":
    unittest.main()
