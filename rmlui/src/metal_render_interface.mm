// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT), Apple's Metal documentation, and RmlUi's MIT GL3 backend as a
// behavioural reference (premultiplied blending, the orthographic projection,
// scissor semantics). It contains no code from any GPL-licensed project.

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
)msl";

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
    id<MTLRenderPipelineState> colour = nil;
    id<MTLRenderPipelineState> texture = nil;
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

    // Frame state.
    id<MTLRenderCommandEncoder> encoder = nil;
    const Pipelines* frame_pipelines = nullptr;
    MetalFrameTarget frame{};
    Rml::Matrix4f projection = Rml::Matrix4f::Identity();
    Rml::Matrix4f transform = Rml::Matrix4f::Identity();
    bool scissor_enabled = false;
    Rml::Rectanglei scissor = Rml::Rectanglei::FromSize({0, 0});

    std::size_t unsupported = 0;

    const Pipelines* pipelines_for(MTLPixelFormat format) {
        const auto key = static_cast<NSUInteger>(format);
        if (auto found = pipelines.find(key); found != pipelines.end())
            return &found->second;
        Pipelines built;
        for (int textured = 0; textured < 2; ++textured) {
            MTLRenderPipelineDescriptor* descriptor = [[MTLRenderPipelineDescriptor alloc] init];
            descriptor.vertexFunction = [library newFunctionWithName:@"vellum_rmlui_vertex"];
            descriptor.fragmentFunction = [library
                newFunctionWithName:textured ? @"vellum_rmlui_texture" : @"vellum_rmlui_colour"];
            descriptor.vertexDescriptor = vertex_layout;
            MTLRenderPipelineColorAttachmentDescriptor* colour = descriptor.colorAttachments[0];
            colour.pixelFormat = format;
            colour.blendingEnabled = YES;
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
            NSError* error = nil;
            id<MTLRenderPipelineState> state =
                [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
            if (!state) {
                Rml::Log::Message(Rml::Log::LT_ERROR, "Metal pipeline failed: %s",
                                  error ? error.localizedDescription.UTF8String : "unknown");
                return nullptr;
            }
            (textured ? built.texture : built.colour) = state;
        }
        return &pipelines.emplace(key, built).first->second;
    }

    void apply_scissor() {
        const auto width = static_cast<NSUInteger>(frame.target.width);
        const auto height = static_cast<NSUInteger>(frame.target.height);
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
        [encoder setScissorRect:rect];
    }
};

MetalRenderInterface::MetalRenderInterface(id<MTLDevice> device)
    : state_(std::make_unique<State>()) {
    state_->device = device;
    if (!device)
        return;
    NSError* error = nil;
    state_->library = [device newLibraryWithSource:@(kShaderSource) options:nil error:&error];
    if (!state_->library) {
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
    state_->vertex_layout = layout;

    MTLSamplerDescriptor* sampler = [[MTLSamplerDescriptor alloc] init];
    sampler.minFilter = MTLSamplerMinMagFilterLinear;
    sampler.magFilter = MTLSamplerMinMagFilterLinear;
    sampler.sAddressMode = MTLSamplerAddressModeClampToEdge;
    sampler.tAddressMode = MTLSamplerAddressModeClampToEdge;
    state_->sampler = [device newSamplerStateWithDescriptor:sampler];
}

MetalRenderInterface::~MetalRenderInterface() = default;

bool MetalRenderInterface::valid() const noexcept {
    return state_->device && state_->library && state_->sampler;
}

std::size_t MetalRenderInterface::unsupported_calls() const noexcept {
    return state_->unsupported;
}

void MetalRenderInterface::begin_frame(const MetalFrameTarget& frame) {
    auto& s = *state_;
    s.encoder = nil;
    s.frame_pipelines = nullptr;
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
    if (!s.frame_pipelines)
        return;
    s.frame = frame;
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = frame.target;
    pass.colorAttachments[0].loadAction = frame.clear ? MTLLoadActionClear : MTLLoadActionLoad;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    s.encoder = [frame.command_buffer renderCommandEncoderWithDescriptor:pass];
    if (!s.encoder)
        return;
    [s.encoder setViewport:frame.viewport];
    // RmlUi coordinates: origin top-left, y down, in context units.
    s.projection = Rml::Matrix4f::ProjectOrtho(
        0.0f, static_cast<float>(frame.viewport.width / frame.scale),
        static_cast<float>(frame.viewport.height / frame.scale), 0.0f, -10000.0f, 10000.0f);
    s.transform = Rml::Matrix4f::Identity();
    s.scissor_enabled = false;
    s.apply_scissor();
}

void MetalRenderInterface::end_frame() {
    auto& s = *state_;
    if (s.encoder)
        [s.encoder endEncoding];
    s.encoder = nil;
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
    if (!s.encoder)
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
    [s.encoder setRenderPipelineState:texture ? s.frame_pipelines->texture
                                              : s.frame_pipelines->colour];
    Uniforms uniforms{};
    const Rml::Matrix4f combined = s.projection * s.transform;
    std::memcpy(uniforms.transform, combined.data(), sizeof(uniforms.transform));
    uniforms.translation[0] = translation.x;
    uniforms.translation[1] = translation.y;
    [s.encoder setVertexBuffer:found->second.vertices offset:0 atIndex:0];
    [s.encoder setVertexBytes:&uniforms length:sizeof(uniforms) atIndex:1];
    if (texture) {
        [s.encoder setFragmentTexture:texture atIndex:0];
        [s.encoder setFragmentSamplerState:s.sampler atIndex:0];
    }
    [s.encoder drawIndexedPrimitives:MTLPrimitiveTypeTriangle
                          indexCount:found->second.index_count
                           indexType:MTLIndexTypeUInt32
                         indexBuffer:found->second.indices
                   indexBufferOffset:0];
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
    if (state_->encoder)
        state_->apply_scissor();
}

void MetalRenderInterface::SetScissorRegion(Rml::Rectanglei region) {
    state_->scissor = region;
    if (state_->encoder)
        state_->apply_scissor();
}

void MetalRenderInterface::SetTransform(const Rml::Matrix4f* transform) {
    state_->transform = transform ? *transform : Rml::Matrix4f::Identity();
}

void MetalRenderInterface::EnableClipMask(bool enable) {
    state_->unsupported += enable ? 1 : 0;
}

void MetalRenderInterface::RenderToClipMask(Rml::ClipMaskOperation, Rml::CompiledGeometryHandle,
                                            Rml::Vector2f) {
    ++state_->unsupported;
}

Rml::LayerHandle MetalRenderInterface::PushLayer() {
    ++state_->unsupported;
    return 0;
}

void MetalRenderInterface::CompositeLayers(Rml::LayerHandle, Rml::LayerHandle, Rml::BlendMode,
                                           Rml::Span<const Rml::CompiledFilterHandle>) {
    ++state_->unsupported;
}

void MetalRenderInterface::PopLayer() {
    ++state_->unsupported;
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
