#pragma once
// The CPU triangle rasteriser shared by SoftRenderer and CpuRenderInterface.
// Internal to the module.
//
// Deterministic by construction: vertex positions are snapped to 1/256 pixel,
// coverage and colour interpolation use integer edge functions with a
// top-left fill rule, texture sampling is nearest-texel, and blending is
// premultiplied in 8-bit integer arithmetic. The only floating-point steps
// (projection and texture coordinates) are built with -ffp-contract=off, so
// the same input gives the same bytes on every platform.

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace vellum::rmlui::raster {

// Coordinates are confined to +/-16384 pixels so every edge-function product
// stays well inside int64: differences fit in 23 bits, edge values in 47 bits,
// and colour sums (three edge values times 255) in 57 bits.
constexpr int kSubpixelBits = 8;
constexpr std::int64_t kSubpixelScale = std::int64_t{1} << kSubpixelBits;
constexpr double kMaxCoordinate = 16384.0;

struct Fixed {
    std::int64_t x = 0;
    std::int64_t y = 0;
};

inline std::int64_t edge(const Fixed& a, const Fixed& b, std::int64_t px, std::int64_t py) {
    return (b.x - a.x) * (py - a.y) - (b.y - a.y) * (px - a.x);
}

// With y pointing down and positive area, the top edge runs left to right
// and a left edge runs upward.
inline bool top_left(const Fixed& a, const Fixed& b) {
    const auto dy = b.y - a.y;
    const auto dx = b.x - a.x;
    return dy < 0 || (dy == 0 && dx > 0);
}

inline std::uint32_t mul255(std::uint32_t a, std::uint32_t b) {
    return (a * b + 127u) / 255u;
}

// A packed colour: red in the high byte.
inline std::uint32_t channel(std::uint32_t packed, int index) {
    return (packed >> (24 - 8 * index)) & 0xffu;
}

// Projects (x, y) through an optional column-major 4x4 transform (z = 0) and
// a scale, and snaps it. False when the point is behind the viewer, not
// finite, or out of range.
inline bool project(double x, double y, const float* transform, double scale, Fixed& out) {
    if (transform) {
        const auto* m = transform;
        const double cx = static_cast<double>(m[0]) * x + static_cast<double>(m[4]) * y +
                          static_cast<double>(m[12]);
        const double cy = static_cast<double>(m[1]) * x + static_cast<double>(m[5]) * y +
                          static_cast<double>(m[13]);
        const double cw = static_cast<double>(m[3]) * x + static_cast<double>(m[7]) * y +
                          static_cast<double>(m[15]);
        if (!(cw > 1e-9))
            return false;
        x = cx / cw;
        y = cy / cw;
    }
    x *= scale;
    y *= scale;
    if (!std::isfinite(x) || !std::isfinite(y) || std::fabs(x) > kMaxCoordinate ||
        std::fabs(y) > kMaxCoordinate)
        return false;
    out.x = std::llround(x * static_cast<double>(kSubpixelScale));
    out.y = std::llround(y * static_cast<double>(kSubpixelScale));
    return true;
}

struct Vertex {
    Fixed position;
    std::uint32_t colour = 0; // premultiplied, red in the high byte
    float u = 0.0f;
    float v = 0.0f;
};

struct Texture {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    const std::uint8_t* rgba = nullptr; // premultiplied RGBA8, tightly packed
};

struct Target {
    std::uint8_t* rgba = nullptr; // premultiplied RGBA8, top row first
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::size_t stride = 0; // bytes per row
};

// Pixels [x0, x1) x [y0, y1) may be written.
struct Clip {
    std::int64_t x0 = 0;
    std::int64_t y0 = 0;
    std::int64_t x1 = 0;
    std::int64_t y1 = 0;
};

// A pixel is drawn only where mask[y * width + x] == reference.
struct MaskTest {
    const std::uint8_t* mask = nullptr;
    std::uint32_t width = 0;
    std::uint8_t reference = 0;
};

enum class Blend : std::uint8_t { over, replace };

// Calls `pixel(x, y, w0, w1, w2, area)` for every pixel centre the triangle
// covers inside `clip`, with the weights of (a, b, c) in the order the
// vertices were given after winding is made positive (the order is passed
// back through `ordered`). Returns false when nothing could be covered.
template <class PixelFn>
bool cover(Fixed p0, Fixed p1, Fixed p2, Clip clip, int ordered[3], PixelFn&& pixel) {
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
    return cover(a.position, b.position, c.position, clip, ordered,
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

} // namespace vellum::rmlui::raster
