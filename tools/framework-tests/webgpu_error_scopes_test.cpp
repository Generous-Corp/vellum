// Drives the scripted WebGPU binding against a headless Dawn device: script
// error scopes catch the errors that bad WGSL and a bad pipeline cause, so the
// device's uncaptured-error path stays quiet, and valid work reports nothing.
//
// An uncaptured error is one no error scope on the device claims. The test
// holds its own validation scope around the script, outside the binding's: a
// binding that lets an error escape its scopes lands it in this one, so an
// empty outer scope is the same fact as a silent uncaptured-error callback.
// Exits 77 (skipped) when no Dawn adapter is available.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <vellum/state/store.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <webgpu/webgpu_cpp.h>

#include <chrono>
#include <memory>
#include <string>
#include <thread>

namespace {

namespace view = vellum::view;

std::unique_ptr<gpu_runtime::GpuSurface> g_surface;

constexpr const char* kValidWgsl = R"WGSL(
@vertex fn vs(@builtin(vertex_index) i : u32) -> @builtin(position) vec4f {
    var p = array<vec2f, 3>(vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
    return vec4f(p[i], 0.0, 1.0);
}
@fragment fn fs() -> @location(0) vec4f { return vec4f(1.0, 0.0, 0.0, 1.0); }
)WGSL";

// Line 3 is the faulty one: `nonexistent_fn` is not declared.
constexpr const char* kBadWgsl =
    "@vertex fn vs() -> @builtin(position) vec4f {\n"
    "    var x = 1.0;\n"
    "    return nonexistent_fn(x);\n"
    "}\n";

struct Harness {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    view::WidgetBridge bridge;
    wgpu::Device device;
    wgpu::Instance instance;

    Harness() : bridge(engine, root, store, g_surface.get()) {
        root.set_bounds({0, 0, 400, 300});
        device = *static_cast<wgpu::Device*>(g_surface->dawn_device_handle());
        instance = *static_cast<wgpu::Instance*>(g_surface->dawn_instance_handle());
        device.PushErrorScope(wgpu::ErrorFilter::Validation);
        run("globalThis.dev = __createMockGPUDevice(__createGPUAdapter({nativeBridge: true}), {},"
            " {nativeBridge: true});"
            "globalThis.WGSL = " + js_literal(kValidWgsl) + ";");
    }

    static std::string js_literal(const std::string& text) {
        std::string out = "'";
        for (char c : text) {
            if (c == '\n') out += "\\n";
            else if (c == '\'') out += "\\'";
            else out += c;
        }
        return out + "'";
    }

    void run(const std::string& script) { engine.evaluate(script); }

    std::string js_string(const std::string& expression) {
        return engine.evaluate("String(" + expression + ")").toString();
    }

    // Errors the test's outer scope saw, which would otherwise have reached the
    // device's uncaptured-error callback. Empty when none.
    std::string escaped_errors() {
        struct Result { bool done = false; wgpu::ErrorType type = wgpu::ErrorType::NoError; std::string message; };
        auto result = std::make_shared<Result>();
        device.PopErrorScope(wgpu::CallbackMode::AllowProcessEvents,
                             [result](wgpu::PopErrorScopeStatus, wgpu::ErrorType type, wgpu::StringView message) {
                                 result->done = true;
                                 result->type = type;
                                 if (message.data != nullptr) {
                                     result->message = message.length == wgpu::kStrlen
                                                           ? std::string(message.data)
                                                           : std::string(message.data, message.length);
                                 }
                             });
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (!result->done && std::chrono::steady_clock::now() < deadline) {
            instance.ProcessEvents();
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!result->done) return "outer scope did not resolve";
        return result->type == wgpu::ErrorType::NoError ? std::string() : result->message;
    }

    // Pops the script scope and stores the resolved error in globalThis.popped
    // (null when none), running pending promise jobs before returning.
    void pop_into_popped() {
        run("globalThis.popped = 'pending'; dev.popErrorScope().then(function (e) { globalThis.popped = e; });");
        for (int i = 0; i < 10 && js_string("popped") == "pending"; ++i) engine.pump_message_loop();
    }
};

FRAMEWORK_TEST(bad_wgsl_is_caught_by_the_script_scope_and_not_left_uncaptured) {
    Harness h;
    h.run("dev.pushErrorScope('validation');"
          "dev.createShaderModule({code: " + Harness::js_literal(kBadWgsl) + "});");
    h.pop_into_popped();

    CHECK(h.js_string("popped !== null && popped !== 'pending'") == "true");
    CHECK(h.js_string("popped.name") == "GPUValidationError");
    CHECK(h.js_string("popped.message.length > 0") == "true");
    CHECK(h.js_string("popped.diagnostics.length > 0") == "true");
    CHECK(h.js_string("popped.diagnostics[0].type") == "error");
    CHECK(h.js_string("popped.diagnostics[0].lineNum") == "3");
    CHECK(h.js_string("popped.diagnostics[0].linePos > 0") == "true");
    CHECK(h.escaped_errors().empty());
}

FRAMEWORK_TEST(bad_wgsl_reports_its_line_and_column_in_the_compilation_info) {
    Harness h;
    h.run("dev.pushErrorScope('validation');"
          "globalThis.mod = dev.createShaderModule({code: " + Harness::js_literal(kBadWgsl) + "});"
          "mod.getCompilationInfo().then(function (info) { globalThis.info = info; });");
    for (int i = 0; i < 10 && h.js_string("typeof info") != "object"; ++i) h.engine.pump_message_loop();
    CHECK(h.js_string("info.messages.length > 0") == "true");
    CHECK(h.js_string("info.messages[0].lineNum") == "3");
    CHECK(h.js_string("info.messages[0].linePos > 0") == "true");
    h.pop_into_popped();
    CHECK(h.escaped_errors().empty());
}

FRAMEWORK_TEST(valid_wgsl_reports_no_error) {
    Harness h;
    h.run("dev.pushErrorScope('validation');"
          "globalThis.mod = dev.createShaderModule({code: WGSL});"
          "mod.getCompilationInfo().then(function (info) { globalThis.info = info; });");
    for (int i = 0; i < 10 && h.js_string("typeof info") != "object"; ++i) h.engine.pump_message_loop();
    CHECK(h.js_string("info.messages.length") == "0");
    h.pop_into_popped();
    CHECK(h.js_string("popped === null") == "true");
    CHECK(h.escaped_errors().empty());
}

FRAMEWORK_TEST(a_pipeline_validation_error_is_caught_the_same_way) {
    Harness h;
    h.run("__gpuCanvasConfigureImpl('c', 32, 32, 'bgra8unorm');"
          "dev.pushErrorScope('validation');"
          // Valid WGSL whose requested vertex entry point does not exist: the
          // module compiles, the pipeline does not.
          "globalThis.drawn = __gpuQueueDrawImpl('c', WGSL, 'no_such_entry', WGSL, 'fs',"
          " 'bgra8unorm', 'triangle-list', 3, 1, 0, 0, '');");
    h.pop_into_popped();

    CHECK(h.js_string("popped !== null && popped !== 'pending'") == "true");
    CHECK(h.js_string("popped.name") == "GPUValidationError");
    CHECK(h.js_string("popped.message.length > 0") == "true");
    CHECK(h.escaped_errors().empty());
}

FRAMEWORK_TEST(a_valid_draw_reports_no_error) {
    Harness h;
    h.run("__gpuCanvasConfigureImpl('c', 32, 32, 'bgra8unorm');"
          "dev.pushErrorScope('validation');"
          "globalThis.drawn = __gpuQueueDrawImpl('c', WGSL, 'vs', WGSL, 'fs',"
          " 'bgra8unorm', 'triangle-list', 3, 1, 0, 0, '');");
    h.pop_into_popped();

    CHECK(h.js_string("drawn") == "true");
    CHECK(h.js_string("popped === null") == "true");
    CHECK(h.escaped_errors().empty());
}

FRAMEWORK_TEST(an_error_goes_to_the_innermost_scope_with_a_matching_filter) {
    Harness h;
    h.run("dev.pushErrorScope('validation');"
          "dev.pushErrorScope('out-of-memory');"
          "dev.createShaderModule({code: " + Harness::js_literal(kBadWgsl) + "});"
          "globalThis.inner = 'pending'; globalThis.outer = 'pending';"
          "dev.popErrorScope().then(function (e) { globalThis.inner = e; });"
          "dev.popErrorScope().then(function (e) { globalThis.outer = e; });");
    for (int i = 0; i < 10 && h.js_string("outer") == "pending"; ++i) h.engine.pump_message_loop();

    CHECK(h.js_string("inner === null") == "true");
    CHECK(h.js_string("outer !== null && outer.name") == "GPUValidationError");
    CHECK(h.escaped_errors().empty());
}

} // namespace

int main() {
    // The device library leaves Dawn's proc table to the host; this executable
    // is the host, so it registers the library's own native table.
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    g_surface = gpu_runtime::GpuSurface::create_dawn();
    gpu_runtime::GpuSurface::Config config;
    config.width = 64;
    config.height = 64;
    config.vsync = false;
    config.backend_preference = gpu_runtime::GpuSurface::AdapterBackendPreference::null_backend;
    if (!g_surface || !g_surface->initialize(config) || !g_surface->adapter_info().native_bridge) {
        std::printf("SKIP no Dawn adapter available\n");
        return 77;
    }
    return framework_test::run_all();
}
