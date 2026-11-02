#pragma once

#include <vellum/rmlui/backend.hpp>

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace vellum::rmlui {

// A CPU rasteriser for the Backend's draw commands, for headless rendering
// and CI. It needs no GPU, no window and no Vellum renderer.
//
// Output is deterministic: vertices are snapped to 1/256 pixel, coverage and
// colour interpolation use integer edge functions with a top-left fill rule,
// texture sampling is nearest-texel, and blending is premultiplied source-over
// in 8-bit integer arithmetic. There is no anti-aliasing beyond the alpha
// RmlUi already writes into its geometry.
class SoftRenderer final : public RenderInterface {
  public:
    // Decodes an image asset (the bytes RmlUi loaded from a texture path) to
    // premultiplied RGBA8. Without a decoder, draws that reference an image
    // asset are skipped and counted in skipped_draws().
    using ImageDecoder = std::function<bool(const std::string& encoded, std::uint32_t& width,
                                            std::uint32_t& height,
                                            std::vector<std::uint8_t>& premultiplied_rgba)>;

    // clear_rgba is straight (not premultiplied) RGBA, red in the high byte.
    explicit SoftRenderer(std::uint32_t clear_rgba = 0x000000ffu);

    void begin_frame(std::uint32_t width, std::uint32_t height) override;
    void submit(const DrawCommand& command) override;
    void end_frame() override;

    void set_image_decoder(ImageDecoder decoder) {
        decoder_ = std::move(decoder);
    }

    [[nodiscard]] std::uint32_t width() const noexcept {
        return width_;
    }
    [[nodiscard]] std::uint32_t height() const noexcept {
        return height_;
    }
    // Premultiplied RGBA8, row-major, top row first.
    [[nodiscard]] const std::vector<std::uint8_t>& pixels() const noexcept {
        return pixels_;
    }
    [[nodiscard]] std::size_t triangles_drawn() const noexcept {
        return triangles_;
    }
    [[nodiscard]] std::size_t skipped_draws() const noexcept {
        return skipped_;
    }

    // Encodes the last frame as a PNG (straight alpha). Empty when no frame
    // has been rendered.
    [[nodiscard]] std::string encode_png() const;
    bool write_png(const std::filesystem::path& path) const;

  private:
    struct Texture {
        std::uint32_t width = 0;
        std::uint32_t height = 0;
        const std::uint8_t* rgba = nullptr;
    };
    void draw_triangle(const DrawCommand& command, const DrawVertex& a, const DrawVertex& b,
                       const DrawVertex& c, const Texture* texture);

    std::uint32_t clear_rgba_;
    std::uint32_t width_ = 0;
    std::uint32_t height_ = 0;
    std::vector<std::uint8_t> pixels_;
    std::size_t triangles_ = 0;
    std::size_t skipped_ = 0;
    ImageDecoder decoder_;
    std::vector<std::uint8_t> decoded_;
};

} // namespace vellum::rmlui
