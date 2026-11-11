// The rasteriser's fast paths against the plain per-pixel rasteriser they
// replaced (kept below as `reference`, unchanged): random triangles with flat
// and interpolated colours, opaque, translucent and transparent sources,
// textures, clip masks, both blend modes and random clips must give the same
// bytes. The pinned output hash (cpu_hash_test.cpp) checks one scene; this
// checks the arithmetic case by case.
//
// `--break-flat` is a hand-check that the comparison sees a difference: it
// rounds flat colours one step low in the reference and requires a mismatch.
#include "soft_raster.hpp"

#include "test_support.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <string_view>
#include <vector>

namespace reference {
using namespace vellum::rmlui::raster;
bool g_break_flat = false;

// Calls `pixel(x, y, w0, w1, w2, area)` for every pixel centre the triangle
// covers inside `clip`, with the weights of (a, b, c) in the order the
// vertices were given after winding is made positive (the order is passed
// back through `ordered`). Returns false when nothing could be covered.
template <class PixelFn>
bool ref_cover(Fixed p0, Fixed p1, Fixed p2, Clip clip, int ordered[3], PixelFn&& pixel) {
    ordered[0] = 0;
    ordered[1] = 1;
    ordered[2] = 2;
    auto area = edge(p0, p1, p2.x, p2.y);
    if (area == 0)
        return false;
    if (area < 0) {
        std::swap(p1, p2);
        std::swap(ordered[1], ordered[2]);
        area = -area;
    }
    const auto floor_pixel = [](std::int64_t value) {
        return value >= 0 ? value >> kSubpixelBits : -((-value + kSubpixelScale - 1) >> kSubpixelBits);
    };
    const auto x0 = std::max(clip.x0, floor_pixel(std::min({p0.x, p1.x, p2.x})));
    const auto y0 = std::max(clip.y0, floor_pixel(std::min({p0.y, p1.y, p2.y})));
    const auto x1 = std::min(clip.x1 - 1, floor_pixel(std::max({p0.x, p1.x, p2.x})));
    const auto y1 = std::min(clip.y1 - 1, floor_pixel(std::max({p0.y, p1.y, p2.y})));
    if (x0 > x1 || y0 > y1)
        return false;

    const std::int64_t bias0 = top_left(p1, p2) ? 0 : -1;
    const std::int64_t bias1 = top_left(p2, p0) ? 0 : -1;
    const std::int64_t bias2 = top_left(p0, p1) ? 0 : -1;
    const std::int64_t half = kSubpixelScale / 2;
    for (auto y = y0; y <= y1; ++y) {
        const auto py = y * kSubpixelScale + half;
        for (auto x = x0; x <= x1; ++x) {
            const auto px = x * kSubpixelScale + half;
            const auto w0 = edge(p1, p2, px, py);
            const auto w1 = edge(p2, p0, px, py);
            const auto w2 = edge(p0, p1, px, py);
            if (w0 + bias0 < 0 || w1 + bias1 < 0 || w2 + bias2 < 0)
                continue;
            pixel(x, y, w0, w1, w2, area);
        }
    }
    return true;
}

// Shades and blends one triangle into `target`. Returns false when the
// triangle covered nothing inside `clip`.
inline bool draw_triangle(const Target& target, Clip clip, const Vertex& a, const Vertex& b,
                          const Vertex& c, const Texture* texture, const MaskTest* mask = nullptr,
                          Blend blend = Blend::over) {
    const Vertex* given[3] = {&a, &b, &c};
    int ordered[3];
    // `cover` reorders p1/p2 for winding; resolve the vertices after it does.
    const Vertex* v[3] = {&a, &b, &c};
    bool resolved = false;
    return ref_cover(a.position, b.position, c.position, clip, ordered,
                 [&](std::int64_t x, std::int64_t y, std::int64_t w0, std::int64_t w1,
                     std::int64_t w2, std::int64_t area) {
                     if (!resolved) {
                         for (int i = 0; i < 3; ++i)
                             v[i] = given[ordered[i]];
                         resolved = true;
                     }
                     if (mask && mask->mask[static_cast<std::size_t>(y) * mask->width +
                                            static_cast<std::size_t>(x)] != mask->reference)
                         return;
                     std::uint32_t src[4];
                     for (int k = 0; k < 4; ++k) {
                         const auto sum = w0 * channel(v[0]->colour, k) + w1 * channel(v[1]->colour, k) +
                                          w2 * channel(v[2]->colour, k);
                         src[k] = static_cast<std::uint32_t>((sum + area / 2) / area);
                         if (g_break_flat && src[k] > 0 && v[0]->colour == v[1]->colour && v[1]->colour == v[2]->colour)
                             src[k] -= 1;
                     }
                     if (texture) {
                         const auto darea = static_cast<double>(area);
                         const double u =
                             (static_cast<double>(w0) * v[0]->u + static_cast<double>(w1) * v[1]->u +
                              static_cast<double>(w2) * v[2]->u) /
                             darea;
                         const double tv =
                             (static_cast<double>(w0) * v[0]->v + static_cast<double>(w1) * v[1]->v +
                              static_cast<double>(w2) * v[2]->v) /
                             darea;
                         const auto tx = std::clamp<std::int64_t>(
                             static_cast<std::int64_t>(std::floor(u * texture->width)), 0,
                             std::int64_t{texture->width} - 1);
                         const auto ty = std::clamp<std::int64_t>(
                             static_cast<std::int64_t>(std::floor(tv * texture->height)), 0,
                             std::int64_t{texture->height} - 1);
                         const auto* texel = texture->rgba + (static_cast<std::size_t>(ty) * texture->width +
                                                              static_cast<std::size_t>(tx)) *
                                                                 4u;
                         for (int k = 0; k < 4; ++k)
                             src[k] = mul255(texel[k], src[k]);
                     }
                     auto* dst = target.rgba + static_cast<std::size_t>(y) * target.stride +
                                 static_cast<std::size_t>(x) * 4u;
                     if (blend == Blend::replace) {
                         for (int k = 0; k < 4; ++k)
                             dst[k] = static_cast<std::uint8_t>(src[k]);
                         return;
                     }
                     const auto inverse = 255u - src[3];
                     for (int k = 0; k < 4; ++k)
                         dst[k] = static_cast<std::uint8_t>(std::min(255u, src[k] + mul255(dst[k], inverse)));
                 });
}

} // namespace reference

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kW = 48;
constexpr std::uint32_t kH = 40;

struct Case {
    raster::Vertex v[3];
    raster::Clip clip;
    bool textured;
    bool masked;
    raster::Blend blend;
};

std::uint32_t colour(std::mt19937& rng, int kind) {
    // kind 0: opaque, 1: translucent, 2: transparent, 3: alpha 0 with colour (additive)
    std::uniform_int_distribution<int> byte(0, 255);
    std::uint32_t a = kind == 0 ? 255u : kind == 2 || kind == 3 ? 0u : static_cast<std::uint32_t>(byte(rng));
    std::uint32_t c[3];
    for (auto& x : c)
        x = kind == 2 ? 0u : kind == 3 ? static_cast<std::uint32_t>(byte(rng)) : static_cast<std::uint32_t>(byte(rng)) * a / 255u;
    return (c[0] << 24) | (c[1] << 16) | (c[2] << 8) | a;
}

Case random_case(std::mt19937& rng) {
    std::uniform_int_distribution<std::int64_t> coordinate(-8 * 256, (kW + 8) * 256);
    std::uniform_int_distribution<int> pick(0, 99);
    std::uniform_real_distribution<float> uv(-0.25f, 1.25f);
    Case c{};
    const bool flat = pick(rng) < 60;
    const int kind = pick(rng) % 4;
    const auto shared = colour(rng, kind);
    for (auto& v : c.v) {
        v.position = {coordinate(rng), coordinate(rng)};
        // Some vertices on whole and half pixels, where the fill rule decides.
        if (pick(rng) < 30) {
            v.position.x &= ~std::int64_t{127};
            v.position.y &= ~std::int64_t{127};
        }
        v.colour = flat ? shared : colour(rng, pick(rng) % 4);
        v.u = uv(rng);
        v.v = uv(rng);
        // Some coordinates on texel boundaries (the texture is 5 x 3), where
        // the texel index is decided by rounding.
        if (pick(rng) < 30) {
            v.u = static_cast<float>(pick(rng) % 7) / 5.0f;
            v.v = static_cast<float>(pick(rng) % 5) / 3.0f;
        }
    }
    if (pick(rng) < 10) // degenerate
        c.v[2].position = c.v[1].position;
    std::uniform_int_distribution<std::int64_t> cx(0, kW), cy(0, kH);
    if (pick(rng) < 50) {
        c.clip = {0, 0, kW, kH};
    } else {
        auto x0 = cx(rng), x1 = cx(rng), y0 = cy(rng), y1 = cy(rng);
        c.clip = {std::min(x0, x1), std::min(y0, y1), std::max(x0, x1), std::max(y0, y1)};
    }
    c.textured = pick(rng) < 40;
    c.masked = pick(rng) < 30;
    c.blend = pick(rng) < 15 ? raster::Blend::replace : raster::Blend::over;
    return c;
}

} // namespace

int main(int argc, char** argv) {
    const bool break_flat = argc > 1 && std::string_view(argv[1]) == "--break-flat";
    reference::g_break_flat = break_flat;
    std::mt19937 rng(20261109);
    std::vector<std::uint8_t> texels(5 * 3 * 4);
    for (std::size_t i = 0; i < texels.size() / 4; ++i) {
        const auto alpha = static_cast<std::uint8_t>(i % 3 == 0 ? 255 : i % 3 == 1 ? 0 : 140);
        for (int k = 0; k < 3; ++k)
            texels[i * 4 + static_cast<std::size_t>(k)] = static_cast<std::uint8_t>((i * 41 + static_cast<std::size_t>(k) * 67) % (alpha + 1u));
        texels[i * 4 + 3] = alpha;
    }
    const raster::Texture texture{5, 3, texels.data()};
    std::vector<std::uint8_t> mask(kW * kH);
    for (std::size_t i = 0; i < mask.size(); ++i)
        mask[i] = static_cast<std::uint8_t>((i * 7 + i / kW) % 3);
    const raster::MaskTest mask_test{mask.data(), kW, 1};

    std::vector<std::uint8_t> start(kW * kH * 4);
    std::size_t mismatches = 0;
    constexpr int kCases = 20000;
    for (int i = 0; i < kCases; ++i) {
        for (auto& b : start)
            b = static_cast<std::uint8_t>(rng());
        const Case c = random_case(rng);
        std::vector<std::uint8_t> fast = start;
        std::vector<std::uint8_t> slow = start;
        const raster::Target fast_target{fast.data(), kW, kH, kW * 4};
        const raster::Target slow_target{slow.data(), kW, kH, kW * 4};
        const raster::Texture* t = c.textured ? &texture : nullptr;
        const raster::MaskTest* m = c.masked ? &mask_test : nullptr;
        const bool a = raster::draw_triangle(fast_target, c.clip, c.v[0], c.v[1], c.v[2], t, m, c.blend);
        const bool b = reference::draw_triangle(slow_target, c.clip, c.v[0], c.v[1], c.v[2], t, m, c.blend);
        if (fast != slow || a != b) {
            if (mismatches < 3 && !break_flat)
                std::fprintf(stderr, "case %d differs (returned %d vs %d)\n", i, int(a), int(b));
            ++mismatches;
        }
    }
    if (break_flat) {
        if (mismatches == 0) {
            std::fprintf(stderr, "control: a reference rounding flat colours low matched, so the comparison "
                                 "cannot see a rounding fault\n");
            return 1;
        }
        std::printf("control: %zu of %d cases differ from a broken reference\n", mismatches, kCases);
        return 0;
    }
    if (mismatches) {
        std::fprintf(stderr, "%zu of %d cases differ from the reference rasteriser\n", mismatches, kCases);
        return 1;
    }
    std::printf("%d random triangles match the reference rasteriser byte for byte\n", kCases);
    return 0;
}
