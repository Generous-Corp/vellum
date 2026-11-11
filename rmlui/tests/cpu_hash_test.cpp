// The CPU renderer's output pinned by hash: a scene that uses every feature
// the renderer draws (vertex colours, translucency, a texture, scissor, a
// sheared and perspective transform, a target scale, clip masks, layers and a
// saved layer), driven through Rml::RenderInterface calls directly. The same
// calls must give the same bytes on macOS, Windows and Linux, so the hash is
// one constant for every platform.
//
// The input is built here rather than laid out by RmlUi from a document:
// RmlUi's layout is floating point compiled with each platform's default
// contraction rules, so a document is not the same input everywhere, while
// these calls are.
//
// The scene is also drawn on 2 and 4 threads, where the frame is recorded and
// replayed in bands of rows, and must hash the same. Its SaveLayerAsTexture
// comes mid-frame, after draws into the layer it copies, so it must flush the
// recording first: a copy taken without the flush reads an undrawn layer.
//
// The control renders the same scene into a buffer of random bytes with
// clear=false. The scene leaves pixels untouched, so the hash must change:
// a hash that cannot see uninitialised memory proves nothing about it.
//
// `vellum-rmlui-cpu-hash-test --print` prints the hash without checking it.
#include <vellum/rmlui/cpu_render_interface.hpp>

#include "test_support.hpp"

#include <RmlUi/Core/Dictionary.h>
#include <RmlUi/Core/Matrix4.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <string_view>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kWidth = 96;
constexpr std::uint32_t kHeight = 64;
constexpr const char* kExpected = "f2edf3fb027760eb03e8664b6d4536835089b68704197ef26398617b238f0d81";

Rml::Vertex vertex(float x, float y, Rml::ColourbPremultiplied colour, float u, float v) {
    Rml::Vertex out;
    out.position = {x, y};
    out.colour = colour;
    out.tex_coord = {u, v};
    return out;
}

Rml::CompiledGeometryHandle quad(CpuRenderInterface& r, float x0, float y0, float x1, float y1,
                                 Rml::ColourbPremultiplied colour) {
    const std::array<Rml::Vertex, 4> v = {vertex(x0, y0, colour, 0.0f, 0.0f), vertex(x1, y0, colour, 1.0f, 0.0f),
                                          vertex(x1, y1, colour, 1.0f, 1.0f), vertex(x0, y1, colour, 0.0f, 1.0f)};
    const std::array<int, 6> i = {0, 1, 2, 0, 2, 3};
    return r.CompileGeometry(Rml::Span<const Rml::Vertex>(v.data(), v.size()),
                             Rml::Span<const int>(i.data(), i.size()));
}

Rml::CompiledGeometryHandle triangle(CpuRenderInterface& r, Rml::Vector2f a, Rml::Vector2f b, Rml::Vector2f c,
                                     Rml::ColourbPremultiplied ca, Rml::ColourbPremultiplied cb,
                                     Rml::ColourbPremultiplied cc) {
    const std::array<Rml::Vertex, 3> v = {vertex(a.x, a.y, ca, 0.0f, 0.0f), vertex(b.x, b.y, cb, 1.0f, 0.0f),
                                          vertex(c.x, c.y, cc, 0.5f, 1.0f)};
    const std::array<int, 3> i = {0, 1, 2};
    return r.CompileGeometry(Rml::Span<const Rml::Vertex>(v.data(), v.size()),
                             Rml::Span<const int>(i.data(), i.size()));
}

// Renders the scene into `buffer` (kWidth x kHeight, tightly packed).
void scene(std::vector<std::uint8_t>& buffer, bool clear, unsigned threads = 1) {
    CpuRenderInterface r;
    r.set_threads(threads);
    VELLUM_RMLUI_CHECK(r.threads() == threads);
    // A 4x4 texture with translucent texels.
    std::array<std::uint8_t, 64> texels{};
    for (int i = 0; i < 16; ++i) {
        const auto alpha = static_cast<std::uint8_t>(255 - i * 12);
        texels[i * 4 + 0] = static_cast<std::uint8_t>((i * 37) % alpha);
        texels[i * 4 + 1] = static_cast<std::uint8_t>((i * 59) % alpha);
        texels[i * 4 + 2] = static_cast<std::uint8_t>((i * 83) % alpha);
        texels[i * 4 + 3] = alpha;
    }
    const auto texture = r.GenerateTexture(Rml::Span<const Rml::byte>(texels.data(), texels.size()), {4, 4});
    VELLUM_RMLUI_CHECK(texture != 0);

    CpuFrameTarget frame;
    frame.rgba = buffer.data();
    frame.width = kWidth;
    frame.height = kHeight;
    frame.scale = 1.5f;
    frame.clear = clear;
    VELLUM_RMLUI_CHECK(r.begin_frame(frame));

    // Interpolated vertex colours, then a translucent quad over them.
    r.RenderGeometry(triangle(r, {1.0f, 1.0f}, {30.5f, 3.25f}, {9.75f, 26.0f}, {255, 0, 0, 255},
                              {0, 200, 0, 200}, {0, 0, 120, 160}),
                     {0.0f, 0.0f}, 0);
    r.RenderGeometry(quad(r, 6.0f, 6.0f, 22.0f, 18.0f, {60, 30, 0, 90}), {0.375f, 0.0f}, 0);

    // A texture, tinted, under a scissor.
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({40, 2}, {30, 20}));
    r.RenderGeometry(quad(r, 24.0f, 0.0f, 50.0f, 16.0f, {200, 220, 255, 255}), {0.0f, 0.0f}, texture);
    r.EnableScissorRegion(false);

    // A sheared transform with mild perspective, built from exact literals.
    const Rml::Matrix4f shear = Rml::Matrix4f::FromRows({1.0f, 0.5f, 0.0f, 4.0f}, {0.25f, 1.0f, 0.0f, 20.0f},
                                                        {0.0f, 0.0f, 1.0f, 0.0f}, {0.0f, 0.0078125f, 0.0f, 1.0f});
    r.SetTransform(&shear);
    r.RenderGeometry(quad(r, 0.0f, 0.0f, 14.0f, 10.0f, {255, 255, 255, 255}), {0.0f, 0.0f}, texture);
    r.SetTransform(nullptr);

    // Clip masks: a triangle intersected with a band, then a fill under it.
    r.EnableClipMask(true);
    r.RenderToClipMask(Rml::ClipMaskOperation::Set,
                       triangle(r, {36.0f, 20.0f}, {62.0f, 22.0f}, {44.0f, 41.0f}, {255, 255, 255, 255},
                                {255, 255, 255, 255}, {255, 255, 255, 255}),
                       {0.0f, 0.0f});
    r.RenderToClipMask(Rml::ClipMaskOperation::Intersect, quad(r, 30.0f, 24.0f, 64.0f, 34.0f, {255, 0, 0, 255}),
                       {0.0f, 0.0f});
    r.RenderGeometry(quad(r, 30.0f, 18.0f, 64.0f, 42.0f, {0, 180, 90, 230}), {0.0f, 0.0f}, 0);
    r.EnableClipMask(false);

    // A layer composited with blending, and a saved copy of it drawn again.
    const auto layer = r.PushLayer();
    r.RenderGeometry(quad(r, 2.0f, 30.0f, 20.0f, 41.0f, {120, 0, 120, 180}), {0.0f, 0.0f}, 0);
    r.RenderGeometry(triangle(r, {4.0f, 41.0f}, {18.0f, 28.0f}, {22.0f, 41.0f}, {0, 0, 0, 0},
                              {200, 200, 0, 255}, {0, 100, 100, 128}),
                     {0.0f, 0.0f}, 0);
    r.EnableScissorRegion(true);
    r.SetScissorRegion(Rml::Rectanglei::FromPositionSize({3, 28}, {19, 14}));
    const auto saved = r.SaveLayerAsTexture();
    VELLUM_RMLUI_CHECK(saved != 0);
    r.EnableScissorRegion(false);
    r.CompositeLayers(layer, 0, Rml::BlendMode::Blend, {});
    r.PopLayer();
    r.RenderGeometry(quad(r, 26.0f, 30.0f, 46.0f, 41.0f, {255, 255, 255, 255}), {0.0f, 0.0f}, saved);

    r.end_frame();
    VELLUM_RMLUI_CHECK(r.unsupported_calls() == 0);
}

std::string hash(const std::vector<std::uint8_t>& buffer) {
    return test::sha256_hex(std::string_view(reinterpret_cast<const char*>(buffer.data()), buffer.size()));
}

} // namespace

int main(int argc, char** argv) {
    std::vector<std::uint8_t> buffer(static_cast<std::size_t>(kWidth) * kHeight * 4u, 0);
    scene(buffer, true);
    const auto actual = hash(buffer);
    if (argc > 1 && std::string_view(argv[1]) == "--print") {
        std::printf("%s\n", actual.c_str());
        return 0;
    }

    // The scene draws something, and leaves something untouched.
    std::size_t untouched = 0;
    for (std::size_t i = 3; i < buffer.size(); i += 4)
        untouched += buffer[i] == 0 ? 1u : 0u;
    VELLUM_RMLUI_CHECK(untouched > 0 && untouched < buffer.size() / 4);

    if (actual != kExpected) {
        std::fprintf(stderr, "cpu output hash %s differs from the pinned %s\n", actual.c_str(), kExpected);
        return 1;
    }

    // Split into bands on 2 and 4 threads: the same bytes.
    for (const unsigned threads : {2u, 4u}) {
        std::vector<std::uint8_t> banded(buffer.size(), 0);
        scene(banded, true, threads);
        if (hash(banded) != kExpected) {
            std::fprintf(stderr, "cpu output hash on %u threads %s differs from the pinned %s\n", threads,
                         hash(banded).c_str(), kExpected);
            return 1;
        }
    }

    // Control: uninitialised memory under an uncleared frame changes the hash.
    std::vector<std::uint8_t> garbage(buffer.size());
    std::random_device device;
    std::mt19937 generator(device());
    for (auto& value : garbage)
        value = static_cast<std::uint8_t>(generator() & 0xffu);
    scene(garbage, false);
    if (hash(garbage) == kExpected) {
        std::fprintf(stderr, "control: a frame over random memory hashed as the pinned output, so the hash "
                             "cannot see uninitialised pixels\n");
        return 1;
    }
    std::printf("cpu output hash %s (pinned) on 1, 2 and 4 threads; the uncleared control differs\n",
                actual.c_str());
    return 0;
}
