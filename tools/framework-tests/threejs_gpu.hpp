// GPU side of the Three.js bridge tests: one headless Dawn surface per test
// executable, and a readback of what a scripted canvas composites through
// Skia, which is the path CanvasWidget::paint takes in a live view.
#pragma once

#include "threejs_harness.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>
#include <vellum/view/canvas_widget.hpp>

#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

namespace threejs_test {

enum class AdapterNeed {
    // A real adapter, or the test is skipped: the case reads pixels back.
    rendering,
    // A real adapter when there is one, else Dawn's Null adapter: the case
    // checks behaviour that does not depend on what reaches the pixels.
    validation,
};

inline std::unique_ptr<gpu_runtime::GpuSurface>& surface() {
    static std::unique_ptr<gpu_runtime::GpuSurface> instance;
    return instance;
}

inline std::unique_ptr<gpu_runtime::GpuSurface> try_surface(gpu_runtime::GpuSurface::AdapterBackendPreference backend) {
    auto candidate = gpu_runtime::GpuSurface::create_dawn();
    gpu_runtime::GpuSurface::Config config;
    config.width = 320;
    config.height = 220;
    config.vsync = false;
    config.backend_preference = backend;
    if (!candidate || !candidate->initialize(config) || !candidate->adapter_info().native_bridge) return nullptr;
    return candidate;
}

// Sets up surface() and returns 0, or prints why not and returns 77 (skipped).
inline int open_surface(AdapterNeed need) {
    // The device library leaves Dawn's proc table to the host; this executable
    // is the host, so it registers the library's own native table.
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    using Backend = gpu_runtime::GpuSurface::AdapterBackendPreference;
    // VELLUM_THREEJS_TEST_ADAPTER=null runs as a host with no real adapter.
    const char* forced = std::getenv("VELLUM_THREEJS_TEST_ADAPTER");
    const bool null_only = forced != nullptr && std::string(forced) == "null";
    auto real = null_only ? nullptr : try_surface(Backend::default_backend);
    if (real && real->adapter_info().null_backend) real.reset();
    if (real) {
        surface() = std::move(real);
    } else if (need == AdapterNeed::validation) {
        surface() = try_surface(Backend::null_backend);
    }
    if (!surface()) {
        std::printf("SKIP no %sDawn adapter available\n", need == AdapterNeed::rendering ? "rendering " : "");
        return 77;
    }
    const auto& info = surface()->adapter_info();
    std::printf("adapter: %s (%s)\n", info.name.c_str(), info.backend.c_str());
    return 0;
}

struct Readback {
    bool painted = false;
    uint32_t width = 0;
    uint32_t height = 0;
    std::vector<uint8_t> rgba;

    bool ok() const { return painted && !rgba.empty() && rgba.size() == size_t(width) * height * 4u; }

    const uint8_t* at(uint32_t x, uint32_t y) const { return rgba.data() + (size_t(y) * width + x) * 4u; }
    const uint8_t* center() const { return at(width / 2u, height / 2u); }

    // Pixels for which `pred(r, g, b)` holds.
    template <typename Pred> size_t count(Pred pred) const {
        size_t n = 0;
        for (size_t i = 0; i + 3 < rgba.size(); i += 4)
            if (pred(int(rgba[i]), int(rgba[i + 1]), int(rgba[i + 2]))) ++n;
        return n;
    }
};

// Lays out the view, paints the canvas element `element_id` into a Skia
// surface of `width` x `height` on the shared device and reads it back.
inline Readback paint_canvas(ScriptedView& v, const std::string& element_id, uint32_t width, uint32_t height) {
    Readback out;
    v.root.layout_children();
    const auto native_id = v.js_string("document.getElementById('" + element_id + "')._id");
    auto* widget = dynamic_cast<view::CanvasWidget*>(v.bridge.widget(native_id));
    if (widget == nullptr) return out;
    auto skia = vellum::render::SkiaSurface::create(*surface(), {.width = width, .height = height});
    if (!skia || !skia->is_available() || !surface()->begin_frame()) return out;
    auto* canvas = skia->begin_frame();
    if (canvas == nullptr) return out;
    widget->paint(*canvas);
    out.painted = widget->last_native_gpu_texture_draw_succeeded();
    skia->end_frame();
    const bool read = skia->read_current_rgba(out.rgba, out.width, out.height);
    surface()->end_frame();
    if (!read) out.rgba.clear();
    return out;
}

// Prints the center pixel. With VELLUM_THREEJS_TEST_DUMP set to a directory,
// also writes the readback there as <label>-<w>x<h>.rgba for inspection.
inline void report(const char* label, const Readback& r) {
    if (!r.ok()) {
        std::fprintf(stderr, "  %s: no readback (painted=%d)\n", label, int(r.painted));
        return;
    }
    if (const char* dir = std::getenv("VELLUM_THREEJS_TEST_DUMP")) {
        const auto name = std::string(label) + "-" + std::to_string(r.width) + "x" + std::to_string(r.height) + ".rgba";
        std::ofstream(std::filesystem::path(dir) / name, std::ios::binary)
            .write(reinterpret_cast<const char*>(r.rgba.data()), std::streamsize(r.rgba.size()));
    }
    const auto* c = r.center();
    std::fprintf(stderr, "  %s: %ux%u center rgb=%d,%d,%d\n", label, r.width, r.height, c[0], c[1], c[2]);
}

// Boilerplate a scene module needs: a canvas element of the given size in
// `parent`, its webgpu context, and an initialised renderer on it.
inline std::string renderer_setup(const std::string& id, int width, int height, const std::string& parent) {
    return "const canvas = document.createElement('canvas');\n"
           "canvas.id = '" + id + "'; canvas.width = " + std::to_string(width) +
           "; canvas.height = " + std::to_string(height) + ";\n" + parent + ".appendChild(canvas);\n"
           "const context = canvas.getContext('webgpu');\n"
           "const renderer = new THREE.WebGPURenderer({canvas: new CanvasAdapter(canvas), context, antialias: false});\n"
           "await renderer.init();\n"
           "function frame(scene, camera) {\n"
           "    renderer.render(scene, camera);\n"
           "    if (typeof context.present === 'function') context.present();\n"
           "}\n";
}

} // namespace threejs_test
