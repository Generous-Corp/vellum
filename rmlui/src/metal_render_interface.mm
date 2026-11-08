// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT), Apple's Metal documentation, and RmlUi's MIT GL3 backend as a
// behavioural reference (premultiplied blending, the orthographic projection,
// scissor semantics, the stencil clip-mask operations and the layer stack). It
// contains no code from any GPL-licensed project.

#import <vellum/rmlui/metal_render_interface.hpp>

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

#include <RmlUi/Core/Core.h>
#include <RmlUi/Core/FileInterface.h>
#include <RmlUi/Core/Math.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <unordered_map>
#include <vector>

namespace vellum::rmlui {

namespace {

// RmlUi's vertex: position, premultiplied RGBA8 colour, texture coordinate.
static_assert(sizeof(Rml::Vertex) == 20, "unexpected Rml::Vertex layout");

constexpr const char* kShaderSource = R"msl(
#include <metal_stdlib>
using namespace metal;

struct VertexIn {
    float2 position [[attribute(0)]];
    float4 colour [[attribute(1)]];
    float2 uv [[attribute(2)]];
};

struct VertexOut {
    float4 position [[position]];
    float4 colour;
    float2 uv;
};

struct Uniforms {
    float4x4 transform;   // projection * RmlUi transform
    float2 translation;
};

vertex VertexOut vellum_rmlui_vertex(VertexIn in [[stage_in]],
                                     constant Uniforms& uniforms [[buffer(1)]]) {
    VertexOut out;
    out.position = uniforms.transform * float4(in.position + uniforms.translation, 0.0, 1.0);
    out.colour = in.colour;
    out.uv = in.uv;
    return out;
}

fragment float4 vellum_rmlui_colour(VertexOut in [[stage_in]]) {
    return in.colour;
}

fragment float4 vellum_rmlui_texture(VertexOut in [[stage_in]],
                                     texture2d<float> texture [[texture(0)]],
                                     sampler texture_sampler [[sampler(0)]]) {
    return texture.sample(texture_sampler, in.uv) * in.colour;
}

// One triangle covering the viewport, generated from the vertex index; uv maps
// the viewport onto a whole layer texture, top row first.
vertex VertexOut vellum_rmlui_fullscreen(uint id [[vertex_id]]) {
    const float2 corner = float2((id << 1) & 2, id & 2);
    VertexOut out;
    out.position = float4(corner * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    out.colour = float4(1.0);
    out.uv = corner;
    return out;
}

fragment float4 vellum_rmlui_passthrough(VertexOut in [[stage_in]],
                                         texture2d<float> texture [[texture(0)]]) {
    constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
    return texture.sample(nearest, in.uv);
}
)msl";

constexpr MTLPixelFormat kStencilFormat = MTLPixelFormatStencil8;

struct Uniforms {
    float transform[16];
    float translation[2];
    float padding[2];
};

struct Geometry {
    id<MTLBuffer> vertices = nil;
    id<MTLBuffer> indices = nil;
    NSUInteger index_count = 0;
};

struct Pipelines {
    id<MTLRenderPipelineState> colour = nil;       // geometry, premultiplied over
    id<MTLRenderPipelineState> texture = nil;      // textured geometry
    id<MTLRenderPipelineState> mask = nil;         // geometry into the stencil only
    id<MTLRenderPipelineState> mask_clear = nil;   // viewport into the stencil only
    id<MTLRenderPipelineState> blend = nil;        // layer composite, premultiplied over
    id<MTLRenderPipelineState> replace = nil;      // layer composite, no blending
};

// Decodes an encoded image to premultiplied RGBA8, top row first.
bool decode_premultiplied(const std::vector<Rml::byte>& encoded, int& width, int& height,
                          std::vector<std::uint8_t>& out) {
    NSData* data = [NSData dataWithBytesNoCopy:const_cast<Rml::byte*>(encoded.data())
                                        length:encoded.size()
                                  freeWhenDone:NO];
    CGImageSourceRef source = CGImageSourceCreateWithData((__bridge CFDataRef)data, nullptr);
    if (!source)
        return false;
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, nullptr);
    CFRelease(source);
    if (!image)
        return false;
    width = static_cast<int>(CGImageGetWidth(image));
    height = static_cast<int>(CGImageGetHeight(image));
    bool ok = width > 0 && height > 0;
    if (ok) {
        out.assign(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4u, 0);
        CGColorSpaceRef colour_space = CGColorSpaceCreateDeviceRGB();
        CGContextRef context = CGBitmapContextCreate(
            out.data(), static_cast<std::size_t>(width), static_cast<std::size_t>(height), 8,
            static_cast<std::size_t>(width) * 4u, colour_space,
            static_cast<CGBitmapInfo>(kCGImageAlphaPremultipliedLast) | kCGBitmapByteOrderDefault);
        CGColorSpaceRelease(colour_space);
        ok = context != nullptr;
        if (ok) {
            CGContextDrawImage(context, CGRectMake(0, 0, width, height), image);
            CGContextRelease(context);
        }
    }
    CGImageRelease(image);
    return ok;
}

} // namespace

struct MetalRenderInterface::State {
    id<MTLDevice> device = nil;
    id<MTLLibrary> library = nil;
    id<MTLSamplerState> sampler = nil;
    MTLVertexDescriptor* vertex_layout = nil;
    std::unordered_map<NSUInteger, Pipelines> pipelines; // by MTLPixelFormat
    MTLPixelFormat frame_format = MTLPixelFormatInvalid;
    std::unordered_map<std::uintptr_t, Geometry> geometries;
    std::unordered_map<std::uintptr_t, id<MTLTexture>> textures;
    std::uintptr_t next_handle = 1;

    // Depth-stencil states for the clip mask.
    id<MTLDepthStencilState> stencil_off = nil;    // clip mask disabled
    id<MTLDepthStencilState> stencil_test = nil;   // draw where stencil == reference
    id<MTLDepthStencilState> stencil_set = nil;    // write the reference everywhere drawn
    id<MTLDepthStencilState> stencil_incr = nil;   // increment everywhere drawn

    // Render targets, sized to the frame target. Layer 0 is the base layer;
    // end_frame() copies it to the target. All layers share one stencil.
    std::vector<id<MTLTexture>> layer_pool;
    id<MTLTexture> stencil = nil;
    NSUInteger target_width = 0;
    NSUInteger target_height = 0;
    int layer_count = 0;

    // Frame state.
    id<MTLRenderCommandEncoder> encoder = nil;
    int encoder_layer = -1;
    bool stencil_cleared = false;
    const Pipelines* frame_pipelines = nullptr;
    MetalFrameTarget frame{};
    Rml::Matrix4f projection = Rml::Matrix4f::Identity();
    Rml::Matrix4f transform = Rml::Matrix4f::Identity();
    bool scissor_enabled = false;
    Rml::Rectanglei scissor = Rml::Rectanglei::FromSize({0, 0});
    bool clip_enabled = false;
    std::uint32_t stencil_reference = 0;

    std::size_t unsupported = 0;

    id<MTLRenderPipelineState> build_pipeline(MTLPixelFormat format, NSString* vertex,
                                              NSString* fragment, bool blend, bool write_colour,
                                              bool with_vertex_layout) {
        MTLRenderPipelineDescriptor* descriptor = [[MTLRenderPipelineDescriptor alloc] init];
        descriptor.vertexFunction = [library newFunctionWithName:vertex];
        descriptor.fragmentFunction = [library newFunctionWithName:fragment];
        if (with_vertex_layout)
            descriptor.vertexDescriptor = vertex_layout;
        descriptor.stencilAttachmentPixelFormat = kStencilFormat;
        MTLRenderPipelineColorAttachmentDescriptor* colour = descriptor.colorAttachments[0];
        colour.pixelFormat = format;
        colour.writeMask = write_colour ? MTLColorWriteMaskAll : MTLColorWriteMaskNone;
        colour.blendingEnabled = blend ? YES : NO;
        if (blend) {
            // Premultiplied source-over.
            colour.rgbBlendOperation = MTLBlendOperationAdd;
            colour.alphaBlendOperation = MTLBlendOperationAdd;
#if defined(VELLUM_RMLUI_METAL_TEST_STRAIGHT_ALPHA_BLEND)
            // Test-only control: blend as if colours were straight alpha.
            colour.sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
#else
            colour.sourceRGBBlendFactor = MTLBlendFactorOne;
#endif
            colour.sourceAlphaBlendFactor = MTLBlendFactorOne;
            colour.destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
            colour.destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
        }
        NSError* error = nil;
        id<MTLRenderPipelineState> state =
            [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
        if (!state)
            Rml::Log::Message(Rml::Log::LT_ERROR, "Metal pipeline failed: %s",
                              error ? error.localizedDescription.UTF8String : "unknown");
        return state;
    }

    const Pipelines* pipelines_for(MTLPixelFormat format) {
        const auto key = static_cast<NSUInteger>(format);
        if (auto found = pipelines.find(key); found != pipelines.end())
            return &found->second;
        Pipelines built;
        built.colour = build_pipeline(format, @"vellum_rmlui_vertex", @"vellum_rmlui_colour", true,
                                      true, true);
        built.texture = build_pipeline(format, @"vellum_rmlui_vertex", @"vellum_rmlui_texture",
                                       true, true, true);
        built.mask = build_pipeline(format, @"vellum_rmlui_vertex", @"vellum_rmlui_colour", false,
                                    false, true);
        built.mask_clear = build_pipeline(format, @"vellum_rmlui_fullscreen",
                                          @"vellum_rmlui_colour", false, false, false);
        built.blend = build_pipeline(format, @"vellum_rmlui_fullscreen",
                                     @"vellum_rmlui_passthrough", true, true, false);
        built.replace = build_pipeline(format, @"vellum_rmlui_fullscreen",
                                       @"vellum_rmlui_passthrough", false, true, false);
        if (!built.colour || !built.texture || !built.mask || !built.mask_clear || !built.blend ||
            !built.replace)
            return nullptr;
        return &pipelines.emplace(key, built).first->second;
    }

    id<MTLDepthStencilState> make_stencil_state(bool enabled, MTLCompareFunction compare,
                                                MTLStencilOperation pass) {
        MTLDepthStencilDescriptor* descriptor = [[MTLDepthStencilDescriptor alloc] init];
        if (enabled) {
            MTLStencilDescriptor* face = [[MTLStencilDescriptor alloc] init];
            face.stencilCompareFunction = compare;
            face.stencilFailureOperation = MTLStencilOperationKeep;
            face.depthFailureOperation = MTLStencilOperationKeep;
            face.depthStencilPassOperation = pass;
            face.readMask = 0xff;
            face.writeMask = 0xff;
            descriptor.frontFaceStencil = face;
            descriptor.backFaceStencil = face;
        }
        return [device newDepthStencilStateWithDescriptor:descriptor];
    }

    bool ensure_targets(id<MTLTexture> target) {
        const NSUInteger width = target.width;
        const NSUInteger height = target.height;
        if (width != target_width || height != target_height) {
            layer_pool.clear();
            stencil = nil;
            target_width = width;
            target_height = height;
        }
        if (!stencil) {
            MTLTextureDescriptor* descriptor =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:kStencilFormat
                                                                   width:width
                                                                  height:height
                                                               mipmapped:NO];
            descriptor.usage = MTLTextureUsageRenderTarget;
            descriptor.storageMode = MTLStorageModePrivate;
            stencil = [device newTextureWithDescriptor:descriptor];
        }
        return stencil != nil;
    }

    id<MTLTexture> layer_texture(int index) {
        while (static_cast<int>(layer_pool.size()) <= index) {
            MTLTextureDescriptor* descriptor =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:frame_format
                                                                   width:target_width
                                                                  height:target_height
                                                               mipmapped:NO];
            descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
            descriptor.storageMode = MTLStorageModePrivate;
            id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
            if (!texture)
                return nil;
            layer_pool.push_back(texture);
        }
        return layer_pool[static_cast<std::size_t>(index)];
    }

    void end_pass() {
        if (encoder)
            [encoder endEncoding];
        encoder = nil;
        encoder_layer = -1;
    }

    // Starts drawing into a layer, keeping (or clearing) its colour and keeping
    // the shared stencil, cleared once per frame.
    bool open_pass(int layer, bool clear_colour) {
        end_pass();
        id<MTLTexture> colour = layer_texture(layer);
        if (!colour)
            return false;
        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = colour;
        pass.colorAttachments[0].loadAction = clear_colour ? MTLLoadActionClear : MTLLoadActionLoad;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.stencilAttachment.texture = stencil;
        pass.stencilAttachment.loadAction = stencil_cleared ? MTLLoadActionLoad : MTLLoadActionClear;
        pass.stencilAttachment.clearStencil = 0;
        pass.stencilAttachment.storeAction = MTLStoreActionStore;
        stencil_cleared = true;
        encoder = [frame.command_buffer renderCommandEncoderWithDescriptor:pass];
        if (!encoder)
            return false;
        encoder_layer = layer;
        [encoder setViewport:frame.viewport];
        apply_scissor();
        return true;
    }

    bool top_pass() {
        if (encoder && encoder_layer == layer_count - 1)
            return true;
        return layer_count > 0 && open_pass(layer_count - 1, false);
    }

    void apply_clip_state() {
        [encoder setDepthStencilState:clip_enabled ? stencil_test : stencil_off];
        [encoder setStencilReferenceValue:stencil_reference];
    }

    void apply_scissor() {
        const auto width = target_width;
        const auto height = target_height;
        MTLScissorRect rect{0, 0, width, height};
        const double vx = frame.viewport.originX;
        const double vy = frame.viewport.originY;
        const double vw = frame.viewport.width;
        const double vh = frame.viewport.height;
        auto clamp_rect = [&](double x0, double y0, double x1, double y1) {
            x0 = std::clamp(x0, 0.0, static_cast<double>(width));
            y0 = std::clamp(y0, 0.0, static_cast<double>(height));
            x1 = std::clamp(x1, x0, static_cast<double>(width));
            y1 = std::clamp(y1, y0, static_cast<double>(height));
            rect = {static_cast<NSUInteger>(std::lround(x0)), static_cast<NSUInteger>(std::lround(y0)),
                    static_cast<NSUInteger>(std::lround(x1)) - static_cast<NSUInteger>(std::lround(x0)),
                    static_cast<NSUInteger>(std::lround(y1)) - static_cast<NSUInteger>(std::lround(y0))};
        };
        if (scissor_enabled) {
            const double s = frame.scale;
            clamp_rect(vx + scissor.Left() * s, vy + scissor.Top() * s, vx + scissor.Right() * s,
                       vy + scissor.Bottom() * s);
        } else {
            clamp_rect(vx, vy, vx + vw, vy + vh);
        }
        if (encoder)
            [encoder setScissorRect:rect];
    }

    void draw_geometry(const Geometry& geometry, Rml::Vector2f translation,
                       id<MTLRenderPipelineState> pipeline, id<MTLTexture> texture) {
        [encoder setRenderPipelineState:pipeline];
        Uniforms uniforms{};
        const Rml::Matrix4f combined = projection * transform;
        std::memcpy(uniforms.transform, combined.data(), sizeof(uniforms.transform));
        uniforms.translation[0] = translation.x;
        uniforms.translation[1] = translation.y;
        [encoder setVertexBuffer:geometry.vertices offset:0 atIndex:0];
        [encoder setVertexBytes:&uniforms length:sizeof(uniforms) atIndex:1];
        if (texture) {
            [encoder setFragmentTexture:texture atIndex:0];
            [encoder setFragmentSamplerState:sampler atIndex:0];
        }
        [encoder drawIndexedPrimitives:MTLPrimitiveTypeTriangle
                            indexCount:geometry.index_count
                             indexType:MTLIndexTypeUInt32
                           indexBuffer:geometry.indices
                     indexBufferOffset:0];
    }

    void draw_fullscreen(id<MTLRenderPipelineState> pipeline, id<MTLTexture> texture) {
        [encoder setRenderPipelineState:pipeline];
        if (texture)
            [encoder setFragmentTexture:texture atIndex:0];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    }
};

MetalRenderInterface::MetalRenderInterface(id<MTLDevice> device)
    : state_(std::make_unique<State>()) {
    auto& s = *state_;
    s.device = device;
    if (!device)
        return;
    NSError* error = nil;
    s.library = [device newLibraryWithSource:@(kShaderSource) options:nil error:&error];
    if (!s.library) {
        Rml::Log::Message(Rml::Log::LT_ERROR, "Metal shader library failed: %s",
                          error ? error.localizedDescription.UTF8String : "unknown");
        return;
    }
    MTLVertexDescriptor* layout = [MTLVertexDescriptor vertexDescriptor];
    layout.attributes[0].format = MTLVertexFormatFloat2;
    layout.attributes[0].offset = offsetof(Rml::Vertex, position);
    layout.attributes[0].bufferIndex = 0;
    layout.attributes[1].format = MTLVertexFormatUChar4Normalized;
    layout.attributes[1].offset = offsetof(Rml::Vertex, colour);
    layout.attributes[1].bufferIndex = 0;
    layout.attributes[2].format = MTLVertexFormatFloat2;
    layout.attributes[2].offset = offsetof(Rml::Vertex, tex_coord);
    layout.attributes[2].bufferIndex = 0;
    layout.layouts[0].stride = sizeof(Rml::Vertex);
    s.vertex_layout = layout;

    MTLSamplerDescriptor* sampler = [[MTLSamplerDescriptor alloc] init];
    sampler.minFilter = MTLSamplerMinMagFilterLinear;
    sampler.magFilter = MTLSamplerMinMagFilterLinear;
    sampler.sAddressMode = MTLSamplerAddressModeClampToEdge;
    sampler.tAddressMode = MTLSamplerAddressModeClampToEdge;
    s.sampler = [device newSamplerStateWithDescriptor:sampler];

    s.stencil_off = s.make_stencil_state(false, MTLCompareFunctionAlways, MTLStencilOperationKeep);
    s.stencil_test = s.make_stencil_state(true, MTLCompareFunctionEqual, MTLStencilOperationKeep);
    s.stencil_set = s.make_stencil_state(true, MTLCompareFunctionAlways, MTLStencilOperationReplace);
    s.stencil_incr =
        s.make_stencil_state(true, MTLCompareFunctionAlways, MTLStencilOperationIncrementClamp);
}

MetalRenderInterface::~MetalRenderInterface() = default;

bool MetalRenderInterface::valid() const noexcept {
    const auto& s = *state_;
    return s.device && s.library && s.sampler && s.stencil_off && s.stencil_test && s.stencil_set &&
           s.stencil_incr;
}

std::size_t MetalRenderInterface::unsupported_calls() const noexcept {
    return state_->unsupported;
}

void MetalRenderInterface::begin_frame(const MetalFrameTarget& frame) {
    auto& s = *state_;
    s.end_pass();
    s.frame_pipelines = nullptr;
    s.layer_count = 0;
    if (!valid() || !frame.command_buffer || !frame.target || frame.scale <= 0.0f)
        return;
    if (s.frame_format == MTLPixelFormatInvalid) {
        s.frame_format = frame.target.pixelFormat;
    } else if (frame.target.pixelFormat != s.frame_format) {
        Rml::Log::Message(Rml::Log::LT_ERROR,
                          "MetalRenderInterface: the target's pixel format changed; a renderer "
                          "draws to one format for its lifetime");
        return;
    }
    s.frame_pipelines = s.pipelines_for(frame.target.pixelFormat);
    if (!s.frame_pipelines || !s.ensure_targets(frame.target))
        return;
    s.frame = frame;
    s.projection = Rml::Matrix4f::ProjectOrtho(
        0.0f, static_cast<float>(frame.viewport.width / frame.scale),
        static_cast<float>(frame.viewport.height / frame.scale), 0.0f, -10000.0f, 10000.0f);
    s.transform = Rml::Matrix4f::Identity();
    s.scissor_enabled = false;
    s.clip_enabled = false;
    s.stencil_reference = 0;
    s.stencil_cleared = false;

    // The base layer starts transparent, or from the target's current contents.
    id<MTLTexture> base = s.layer_texture(0);
    if (!base)
        return;
    bool loaded = false;
    if (!frame.clear) {
        if (frame.target.framebufferOnly) {
            Rml::Log::Message(Rml::Log::LT_ERROR,
                              "MetalRenderInterface: clear=false needs a target that is not "
                              "framebuffer-only; the frame starts transparent");
        } else {
            id<MTLBlitCommandEncoder> blit = [frame.command_buffer blitCommandEncoder];
            [blit copyFromTexture:frame.target toTexture:base];
            [blit endEncoding];
            loaded = true;
        }
    }
    s.layer_count = 1;
    s.open_pass(0, !loaded);
}

void MetalRenderInterface::end_frame() {
    auto& s = *state_;
    if (s.layer_count == 0 || !s.frame_pipelines) {
        s.end_pass();
        return;
    }
    s.end_pass();
    // Copy the base layer to the target, replacing it.
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = s.frame.target;
    pass.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.stencilAttachment.texture = s.stencil;
    pass.stencilAttachment.loadAction = MTLLoadActionDontCare;
    pass.stencilAttachment.storeAction = MTLStoreActionDontCare;
    id<MTLRenderCommandEncoder> copy = [s.frame.command_buffer renderCommandEncoderWithDescriptor:pass];
    if (copy) {
        [copy setViewport:MTLViewport{0.0, 0.0, double(s.target_width), double(s.target_height), 0.0,
                                      1.0}];
        [copy setDepthStencilState:s.stencil_off];
        [copy setRenderPipelineState:s.frame_pipelines->replace];
        [copy setFragmentTexture:s.layer_pool[0] atIndex:0];
        [copy drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [copy endEncoding];
    }
    s.layer_count = 0;
    s.frame_pipelines = nullptr;
}

Rml::CompiledGeometryHandle MetalRenderInterface::CompileGeometry(
    Rml::Span<const Rml::Vertex> vertices, Rml::Span<const int> indices) {
    auto& s = *state_;
    if (!valid() || vertices.empty() || indices.empty())
        return 0;
    for (const int index : indices)
        if (index < 0 || static_cast<std::size_t>(index) >= vertices.size())
            return 0;
    Geometry geometry;
    geometry.vertices = [s.device newBufferWithBytes:vertices.data()
                                              length:vertices.size() * sizeof(Rml::Vertex)
                                             options:MTLResourceStorageModeShared];
    std::vector<std::uint32_t> converted(indices.begin(), indices.end());
    geometry.indices = [s.device newBufferWithBytes:converted.data()
                                             length:converted.size() * sizeof(std::uint32_t)
                                            options:MTLResourceStorageModeShared];
    geometry.index_count = converted.size();
    if (!geometry.vertices || !geometry.indices)
        return 0;
    const auto handle = s.next_handle++;
    s.geometries.emplace(handle, geometry);
    return static_cast<Rml::CompiledGeometryHandle>(handle);
}

void MetalRenderInterface::RenderGeometry(Rml::CompiledGeometryHandle handle,
                                          Rml::Vector2f translation,
                                          Rml::TextureHandle texture_handle) {
    auto& s = *state_;
    if (!s.frame_pipelines || !s.top_pass())
        return;
    const auto found = s.geometries.find(static_cast<std::uintptr_t>(handle));
    if (found == s.geometries.end())
        return;
    id<MTLTexture> texture = nil;
    if (texture_handle) {
        const auto t = s.textures.find(static_cast<std::uintptr_t>(texture_handle));
        if (t == s.textures.end())
            return;
        texture = t->second;
    }
    s.apply_clip_state();
    s.draw_geometry(found->second, translation,
                    texture ? s.frame_pipelines->texture : s.frame_pipelines->colour, texture);
}

void MetalRenderInterface::ReleaseGeometry(Rml::CompiledGeometryHandle handle) {
    state_->geometries.erase(static_cast<std::uintptr_t>(handle));
}

Rml::TextureHandle MetalRenderInterface::GenerateTexture(Rml::Span<const Rml::byte> source,
                                                         Rml::Vector2i dimensions) {
    auto& s = *state_;
    if (!valid() || dimensions.x <= 0 || dimensions.y <= 0 ||
        source.size() != static_cast<std::size_t>(dimensions.x) *
                             static_cast<std::size_t>(dimensions.y) * 4u)
        return 0;
    MTLTextureDescriptor* descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:static_cast<NSUInteger>(dimensions.x)
                                                          height:static_cast<NSUInteger>(dimensions.y)
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> texture = [s.device newTextureWithDescriptor:descriptor];
    if (!texture)
        return 0;
    // RmlUi generates premultiplied RGBA8, top row first: upload unchanged.
    [texture replaceRegion:MTLRegionMake2D(0, 0, static_cast<NSUInteger>(dimensions.x),
                                           static_cast<NSUInteger>(dimensions.y))
               mipmapLevel:0
                 withBytes:source.data()
               bytesPerRow:static_cast<NSUInteger>(dimensions.x) * 4u];
    const auto handle = s.next_handle++;
    s.textures.emplace(handle, texture);
    return static_cast<Rml::TextureHandle>(handle);
}

Rml::TextureHandle MetalRenderInterface::LoadTexture(Rml::Vector2i& dimensions,
                                                     const Rml::String& source) {
    Rml::FileInterface* files = Rml::GetFileInterface();
    if (!files)
        return 0;
    const Rml::FileHandle file = files->Open(source);
    if (!file)
        return 0;
    const std::size_t length = files->Length(file);
    std::vector<Rml::byte> encoded(length);
    const std::size_t read = length ? files->Read(encoded.data(), length, file) : 0;
    files->Close(file);
    if (read != length || length == 0)
        return 0;
    int width = 0;
    int height = 0;
    std::vector<std::uint8_t> pixels;
    if (!decode_premultiplied(encoded, width, height, pixels))
        return 0;
    dimensions = {width, height};
    return GenerateTexture(Rml::Span<const Rml::byte>(pixels.data(), pixels.size()), dimensions);
}

void MetalRenderInterface::ReleaseTexture(Rml::TextureHandle handle) {
    state_->textures.erase(static_cast<std::uintptr_t>(handle));
}

void MetalRenderInterface::EnableScissorRegion(bool enable) {
    state_->scissor_enabled = enable;
    state_->apply_scissor();
}

void MetalRenderInterface::SetScissorRegion(Rml::Rectanglei region) {
    state_->scissor = region;
    state_->apply_scissor();
}

void MetalRenderInterface::SetTransform(const Rml::Matrix4f* transform) {
    state_->transform = transform ? *transform : Rml::Matrix4f::Identity();
}

void MetalRenderInterface::EnableClipMask(bool enable) {
    state_->clip_enabled = enable;
}

void MetalRenderInterface::RenderToClipMask(Rml::ClipMaskOperation operation,
                                            Rml::CompiledGeometryHandle handle,
                                            Rml::Vector2f translation) {
    auto& s = *state_;
    if (!s.frame_pipelines || !s.top_pass())
        return;
    const auto found = s.geometries.find(static_cast<std::uintptr_t>(handle));
    if (found == s.geometries.end())
        return;
    using Rml::ClipMaskOperation;
    if (operation == ClipMaskOperation::Set || operation == ClipMaskOperation::SetInverse) {
        // Clear the stencil to 0 within the scissor, as a scissored clear does.
        [s.encoder setDepthStencilState:s.stencil_set];
        [s.encoder setStencilReferenceValue:0];
        s.draw_fullscreen(s.frame_pipelines->mask_clear, nil);
    }
    switch (operation) {
    case ClipMaskOperation::Set:
        // 1 inside the geometry; draw where the stencil is 1.
        [s.encoder setDepthStencilState:s.stencil_set];
        [s.encoder setStencilReferenceValue:1];
        s.stencil_reference = 1;
        break;
    case ClipMaskOperation::SetInverse:
        // 1 inside the geometry; draw where the stencil is still 0.
        [s.encoder setDepthStencilState:s.stencil_set];
        [s.encoder setStencilReferenceValue:1];
        s.stencil_reference = 0;
        break;
    case ClipMaskOperation::Intersect:
        // Raise the value inside the geometry; only pixels that were at the
        // reference reach the next one.
        [s.encoder setDepthStencilState:s.stencil_incr];
        s.stencil_reference += 1;
        break;
    }
    s.draw_geometry(found->second, translation, s.frame_pipelines->mask, nil);
    s.apply_clip_state();
}

Rml::LayerHandle MetalRenderInterface::PushLayer() {
    auto& s = *state_;
    if (!s.frame_pipelines || s.layer_count == 0)
        return 0;
    const int layer = s.layer_count;
    if (!s.layer_texture(layer))
        return 0;
    s.layer_count += 1;
    s.open_pass(layer, true);
    return static_cast<Rml::LayerHandle>(layer);
}

void MetalRenderInterface::CompositeLayers(Rml::LayerHandle source, Rml::LayerHandle destination,
                                           Rml::BlendMode blend_mode,
                                           Rml::Span<const Rml::CompiledFilterHandle> filters) {
    auto& s = *state_;
    const auto source_index = static_cast<int>(source);
    const auto destination_index = static_cast<int>(destination);
    if (!s.frame_pipelines || source_index < 0 || source_index >= s.layer_count ||
        destination_index < 0 || destination_index >= s.layer_count ||
        source_index == destination_index)
        return;
    if (!filters.empty())
        ++s.unsupported; // filters are not drawn yet; the layer composites unfiltered
    if (!s.open_pass(destination_index, false))
        return;
    s.apply_clip_state();
    s.draw_fullscreen(blend_mode == Rml::BlendMode::Replace ? s.frame_pipelines->replace
                                                            : s.frame_pipelines->blend,
                      s.layer_pool[static_cast<std::size_t>(source_index)]);
    if (destination_index != s.layer_count - 1)
        s.top_pass();
}

void MetalRenderInterface::PopLayer() {
    auto& s = *state_;
    if (s.layer_count <= 1)
        return;
    s.layer_count -= 1;
    s.top_pass();
}

Rml::TextureHandle MetalRenderInterface::SaveLayerAsTexture() {
    auto& s = *state_;
    if (!s.frame_pipelines || s.layer_count == 0 || !s.scissor_enabled)
        return 0;
    // The scissor region of the top layer, in target pixels.
    const double scale = s.frame.scale;
    const auto x0 = static_cast<NSInteger>(std::lround(s.frame.viewport.originX + s.scissor.Left() * scale));
    const auto y0 = static_cast<NSInteger>(std::lround(s.frame.viewport.originY + s.scissor.Top() * scale));
    const auto x1 = static_cast<NSInteger>(std::lround(s.frame.viewport.originX + s.scissor.Right() * scale));
    const auto y1 = static_cast<NSInteger>(std::lround(s.frame.viewport.originY + s.scissor.Bottom() * scale));
    const NSInteger cx0 = std::clamp<NSInteger>(x0, 0, static_cast<NSInteger>(s.target_width));
    const NSInteger cy0 = std::clamp<NSInteger>(y0, 0, static_cast<NSInteger>(s.target_height));
    const NSInteger cx1 = std::clamp<NSInteger>(x1, cx0, static_cast<NSInteger>(s.target_width));
    const NSInteger cy1 = std::clamp<NSInteger>(y1, cy0, static_cast<NSInteger>(s.target_height));
    if (cx1 <= cx0 || cy1 <= cy0)
        return 0;
    MTLTextureDescriptor* descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:s.frame_format
                                                           width:static_cast<NSUInteger>(cx1 - cx0)
                                                          height:static_cast<NSUInteger>(cy1 - cy0)
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> texture = [s.device newTextureWithDescriptor:descriptor];
    if (!texture)
        return 0;
    const int top = s.layer_count - 1;
    s.end_pass();
    id<MTLBlitCommandEncoder> blit = [s.frame.command_buffer blitCommandEncoder];
    [blit copyFromTexture:s.layer_pool[static_cast<std::size_t>(top)]
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(static_cast<NSUInteger>(cx0), static_cast<NSUInteger>(cy0), 0)
               sourceSize:MTLSizeMake(static_cast<NSUInteger>(cx1 - cx0),
                                      static_cast<NSUInteger>(cy1 - cy0), 1)
                toTexture:texture
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:MTLOriginMake(0, 0, 0)];
    [blit endEncoding];
    s.top_pass();
    const auto handle = s.next_handle++;
    s.textures.emplace(handle, texture);
    return static_cast<Rml::TextureHandle>(handle);
}

Rml::CompiledFilterHandle MetalRenderInterface::SaveLayerAsMaskImage() {
    ++state_->unsupported;
    return 0;
}

Rml::CompiledFilterHandle MetalRenderInterface::CompileFilter(const Rml::String&,
                                                              const Rml::Dictionary&) {
    ++state_->unsupported;
    return 0;
}

Rml::CompiledShaderHandle MetalRenderInterface::CompileShader(const Rml::String&,
                                                              const Rml::Dictionary&) {
    ++state_->unsupported;
    return 0;
}

} // namespace vellum::rmlui
