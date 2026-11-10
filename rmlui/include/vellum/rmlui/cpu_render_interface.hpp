#pragma once
// An Rml::RenderInterface that draws RmlUi on the CPU into a caller-owned
// premultiplied RGBA8 buffer (VellumRmlUi::Cpu, VELLUM_RMLUI_CPU=ON). It
// depends only on RmlUi: not on Vellum, not on Pulp, and not on this module's
// Core target, so any RmlUi user can adopt it, on any platform.
//
// Output is deterministic: the same calls give the same bytes on macOS,
// Windows and Linux. It uses the same rasteriser as SoftRenderer (vertices
// snapped to 1/256 pixel, integer edge functions with a top-left fill rule,
// nearest-texel sampling, premultiplied source-over in 8-bit integers).
//
// Frame contract: begin_frame() names the buffer (top row first, the stride
// given), RmlUi renders, end_frame() finishes. The renderer never allocates
// or presents the target.
//
// Drawn: compiled geometry with vertex colours and texture coordinates,
// generated textures, loaded textures (through a decoder the caller
// supplies), scissor, transforms, premultiplied blending. Not drawn yet:
// clip masks, layers, filters, shaders and mask images. Each such call is
// counted in unsupported_calls() (by kind in unsupported(kind)) and logged
// once per kind, never dropped silently; a layer composite draws into the
// base target unfiltered.
//
// Threading: one instance per thread, no global state, so several editors and
// an offline renderer can each own one. (RmlUi itself is process-global; this
// is about the renderer.)
//
// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT) and Vellum's own SoftRenderer. It contains no code from any
// GPL-licensed project.

#include <RmlUi/Core/RenderInterface.h>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace vellum::rmlui {

struct CpuFrameTarget {
    std::uint8_t* rgba = nullptr;  // premultiplied RGBA8, caller-owned
    std::uint32_t width = 0;       // pixels
    std::uint32_t height = 0;      // pixels
    std::size_t stride = 0;        // bytes per row; 0 means width * 4
    float scale = 1.0f;            // RmlUi context units to target pixels
    bool clear = true;             // start transparent; false keeps the contents
};

class CpuRenderInterface final : public Rml::RenderInterface {
  public:
    // Decodes an encoded image to premultiplied RGBA8, top row first.
    using ImageDecoder = std::function<bool(const std::vector<Rml::byte>& encoded, std::uint32_t& width,
                                            std::uint32_t& height, std::vector<std::uint8_t>& rgba)>;
    // Called for a texture wider or taller than the limit. May replace the
    // image (and its size) to fit, and returns true to keep it; false refuses
    // the texture. The renderer never scales on its own.
    using TextureClampRule = std::function<bool(std::uint32_t& width, std::uint32_t& height,
                                                std::vector<std::uint8_t>& rgba)>;

    CpuRenderInterface();
    ~CpuRenderInterface() override;
    CpuRenderInterface(const CpuRenderInterface&) = delete;
    CpuRenderInterface& operator=(const CpuRenderInterface&) = delete;

    // False, drawing nothing, for a null buffer, a zero size, a stride
    // smaller than width * 4 or a scale that is not positive.
    bool begin_frame(const CpuFrameTarget& frame);
    void end_frame();

    void set_image_decoder(ImageDecoder decoder);
    // 0 means no limit (the default).
    void set_texture_limit(std::uint32_t max_side, TextureClampRule rule);
    [[nodiscard]] std::uint32_t texture_limit() const noexcept;

    // Calls this renderer does not draw, since construction: in total, and by
    // kind ("clip_mask", "layer", "filter", "shader", "mask_image").
    [[nodiscard]] std::size_t unsupported_calls() const noexcept;
    [[nodiscard]] std::size_t unsupported(const std::string& kind) const;

    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex> vertices,
                                                Rml::Span<const int> indices) override;
    void RenderGeometry(Rml::CompiledGeometryHandle geometry, Rml::Vector2f translation,
                        Rml::TextureHandle texture) override;
    void ReleaseGeometry(Rml::CompiledGeometryHandle geometry) override;
    Rml::TextureHandle LoadTexture(Rml::Vector2i& texture_dimensions, const Rml::String& source) override;
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte> source,
                                       Rml::Vector2i source_dimensions) override;
    void ReleaseTexture(Rml::TextureHandle texture) override;
    void EnableScissorRegion(bool enable) override;
    void SetScissorRegion(Rml::Rectanglei region) override;
    void SetTransform(const Rml::Matrix4f* transform) override;

    void EnableClipMask(bool enable) override;
    void RenderToClipMask(Rml::ClipMaskOperation operation, Rml::CompiledGeometryHandle geometry,
                          Rml::Vector2f translation) override;
    Rml::LayerHandle PushLayer() override;
    void CompositeLayers(Rml::LayerHandle source, Rml::LayerHandle destination, Rml::BlendMode blend_mode,
                         Rml::Span<const Rml::CompiledFilterHandle> filters) override;
    void PopLayer() override;
    Rml::TextureHandle SaveLayerAsTexture() override;
    Rml::CompiledFilterHandle SaveLayerAsMaskImage() override;
    Rml::CompiledFilterHandle CompileFilter(const Rml::String& name, const Rml::Dictionary& parameters) override;
    void ReleaseFilter(Rml::CompiledFilterHandle filter) override;
    Rml::CompiledShaderHandle CompileShader(const Rml::String& name, const Rml::Dictionary& parameters) override;
    void RenderShader(Rml::CompiledShaderHandle shader, Rml::CompiledGeometryHandle geometry,
                      Rml::Vector2f translation, Rml::TextureHandle texture) override;
    void ReleaseShader(Rml::CompiledShaderHandle shader) override;

  private:
    struct State;
    std::unique_ptr<State> state_;
};

} // namespace vellum::rmlui
