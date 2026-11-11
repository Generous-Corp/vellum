// The CPU renderer's speed, recorded and not gated, on a 2275 x 1197 context
// through Rml::RenderInterface calls. The first argument is the target scale
// (default 1; 2 gives a 4550 x 2394 pixel frame), the second the renderer's
// thread count (default 1). Two scenes:
//
// - quads: 400 textured quads (a 20 x 20 grid of 64 x 64 texel tiles, each
//   about 113 x 59 context units, half of them translucent) over a full-frame
//   opaque background.
// - clips: the same background, then 100 rounded panels, each clipped the way
//   RmlUi clips `overflow: hidden` with a border radius: a clip mask set from
//   a 32-segment rounded shape and intersected with a rectangle, then a
//   textured fill under it. Every fourth panel is drawn in a layer and
//   composited.
//
// Prints, per scene, the median and fastest of the timed frames; for quads
// also the clear alone and the clear plus the background, to show where the
// time goes. Registered with the `bench` label, outside the required tests.
#include <vellum/rmlui/cpu_render_interface.hpp>

#include "test_support.hpp"

#include <RmlUi/Core/Dictionary.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kWidth = 2275;
constexpr std::uint32_t kHeight = 1197;
constexpr int kFrames = 15;

Rml::CompiledGeometryHandle quad(CpuRenderInterface& r, float x0, float y0, float x1, float y1,
                                 Rml::ColourbPremultiplied colour) {
    std::array<Rml::Vertex, 4> v;
    const float corners[4][4] = {{x0, y0, 0, 0}, {x1, y0, 1, 0}, {x1, y1, 1, 1}, {x0, y1, 0, 1}};
    for (int i = 0; i < 4; ++i) {
        v[i].position = {corners[i][0], corners[i][1]};
        v[i].colour = colour;
        v[i].tex_coord = {corners[i][2], corners[i][3]};
    }
    const std::array<int, 6> i = {0, 1, 2, 0, 2, 3};
    return r.CompileGeometry(Rml::Span<const Rml::Vertex>(v.data(), v.size()),
                             Rml::Span<const int>(i.data(), i.size()));
}

// A rounded rectangle as a triangle fan around its centre, 8 segments a corner.
Rml::CompiledGeometryHandle rounded(CpuRenderInterface& r, float x0, float y0, float x1, float y1, float radius) {
    std::vector<Rml::Vertex> v;
    Rml::Vertex centre;
    centre.position = {(x0 + x1) * 0.5f, (y0 + y1) * 0.5f};
    centre.colour = {255, 255, 255, 255};
    v.push_back(centre);
    const float cx[4] = {x1 - radius, x0 + radius, x0 + radius, x1 - radius};
    const float cy[4] = {y0 + radius, y0 + radius, y1 - radius, y1 - radius};
    for (int corner = 0; corner < 4; ++corner)
        for (int step = 0; step <= 8; ++step) {
            const double angle = (corner * 90.0 + step * 11.25) * 3.14159265358979323846 / 180.0;
            Rml::Vertex p = centre;
            p.position = {cx[corner] + radius * static_cast<float>(std::cos(angle)),
                          cy[corner] - radius * static_cast<float>(std::sin(angle))};
            v.push_back(p);
        }
    std::vector<int> indices;
    const int ring = static_cast<int>(v.size()) - 1;
    for (int i = 0; i < ring; ++i) {
        indices.push_back(0);
        indices.push_back(1 + i);
        indices.push_back(1 + (i + 1) % ring);
    }
    return r.CompileGeometry(Rml::Span<const Rml::Vertex>(v.data(), v.size()),
                             Rml::Span<const int>(indices.data(), indices.size()));
}

double median_ms(int frames, const std::function<void()>& draw, double* fastest = nullptr) {
    std::vector<double> times;
    for (int i = 0; i < frames + 1; ++i) {
        const auto start = std::chrono::steady_clock::now();
        draw();
        const double ms =
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        if (i > 0) // the first frame warms the caches
            times.push_back(ms);
    }
    std::sort(times.begin(), times.end());
    if (fastest)
        *fastest = times.front();
    return times[times.size() / 2];
}

} // namespace

int main(int argc, char** argv) {
    const float scale = argc > 1 ? std::strtof(argv[1], nullptr) : 1.0f;
    VELLUM_RMLUI_CHECK(scale >= 1.0f && scale <= 4.0f);
    const auto threads = argc > 2 ? static_cast<unsigned>(std::strtoul(argv[2], nullptr, 10)) : 1u;
    VELLUM_RMLUI_CHECK(threads >= 1 && threads <= 16);

    CpuRenderInterface r;
    r.set_threads(threads);
    std::vector<std::uint8_t> texels(64 * 64 * 4);
    for (std::size_t i = 0; i < 64 * 64; ++i) {
        const auto alpha = static_cast<std::uint8_t>(i % 3 == 0 ? 160 : 255);
        texels[i * 4 + 0] = static_cast<std::uint8_t>((i * 7) % alpha);
        texels[i * 4 + 1] = static_cast<std::uint8_t>((i * 13) % alpha);
        texels[i * 4 + 2] = static_cast<std::uint8_t>((i * 29) % alpha);
        texels[i * 4 + 3] = alpha;
    }
    const auto texture = r.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), texels.size()), {64, 64});
    VELLUM_RMLUI_CHECK(texture != 0);

    const auto background = quad(r, 0.0f, 0.0f, float(kWidth), float(kHeight), {24, 26, 30, 255});
    std::vector<Rml::CompiledGeometryHandle> tiles;
    {
        const float w = float(kWidth) / 20;
        const float h = float(kHeight) / 20;
        for (int y = 0; y < 20; ++y)
            for (int x = 0; x < 20; ++x) {
                const bool translucent = (x + y) % 2 == 1;
                const Rml::ColourbPremultiplied tint = translucent ? Rml::ColourbPremultiplied{180, 180, 180, 180}
                                                                   : Rml::ColourbPremultiplied{255, 255, 255, 255};
                tiles.push_back(quad(r, x * w + 2.0f, y * h + 2.0f, (x + 1) * w - 2.0f, (y + 1) * h - 2.0f, tint));
            }
    }
    struct Panel {
        Rml::CompiledGeometryHandle shape;
        Rml::CompiledGeometryHandle band;
        Rml::CompiledGeometryHandle fill;
    };
    std::vector<Panel> panels;
    {
        const float w = float(kWidth) / 10;
        const float h = float(kHeight) / 10;
        for (int y = 0; y < 10; ++y)
            for (int x = 0; x < 10; ++x) {
                const float x0 = x * w + 6.0f, y0 = y * h + 6.0f, x1 = (x + 1) * w - 6.0f, y1 = (y + 1) * h - 6.0f;
                panels.push_back({rounded(r, x0, y0, x1, y1, 24.0f),
                                  quad(r, x0, y0 + 10.0f, x1, y1 - 10.0f, {255, 255, 255, 255}),
                                  quad(r, x0, y0, x1, y1, {220, 220, 220, 220})});
            }
    }

    const auto pixels_w = static_cast<std::uint32_t>(kWidth * scale);
    const auto pixels_h = static_cast<std::uint32_t>(kHeight * scale);
    std::vector<std::uint8_t> buffer(static_cast<std::size_t>(pixels_w) * pixels_h * 4u);
    CpuFrameTarget frame;
    frame.rgba = buffer.data();
    frame.width = pixels_w;
    frame.height = pixels_h;
    frame.scale = scale;

    const double clear_ms = median_ms(kFrames, [&] {
        VELLUM_RMLUI_CHECK(r.begin_frame(frame));
        r.end_frame();
    });
    const double background_ms = median_ms(kFrames, [&] {
        VELLUM_RMLUI_CHECK(r.begin_frame(frame));
        r.RenderGeometry(background, {0.0f, 0.0f}, 0);
        r.end_frame();
    });
    double quads_fastest = 0.0;
    const double quads_ms = median_ms(
        kFrames,
        [&] {
            VELLUM_RMLUI_CHECK(r.begin_frame(frame));
            r.RenderGeometry(background, {0.0f, 0.0f}, 0);
            for (const auto tile : tiles)
                r.RenderGeometry(tile, {0.0f, 0.0f}, texture);
            r.end_frame();
        },
        &quads_fastest);
    double clips_fastest = 0.0;
    const double clips_ms = median_ms(
        kFrames,
        [&] {
            VELLUM_RMLUI_CHECK(r.begin_frame(frame));
            r.RenderGeometry(background, {0.0f, 0.0f}, 0);
            for (std::size_t i = 0; i < panels.size(); ++i) {
                const bool layered = i % 4 == 0;
                Rml::LayerHandle layer = 0;
                if (layered)
                    layer = r.PushLayer();
                r.EnableClipMask(true);
                r.RenderToClipMask(Rml::ClipMaskOperation::Set, panels[i].shape, {0.0f, 0.0f});
                r.RenderToClipMask(Rml::ClipMaskOperation::Intersect, panels[i].band, {0.0f, 0.0f});
                r.RenderGeometry(panels[i].fill, {0.0f, 0.0f}, texture);
                r.EnableClipMask(false);
                if (layered) {
                    r.CompositeLayers(layer, 0, Rml::BlendMode::Blend, {});
                    r.PopLayer();
                }
            }
            r.end_frame();
        },
        &clips_fastest);
    VELLUM_RMLUI_CHECK(r.unsupported_calls() == 0);

    std::printf("cpu bench: %ux%u px (scale %.0f), %u thread%s\n", pixels_w, pixels_h, scale, threads,
                threads == 1 ? "" : "s");
    std::printf("  quads: %zu textured quads: median %.2f ms, fastest %.2f ms; clear alone %.2f ms, "
                "clear + background %.2f ms\n",
                tiles.size(), quads_ms, quads_fastest, clear_ms, background_ms);
    std::printf("  clips: %zu clipped panels, %zu in layers: median %.2f ms, fastest %.2f ms\n", panels.size(),
                (panels.size() + 3) / 4, clips_ms, clips_fastest);
    std::printf("  (%d timed frames each)\n", kFrames);
    return 0;
}
