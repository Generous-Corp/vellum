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
#include <cstring>

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

// Pixels [x0, x1] x [y0, y1] (inclusive) a triangle's bounding box covers
// inside `clip`; empty when x0 > x1 or y0 > y1.
inline Clip bounds(Fixed p0, Fixed p1, Fixed p2, Clip clip) {
    const auto floor_pixel = [](std::int64_t value) {
        return value >= 0 ? value >> kSubpixelBits : -((-value + kSubpixelScale - 1) >> kSubpixelBits);
    };
    return {std::max(clip.x0, floor_pixel(std::min({p0.x, p1.x, p2.x}))),
            std::max(clip.y0, floor_pixel(std::min({p0.y, p1.y, p2.y}))),
            std::min(clip.x1 - 1, floor_pixel(std::max({p0.x, p1.x, p2.x}))),
            std::min(clip.y1 - 1, floor_pixel(std::max({p0.y, p1.y, p2.y})))};
}

// Calls `span(y, x_begin, x_end, w0, w1, w2, step0, step1, step2, area)` for
// each row the triangle covers inside `clip`, once per row: pixel centres
// x_begin <= x < x_end are covered (a triangle is convex, so a row's covered
// pixels are contiguous), w0..w2 are the edge values at x_begin, and adding
// step0..step2 moves them one pixel right. The weights belong to the vertices
// in the order given after winding is made positive (the order is passed back
// through `ordered`). Returns false when nothing could be covered.
//
// Every value is the exact integer the per-pixel edge function gives: a span's
// ends come from integer division of the same edge values, and stepping adds
// the edge's exact x derivative.
template <class SpanFn>
bool cover_spans(Fixed p0, Fixed p1, Fixed p2, Clip clip, int ordered[3], SpanFn&& span) {
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
    const Clip box = bounds(p0, p1, p2, clip);
    if (box.x0 > box.x1 || box.y0 > box.y1)
        return false;

    const Fixed* from[3] = {&p1, &p2, &p0};
    const Fixed* to[3] = {&p2, &p0, &p1};
    std::int64_t bias[3];
    std::int64_t step_x[3]; // one pixel right
    std::int64_t step_y[3]; // one pixel down
    std::int64_t row[3];    // at (box.x0, box.y0)
    const std::int64_t half = kSubpixelScale / 2;
    const auto px0 = box.x0 * kSubpixelScale + half;
    const auto py0 = box.y0 * kSubpixelScale + half;
    for (int e = 0; e < 3; ++e) {
        bias[e] = top_left(*from[e], *to[e]) ? 0 : -1;
        step_x[e] = -(to[e]->y - from[e]->y) * kSubpixelScale;
        step_y[e] = (to[e]->x - from[e]->x) * kSubpixelScale;
        row[e] = edge(*from[e], *to[e], px0, py0);
    }
    for (auto y = box.y0; y <= box.y1; ++y) {
        // Pixel offsets [lo, hi] from box.x0 where every biased edge value is >= 0.
        std::int64_t lo = 0;
        std::int64_t hi = box.x1 - box.x0;
        for (int e = 0; e < 3 && lo <= hi; ++e) {
            const auto value = row[e] + bias[e];
            const auto a = step_x[e];
            if (a > 0) {
                if (value < 0)
                    lo = std::max(lo, (-value + a - 1) / a);
            } else if (a < 0) {
                if (value < 0)
                    hi = -1;
                else
                    hi = std::min(hi, value / -a);
            } else if (value < 0) {
                hi = -1;
            }
        }
        if (lo <= hi)
            span(y, box.x0 + lo, box.x0 + hi + 1, row[0] + step_x[0] * lo, row[1] + step_x[1] * lo,
                 row[2] + step_x[2] * lo, step_x[0], step_x[1], step_x[2], area);
        for (int e = 0; e < 3; ++e)
            row[e] += step_y[e];
    }
    return true;
}

// Calls `pixel(x, y, w0, w1, w2, area)` for every pixel centre the triangle
// covers inside `clip`; see cover_spans for the weights and `ordered`.
template <class PixelFn>
bool cover(Fixed p0, Fixed p1, Fixed p2, Clip clip, int ordered[3], PixelFn&& pixel) {
    return cover_spans(p0, p1, p2, clip, ordered,
                       [&](std::int64_t y, std::int64_t x_begin, std::int64_t x_end, std::int64_t w0,
                           std::int64_t w1, std::int64_t w2, std::int64_t s0, std::int64_t s1, std::int64_t s2,
                           std::int64_t area) {
                           for (auto x = x_begin; x < x_end; ++x, w0 += s0, w1 += s1, w2 += s2)
                               pixel(x, y, w0, w1, w2, area);
                       });
}

// Premultiplied source-over of `src` onto `dst`, or a plain store for an
// opaque source, which gives the same bytes: with src[3] == 255 the
// destination term mul255(dst, 0) is 0.
inline void blend_over(std::uint8_t* dst, const std::uint32_t src[4]) {
    if (src[3] == 255u) {
        for (int k = 0; k < 4; ++k)
            dst[k] = static_cast<std::uint8_t>(src[k]);
        return;
    }
    const auto inverse = 255u - src[3];
    for (int k = 0; k < 4; ++k)
        dst[k] = static_cast<std::uint8_t>(std::min(255u, src[k] + mul255(dst[k], inverse)));
}

// The test-only straight-alpha control, applied to a shaded source colour.
inline void straight_alpha_control([[maybe_unused]] std::uint32_t src[4]) {
#if defined(VELLUM_RMLUI_CPU_TEST_STRAIGHT_ALPHA_BLEND)
    // Test-only control: blend as if colours were straight alpha.
    for (int k = 0; k < 3; ++k)
        src[k] = mul255(src[k], src[3]);
#endif
}

// Shades and blends one triangle into `target`. Returns false when the
// triangle covered nothing inside `clip`.
//
// Fast paths, each giving the bytes the general path gives:
// - Equal vertex colours: the weights always sum to the triangle's area (an
//   identity of the three edge functions), so the rounded interpolation
//   (area * c + area / 2) / area is exactly c and needs no division.
// - An untextured, unmasked, opaque flat colour is stored across the span.
// - An opaque source is stored rather than blended (see blend_over).
// - A fully transparent source (all four channels 0) leaves the pixel as it
//   is: mul255(d, 255) == d for every byte d.
inline bool draw_triangle(const Target& target, Clip clip, const Vertex& a, const Vertex& b,
                          const Vertex& c, const Texture* texture, const MaskTest* mask = nullptr,
                          Blend blend = Blend::over) {
    const Vertex* given[3] = {&a, &b, &c};
    int ordered[3];
    const Vertex* v[3] = {&a, &b, &c};
    bool resolved = false;
    const bool flat = a.colour == b.colour && b.colour == c.colour;
    std::uint32_t flat_src[4];
    for (int k = 0; k < 4; ++k)
        flat_src[k] = channel(a.colour, k);
    std::uint32_t flat_shaded[4] = {flat_src[0], flat_src[1], flat_src[2], flat_src[3]};
    straight_alpha_control(flat_shaded);
    const bool solid = flat && !texture && !mask;
    // Replace stores the unshaded colour; an opaque source is unchanged by
    // the straight-alpha control, so the same bytes serve both.
    std::uint8_t solid_bytes[4];
    for (int k = 0; k < 4; ++k)
        solid_bytes[k] = static_cast<std::uint8_t>(flat_src[k]);
    const bool solid_store = solid && (blend == Blend::replace || flat_shaded[3] == 255u);
    const bool solid_noop = solid && blend == Blend::over && flat_shaded[0] == 0 && flat_shaded[1] == 0 &&
                            flat_shaded[2] == 0 && flat_shaded[3] == 0;

    return cover_spans(
        a.position, b.position, c.position, clip, ordered,
        [&](std::int64_t y, std::int64_t x_begin, std::int64_t x_end, std::int64_t w0, std::int64_t w1,
            std::int64_t w2, std::int64_t s0, std::int64_t s1, std::int64_t s2, std::int64_t area) {
            if (!resolved) {
                for (int i = 0; i < 3; ++i)
                    v[i] = given[ordered[i]];
                resolved = true;
            }
            auto* row = target.rgba + static_cast<std::size_t>(y) * target.stride;
            if (solid_noop)
                return;
            if (solid_store) {
                for (auto x = x_begin; x < x_end; ++x)
                    std::memcpy(row + static_cast<std::size_t>(x) * 4u, solid_bytes, 4);
                return;
            }
            const std::uint8_t* mask_row =
                mask ? mask->mask + static_cast<std::size_t>(y) * mask->width : nullptr;
            const double darea = static_cast<double>(area);
            for (auto x = x_begin; x < x_end; ++x, w0 += s0, w1 += s1, w2 += s2) {
                if (mask_row && mask_row[x] != mask->reference)
                    continue;
                std::uint32_t src[4];
                if (flat) {
                    for (int k = 0; k < 4; ++k)
                        src[k] = flat_src[k];
                } else {
                    for (int k = 0; k < 4; ++k) {
                        const auto sum = w0 * channel(v[0]->colour, k) + w1 * channel(v[1]->colour, k) +
                                         w2 * channel(v[2]->colour, k);
                        src[k] = static_cast<std::uint32_t>((sum + area / 2) / area);
                    }
                }
                if (texture) {
                    const double nu = static_cast<double>(w0) * v[0]->u + static_cast<double>(w1) * v[1]->u +
                                      static_cast<double>(w2) * v[2]->u;
                    const double nv = static_cast<double>(w0) * v[0]->v + static_cast<double>(w1) * v[1]->v +
                                      static_cast<double>(w2) * v[2]->v;
                    const auto tx = std::clamp<std::int64_t>(
                        static_cast<std::int64_t>(std::floor(nu / darea * texture->width)), 0,
                        std::int64_t{texture->width} - 1);
                    const auto ty = std::clamp<std::int64_t>(
                        static_cast<std::int64_t>(std::floor(nv / darea * texture->height)), 0,
                        std::int64_t{texture->height} - 1);
                    const auto* texel = texture->rgba +
                                        (static_cast<std::size_t>(ty) * texture->width + static_cast<std::size_t>(tx)) *
                                            4u;
                    for (int k = 0; k < 4; ++k)
                        src[k] = mul255(texel[k], src[k]);
                }
                auto* dst = row + static_cast<std::size_t>(x) * 4u;
                if (blend == Blend::replace) {
                    for (int k = 0; k < 4; ++k)
                        dst[k] = static_cast<std::uint8_t>(src[k]);
                    continue;
                }
                straight_alpha_control(src);
                if ((src[0] | src[1] | src[2] | src[3]) == 0u)
                    continue;
                blend_over(dst, src);
            }
        });
}

} // namespace vellum::rmlui::raster
