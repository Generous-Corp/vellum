// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT) and Vellum's own SoftRenderer, whose rasteriser it shares. It
// contains no code from any GPL-licensed project.

#include <vellum/rmlui/cpu_render_interface.hpp>

#include "soft_raster.hpp"

#include <RmlUi/Core/Core.h>
#include <RmlUi/Core/FileInterface.h>
#include <RmlUi/Core/Log.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <set>
#include <type_traits>
#include <unordered_map>
#include <utility>

namespace vellum::rmlui {

namespace {

// Colours and texture coordinates are kept ready for the rasteriser; the
// positions stay in context units, since each draw projects them through its
// own translation, transform and scale.
struct Geometry {
    std::vector<raster::Vertex> vertices;
    std::vector<Rml::Vector2f> positions;
    std::vector<int> indices;
};

struct Texture {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::uint8_t> rgba;
};

std::uint32_t pack(Rml::ColourbPremultiplied colour) {
    return (std::uint32_t{colour.red} << 24) | (std::uint32_t{colour.green} << 16) |
           (std::uint32_t{colour.blue} << 8) | std::uint32_t{colour.alpha};
}

// Column-major floats, whichever layout RmlUi was built with.
void column_major(const Rml::Matrix4f& matrix, float out[16]) {
    if constexpr (std::is_same_v<Rml::Matrix4f, Rml::RowMajorMatrix4f>)
        std::memcpy(out, matrix.Transpose().data(), sizeof(float) * 16);
    else
        std::memcpy(out, matrix.data(), sizeof(float) * 16);
}

} // namespace

struct CpuRenderInterface::State {
    std::unordered_map<std::uintptr_t, Geometry> geometries;
    std::unordered_map<std::uintptr_t, Texture> textures;
    std::uintptr_t next_handle = 1;

    ImageDecoder decoder;
    std::uint32_t texture_limit = 0;
    TextureClampRule clamp_rule;

    std::map<std::string, std::size_t> unsupported;
    std::size_t unsupported_total = 0;
    std::set<std::string> logged;

    // Frame state.
    raster::Target target{};
    double scale = 1.0;
    bool in_frame = false;
    bool scissor_enabled = false;
    Rml::Rectanglei scissor = Rml::Rectanglei::FromSize({0, 0});
    bool transform_enabled = false;
    float transform[16] = {};

    void count(const char* kind) {
        ++unsupported[kind];
        ++unsupported_total;
        if (logged.insert(kind).second && Rml::GetSystemInterface())
            Rml::Log::Message(Rml::Log::LT_WARNING,
                              "CpuRenderInterface: %s is not drawn; counted in unsupported_calls()", kind);
    }

    raster::Clip clip() const {
        raster::Clip c{0, 0, target.width, target.height};
        if (!scissor_enabled)
            return c;
        const auto edge = [&](int value) { return static_cast<std::int64_t>(std::lround(value * scale)); };
        c.x0 = std::max<std::int64_t>(c.x0, edge(scissor.Left()));
        c.y0 = std::max<std::int64_t>(c.y0, edge(scissor.Top()));
        c.x1 = std::min<std::int64_t>(c.x1, edge(scissor.Right()));
        c.y1 = std::min<std::int64_t>(c.y1, edge(scissor.Bottom()));
        return c;
    }

    Rml::TextureHandle store(std::uint32_t width, std::uint32_t height, std::vector<std::uint8_t> rgba) {
        if (texture_limit != 0 && (width > texture_limit || height > texture_limit)) {
            if (!clamp_rule || !clamp_rule(width, height, rgba))
                return 0;
            if (width == 0 || height == 0 || rgba.size() != static_cast<std::size_t>(width) * height * 4u)
                return 0;
        }
        const auto handle = next_handle++;
        textures.emplace(handle, Texture{width, height, std::move(rgba)});
        return static_cast<Rml::TextureHandle>(handle);
    }
};

CpuRenderInterface::CpuRenderInterface() : state_(std::make_unique<State>()) {}
CpuRenderInterface::~CpuRenderInterface() = default;

bool CpuRenderInterface::begin_frame(const CpuFrameTarget& frame) {
    auto& s = *state_;
    s.in_frame = false;
    const std::size_t stride = frame.stride ? frame.stride : static_cast<std::size_t>(frame.width) * 4u;
    if (!frame.rgba || frame.width == 0 || frame.height == 0 ||
        stride < static_cast<std::size_t>(frame.width) * 4u || !(frame.scale > 0.0f) ||
        !std::isfinite(frame.scale))
        return false;
    s.target = {frame.rgba, frame.width, frame.height, stride};
    s.scale = static_cast<double>(frame.scale);
    s.scissor_enabled = false;
    s.transform_enabled = false;
    if (frame.clear)
        for (std::uint32_t y = 0; y < frame.height; ++y)
            std::memset(frame.rgba + static_cast<std::size_t>(y) * stride, 0, static_cast<std::size_t>(frame.width) * 4u);
    s.in_frame = true;
    return true;
}

void CpuRenderInterface::end_frame() {
    state_->in_frame = false;
}

void CpuRenderInterface::set_image_decoder(ImageDecoder decoder) {
    state_->decoder = std::move(decoder);
}

void CpuRenderInterface::set_texture_limit(std::uint32_t max_side, TextureClampRule rule) {
    state_->texture_limit = max_side;
    state_->clamp_rule = std::move(rule);
}

std::uint32_t CpuRenderInterface::texture_limit() const noexcept {
    return state_->texture_limit;
}

std::size_t CpuRenderInterface::unsupported_calls() const noexcept {
    return state_->unsupported_total;
}

std::size_t CpuRenderInterface::unsupported(const std::string& kind) const {
    const auto it = state_->unsupported.find(kind);
    return it == state_->unsupported.end() ? 0 : it->second;
}

Rml::CompiledGeometryHandle CpuRenderInterface::CompileGeometry(Rml::Span<const Rml::Vertex> vertices,
                                                                Rml::Span<const int> indices) {
    auto& s = *state_;
    if (vertices.empty() || indices.empty())
        return 0;
    Geometry geometry;
    geometry.vertices.reserve(vertices.size());
    geometry.positions.reserve(vertices.size());
    for (const auto& vertex : vertices) {
        raster::Vertex v;
        v.colour = pack(vertex.colour);
        v.u = vertex.tex_coord.x;
        v.v = vertex.tex_coord.y;
        geometry.vertices.push_back(v);
        geometry.positions.push_back(vertex.position);
    }
    for (const int index : indices)
        if (index < 0 || static_cast<std::size_t>(index) >= vertices.size())
            return 0;
    geometry.indices.assign(indices.begin(), indices.end());
    const auto handle = s.next_handle++;
    s.geometries.emplace(handle, std::move(geometry));
    return static_cast<Rml::CompiledGeometryHandle>(handle);
}

void CpuRenderInterface::RenderGeometry(Rml::CompiledGeometryHandle handle, Rml::Vector2f translation,
                                        Rml::TextureHandle texture_handle) {
    auto& s = *state_;
    if (!s.in_frame)
        return;
    const auto found = s.geometries.find(static_cast<std::uintptr_t>(handle));
    if (found == s.geometries.end())
        return;
    raster::Texture texture;
    const raster::Texture* sampled = nullptr;
    if (texture_handle) {
        const auto t = s.textures.find(static_cast<std::uintptr_t>(texture_handle));
        if (t == s.textures.end())
            return;
        texture = {t->second.width, t->second.height, t->second.rgba.data()};
        sampled = &texture;
    }
    const Geometry& geometry = found->second;
    const raster::Clip clip = s.clip();
    const float* transform = s.transform_enabled ? s.transform : nullptr;
    raster::Vertex v[3];
    for (std::size_t i = 0; i + 2 < geometry.indices.size(); i += 3) {
        bool visible = true;
        for (int k = 0; k < 3; ++k) {
            const auto index = static_cast<std::size_t>(geometry.indices[i + static_cast<std::size_t>(k)]);
            v[k] = geometry.vertices[index];
            const auto& p = geometry.positions[index];
            visible = visible &&
                      raster::project(static_cast<double>(p.x) + static_cast<double>(translation.x),
                                      static_cast<double>(p.y) + static_cast<double>(translation.y), transform,
                                      s.scale, v[k].position);
        }
        if (visible)
            raster::draw_triangle(s.target, clip, v[0], v[1], v[2], sampled);
    }
}

void CpuRenderInterface::ReleaseGeometry(Rml::CompiledGeometryHandle handle) {
    state_->geometries.erase(static_cast<std::uintptr_t>(handle));
}

Rml::TextureHandle CpuRenderInterface::LoadTexture(Rml::Vector2i& dimensions, const Rml::String& source) {
    auto& s = *state_;
    if (!s.decoder)
        return 0;
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
    if (length == 0 || read != length)
        return 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::uint8_t> rgba;
    if (!s.decoder(encoded, width, height, rgba) || width == 0 || height == 0 ||
        rgba.size() != static_cast<std::size_t>(width) * height * 4u)
        return 0;
    const auto handle = s.store(width, height, std::move(rgba));
    if (handle) {
        const auto& stored = s.textures.at(static_cast<std::uintptr_t>(handle));
        dimensions = {static_cast<int>(stored.width), static_cast<int>(stored.height)};
    }
    return handle;
}

Rml::TextureHandle CpuRenderInterface::GenerateTexture(Rml::Span<const Rml::byte> source,
                                                       Rml::Vector2i dimensions) {
    if (dimensions.x <= 0 || dimensions.y <= 0 ||
        source.size() != static_cast<std::size_t>(dimensions.x) * static_cast<std::size_t>(dimensions.y) * 4u)
        return 0;
    // RmlUi generates premultiplied RGBA8, top row first: keep it as given.
    return state_->store(static_cast<std::uint32_t>(dimensions.x), static_cast<std::uint32_t>(dimensions.y),
                         std::vector<std::uint8_t>(source.begin(), source.end()));
}

void CpuRenderInterface::ReleaseTexture(Rml::TextureHandle handle) {
    state_->textures.erase(static_cast<std::uintptr_t>(handle));
}

void CpuRenderInterface::EnableScissorRegion(bool enable) {
    state_->scissor_enabled = enable;
}

void CpuRenderInterface::SetScissorRegion(Rml::Rectanglei region) {
    state_->scissor = region;
}

void CpuRenderInterface::SetTransform(const Rml::Matrix4f* transform) {
    auto& s = *state_;
    s.transform_enabled = transform != nullptr;
    if (transform)
        column_major(*transform, s.transform);
}

void CpuRenderInterface::EnableClipMask(bool enable) {
    if (enable)
        state_->count("clip_mask");
}

void CpuRenderInterface::RenderToClipMask(Rml::ClipMaskOperation, Rml::CompiledGeometryHandle, Rml::Vector2f) {
    state_->count("clip_mask");
}

Rml::LayerHandle CpuRenderInterface::PushLayer() {
    // Content keeps drawing into the base target.
    state_->count("layer");
    return static_cast<Rml::LayerHandle>(1);
}

void CpuRenderInterface::CompositeLayers(Rml::LayerHandle, Rml::LayerHandle, Rml::BlendMode,
                                         Rml::Span<const Rml::CompiledFilterHandle> filters) {
    state_->count("layer");
    for (std::size_t i = 0; i < filters.size(); ++i)
        state_->count("filter");
}

void CpuRenderInterface::PopLayer() {}

Rml::TextureHandle CpuRenderInterface::SaveLayerAsTexture() {
    state_->count("layer");
    return 0;
}

Rml::CompiledFilterHandle CpuRenderInterface::SaveLayerAsMaskImage() {
    state_->count("mask_image");
    return 0;
}

Rml::CompiledFilterHandle CpuRenderInterface::CompileFilter(const Rml::String&, const Rml::Dictionary&) {
    state_->count("filter");
    return 0;
}

void CpuRenderInterface::ReleaseFilter(Rml::CompiledFilterHandle) {}

Rml::CompiledShaderHandle CpuRenderInterface::CompileShader(const Rml::String&, const Rml::Dictionary&) {
    state_->count("shader");
    return 0;
}

void CpuRenderInterface::RenderShader(Rml::CompiledShaderHandle, Rml::CompiledGeometryHandle, Rml::Vector2f,
                                      Rml::TextureHandle) {
    state_->count("shader");
}

void CpuRenderInterface::ReleaseShader(Rml::CompiledShaderHandle) {}

} // namespace vellum::rmlui
