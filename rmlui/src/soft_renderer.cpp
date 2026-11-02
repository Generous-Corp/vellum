#include <vellum/rmlui/soft_renderer.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <unordered_map>
#include <utility>

namespace vellum::rmlui {

std::string encode_png_rgba(const std::uint8_t* straight_rgba, std::uint32_t width,
                            std::uint32_t height);

namespace {

// Vertex positions are snapped to 1/256 pixel. Coordinates are confined to
// +/-16384 pixels so every edge-function product stays well inside int64:
// differences fit in 23 bits, edge values in 47 bits, and colour sums
// (three edge values times 255) in 57 bits.
constexpr int kSubpixelBits = 8;
constexpr std::int64_t kSubpixelScale = std::int64_t{1} << kSubpixelBits;
constexpr double kMaxCoordinate = 16384.0;

struct Fixed {
    std::int64_t x = 0;
    std::int64_t y = 0;
};

std::int64_t edge(const Fixed& a, const Fixed& b, std::int64_t px, std::int64_t py) {
    return (b.x - a.x) * (py - a.y) - (b.y - a.y) * (px - a.x);
}

// With y pointing down and positive area, the top edge runs left to right
// and a left edge runs upward.
bool top_left(const Fixed& a, const Fixed& b) {
    const auto dy = b.y - a.y;
    const auto dx = b.x - a.x;
    return dy < 0 || (dy == 0 && dx > 0);
}

std::uint32_t mul255(std::uint32_t a, std::uint32_t b) {
    return (a * b + 127u) / 255u;
}

std::uint32_t channel(std::uint32_t packed, int index) {
    return (packed >> (24 - 8 * index)) & 0xffu;
}

bool project(const DrawCommand& command, const DrawVertex& vertex, Fixed& out) {
    double x = static_cast<double>(vertex.x) + static_cast<double>(command.translation_x);
    double y = static_cast<double>(vertex.y) + static_cast<double>(command.translation_y);
    if (command.transform_enabled) {
        const auto& m = command.transform;
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
    if (!std::isfinite(x) || !std::isfinite(y) || std::fabs(x) > kMaxCoordinate ||
        std::fabs(y) > kMaxCoordinate)
        return false;
    out.x = std::llround(x * static_cast<double>(kSubpixelScale));
    out.y = std::llround(y * static_cast<double>(kSubpixelScale));
    return true;
}

} // namespace

SoftRenderer::SoftRenderer(std::uint32_t clear_rgba) : clear_rgba_(clear_rgba) {}

void SoftRenderer::begin_frame(std::uint32_t width, std::uint32_t height) {
    width_ = width;
    height_ = height;
    triangles_ = 0;
    skipped_ = 0;
    const auto alpha = channel(clear_rgba_, 3);
    const std::uint8_t clear[4] = {static_cast<std::uint8_t>(mul255(channel(clear_rgba_, 0), alpha)),
                                   static_cast<std::uint8_t>(mul255(channel(clear_rgba_, 1), alpha)),
                                   static_cast<std::uint8_t>(mul255(channel(clear_rgba_, 2), alpha)),
                                   static_cast<std::uint8_t>(alpha)};
    pixels_.assign(static_cast<std::size_t>(width) * height * 4u, 0);
    for (std::size_t i = 0; i < pixels_.size(); i += 4)
        std::copy_n(clear, 4, pixels_.begin() + static_cast<std::ptrdiff_t>(i));
}

void SoftRenderer::end_frame() {}

void SoftRenderer::submit(const DrawCommand& command) {
    if (!command.vertices || !command.indices || width_ == 0 || height_ == 0)
        return;
    Texture texture;
    const Texture* sampled = nullptr;
    if (command.kind == DrawCommand::Kind::texture && command.texture_bytes) {
        if (command.texture_path.empty()) {
            const auto expected = static_cast<std::size_t>(command.texture_width) *
                                  command.texture_height * 4u;
            if (command.texture_width == 0 || command.texture_bytes->size() != expected) {
                ++skipped_;
                return;
            }
            texture = {command.texture_width, command.texture_height,
                       reinterpret_cast<const std::uint8_t*>(command.texture_bytes->data())};
        } else {
            std::uint32_t w = 0;
            std::uint32_t h = 0;
            decoded_.clear();
            if (!decoder_ || !decoder_(*command.texture_bytes, w, h, decoded_) || w == 0 ||
                h == 0 || decoded_.size() != static_cast<std::size_t>(w) * h * 4u) {
                ++skipped_;
                return;
            }
            texture = {w, h, decoded_.data()};
        }
        sampled = &texture;
    }
    const auto& vertices = *command.vertices;
    const auto& indices = *command.indices;
    for (std::size_t i = 0; i + 2 < indices.size(); i += 3) {
        const auto a = indices[i];
        const auto b = indices[i + 1];
        const auto c = indices[i + 2];
        if (a >= vertices.size() || b >= vertices.size() || c >= vertices.size())
            continue;
        draw_triangle(command, vertices[a], vertices[b], vertices[c], sampled);
    }
}

void SoftRenderer::draw_triangle(const DrawCommand& command, const DrawVertex& va,
                                 const DrawVertex& vb, const DrawVertex& vc,
                                 const Texture* texture) {
    Fixed p0;
    Fixed p1;
    Fixed p2;
    if (!project(command, va, p0) || !project(command, vb, p1) || !project(command, vc, p2))
        return;
    const DrawVertex* v0 = &va;
    const DrawVertex* v1 = &vb;
    const DrawVertex* v2 = &vc;
    auto area = edge(p0, p1, p2.x, p2.y);
    if (area == 0)
        return;
    if (area < 0) {
        std::swap(p1, p2);
        std::swap(v1, v2);
        area = -area;
    }

    std::int64_t clip_x0 = 0;
    std::int64_t clip_y0 = 0;
    std::int64_t clip_x1 = width_;
    std::int64_t clip_y1 = height_;
    if (command.scissor_enabled) {
        clip_x0 = std::max<std::int64_t>(clip_x0, command.scissor.x);
        clip_y0 = std::max<std::int64_t>(clip_y0, command.scissor.y);
        clip_x1 = std::min<std::int64_t>(
            clip_x1, std::int64_t{command.scissor.x} + std::max(0, command.scissor.width));
        clip_y1 = std::min<std::int64_t>(
            clip_y1, std::int64_t{command.scissor.y} + std::max(0, command.scissor.height));
    }
    const auto floor_pixel = [](std::int64_t value) {
        return value >= 0 ? value >> kSubpixelBits : -((-value + kSubpixelScale - 1) >> kSubpixelBits);
    };
    const auto x0 = std::max(clip_x0, floor_pixel(std::min({p0.x, p1.x, p2.x})));
    const auto y0 = std::max(clip_y0, floor_pixel(std::min({p0.y, p1.y, p2.y})));
    const auto x1 = std::min(clip_x1 - 1, floor_pixel(std::max({p0.x, p1.x, p2.x})));
    const auto y1 = std::min(clip_y1 - 1, floor_pixel(std::max({p0.y, p1.y, p2.y})));
    if (x0 > x1 || y0 > y1)
        return;
    ++triangles_;

    const std::int64_t bias0 = top_left(p1, p2) ? 0 : -1;
    const std::int64_t bias1 = top_left(p2, p0) ? 0 : -1;
    const std::int64_t bias2 = top_left(p0, p1) ? 0 : -1;
    const std::int64_t half = kSubpixelScale / 2;
    const auto darea = static_cast<double>(area);

    for (auto y = y0; y <= y1; ++y) {
        const auto py = y * kSubpixelScale + half;
        for (auto x = x0; x <= x1; ++x) {
            const auto px = x * kSubpixelScale + half;
            const auto w0 = edge(p1, p2, px, py);
            const auto w1 = edge(p2, p0, px, py);
            const auto w2 = edge(p0, p1, px, py);
            if (w0 + bias0 < 0 || w1 + bias1 < 0 || w2 + bias2 < 0)
                continue;
            std::uint32_t src[4];
            for (int k = 0; k < 4; ++k) {
                const auto sum = w0 * channel(v0->colour, k) + w1 * channel(v1->colour, k) +
                                 w2 * channel(v2->colour, k);
                src[k] = static_cast<std::uint32_t>((sum + area / 2) / area);
            }
            if (texture) {
                const double u =
                    (static_cast<double>(w0) * v0->u + static_cast<double>(w1) * v1->u +
                     static_cast<double>(w2) * v2->u) /
                    darea;
                const double v =
                    (static_cast<double>(w0) * v0->v + static_cast<double>(w1) * v1->v +
                     static_cast<double>(w2) * v2->v) /
                    darea;
                const auto tx = std::clamp<std::int64_t>(
                    static_cast<std::int64_t>(std::floor(u * texture->width)), 0,
                    std::int64_t{texture->width} - 1);
                const auto ty = std::clamp<std::int64_t>(
                    static_cast<std::int64_t>(std::floor(v * texture->height)), 0,
                    std::int64_t{texture->height} - 1);
                const auto* texel =
                    texture->rgba + (static_cast<std::size_t>(ty) * texture->width +
                                     static_cast<std::size_t>(tx)) *
                                        4u;
                for (int k = 0; k < 4; ++k)
                    src[k] = mul255(texel[k], src[k]);
            }
            auto* dst = pixels_.data() +
                        (static_cast<std::size_t>(y) * width_ + static_cast<std::size_t>(x)) * 4u;
            const auto inverse = 255u - src[3];
            for (int k = 0; k < 4; ++k)
                dst[k] = static_cast<std::uint8_t>(std::min(255u, src[k] + mul255(dst[k], inverse)));
        }
    }
}

std::string SoftRenderer::encode_png() const {
    if (width_ == 0 || height_ == 0 || pixels_.empty())
        return {};
    std::vector<std::uint8_t> straight(pixels_.size());
    for (std::size_t i = 0; i < pixels_.size(); i += 4) {
        const std::uint32_t alpha = pixels_[i + 3];
        straight[i + 3] = static_cast<std::uint8_t>(alpha);
        for (int k = 0; k < 3; ++k) {
            straight[i + k] =
                alpha == 0 ? 0
                           : static_cast<std::uint8_t>(
                                 std::min(255u, (pixels_[i + k] * 255u + alpha / 2u) / alpha));
        }
    }
    return encode_png_rgba(straight.data(), width_, height_);
}

bool SoftRenderer::write_png(const std::filesystem::path& path) const {
    const auto png = encode_png();
    if (png.empty())
        return false;
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    stream.write(png.data(), static_cast<std::streamsize>(png.size()));
    return static_cast<bool>(stream);
}

} // namespace vellum::rmlui
