#pragma once

// An Rml::RenderInterface that draws RmlUi with Metal (VellumRmlUi::Metal,
// VELLUM_RMLUI_METAL=ON). It depends only on RmlUi and Apple's Metal,
// QuartzCore, Foundation, CoreGraphics and ImageIO frameworks: not on Vellum,
// not on Pulp, and not on this module's Core or Native targets, so any RmlUi
// user can adopt it.
//
// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT), Apple's Metal documentation, and RmlUi's MIT GL3 backend as a
// behavioural reference (premultiplied blending, the orthographic projection,
// scissor semantics). It contains no code from any GPL-licensed project.
//
// This header is Objective-C++: include it from a .mm file.
//
// Frame contract: the caller owns presentation. For each frame it passes a
// command buffer and a colour texture (a CAMetalDrawable's texture, or an
// offscreen texture) to begin_frame(), lets RmlUi render, then calls
// end_frame() and presents and commits the command buffer itself. The renderer
// never presents, commits, or touches CAMetalLayer or AppKit.
//
// Output is premultiplied alpha, blended source-over (one, one minus source
// alpha), as RmlUi produces its vertex colours and generated textures.
//
// Threading: like every RmlUi call, frames are rendered from one thread at a
// time in the process.
//
// Pixel format: fixed per renderer at the first begin_frame(); a later frame
// with another format draws nothing and is logged as a caller error. The
// viewport is the region to draw into, normally the whole target.
//
// Supported in this version: compiled geometry, generated and loaded textures,
// scissor and transforms. Clip masks, layers, filters and shaders are not yet
// drawn; calls to them are counted in unsupported_calls().

#if !defined(__OBJC__)
#error "vellum/rmlui/metal_render_interface.hpp is Objective-C++; include it from a .mm file"
#endif

#import <Metal/Metal.h>

#include <RmlUi/Core/RenderInterface.h>

#include <cstddef>
#include <cstdint>
#include <memory>

namespace vellum::rmlui {

struct MetalFrameTarget {
    // Caller-owned; the renderer encodes into it and never commits it.
    id<MTLCommandBuffer> command_buffer = nil;
    // The colour attachment: BGRA8Unorm, RGBA8Unorm or their sRGB variants.
    id<MTLTexture> target = nil;
    // The region to draw into, in target pixels.
    MTLViewport viewport{};
    // RmlUi context units to target pixels. Set the context's dimensions to
    // the viewport size divided by this.
    float scale = 1.0f;
    // Clear the target to transparent before drawing.
    bool clear = true;
};

class MetalRenderInterface final : public Rml::RenderInterface {
  public:
    // Pipelines and resources are created on this device for the renderer's
    // lifetime; build a new renderer to change device.
    explicit MetalRenderInterface(id<MTLDevice> device);
    ~MetalRenderInterface() override;
    MetalRenderInterface(const MetalRenderInterface&) = delete;
    MetalRenderInterface& operator=(const MetalRenderInterface&) = delete;

    // False when the device could not provide the pipelines (for example the
    // shader library failed to compile). begin_frame() then draws nothing.
    [[nodiscard]] bool valid() const noexcept;

    void begin_frame(const MetalFrameTarget& frame);
    void end_frame();

    // RmlUi render calls this version does not draw yet (clip masks, layers,
    // filters, shaders), counted since construction.
    [[nodiscard]] std::size_t unsupported_calls() const noexcept;

    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex> vertices,
                                                Rml::Span<const int> indices) override;
    void RenderGeometry(Rml::CompiledGeometryHandle geometry, Rml::Vector2f translation,
                        Rml::TextureHandle texture) override;
    void ReleaseGeometry(Rml::CompiledGeometryHandle geometry) override;

    Rml::TextureHandle LoadTexture(Rml::Vector2i& texture_dimensions,
                                   const Rml::String& source) override;
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
    void CompositeLayers(Rml::LayerHandle source, Rml::LayerHandle destination,
                         Rml::BlendMode blend_mode,
                         Rml::Span<const Rml::CompiledFilterHandle> filters) override;
    void PopLayer() override;
    Rml::CompiledFilterHandle CompileFilter(const Rml::String& name,
                                            const Rml::Dictionary& parameters) override;
    Rml::CompiledShaderHandle CompileShader(const Rml::String& name,
                                            const Rml::Dictionary& parameters) override;

  private:
    struct State;
    std::unique_ptr<State> state_;
};

} // namespace vellum::rmlui
