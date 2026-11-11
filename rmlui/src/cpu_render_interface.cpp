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

// A pixel rectangle [x0, x1) x [y0, y1); empty when x0 >= x1 or y0 >= y1.
struct Rect {
    std::int64_t x0 = 0;
    std::int64_t y0 = 0;
    std::int64_t x1 = 0;
    std::int64_t y1 = 0;
    [[nodiscard]] bool empty() const {
        return x0 >= x1 || y0 >= y1;
    }
    void add(const Rect& other) {
        if (other.empty())
            return;
        if (empty()) {
            *this = other;
            return;
        }
        x0 = std::min(x0, other.x0);
        y0 = std::min(y0, other.y0);
        x1 = std::max(x1, other.x1);
        y1 = std::max(y1, other.y1);
    }
    [[nodiscard]] Rect intersect(const Rect& other) const {
        Rect out{std::max(x0, other.x0), std::max(y0, other.y0), std::min(x1, other.x1), std::min(y1, other.y1)};
        return out.empty() ? Rect{} : out;
    }
    [[nodiscard]] bool contains(const Rect& other) const {
        return other.empty() || (x0 <= other.x0 && y0 <= other.y0 && x1 >= other.x1 && y1 >= other.y1);
    }
};

Rect rect(const raster::Clip& clip) {
    const Rect out{clip.x0, clip.y0, clip.x1, clip.y1};
    return out.empty() ? Rect{} : out;
}

// The pixels a triangle may write inside `clip`.
Rect touched(const raster::Fixed& a, const raster::Fixed& b, const raster::Fixed& c, const raster::Clip& clip) {
    const raster::Clip box = raster::bounds(a, b, c, clip);
    return rect({box.x0, box.y0, box.x1 + 1, box.y1 + 1});
}

// Zeroes `area` (clamped to the buffer) in a tightly packed buffer of
// `bytes_per_pixel` bytes a pixel.
void zero(std::uint8_t* data, std::size_t width, std::size_t bytes_per_pixel, const Rect& area) {
    if (area.empty())
        return;
    for (auto y = area.y0; y < area.y1; ++y)
        std::memset(data + (static_cast<std::size_t>(y) * width + static_cast<std::size_t>(area.x0)) * bytes_per_pixel,
                    0, static_cast<std::size_t>(area.x1 - area.x0) * bytes_per_pixel);
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

    // Frame state. `base` is the caller's buffer (layer 0); `target` is the
    // layer being drawn into, the top of the stack.
    raster::Target base{};
    raster::Target target{};
    // Layers above the base, tightly packed, kept for reuse across frames.
    // Each pooled buffer is zero outside its `layer_dirty` rectangle, so
    // pushing a layer clears only what was drawn there before, and blending a
    // layer down visits only what was drawn into it.
    std::vector<std::vector<std::uint8_t>> layer_pool;
    std::vector<Rect> layer_dirty;
    int layer_count = 0; // including the base
    // The clip mask: one byte per target pixel, standing in for a stencil.
    std::vector<std::uint8_t> mask;
    Rect mask_dirty; // the mask is zero outside it
    bool clip_enabled = false;
    std::uint8_t reference = 0;
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

    // Records that `area` of layer `index` may have been written.
    void dirty(int index, const Rect& area) {
        if (index > 0)
            layer_dirty[static_cast<std::size_t>(index - 1)].add(area);
    }

    raster::Target layer_target(int index) {
        if (index == 0)
            return base;
        auto& buffer = layer_pool[static_cast<std::size_t>(index - 1)];
        return {buffer.data(), base.width, base.height, static_cast<std::size_t>(base.width) * 4u};
    }

    const raster::MaskTest* mask_test(raster::MaskTest& storage) const {
        if (!clip_enabled)
            return nullptr;
        storage = {mask.data(), base.width, reference};
        return &storage;
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
    const bool dimensions_changed = s.base.width != frame.width || s.base.height != frame.height;
    s.base = {frame.rgba, frame.width, frame.height, stride};
    s.target = s.base;
    s.layer_count = 1;
    if (dimensions_changed ||
        (s.layer_pool.size() &&
         s.layer_pool.front().size() != static_cast<std::size_t>(frame.width) * frame.height * 4u)) {
        s.layer_pool.clear();
        s.layer_dirty.clear();
    }
    const std::size_t mask_size = static_cast<std::size_t>(frame.width) * frame.height;
    if (dimensions_changed || s.mask.size() != mask_size)
        s.mask.assign(mask_size, 0);
    else
        zero(s.mask.data(), frame.width, 1, s.mask_dirty);
    s.mask_dirty = {};
    s.clip_enabled = false;
    s.reference = 0;
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
        raster::MaskTest storage;
        if (visible && raster::draw_triangle(s.target, clip, v[0], v[1], v[2], sampled, s.mask_test(storage)))
            s.dirty(s.layer_count - 1, touched(v[0].position, v[1].position, v[2].position, clip));
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
    state_->clip_enabled = enable;
}

void CpuRenderInterface::RenderToClipMask(Rml::ClipMaskOperation operation, Rml::CompiledGeometryHandle handle,
                                          Rml::Vector2f translation) {
    auto& s = *state_;
    if (!s.in_frame)
        return;
    const auto found = s.geometries.find(static_cast<std::uintptr_t>(handle));
    if (found == s.geometries.end())
        return;
    const raster::Clip clip = s.clip();
    const std::size_t width = s.base.width;
    using Rml::ClipMaskOperation;
    if (operation == ClipMaskOperation::Set || operation == ClipMaskOperation::SetInverse) {
        // A fresh mask inside the scissor: 0 everywhere, then 1 under the
        // geometry. Outside `mask_dirty` it is 0 already.
        const Rect scissor = rect(clip);
        zero(s.mask.data(), width, 1, scissor.intersect(s.mask_dirty));
        if (scissor.contains(s.mask_dirty))
            s.mask_dirty = {};
        s.reference = operation == ClipMaskOperation::Set ? 1 : 0;
    } else {
        // Intersect: raise the value under the geometry; only pixels that were
        // at the reference reach the next one.
        s.reference = static_cast<std::uint8_t>(s.reference + 1);
    }
    const bool increment = operation == ClipMaskOperation::Intersect;
    const Geometry& geometry = found->second;
    const float* transform = s.transform_enabled ? s.transform : nullptr;
    for (std::size_t i = 0; i + 2 < geometry.indices.size(); i += 3) {
        raster::Fixed p[3];
        bool visible = true;
        for (int k = 0; k < 3; ++k) {
            const auto& position = geometry.positions[static_cast<std::size_t>(geometry.indices[i + static_cast<std::size_t>(k)])];
            visible = visible && raster::project(static_cast<double>(position.x) + static_cast<double>(translation.x),
                                                 static_cast<double>(position.y) + static_cast<double>(translation.y),
                                                 transform, s.scale, p[k]);
        }
        if (!visible)
            continue;
        int ordered[3];
        const bool covered = raster::cover_spans(
            p[0], p[1], p[2], clip, ordered,
            [&](std::int64_t y, std::int64_t x_begin, std::int64_t x_end, std::int64_t, std::int64_t, std::int64_t,
                std::int64_t, std::int64_t, std::int64_t, std::int64_t) {
                auto* row = s.mask.data() + static_cast<std::size_t>(y) * width;
                if (!increment) {
                    std::memset(row + x_begin, 1, static_cast<std::size_t>(x_end - x_begin));
                    return;
                }
                for (auto x = x_begin; x < x_end; ++x)
                    row[x] = static_cast<std::uint8_t>(std::min(255, row[x] + 1));
            });
        if (covered)
            s.mask_dirty.add(touched(p[0], p[1], p[2], clip));
    }
}

Rml::LayerHandle CpuRenderInterface::PushLayer() {
    auto& s = *state_;
    if (!s.in_frame)
        return 0;
    const auto index = s.layer_count;
    const std::size_t bytes = static_cast<std::size_t>(s.base.width) * s.base.height * 4u;
    if (s.layer_pool.size() < static_cast<std::size_t>(index)) {
        s.layer_pool.emplace_back();
        s.layer_dirty.emplace_back();
    }
    auto& buffer = s.layer_pool[static_cast<std::size_t>(index - 1)];
    auto& dirty = s.layer_dirty[static_cast<std::size_t>(index - 1)];
    if (buffer.size() != bytes)
        buffer.assign(bytes, 0);
    else
        zero(buffer.data(), s.base.width, 4, dirty);
    dirty = {};
    s.layer_count += 1;
    s.target = s.layer_target(index);
    return static_cast<Rml::LayerHandle>(index);
}

void CpuRenderInterface::CompositeLayers(Rml::LayerHandle source, Rml::LayerHandle destination,
                                         Rml::BlendMode blend_mode, Rml::Span<const Rml::CompiledFilterHandle> filters) {
    auto& s = *state_;
    const auto from = static_cast<int>(source);
    const auto to = static_cast<int>(destination);
    if (!s.in_frame || from < 0 || from >= s.layer_count || to < 0 || to >= s.layer_count || from == to)
        return;
    // Filters are not drawn; the layer composites unfiltered.
    for (std::size_t i = 0; i < filters.size(); ++i)
        s.count("filter");
    const raster::Target src = s.layer_target(from);
    const raster::Target dst = s.layer_target(to);
    const raster::Clip clip = s.clip();
    raster::MaskTest storage;
    const raster::MaskTest* mask = s.mask_test(storage);
    const bool replace = blend_mode == Rml::BlendMode::Replace;
    // Blending a transparent pixel (all four channels 0) changes nothing, and
    // a layer is all 0 outside its dirty rectangle, so a blend visits only
    // that. Replace writes every pixel in the clip.
    Rect area = rect(clip);
    if (!replace && from > 0)
        area = area.intersect(s.layer_dirty[static_cast<std::size_t>(from - 1)]);
    for (auto y = area.y0; y < area.y1; ++y) {
        const auto* in_row = src.rgba + static_cast<std::size_t>(y) * src.stride;
        auto* out_row = dst.rgba + static_cast<std::size_t>(y) * dst.stride;
        const std::uint8_t* mask_row = mask ? mask->mask + static_cast<std::size_t>(y) * mask->width : nullptr;
        for (auto x = area.x0; x < area.x1; ++x) {
            if (mask_row && mask_row[x] != mask->reference)
                continue;
            const auto* in = in_row + static_cast<std::size_t>(x) * 4u;
            auto* out = out_row + static_cast<std::size_t>(x) * 4u;
            if (replace) {
                std::memcpy(out, in, 4);
                continue;
            }
            const std::uint32_t pixel[4] = {in[0], in[1], in[2], in[3]};
            if ((pixel[0] | pixel[1] | pixel[2] | pixel[3]) == 0u)
                continue;
            raster::blend_over(out, pixel);
        }
    }
    s.dirty(to, area);
}

void CpuRenderInterface::PopLayer() {
    auto& s = *state_;
    if (s.layer_count <= 1)
        return;
    s.layer_count -= 1;
    s.target = s.layer_target(s.layer_count - 1);
}

Rml::TextureHandle CpuRenderInterface::SaveLayerAsTexture() {
    auto& s = *state_;
    if (!s.in_frame || !s.scissor_enabled)
        return 0;
    const raster::Clip clip = s.clip();
    if (clip.x1 <= clip.x0 || clip.y1 <= clip.y0)
        return 0;
    const auto width = static_cast<std::uint32_t>(clip.x1 - clip.x0);
    const auto height = static_cast<std::uint32_t>(clip.y1 - clip.y0);
    std::vector<std::uint8_t> rgba(static_cast<std::size_t>(width) * height * 4u);
    for (std::uint32_t y = 0; y < height; ++y)
        std::memcpy(rgba.data() + static_cast<std::size_t>(y) * width * 4u,
                    s.target.rgba + static_cast<std::size_t>(clip.y0 + y) * s.target.stride +
                        static_cast<std::size_t>(clip.x0) * 4u,
                    static_cast<std::size_t>(width) * 4u);
    // The copy is the client's own drawing, not a source image: no limit applies.
    const auto handle = s.next_handle++;
    s.textures.emplace(handle, Texture{width, height, std::move(rgba)});
    return static_cast<Rml::TextureHandle>(handle);
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
