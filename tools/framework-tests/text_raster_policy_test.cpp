// The glyph preblend every Skia surface is created with
// (vellum/canvas/text_raster_policy.hpp).
//
// Contrast and gamma live on the SURFACE, so the property that matters is that
// every surface a frame's text can pass through carries the same one. A layer
// or scene target created with defaults caches its text at a different weight
// from the same text painted directly, and nothing else fails when it does.
// The policy selection and tuples are pure; the rest render text on a real
// Dawn device and read it back. The weight claims (a `chromium` surface paints
// `auto` text within a few percent of a `legacy` one) are macOS measurements,
// since CoreText rasterizes through Skia there, and are asserted on Apple only;
// the plumbing assertions run everywhere. Offscreen only. The executable exits
// 77 (skipped) when there is no Dawn adapter.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <vellum/canvas/bundled_fonts.hpp>
#include <vellum/canvas/canvas.hpp>
#include <vellum/canvas/text_raster_policy.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/render/skia_surface.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace {

namespace canvas = vellum::canvas;
using canvas::FontSmoothing;
using canvas::TextRasterPolicy;
using vellum::render::HeadlessSurface;

constexpr int kW = 120;
constexpr int kH = 32;
constexpr float kScale = 2.0f;
constexpr const char* kFamily = "Text Raster Policy Mono";

bool register_fixture_font() {
    static const bool registered =
        canvas::register_font_file(VELLUM_TEST_FONT_PATH, kFamily);
    return registered;
}

// A Spectr toolbar label: JetBrains Mono 10px, 1px tracking, 85% white.
void draw_label(canvas::Canvas& c) {
    c.set_font_full(kFamily, 10.0f, 400, 0, 1.0f);
    c.set_fill_color(canvas::Color::rgba8(255, 255, 255, 217));
    c.fill_text("SCULPT", 8.0f, 20.0f);
}

HeadlessSurface::Config headless_config(TextRasterPolicy policy) {
    HeadlessSurface::Config cfg;
    cfg.width = kW;
    cfg.height = kH;
    cfg.scale_factor = kScale;
    cfg.clear_r = 14;
    cfg.clear_g = 16;
    cfg.clear_b = 19;
    cfg.text_raster_policy = policy;
    return cfg;
}

std::vector<uint8_t> render_direct(TextRasterPolicy policy) {
    std::string err;
    auto surface = HeadlessSurface::create(headless_config(policy), &err);
    if (!surface)
        return {};
    return surface->render_rgba([](canvas::Canvas& c) { draw_label(c); }).pixels;
}

// Glyph ink above the flat background: the summed luminance excess. Ink ratios
// are position-blind, so they compare weight and nothing else.
double ink_of(const std::vector<uint8_t>& px) {
    if (px.size() < 4)
        return 0.0;
    auto luma = [&](size_t i) { return (px[i] + px[i + 1] + px[i + 2]) / 3.0; };
    const double bg = luma(0);
    double ink = 0.0;
    for (size_t i = 0; i + 3 < px.size(); i += 4)
        ink += std::max(0.0, luma(i) - bg);
    return ink;
}

FRAMEWORK_TEST(policy_turns_chromium_when_any_text_node_is_antialiased) {
    using canvas::select_text_raster_policy;
    CHECK(select_text_raster_policy({}) == TextRasterPolicy::legacy);

    const std::array<FontSmoothing, 3> all_auto{FontSmoothing::auto_smoothing,
                                                FontSmoothing::auto_smoothing,
                                                FontSmoothing::auto_smoothing};
    CHECK(select_text_raster_policy(all_auto) == TextRasterPolicy::legacy);

    // A body on `auto` with one `antialiased` span: the surface flips. Taking
    // the root's mode instead would paint the span about 21% too heavy.
    const std::array<FontSmoothing, 3> one_antialiased_span{
        FontSmoothing::auto_smoothing, FontSmoothing::antialiased, FontSmoothing::auto_smoothing};
    CHECK(select_text_raster_policy(one_antialiased_span) == TextRasterPolicy::chromium);

    const std::array<FontSmoothing, 1> antialiased_body{FontSmoothing::antialiased};
    CHECK(select_text_raster_policy(antialiased_body) == TextRasterPolicy::chromium);
}

FRAMEWORK_TEST(raster_tuples_are_the_measured_contrast_and_gamma) {
    CHECK(canvas::text_raster_tuple(TextRasterPolicy::chromium).contrast == 0.0f);
    CHECK(canvas::text_raster_tuple(TextRasterPolicy::chromium).gamma == 1.0f);
    CHECK(canvas::text_raster_tuple(TextRasterPolicy::legacy).contrast == 0.5f);
    CHECK(canvas::text_raster_tuple(TextRasterPolicy::legacy).gamma == 0.0f);
}

// A surface's retained layer must rasterize text under the surface's own
// policy: the label recorded into a cacheable layer and composited back is
// within one level of the same label painted directly.
FRAMEWORK_TEST(a_gpu_surface_carries_its_policy_into_retained_layers) {
    CHECK(register_fixture_font());
    std::vector<std::vector<uint8_t>> frames;
    for (const TextRasterPolicy policy : {TextRasterPolicy::legacy, TextRasterPolicy::chromium}) {
        std::string err;
        auto surface = HeadlessSurface::create(headless_config(policy), &err);
        CHECK(surface != nullptr);
        if (!surface)
            return;
        const auto direct = surface->render_rgba([](canvas::Canvas& c) { draw_label(c); });
        const auto layered = surface->render_rgba([](canvas::Canvas& c) {
            c.begin_layer({0.0f, 0.0f, static_cast<float>(kW), static_cast<float>(kH)},
                          /*cacheable=*/true);
            draw_label(c);
            const auto handle = c.end_layer();
            c.draw_layer(handle);
        });
        CHECK(!direct.empty());
        CHECK(ink_of(direct.pixels) > 0.0);
        CHECK(direct.pixels.size() == layered.pixels.size());
        int worst = 0;
        for (size_t i = 0; i < std::min(direct.pixels.size(), layered.pixels.size()); ++i)
            worst = std::max(worst, std::abs(static_cast<int>(direct.pixels[i]) -
                                             static_cast<int>(layered.pixels[i])));
        if (worst > 1)
            std::fprintf(stderr, "  policy %d: layered text differs by %d levels\n",
                         static_cast<int>(policy), worst);
        CHECK(worst <= 1);
        frames.push_back(direct.pixels);
    }
#if defined(__APPLE__)
    CHECK(frames.size() == 2 && frames[0] != frames[1]);
#endif
}

#if defined(__APPLE__)
// Without this, the layered/direct agreement above would also pass for a
// policy that never reached the glyph rasterizer. With the painter's hinting,
// the linear preblend leaves CoreText smoothing on and adds about 2% ink.
FRAMEWORK_TEST(a_chromium_surface_changes_how_text_rasterizes) {
    CHECK(register_fixture_font());
    const auto legacy = render_direct(TextRasterPolicy::legacy);
    const auto chromium = render_direct(TextRasterPolicy::chromium);
    CHECK(!legacy.empty());
    CHECK(chromium != legacy);
    CHECK(ink_of(chromium) / std::max(ink_of(legacy), 1.0) > 1.005);
}

// A body set `antialiased` with one span on `auto`: the surface is chromium and
// the span keeps its own hinting. A legacy surface paints `auto` text exactly
// as Chromium does, so it is the span's reference.
FRAMEWORK_TEST(an_auto_span_on_a_chromium_surface_stays_within_the_ink_band) {
    const std::array<FontSmoothing, 2> page{FontSmoothing::antialiased,
                                            FontSmoothing::auto_smoothing};
    CHECK(canvas::select_text_raster_policy(page) == TextRasterPolicy::chromium);

    CHECK(register_fixture_font());
    const double reference = ink_of(render_direct(TextRasterPolicy::legacy));
    const double span = ink_of(render_direct(canvas::select_text_raster_policy(page)));
    CHECK(reference > 0.0);
    CHECK(std::abs(span / reference - 1.0) <= 0.03);
}
#endif

// One frame of the label on `skia`, read back. With persistent scene on, the
// frame is drawn into the retained scene target instead of the offscreen one.
std::vector<uint8_t> skia_surface_frame(gpu_runtime::GpuSurface& gpu, vellum::render::SkiaSurface& skia) {
    std::vector<uint8_t> pixels;
    if (!gpu.begin_frame())
        return pixels;
    auto* c = skia.begin_frame();
    if (!c) {
        gpu.end_frame();
        return pixels;
    }
    c->set_fill_color(canvas::Color::rgba8(14, 16, 19));
    c->fill_rect(0, 0, kW, kH);
    draw_label(*c);
    uint32_t w = 0, h = 0;
    if (!skia.read_current_rgba(pixels, w, h))
        pixels.clear();
    skia.end_frame();
    gpu.end_frame();
    return pixels;
}

FRAMEWORK_TEST(the_persistent_scene_frame_matches_the_offscreen_frame_under_each_policy) {
    CHECK(register_fixture_font());
    auto gpu = gpu_runtime::GpuSurface::create_dawn();
    CHECK(gpu != nullptr);
    if (!gpu)
        return;
    gpu_runtime::GpuSurface::Config gpu_config{};
    gpu_config.width = kW;
    gpu_config.height = kH;
    CHECK(gpu->initialize(gpu_config));

    std::vector<std::vector<uint8_t>> offscreen_frames;
    for (const TextRasterPolicy policy : {TextRasterPolicy::legacy, TextRasterPolicy::chromium}) {
        vellum::render::SkiaSurface::Config config{};
        config.width = kW;
        config.height = kH;
        config.scale_factor = kScale;
        config.text_raster_policy = policy;
        auto skia = vellum::render::SkiaSurface::create(*gpu, config);
        CHECK(skia != nullptr);
        if (!skia)
            return;

        const auto offscreen = skia_surface_frame(*gpu, *skia);
        CHECK(skia->set_persistent_scene(true));
        const auto scene = skia_surface_frame(*gpu, *skia);
        CHECK(!offscreen.empty());
        CHECK(offscreen.size() == scene.size());
        CHECK(offscreen == scene);
        offscreen_frames.push_back(offscreen);
    }
#if defined(__APPLE__)
    CHECK(offscreen_frames.size() == 2 && offscreen_frames[0] != offscreen_frames[1]);
#endif
}

FRAMEWORK_TEST(changing_a_skia_surfaces_policy_is_reported_back) {
    auto gpu = gpu_runtime::GpuSurface::create_dawn();
    CHECK(gpu != nullptr);
    if (!gpu)
        return;
    gpu_runtime::GpuSurface::Config gpu_config{};
    gpu_config.width = kW;
    gpu_config.height = kH;
    CHECK(gpu->initialize(gpu_config));
    vellum::render::SkiaSurface::Config config{};
    config.width = kW;
    config.height = kH;
    config.text_raster_policy = TextRasterPolicy::chromium;
    auto skia = vellum::render::SkiaSurface::create(*gpu, config);
    CHECK(skia != nullptr);
    if (!skia)
        return;
    CHECK(skia->text_raster_policy() == TextRasterPolicy::chromium);
    skia->set_text_raster_policy(TextRasterPolicy::legacy);
    CHECK(skia->text_raster_policy() == TextRasterPolicy::legacy);
}

} // namespace

int main() {
    // The selection and tuple cases are pure; the rest need a device.
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    std::string err;
    auto probe = HeadlessSurface::create(headless_config(TextRasterPolicy::legacy), &err);
    if (!probe) {
        std::printf("SKIP no headless GPU surface: %s\n", err.c_str());
        return 77;
    }
    probe.reset();
    return framework_test::run_all();
}
