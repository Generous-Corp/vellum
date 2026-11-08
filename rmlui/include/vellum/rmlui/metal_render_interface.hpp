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
// scissor semantics, the stencil clip-mask operations, the layer stack, the
// filter parameters and colour matrices, the separable blur with iterative
// half-scaling, and the gradient functions). It contains no code from any
// GPL-licensed project.
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
// Drawn: compiled geometry, generated and loaded textures, scissor,
// transforms, clip masks (a stencil the renderer owns, sized to the target),
// layers (PushLayer, CompositeLayers with the blend and replace modes,
// PopLayer, SaveLayerAsTexture), filters (opacity, blur, drop-shadow,
// brightness, contrast, invert, grayscale, sepia, hue-rotate, saturate),
// mask images (SaveLayerAsMaskImage), and gradient shaders (linear, radial,
// conic and their repeating forms). Rendering goes to an offscreen base layer
// that end_frame() copies to the target; filters run on target-sized
// offscreen textures within the current scissor. Not drawn: the "shader"
// decorator's application-defined shaders and unknown filter names, which are
// counted in unsupported_calls(). Gradients follow RmlUi's GL3 backend in
// easing between colour stops (smoothstep), so they match RmlUi's reference
// rendering rather than CSS's linear interpolation.

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
    // The region to draw into, in target pixels: the whole target. (Layers are
    // target-sized and composited over the viewport.)
    MTLViewport viewport{};
    // RmlUi context units to target pixels. Set the context's dimensions to
    // the viewport size divided by this.
    float scale = 1.0f;
    // Start from transparent. With false, the frame starts from the target's
    // current contents, which needs a target that is not framebuffer-only.
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

    // RmlUi render calls this renderer does not draw (application-defined
    // shaders, unknown filters, filters or shaders with missing parameters),
    // counted since construction.
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
    Rml::TextureHandle SaveLayerAsTexture() override;
    Rml::CompiledFilterHandle SaveLayerAsMaskImage() override;
    Rml::CompiledFilterHandle CompileFilter(const Rml::String& name,
                                            const Rml::Dictionary& parameters) override;
    void ReleaseFilter(Rml::CompiledFilterHandle filter) override;
    Rml::CompiledShaderHandle CompileShader(const Rml::String& name,
                                            const Rml::Dictionary& parameters) override;
    void RenderShader(Rml::CompiledShaderHandle shader, Rml::CompiledGeometryHandle geometry,
                      Rml::Vector2f translation, Rml::TextureHandle texture) override;
    void ReleaseShader(Rml::CompiledShaderHandle shader) override;

  private:
    struct State;
    std::unique_ptr<State> state_;
};

} // namespace vellum::rmlui
