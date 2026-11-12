// CpuRenderInterface driven through Rml::RenderInterface calls directly, with
// no RmlUi context: fill and blend arithmetic, textures, scissor, transforms,
// scale and stride, unsupported calls counted by kind, the texture limit, and
// determinism across threads.
#include <vellum/rmlui/cpu_render_interface.hpp>

#include "test_support.hpp"

#include <RmlUi/Core/Dictionary.h>
#include <RmlUi/Core/Matrix4.h>

#include <array>
#include <cstdint>
#include <cstring>
#include <thread>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kSize = 8;

Rml::Vertex vertex(float x, float y, Rml::ColourbPremultiplied colour, float u = 0.0f, float v = 0.0f) {
    Rml::Vertex out;
    out.position = {x, y};
    out.colour = colour;
    out.tex_coord = {u, v};
    return out;
}

// Two triangles covering [x0, x1) x [y0, y1) in context units.
Rml::CompiledGeometryHandle quad(CpuRenderInterface& r, float x0, float y0, float x1, float y1,
                                 Rml::ColourbPremultiplied colour) {
    const std::array<Rml::Vertex, 4> v = {vertex(x0, y0, colour, 0, 0), vertex(x1, y0, colour, 1, 0),
                                          vertex(x1, y1, colour, 1, 1), vertex(x0, y1, colour, 0, 1)};
    const std::array<int, 6> i = {0, 1, 2, 0, 2, 3};
    return r.CompileGeometry(Rml::Span<const Rml::Vertex>(v.data(), v.size()), Rml::Span<const int>(i.data(), i.size()));
}

const std::uint8_t* px(const std::vector<std::uint8_t>& buffer, std::size_t stride, int x, int y) {
    return buffer.data() + static_cast<std::size_t>(y) * stride + static_cast<std::size_t>(x) * 4u;
}

bool is(const std::uint8_t* p, std::uint8_t r, std::uint8_t g, std::uint8_t b, std::uint8_t a) {
    return p[0] == r && p[1] == g && p[2] == b && p[3] == a;
}

CpuFrameTarget frame(std::vector<std::uint8_t>& buffer, std::uint32_t w = kSize, std::uint32_t h = kSize,
                     std::size_t stride = 0, float scale = 1.0f, bool clear = true) {
    CpuFrameTarget f;
    f.rgba = buffer.data();
    f.width = w;
    f.height = h;
    f.stride = stride;
    f.scale = scale;
    f.clear = clear;
    return f;
}

const Rml::ColourbPremultiplied kRed{255, 0, 0, 255};

// Every test runs twice: drawing at once (1 thread) and recording the frame
// and replaying it in bands (3 threads).
unsigned g_threads = 1;

void test_fill_and_untouched_pixels() {
    // A padded stride and a prefilled buffer: only covered pixels change.
    const std::size_t stride = kSize * 4 + 12;
    std::vector<std::uint8_t> buffer(stride * kSize, 0x7f);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer, kSize, kSize, stride, 1.0f, false)));
    r.RenderGeometry(quad(r, 2, 2, 6, 6, kRed), {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, stride, 2, 2), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, stride, 5, 5), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, stride, 6, 6), 0x7f, 0x7f, 0x7f, 0x7f));
    VELLUM_RMLUI_CHECK(is(px(buffer, stride, 1, 2), 0x7f, 0x7f, 0x7f, 0x7f));
    for (std::uint32_t y = 0; y < kSize; ++y)
        for (std::size_t i = kSize * 4; i < stride; ++i)
            VELLUM_RMLUI_CHECK(buffer[y * stride + i] == 0x7f); // padding
    // clear=true zeroes the frame first (the padding stays).
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer, kSize, kSize, stride)));
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, stride, 1, 2), 0, 0, 0, 0));
    VELLUM_RMLUI_CHECK(buffer[kSize * 4] == 0x7f);
}

void test_premultiplied_blend() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, {0, 0, 255, 255}), {0, 0}, 0);
    r.RenderGeometry(quad(r, 0, 0, 8, 8, {128, 0, 0, 128}), {0, 0}, 0);
    r.end_frame();
    // out = src + dst * (255 - src.a) / 255, rounded: 128 + 0, 0, 0 + 127, 128 + 127.
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 3), 128, 0, 127, 255));

    // Values where rounding shows: 200 * 155 / 255 = 121.57 rounds to 122.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, {0, 0, 200, 200}), {0, 0}, 0);
    r.RenderGeometry(quad(r, 0, 0, 8, 8, {100, 0, 0, 100}), {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 3), 100, 0, 122, 222));
}

void test_textures_nearest() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    const std::array<std::uint8_t, 16> texels = {255, 0, 0, 255, 0, 255, 0, 255,   // red, green
                                                 0, 0, 255, 255, 255, 255, 255, 255}; // blue, white
    const auto texture = r.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), texels.size()), {2, 2});
    VELLUM_RMLUI_CHECK(texture != 0);
    VELLUM_RMLUI_CHECK(r.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), 8), {2, 2}) == 0);
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, {255, 255, 255, 255}), {0, 0}, texture);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 1), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 255, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 6), 0, 0, 255, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 6), 255, 255, 255, 255));
}

void test_scissor_transform_and_scale() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({0, 0}, {4, 8}));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, kRed), {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 3), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 4, 3), 0, 0, 0, 0));

    // A translation transform plus the draw's own translation.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    const Rml::Matrix4f shift = Rml::Matrix4f::Translate(2.0f, 1.0f, 0.0f);
    r.SetTransform(&shift);
    r.RenderGeometry(quad(r, 0, 0, 2, 2, kRed), {1, 0}, 0);
    r.SetTransform(nullptr);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 1), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 4, 2), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 2, 1), 0, 0, 0, 0));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 5, 3), 0, 0, 0, 0));

    // Scale 2: two context units are four pixels, and the scissor scales too.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer, kSize, kSize, 0, 2.0f)));
    r.RenderGeometry(quad(r, 0, 0, 2, 2, kRed), {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 3), 255, 0, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 4, 3), 0, 0, 0, 0));
}

void test_invalid_frames_draw_nothing() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4, 9);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    VELLUM_RMLUI_CHECK(!r.begin_frame(frame(buffer, kSize, kSize, kSize * 4 - 1)));
    VELLUM_RMLUI_CHECK(!r.begin_frame(frame(buffer, 0, kSize)));
    VELLUM_RMLUI_CHECK(!r.begin_frame(frame(buffer, kSize, kSize, 0, 0.0f)));
    CpuFrameTarget none;
    VELLUM_RMLUI_CHECK(!r.begin_frame(none));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, kRed), {0, 0}, 0);
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 3), 9, 9, 9, 9));
}

void test_unsupported_calls_are_counted() {
    CpuRenderInterface r;
    r.set_threads(g_threads);
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    const auto geometry = quad(r, 0, 0, 1, 1, kRed);
    VELLUM_RMLUI_CHECK(r.SaveLayerAsMaskImage() == 0);
    VELLUM_RMLUI_CHECK(r.CompileFilter("blur", {}) == 0);
    VELLUM_RMLUI_CHECK(r.CompileShader("linear-gradient", {}) == 0);
    r.RenderShader(0, geometry, {0, 0}, 0);
    const auto layer = r.PushLayer();
    const Rml::CompiledFilterHandle filters[] = {7};
    r.CompositeLayers(layer, 0, Rml::BlendMode::Blend, Rml::Span<const Rml::CompiledFilterHandle>(filters, 1));
    r.PopLayer();
    r.end_frame();
    VELLUM_RMLUI_CHECK(r.unsupported("filter") == 2);
    VELLUM_RMLUI_CHECK(r.unsupported("shader") == 2);
    VELLUM_RMLUI_CHECK(r.unsupported("mask_image") == 1);
    VELLUM_RMLUI_CHECK(r.unsupported("clip_mask") == 0 && r.unsupported("layer") == 0);
    VELLUM_RMLUI_CHECK(r.unsupported_calls() == 5);
}

// A right triangle over the lower-left half of the 8x8 frame.
Rml::CompiledGeometryHandle lower_left(CpuRenderInterface& r) {
    const std::array<Rml::Vertex, 3> v = {vertex(0, 0, kRed), vertex(8, 8, kRed), vertex(0, 8, kRed)};
    const std::array<int, 3> i = {0, 1, 2};
    return r.CompileGeometry(Rml::Span<const Rml::Vertex>(v.data(), v.size()), Rml::Span<const int>(i.data(), i.size()));
}

void test_clip_masks() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    const auto fill = quad(r, 0, 0, 8, 8, {0, 255, 0, 255});
    const auto mask = lower_left(r);

    // Set: only under the triangle.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::Set, mask, {0, 0});
    r.RenderGeometry(fill, {0, 0}, 0);
    r.EnableClipMask(false);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 6), 0, 255, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 0, 0, 0));

    // SetInverse: everywhere but the triangle.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::SetInverse, mask, {0, 0});
    r.RenderGeometry(fill, {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 6), 0, 0, 0, 0));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 255, 0, 255));

    // Intersect: the triangle and the left half.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::Set, mask, {0, 0});
    r.RenderToClipMask(Rml::ClipMaskOperation::Intersect, quad(r, 0, 0, 4, 8, kRed), {0, 0});
    r.RenderGeometry(fill, {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 6), 0, 255, 0, 255));  // both
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 7), 0, 0, 0, 0));      // triangle only
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 2, 0), 0, 0, 0, 0));      // left half only

    // A mask disabled again draws everywhere.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::Set, mask, {0, 0});
    r.EnableClipMask(false);
    r.RenderGeometry(fill, {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 255, 0, 255));
}

void test_layers() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    const auto blue = quad(r, 0, 0, 8, 8, {0, 0, 255, 255});
    const auto half_red = quad(r, 0, 0, 4, 8, {128, 0, 0, 128});

    // Drawing into a layer leaves the base alone until it is composited.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(blue, {0, 0}, 0);
    const auto layer = r.PushLayer();
    VELLUM_RMLUI_CHECK(layer == 1);
    r.RenderGeometry(half_red, {0, 0}, 0);
    // Drawn at once, the base is visible mid-frame; recorded, it is drawn at
    // end_frame(), so there is nothing to read yet.
    if (g_threads == 1)
        VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 1), 0, 0, 255, 255));
    r.CompositeLayers(layer, 0, Rml::BlendMode::Blend, {});
    r.PopLayer();
    // After the pop, draws reach the base again.
    r.RenderGeometry(quad(r, 7, 7, 8, 8, kRed), {0, 0}, 0);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 1), 128, 0, 127, 255)); // blended
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 0, 255, 255));   // layer empty there
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 7, 7), 255, 0, 0, 255));

    // Replace writes the layer's pixels, transparent ones included.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(blue, {0, 0}, 0);
    const auto replaced = r.PushLayer();
    r.RenderGeometry(half_red, {0, 0}, 0);
    r.CompositeLayers(replaced, 0, Rml::BlendMode::Replace, {});
    r.PopLayer();
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 1), 128, 0, 0, 128));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 0, 0, 0));

    // The composite honours the scissor and the clip mask.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    const auto clipped = r.PushLayer();
    r.RenderGeometry(quad(r, 0, 0, 8, 8, kRed), {0, 0}, 0);
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({0, 0}, {8, 4}));
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::Set, lower_left(r), {0, 0});
    r.CompositeLayers(clipped, 0, Rml::BlendMode::Blend, {});
    r.EnableClipMask(false);
    r.EnableScissorRegion(false);
    r.PopLayer();
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 0, 3), 255, 0, 0, 255)); // inside both
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 1), 0, 0, 0, 0));     // outside the mask
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 6), 0, 0, 0, 0));     // outside the scissor

    // SaveLayerAsTexture copies the scissored region of the top layer.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(quad(r, 2, 2, 4, 4, kRed), {0, 0}, 0);
    VELLUM_RMLUI_CHECK(r.SaveLayerAsTexture() == 0); // needs a scissor
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({2, 2}, {2, 2}));
    const auto saved = r.SaveLayerAsTexture();
    VELLUM_RMLUI_CHECK(saved != 0);
    r.EnableScissorRegion(false);
    r.RenderGeometry(quad(r, 4, 4, 8, 8, {255, 255, 255, 255}), {0, 0}, saved);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 6), 255, 0, 0, 255)); // the copy was all red
    VELLUM_RMLUI_CHECK(r.unsupported_calls() == 0);
}

// A 1 x 28,544 texture whose rows past 8192 are blue and the rest red.
// A Set inside a scissor clears the mask only there: values the previous Set
// left outside the scissor still count. Then the next frame starts clear.
void test_mask_reuse_outside_the_scissor() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    const auto fill = quad(r, 0, 0, 8, 8, {0, 255, 0, 255});
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::Set, quad(r, 0, 0, 8, 8, kRed), {0, 0}); // all 1
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({0, 0}, {4, 8}));
    r.RenderToClipMask(Rml::ClipMaskOperation::Set, quad(r, 0, 0, 2, 8, kRed), {0, 0}); // left 4: 1,1,0,0
    r.EnableScissorRegion(false);
    r.RenderGeometry(fill, {0, 0}, 0);
    r.EnableClipMask(false);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 3), 0, 255, 0, 255)); // inside both Sets
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 3, 3), 0, 0, 0, 0));     // cleared by the second
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 3), 0, 255, 0, 255)); // outside its scissor: kept

    // A new frame: SetInverse of nothing draws everywhere, so no 1 survived.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.EnableClipMask(true);
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({0, 0}, {1, 1}));
    r.RenderToClipMask(Rml::ClipMaskOperation::SetInverse, quad(r, 0, 0, 1, 1, kRed), {0, 0});
    r.EnableScissorRegion(false);
    r.RenderGeometry(fill, {0, 0}, 0);
    r.EnableClipMask(false);
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 6, 6), 0, 255, 0, 255));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 0, 0), 0, 0, 0, 0));
}

// A pooled layer pushed again starts transparent: nothing drawn into it
// before, in this frame or the last, comes back when it is composited.
void test_layer_reuse_starts_clear() {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    CpuRenderInterface r;
    r.set_threads(g_threads);
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    auto layer = r.PushLayer();
    r.RenderGeometry(quad(r, 0, 0, 4, 4, kRed), {0, 0}, 0);
    r.PopLayer(); // never composited
    layer = r.PushLayer();
    r.RenderGeometry(quad(r, 4, 4, 8, 8, {0, 0, 255, 255}), {0, 0}, 0);
    r.CompositeLayers(layer, 0, Rml::BlendMode::Blend, {});
    r.PopLayer();
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 1), 0, 0, 0, 0));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 5, 5), 0, 0, 255, 255));

    // Next frame: Replace writes the whole (empty) layer over a red base.
    VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer)));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, kRed), {0, 0}, 0);
    layer = r.PushLayer();
    r.CompositeLayers(layer, 0, Rml::BlendMode::Replace, {});
    r.PopLayer();
    r.end_frame();
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 5, 5), 0, 0, 0, 0));
    VELLUM_RMLUI_CHECK(is(px(buffer, kSize * 4, 1, 1), 0, 0, 0, 0));
}

void test_equal_area_resize_clears_reused_buffers() {
    const auto check_mask = [](std::uint32_t old_width, std::uint32_t old_height, std::uint32_t width,
                               std::uint32_t height) {
        CpuRenderInterface r;
        r.set_threads(g_threads);
        std::vector<std::uint8_t> old_buffer(static_cast<std::size_t>(old_width) * old_height * 4u);
        std::vector<std::uint8_t> buffer(static_cast<std::size_t>(width) * height * 4u);
        const auto old_full = quad(r, 0, 0, static_cast<float>(old_width), static_cast<float>(old_height), kRed);
        const auto full = quad(r, 0, 0, static_cast<float>(width), static_cast<float>(height), kRed);

        VELLUM_RMLUI_CHECK(r.begin_frame(frame(old_buffer, old_width, old_height)));
        r.EnableClipMask(true);
        r.RenderToClipMask(Rml::ClipMaskOperation::Set, old_full, {0, 0});
        r.end_frame();

        VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer, width, height)));
        r.EnableClipMask(true);
        r.RenderGeometry(full, {0, 0}, 0);
        r.EnableClipMask(false);
        r.end_frame();
        for (std::uint32_t y = 0; y < height; ++y)
            for (std::uint32_t x = 0; x < width; ++x)
                VELLUM_RMLUI_CHECK(is(px(buffer, static_cast<std::size_t>(width) * 4u, x, y), 255, 0, 0, 255));
    };

    const auto check_layer = [](std::uint32_t old_width, std::uint32_t old_height, std::uint32_t width,
                                std::uint32_t height) {
        CpuRenderInterface r;
        r.set_threads(g_threads);
        std::vector<std::uint8_t> old_buffer(static_cast<std::size_t>(old_width) * old_height * 4u);
        std::vector<std::uint8_t> buffer(static_cast<std::size_t>(width) * height * 4u);
        const auto old_full = quad(r, 0, 0, static_cast<float>(old_width), static_cast<float>(old_height), kRed);

        VELLUM_RMLUI_CHECK(r.begin_frame(frame(old_buffer, old_width, old_height)));
        r.PushLayer();
        r.RenderGeometry(old_full, {0, 0}, 0);
        r.PopLayer();
        r.end_frame();

        VELLUM_RMLUI_CHECK(r.begin_frame(frame(buffer, width, height)));
        const auto layer = r.PushLayer();
        r.CompositeLayers(layer, 0, Rml::BlendMode::Blend, {});
        r.PopLayer();
        r.end_frame();
        for (const auto value : buffer)
            VELLUM_RMLUI_CHECK(value == 0);
    };

    check_mask(8, 8, 16, 4);
    check_mask(16, 4, 8, 8);
    check_layer(8, 8, 16, 4);
    check_layer(16, 4, 8, 8);
}

std::vector<std::uint8_t> tall_texture() {
    std::vector<std::uint8_t> texels(28544u * 4u);
    for (std::size_t y = 0; y < 28544u; ++y) {
        const bool blue = y >= 8192u;
        texels[y * 4 + 0] = blue ? 0 : 255;
        texels[y * 4 + 2] = blue ? 255 : 0;
        texels[y * 4 + 3] = 255;
    }
    return texels;
}

std::array<std::uint8_t, 4> bottom_pixel(CpuRenderInterface& r, const std::vector<std::uint8_t>& texels) {
    std::vector<std::uint8_t> buffer(kSize * kSize * 4);
    const auto texture = r.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), texels.size()), {1, 28544});
    VELLUM_RMLUI_CHECK(texture != 0);
    r.begin_frame(frame(buffer));
    r.RenderGeometry(quad(r, 0, 0, 8, 8, {255, 255, 255, 255}), {0, 0}, texture);
    r.end_frame();
    const auto* p = px(buffer, kSize * 4, 0, 7);
    return {p[0], p[1], p[2], p[3]};
}

void test_texture_limit() {
    const auto texels = tall_texture();
    CpuRenderInterface unlimited;
    VELLUM_RMLUI_CHECK(unlimited.texture_limit() == 0);
    const auto full = bottom_pixel(unlimited, texels);
    VELLUM_RMLUI_CHECK((full == std::array<std::uint8_t, 4>{0, 0, 255, 255})); // the bottom rows are blue

    CpuRenderInterface limited;
    int calls = 0;
    limited.set_texture_limit(8192, [&](std::uint32_t& w, std::uint32_t& h, std::vector<std::uint8_t>& rgba) {
        ++calls;
        VELLUM_RMLUI_CHECK(w == 1 && h == 28544);
        h = 8192; // the client's rule: keep the top rows
        rgba.resize(static_cast<std::size_t>(w) * h * 4u);
        return true;
    });
    VELLUM_RMLUI_CHECK(limited.texture_limit() == 8192);
    const auto clamped = bottom_pixel(limited, texels);
    VELLUM_RMLUI_CHECK(calls == 1);
    VELLUM_RMLUI_CHECK((clamped == std::array<std::uint8_t, 4>{255, 0, 0, 255}));

    CpuRenderInterface refusing;
    refusing.set_texture_limit(8192, nullptr);
    VELLUM_RMLUI_CHECK(refusing.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), texels.size()),
                                                {1, 28544}) == 0);
    const std::array<std::uint8_t, 4> small = {1, 2, 3, 4};
    VELLUM_RMLUI_CHECK(refusing.GenerateTexture(Rml::Span<const Rml::byte>(small.data(), 4), {1, 1}) != 0);
}

// The same scene, drawn by one renderer into one buffer.
std::vector<std::uint8_t> scene(std::uint8_t garbage, unsigned threads = 1) {
    std::vector<std::uint8_t> buffer(64 * 48 * 4, garbage);
    CpuRenderInterface r;
    r.set_threads(threads);
    const std::array<std::uint8_t, 16> texels = {255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 40, 40, 40, 80};
    const auto texture = r.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), texels.size()), {2, 2});
    r.begin_frame(frame(buffer, 64, 48, 0, 1.5f));
    for (int i = 0; i < 40; ++i) {
        const float x = static_cast<float>((i * 7) % 37);
        const float y = static_cast<float>((i * 5) % 23);
        const auto geometry = quad(r, x, y, x + 9.3f, y + 6.7f,
                                   {static_cast<Rml::byte>(i * 6), 90, static_cast<Rml::byte>(200 - i * 3), 200});
        const Rml::Matrix4f rotate = Rml::Matrix4f::RotateZ(0.05f * static_cast<float>(i));
        r.SetTransform(i % 3 == 0 ? &rotate : nullptr);
        r.RenderGeometry(geometry, {0.25f, 0.5f}, i % 2 ? texture : 0);
    }
    r.end_frame();
    return buffer;
}

void test_deterministic_across_threads() {
    const auto alone = scene(0x00);
    // clear=true makes the starting contents irrelevant.
    VELLUM_RMLUI_CHECK(scene(0xa5) == alone);
    std::vector<std::uint8_t> a;
    std::vector<std::uint8_t> b;
    std::thread first([&] { a = scene(0x11); });
    std::thread second([&] { b = scene(0x22); });
    first.join();
    second.join();
    VELLUM_RMLUI_CHECK(a == alone);
    VELLUM_RMLUI_CHECK(b == alone);
    // Two instances on two threads, each splitting its draws across its own
    // workers.
    std::thread third([&] { a = scene(0x33, 3); });
    std::thread fourth([&] { b = scene(0x44, 4); });
    third.join();
    fourth.join();
    VELLUM_RMLUI_CHECK(a == alone);
    VELLUM_RMLUI_CHECK(b == alone);
    // The scene draws something: not all zero.
    bool any = false;
    for (const auto value : alone)
        any = any || value != 0;
    VELLUM_RMLUI_CHECK(any);
}

} // namespace

int main() {
    for (const unsigned threads : {1u, 3u}) {
        g_threads = threads;
        test_fill_and_untouched_pixels();
        test_premultiplied_blend();
        test_textures_nearest();
        test_scissor_transform_and_scale();
        test_invalid_frames_draw_nothing();
        test_unsupported_calls_are_counted();
        test_clip_masks();
        test_layers();
        test_mask_reuse_outside_the_scissor();
        test_layer_reuse_starts_clear();
        test_equal_area_resize_clears_reused_buffers();
        test_texture_limit();
        test_deterministic_across_threads();
    }
    return 0;
}
