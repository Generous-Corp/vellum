// The CPU renderer's speed, recorded and not gated: a 2275 x 1197 context
// with 400 textured quads (a 20 x 20 grid of 64 x 64 texel tiles, each about
// 113 x 59 context units, half of them translucent) over a full-frame
// background, rendered through Rml::RenderInterface calls. The first argument
// is the target scale (default 1; 2 gives a 4550 x 2394 pixel frame).
//
// Prints the median and fastest frame, and the median of three partial
// frames, to show where the time goes: the clear alone, the clear plus the
// background, and the full frame. Registered with the `bench` label, outside
// the required tests.
#include <vellum/rmlui/cpu_render_interface.hpp>

#include "test_support.hpp"

#include <RmlUi/Core/Dictionary.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kWidth = 2275;
constexpr std::uint32_t kHeight = 1197;
constexpr int kColumns = 20;
constexpr int kRows = 20;
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

double median_ms(int frames, const std::function<void()>& draw) {
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
    return times[times.size() / 2];
}

} // namespace

int main(int argc, char** argv) {
    const float scale = argc > 1 ? std::strtof(argv[1], nullptr) : 1.0f;
    VELLUM_RMLUI_CHECK(scale >= 1.0f && scale <= 4.0f);
    CpuRenderInterface r;
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
    const float w = float(kWidth) / kColumns;
    const float h = float(kHeight) / kRows;
    for (int y = 0; y < kRows; ++y)
        for (int x = 0; x < kColumns; ++x) {
            const bool translucent = (x + y) % 2 == 1;
            const Rml::ColourbPremultiplied tint =
                translucent ? Rml::ColourbPremultiplied{180, 180, 180, 180} : Rml::ColourbPremultiplied{255, 255, 255, 255};
            tiles.push_back(quad(r, x * w + 2.0f, y * h + 2.0f, (x + 1) * w - 2.0f, (y + 1) * h - 2.0f, tint));
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
    std::vector<double> times;
    for (int i = 0; i < kFrames + 1; ++i) {
        const auto start = std::chrono::steady_clock::now();
        VELLUM_RMLUI_CHECK(r.begin_frame(frame));
        r.RenderGeometry(background, {0.0f, 0.0f}, 0);
        for (const auto tile : tiles)
            r.RenderGeometry(tile, {0.0f, 0.0f}, texture);
        r.end_frame();
        const double ms =
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        if (i > 0)
            times.push_back(ms);
    }
    std::sort(times.begin(), times.end());
    std::printf("cpu bench: %ux%u px (scale %.0f), %zu textured quads: median %.2f ms, fastest %.2f ms "
                "over %d frames; clear alone %.2f ms, clear + background %.2f ms\n",
                pixels_w, pixels_h, scale, tiles.size(), times[times.size() / 2], times.front(), kFrames,
                clear_ms, background_ms);
    return 0;
}
