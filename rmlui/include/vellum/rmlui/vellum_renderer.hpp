#pragma once

#include <vellum/rmlui/backend.hpp>

#include <vellum/graphics/scene.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <cstddef>
#include <cstdint>
#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace vellum::rmlui {

// Draws the Backend's draw commands through Vellum's own renderer: each
// DrawCommand becomes a triangle-mesh node in a vellum::graphics::Scene, and
// end_frame() renders the scene on the SkiaDawnSurface it was given.
//
// This target is optional (VELLUM_RMLUI_VELLUM_RENDERER) and is the only part
// of the module that links Vellum. Core and the soft renderer do not.
//
// Geometry follows the soft renderer: the command's translation and transform
// are applied to each vertex on the CPU, with the same projective divide and
// the same +/-16384 pixel bound, and a triangle with a vertex that cannot be
// projected is dropped. Colours and texture coordinates then interpolate
// linearly in output space, texels are sampled nearest-neighbour, and nothing
// is anti-aliased. Rasterisation and colour arithmetic are the GPU's, so the
// output matches the soft renderer within a tolerance, not byte for byte.
class VellumRenderer final : public RenderInterface {
  public:
    // Decodes an image asset to premultiplied RGBA8. The renderer uses
    // SkiaDawnSurface's bounded PNG decoder by default. A host can replace it
    // for another encoded format. A failed decode skips the draw and increments
    // skipped_draws().
    using ImageDecoder = std::function<bool(const std::string& encoded, std::uint32_t& width,
                                            std::uint32_t& height,
                                            std::vector<std::uint8_t>& premultiplied_rgba)>;

    // `surface` must outlive the renderer. clear_rgba is straight (not
    // premultiplied) RGBA, red in the high byte, as for SoftRenderer.
    using Clock = std::function<std::chrono::steady_clock::time_point()>;

    explicit VellumRenderer(vellum::graphics::SkiaDawnSurface& surface,
                            std::uint32_t clear_rgba = 0x000000ffu,
                            Clock clock = {});

    void begin_frame(std::uint32_t width, std::uint32_t height) override;
    void submit(const DrawCommand& command) override;
    void end_frame() override;
    void on_attach() override;
    void on_detach() override;
    void on_visibility_changed(bool visible) override;
    void on_idle() override;
    void on_close() override;

    void set_image_decoder(ImageDecoder decoder) {
        decoder_ = std::move(decoder);
    }

    // The scene the last frame submitted (or is building).
    [[nodiscard]] const vellum::graphics::Scene& scene() const noexcept {
        return scene_;
    }
    // Whether the last end_frame() rendered; on failure, last_error() says why.
    [[nodiscard]] bool frame_ok() const noexcept {
        return frame_ok_;
    }
    [[nodiscard]] const std::string& last_error() const noexcept {
        return error_;
    }
    [[nodiscard]] std::size_t triangles_submitted() const noexcept {
        return triangles_;
    }
    [[nodiscard]] std::size_t skipped_draws() const noexcept {
        return skipped_;
    }
    [[nodiscard]] std::size_t cached_texture_count() const noexcept { return textures_.size(); }

  private:
    std::shared_ptr<const vellum::graphics::MeshTexture> texture_for(const DrawCommand& command);

    vellum::graphics::SkiaDawnSurface& surface_;
    std::uint32_t clear_rgba_;
    std::uint32_t width_ = 0;
    std::uint32_t height_ = 0;
    vellum::graphics::Scene scene_;
    bool frame_ok_ = false;
    std::string error_;
    std::size_t triangles_ = 0;
    std::size_t skipped_ = 0;
    ImageDecoder decoder_;
    // Generated textures (glyphs, gradients) are shared between draws; each is
    // converted once per frame. The entry holds the source bytes too, so the
    // key's address cannot be reused while the entry exists.
    struct CachedTexture {
        std::shared_ptr<const std::string> source;
        std::shared_ptr<const vellum::graphics::MeshTexture> texture;
        bool decoded = false; // built by the image decoder, not from raw RGBA
    };
    std::unordered_map<const std::string*, CachedTexture> textures_;
    Clock clock_;
    std::chrono::steady_clock::time_point texture_expiry_{};
    bool texture_grace_active_ = false;
    static constexpr std::chrono::milliseconds kTextureGracePeriod{250};

    void collect_expired_textures();
};

} // namespace vellum::rmlui
