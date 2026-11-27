// Caller-supplied SkSL that declares names Graphite uses for its own
// runtime-effect parameters, drawn on a live offscreen Dawn + Skia Graphite
// surface.
//
// Graphite emits a runtime shader's `main(float2 xy)` as a helper whose
// coordinate parameter is named `pos`. Skia releases before chrome/m158 renamed
// the author's parameter but not the author's locals, so a local `pos` in
// `main` either redefined the parameter (the pipeline failed to compile and the
// null pipeline crashed inside insertRecording) or, in a nested block, shadowed
// the coordinates and rendered black. The linked Skia keeps author locals apart
// from that parameter, so every case here paints a horizontal red ramp,
// r = x / 64, and must match a control shader whose local is named `q`: locals
// named `pos`, `inColor` and `destColor` in `main` and in nested blocks,
// globals and uniforms named `pos`, helper-function locals, and a `main` whose
// own parameter is `pos`. These pin the linked Skia's behaviour, so a Skia
// update that brings the collision back fails here instead of in a user's
// editor. Offscreen only; the executable exits 77 (skipped) when there is no
// Dawn adapter or the adapter composites no shader output.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <vellum/canvas/canvas.hpp>
#include <vellum/render/skia_surface.hpp>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

namespace {

using gpu_runtime::GpuSurface;
using vellum::render::SkiaSurface;
namespace canvas = vellum::canvas;

constexpr uint32_t kCell = 64;

struct NamedShader {
    const char* label;
    const char* sksl;
};

// The control: a local that matches no Graphite parameter name.
constexpr NamedShader kControl{
    "control local q",
    "half4 main(float2 xy) { float2 q = xy; return half4(half(q.x / 64.0), 0, 0, 1); }"};

// Each must compile and render exactly like the control.
constexpr NamedShader kNameAlikes[] = {
    {"local pos in main",
     "half4 main(float2 xy) { float2 pos = xy; return half4(half(pos.x / 64.0), 0, 0, 1); }"},
    // A nested `pos` declared before a use of `xy`: if Graphite's coordinate
    // parameter shared the author's scope, the use would bind to this zero
    // local and the ramp would go black.
    {"nested-block pos shadowing xy",
     "half4 main(float2 xy) { half r = 0; { float2 pos = float2(0.0); "
     "r = half(xy.x / 64.0) + half(pos.y); } return half4(r, 0, 0, 1); }"},
    {"local inColor in main",
     "half4 main(float2 xy) { half4 inColor = half4(half(xy.x / 64.0), 0, 0, 1); "
     "return inColor; }"},
    {"local destColor in main",
     "half4 main(float2 xy) { half4 destColor = half4(half(xy.x / 64.0), 0, 0, 1); "
     "return destColor; }"},
    {"nested-block inColor and destColor",
     "half4 main(float2 xy) { half r = 0; { half4 inColor = half4(0); "
     "half4 destColor = half4(0); r = half(xy.x / 64.0) + inColor.r + destColor.r; } "
     "return half4(r, 0, 0, 1); }"},
    {"global const pos read in main",
     "const float2 pos = float2(16.0, 16.0); "
     "half4 main(float2 xy) { return half4(half((xy.x - pos.x + 16.0) / 64.0), 0, 0, 1); }"},
    // A uniform named pos keeps its own (zero) value; Graphite's coordinate
    // parameter does not shadow it.
    {"uniform pos read in main",
     "uniform float2 pos; "
     "half4 main(float2 xy) { return half4(half((xy.x + pos.x) / 64.0), 0, 0, 1); }"},
    {"global pos beside a uniform",
     "uniform float value; float2 pos = float2(16.0, 16.0); "
     "half4 main(float2 xy) { return half4(half((xy.x - pos.x + 16.0) / 64.0), 0, 0, 1); }"},
    {"main parameter named pos",
     "half4 main(float2 pos) { half r = 0; { float2 q = pos; r = half(q.x / 64.0); } "
     "return half4(r, 0, 0, 1); }"},
    {"locals in a helper function",
     "half4 ramp(float2 p) { float2 pos = p; half4 inColor = half4(half(pos.x / 64.0), 0, 0, 1); "
     "half4 destColor = inColor; return destColor; } "
     "half4 main(float2 xy) { return ramp(xy); }"},
};

struct Fixture {
    std::unique_ptr<GpuSurface> gpu;
    std::unique_ptr<SkiaSurface> skia;
};

Fixture make_fixture(uint32_t w, uint32_t h) {
    Fixture f;
    f.gpu = GpuSurface::create_dawn();
    if (!f.gpu)
        return f;
    GpuSurface::Config gpu_config{};
    gpu_config.width = w;
    gpu_config.height = h;
    gpu_config.native_surface_handle = nullptr;
    if (!f.gpu->initialize(gpu_config)) {
        f.gpu.reset();
        return f;
    }
    SkiaSurface::Config skia_config{};
    skia_config.width = w;
    skia_config.height = h;
    skia_config.scale_factor = 1.0f;
    f.skia = SkiaSurface::create(*f.gpu, skia_config);
    return f;
}

struct Frame {
    std::vector<uint8_t> px;
    uint32_t w = 0;
    uint32_t h = 0;
    std::vector<bool> drawn;
};

// Paints every shader into its own kCell-wide column over a blue backdrop, so
// a shader that did not run leaves blue and one that ran leaves a red ramp.
bool paint(Fixture& f, const std::vector<NamedShader>& shaders, Frame& out) {
    if (!f.gpu->begin_frame())
        return false;
    auto* c = f.skia->begin_frame();
    if (!c) {
        f.gpu->end_frame();
        return false;
    }
    c->set_fill_color(canvas::Color::rgba8(0, 0, 255, 255));
    c->fill_rect(0, 0, static_cast<float>(kCell * shaders.size()), kCell);
    canvas::Canvas::ShaderUniforms uniforms{};
    out.drawn.clear();
    for (size_t i = 0; i < shaders.size(); ++i) {
        out.drawn.push_back(c->draw_with_sksl(shaders[i].sksl, static_cast<float>(i * kCell), 0,
                                              kCell, kCell, uniforms));
    }
    f.skia->end_frame();
    f.gpu->end_frame();
    return f.skia->read_current_rgba(out.px, out.w, out.h);
}

const uint8_t* pixel(const Frame& frame, uint32_t x, uint32_t y) {
    return frame.px.data() + (static_cast<size_t>(y) * frame.w + x) * 4;
}

FRAMEWORK_TEST(runtime_effect_locals_named_like_graphites_parameters_render_as_written) {
    std::vector<NamedShader> shaders{kControl};
    for (const auto& s : kNameAlikes)
        shaders.push_back(s);

    for (const auto& s : shaders) {
        if (!canvas::Canvas::compile_sksl(s.sksl).empty())
            std::fprintf(stderr, "  does not compile: %s\n", s.label);
        CHECK(canvas::Canvas::compile_sksl(s.sksl).empty());
    }

    auto f = make_fixture(static_cast<uint32_t>(kCell * shaders.size()), kCell);
    CHECK(f.gpu != nullptr);
    CHECK(f.skia != nullptr);
    if (!f.gpu || !f.skia || !f.skia->is_available())
        return;

    Frame frame;
    CHECK(paint(f, shaders, frame));
    CHECK(frame.w == kCell * shaders.size());
    CHECK(frame.h == kCell);
    if (frame.px.empty() || frame.w != kCell * shaders.size())
        return;

    // The control must have produced the ramp, or the instrument is blind.
    // r = 48 / 64 = 0.75 at the sampled column; blue must be gone.
    const uint8_t* control_mid = pixel(frame, kCell / 2 + kCell / 4, kCell / 2);
    CHECK(frame.drawn[0]);
    CHECK(std::abs(int(control_mid[0]) - 191) <= 3);
    CHECK(control_mid[2] <= 3);
    if (std::abs(int(control_mid[0]) - 191) > 3)
        return;

    for (size_t i = 1; i < shaders.size(); ++i) {
        if (!frame.drawn[i])
            std::fprintf(stderr, "  not drawn: %s\n", shaders[i].label);
        CHECK(frame.drawn[i]);
        for (uint32_t local_x : {8u, 32u, 56u}) {
            const uint32_t x = static_cast<uint32_t>(i * kCell) + local_x;
            const uint8_t* got = pixel(frame, x, kCell / 2);
            // Must match the control column; the blue backdrop means the
            // shader was refused or dropped, black means `pos` shadowed the
            // coordinates.
            const uint8_t* want = pixel(frame, local_x, kCell / 2);
            const bool same = std::abs(int(got[0]) - int(want[0])) <= 1 &&
                              std::abs(int(got[1]) - int(want[1])) <= 1 &&
                              std::abs(int(got[2]) - int(want[2])) <= 1;
            if (!same)
                std::fprintf(stderr, "  %s at x %u: got rgba(%d,%d,%d) want rgba(%d,%d,%d)\n",
                             shaders[i].label, local_x, got[0], got[1], got[2], want[0], want[1],
                             want[2]);
            CHECK(same);
        }
    }
}

} // namespace

int main() {
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    auto probe = make_fixture(static_cast<uint32_t>(kCell), kCell);
    if (!probe.gpu || !probe.skia || !probe.skia->is_available()) {
        std::printf("SKIP Dawn/Graphite unavailable on this host\n");
        return 77;
    }
    if (probe.gpu->adapter_info().backend_type == "Null") {
        std::printf("SKIP the Null backend composites no shader output\n");
        return 77;
    }
    probe = {};
    return framework_test::run_all();
}
