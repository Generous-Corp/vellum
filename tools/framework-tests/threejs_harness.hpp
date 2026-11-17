// Shared pieces of the Three.js bridge tests: the pinned payload's module map,
// a V8 scripted view, a module runner that pumps until the script reports it
// is done, and the small fixtures (a minimal GLB, a canvas adapter) the cases
// build their scenes from.
//
// VELLUM_TEST_THREEJS_DIR is the payload directory configure verified against
// assets/threejs/payload-manifest.json.
#pragma once

#include <vellum/state/store.hpp>
#include <vellum/view/js_engine.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/theme.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#ifndef VELLUM_TEST_THREEJS_DIR
#error "VELLUM_TEST_THREEJS_DIR must name the verified Three.js payload"
#endif

namespace threejs_test {

namespace view = vellum::view;

// The payload file a bare or relative specifier names, the way the demo's own
// resolver maps them: `three/webgpu` and `three` to the builds, `three/addons/`
// to examples/jsm, and the relative imports the builds and addons make.
inline std::optional<std::filesystem::path> payload_file(std::string_view specifier) {
    const std::filesystem::path root(VELLUM_TEST_THREEJS_DIR);
    if (specifier == "three") return root / "build/three.module.js";
    if (specifier == "three/webgpu") return root / "build/three.webgpu.js";
    if (specifier == "./three.core.js" || specifier == "three.core.js") return root / "build/three.core.js";
    constexpr std::string_view addons = "three/addons/";
    if (specifier.substr(0, addons.size()) == addons)
        return root / "examples/jsm" / std::string(specifier.substr(addons.size()));
    constexpr std::string_view utils = "../utils/";
    if (specifier.substr(0, utils.size()) == utils)
        return root / "examples/jsm/utils" / std::string(specifier.substr(utils.size()));
    return std::nullopt;
}

inline std::optional<std::string> read_file(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return std::nullopt;
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

// Resolves payload modules and records every specifier it was asked for.
struct PayloadResolver {
    std::set<std::string> requested;

    view::ModuleResolver callback() {
        return [this](std::string_view specifier) -> std::optional<std::string> {
            requested.emplace(specifier);
            const auto path = payload_file(specifier);
            return path ? read_file(*path) : std::nullopt;
        };
    }
};

// A scripted view on V8 with the bridge loaded. `surface` may be null: the
// mock WebGPU prelude and module loading need no device.
struct ScriptedView {
    view::View root;
    view::ScriptEngine engine{view::JsEngineType::v8};
    vellum::state::StateStore store;
    view::WidgetBridge bridge;

    ScriptedView(float width, float height, vellum::render::GpuSurface* surface = nullptr)
        : bridge(engine, root, store, surface) {
        root.set_bounds({0, 0, width, height});
        root.set_theme(view::Theme::dark());
        bridge.load_script("");
    }

    std::string js_string(const std::string& expression) {
        return engine.evaluate("String(" + expression + ")").toString();
    }

    bool js_true(const std::string& expression) { return js_string("!!(" + expression + ")") == "true"; }
};

// The engine calls back into this while it runs a module, so a test declares
// it before the ScriptedView whose engine runs the module.
struct ModuleOutcome {
    bool completed = false;
    std::string error;
    PayloadResolver resolver;
};

// Runs `code` as a module against the payload and pumps the engine (and, when
// asked, the bridge's frame callbacks) until `done` is true in script or the
// pump budget runs out. The module's own completion is recorded separately, so
// a module that throws before `done` is reported with its error.
inline void run_module(ScriptedView& v, const std::string& code, const std::string& done,
                       ModuleOutcome& outcome, int max_pumps = 512, bool service_frames = false) {
    v.engine.run_module(code, outcome.resolver.callback(),
                        [&outcome](const std::string& error, const choc::value::Value&) {
                            outcome.completed = true;
                            outcome.error = error;
                        });
    for (int i = 0; i < max_pumps; ++i) {
        v.engine.pump_message_loop();
        if (service_frames) v.bridge.service_frame_callbacks();
        if (v.js_true(done)) break;
    }
}

// The fields a Three.js renderer needs from a canvas, forwarded to the
// bridge's canvas element. Pasted into a module after its imports.
inline constexpr const char* kCanvasAdapter = R"JS(
class CanvasAdapter {
    constructor(element) { this.element = element; this.style = element.style || {}; }
    get width() { return this.element.width; }
    set width(v) { this.element.width = v; }
    get height() { return this.element.height; }
    set height(v) { this.element.height = v; }
    get clientWidth() { return this.element.width; }
    get clientHeight() { return this.element.height; }
    addEventListener(...a) { return this.element.addEventListener(...a); }
    removeEventListener(...a) { return this.element.removeEventListener(...a); }
    dispatchEvent(e) { return this.element.dispatchEvent(e); }
    setPointerCapture(id) { if (this.element.setPointerCapture) this.element.setPointerCapture(id); }
    releasePointerCapture(id) { if (this.element.releasePointerCapture) this.element.releasePointerCapture(id); }
}
function errorText(e) { return e && e.message ? String(e.message) : String(e); }
)JS";

// A glTF 2.0 binary with one empty scene: a 12-byte header and a JSON chunk
// padded to four bytes.
inline std::vector<uint8_t> minimal_glb() {
    std::string json = R"({"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[]}]})";
    json.resize((json.size() + 3) / 4 * 4, ' ');
    std::vector<uint8_t> out;
    auto u32 = [&out](uint32_t value) {
        for (int shift = 0; shift < 32; shift += 8) out.push_back(static_cast<uint8_t>(value >> shift));
    };
    u32(0x46546c67u); // "glTF"
    u32(2u);
    u32(static_cast<uint32_t>(20 + json.size()));
    u32(static_cast<uint32_t>(json.size()));
    u32(0x4e4f534au); // "JSON"
    out.insert(out.end(), json.begin(), json.end());
    return out;
}

// Writes `bytes` to a fresh file in a per-test temporary directory and returns
// its path. The directory is what a test hands the bridge as an asset root.
inline std::filesystem::path write_fixture(const std::string& dir_name, const std::string& file_name,
                                           const std::vector<uint8_t>& bytes) {
    const auto dir = std::filesystem::temp_directory_path() / dir_name;
    std::filesystem::create_directories(dir);
    const auto path = dir / file_name;
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    return std::filesystem::canonical(path);
}

inline std::string file_url(const std::filesystem::path& path) { return "file://" + path.generic_string(); }

// Replaces every `token` in `text` with `value`.
inline std::string substitute(std::string text, const std::string& token, const std::string& value) {
    for (auto at = text.find(token); at != std::string::npos; at = text.find(token, at + value.size()))
        text.replace(at, token.size(), value);
    return text;
}

} // namespace threejs_test
