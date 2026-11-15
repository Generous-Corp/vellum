// Loads the pinned Three.js payload into the engine the framework compiles
// today (QuickJS) as ES modules, through the same kind of resolver a scripted
// view uses, and checks that the THREE namespace is defined and works.
// WebGPU rendering itself needs a GPU surface and a module-capable engine; it
// is outside this test.

#include "framework_test.hpp"

#include <vellum/view/script_engine.hpp>

#include <fstream>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#ifndef VELLUM_THREEJS_DIR
#error "VELLUM_THREEJS_DIR must name the verified Three.js payload directory"
#endif

namespace {

namespace view = vellum::view;

std::optional<std::string> read_file(const std::string& relative) {
    std::ifstream in(std::string(VELLUM_THREEJS_DIR) + "/" + relative, std::ios::binary);
    if (!in)
        return std::nullopt;
    std::ostringstream buffer;
    buffer << in.rdbuf();
    return buffer.str();
}

// Maps the import specifiers the payload and a script use to payload files,
// the way a scripted view's resolver does, and records what was asked for.
struct PayloadResolver {
    std::vector<std::string> requested;

    std::optional<std::string> operator()(std::string_view specifier) {
        requested.emplace_back(specifier);
        static const std::map<std::string, std::string> files = {
            {"three", "build/three.module.js"},
            {"three/webgpu", "build/three.webgpu.js"},
            {"three.core.js", "build/three.core.js"},
            {"three.module.js", "build/three.module.js"},
            {"three.webgpu.js", "build/three.webgpu.js"},
            {"OrbitControls.js", "examples/jsm/controls/OrbitControls.js"},
            {"three/addons/controls/OrbitControls.js", "examples/jsm/controls/OrbitControls.js"},
        };
        std::string name(specifier);
        auto slash = name.rfind('/');
        std::string base = slash == std::string::npos ? name : name.substr(slash + 1);
        auto found = files.find(name);
        if (found == files.end())
            found = files.find(base);
        if (found == files.end())
            return std::nullopt;
        return read_file(found->second);
    }
};

struct ModuleRun {
    bool completed = false;
    std::string error;
};

ModuleRun run(view::ScriptEngine& engine, const std::string& code, PayloadResolver& resolver) {
    ModuleRun result;
    engine.run_module(
        code, [&resolver](std::string_view path) { return resolver(path); },
        [&result](const std::string& error, const choc::value::Value&) {
            result.completed = true;
            result.error = error;
        });
    for (int i = 0; i < 100 && !result.completed; ++i)
        engine.pump_message_loop();
    return result;
}

std::string js_string(view::ScriptEngine& engine, const std::string& expression) {
    return engine.evaluate("String(" + expression + ")").toString();
}

FRAMEWORK_TEST(payload_files_are_readable) {
    for (const char* name : {"build/three.core.js", "build/three.module.js", "build/three.webgpu.js",
                             "examples/jsm/controls/OrbitControls.js", "LICENSE"}) {
        auto text = read_file(name);
        CHECK(text.has_value());
        CHECK(text && !text->empty());
    }
}

FRAMEWORK_TEST(quickjs_defines_the_three_namespace_from_the_module_build) {
    view::ScriptEngine engine(view::JsEngineType::quickjs);
    PayloadResolver resolver;
    auto result = run(engine,
                      "import * as THREE from 'three';\n"
                      "globalThis.THREE = THREE;\n",
                      resolver);
    if (!result.error.empty())
        std::fprintf(stderr, "  module error: %s\n", result.error.c_str());
    CHECK(result.completed);
    CHECK(result.error.empty());
    CHECK(js_string(engine, "typeof THREE") == "object");
    CHECK(js_string(engine, "THREE.REVISION") == "183");
    for (const char* name : {"Scene", "PerspectiveCamera", "Mesh", "BoxGeometry", "Vector3", "Matrix4"})
        CHECK(js_string(engine, std::string("typeof THREE.") + name) == "function");
    // The core module was reached through the module build's own import.
    bool saw_core = false;
    for (const auto& specifier : resolver.requested)
        if (specifier.find("three.core.js") != std::string::npos)
            saw_core = true;
    CHECK(saw_core);
}

FRAMEWORK_TEST(three_namespace_computes_real_geometry) {
    view::ScriptEngine engine(view::JsEngineType::quickjs);
    PayloadResolver resolver;
    auto result = run(engine, "import * as THREE from 'three'; globalThis.THREE = THREE;", resolver);
    CHECK(result.error.empty());
    CHECK(js_string(engine, "new THREE.Vector3(3, 4, 0).length()") == "5");
    CHECK(js_string(engine, "new THREE.Box3().setFromObject("
                            "new THREE.Mesh(new THREE.BoxGeometry(2, 4, 6))).getSize(new THREE.Vector3()).toArray().join(',')") ==
          "2,4,6");
}

FRAMEWORK_TEST(addon_module_resolves_against_the_same_three_build) {
    view::ScriptEngine engine(view::JsEngineType::quickjs);
    PayloadResolver resolver;
    auto result = run(engine,
                      "import { OrbitControls } from 'three/addons/controls/OrbitControls.js';\n"
                      "globalThis.OrbitControlsType = typeof OrbitControls;\n",
                      resolver);
    if (!result.error.empty())
        std::fprintf(stderr, "  module error: %s\n", result.error.c_str());
    CHECK(result.error.empty());
    CHECK(js_string(engine, "OrbitControlsType") == "function");
}

FRAMEWORK_TEST(webgpu_build_loads_in_quickjs_to_define_its_renderer) {
    view::ScriptEngine engine(view::JsEngineType::quickjs);
    PayloadResolver resolver;
    auto result = run(engine,
                      "import * as THREE from 'three/webgpu';\n"
                      "globalThis.THREE = THREE;\n",
                      resolver);
    if (!result.error.empty())
        std::fprintf(stderr, "  module error: %s\n", result.error.c_str());
    CHECK(result.error.empty());
    CHECK(js_string(engine, "typeof THREE.WebGPURenderer") == "function");
}

FRAMEWORK_TEST(an_unresolvable_import_reports_an_error) {
    view::ScriptEngine engine(view::JsEngineType::quickjs);
    PayloadResolver resolver;
    auto result = run(engine, "import * as X from 'not-in-the-payload'; globalThis.X = X;", resolver);
    CHECK(!result.error.empty());
}

} // namespace

int main() { return framework_test::run_all(); }
