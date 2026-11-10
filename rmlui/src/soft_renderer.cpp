#include <vellum/rmlui/soft_renderer.hpp>

#include "soft_raster.hpp"

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

using raster::channel;
using raster::mul255;

bool project(const DrawCommand& command, const DrawVertex& vertex, raster::Fixed& out) {
    const double x = static_cast<double>(vertex.x) + static_cast<double>(command.translation_x);
    const double y = static_cast<double>(vertex.y) + static_cast<double>(command.translation_y);
    return raster::project(x, y, command.transform_enabled ? command.transform.data() : nullptr, 1.0,
                           out);
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
    raster::Vertex v[3];
    const DrawVertex* given[3] = {&va, &vb, &vc};
    for (int i = 0; i < 3; ++i) {
        if (!project(command, *given[i], v[i].position))
            return;
        v[i].colour = given[i]->colour;
        v[i].u = given[i]->u;
        v[i].v = given[i]->v;
    }
    raster::Clip clip{0, 0, width_, height_};
    if (command.scissor_enabled) {
        clip.x0 = std::max<std::int64_t>(clip.x0, command.scissor.x);
        clip.y0 = std::max<std::int64_t>(clip.y0, command.scissor.y);
        clip.x1 = std::min<std::int64_t>(
            clip.x1, std::int64_t{command.scissor.x} + std::max(0, command.scissor.width));
        clip.y1 = std::min<std::int64_t>(
            clip.y1, std::int64_t{command.scissor.y} + std::max(0, command.scissor.height));
    }
    const raster::Target target{pixels_.data(), width_, height_, static_cast<std::size_t>(width_) * 4u};
    raster::Texture sampled;
    if (texture)
        sampled = {texture->width, texture->height, texture->rgba};
    if (raster::draw_triangle(target, clip, v[0], v[1], v[2], texture ? &sampled : nullptr))
        ++triangles_;
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
