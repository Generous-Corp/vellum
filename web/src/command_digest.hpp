#pragma once

#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/vector_asset.hpp>

#include <bit>
#include <cstdint>
#include <string>
#include <vector>

namespace vellum::web {

namespace digest_detail {

inline void hash_byte(std::uint32_t& hash, std::uint8_t byte) {
    hash ^= byte;
    hash *= 16777619U;
}

inline void hash_u32(std::uint32_t& hash, std::uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) {
        hash_byte(hash, static_cast<std::uint8_t>((value >> shift) & 0xFFU));
    }
}

inline void hash_string(std::uint32_t& hash, const std::string& value) {
    hash_u32(hash, static_cast<std::uint32_t>(value.size()));
    for (const unsigned char byte : value) hash_byte(hash, byte);
}

inline void hash_float(std::uint32_t& hash, float value) {
    hash_u32(hash, std::bit_cast<std::uint32_t>(value));
}

inline void hash_color(std::uint32_t& hash, const vellum::graphics::Color& color) {
    hash_float(hash, color.red);
    hash_float(hash, color.green);
    hash_float(hash, color.blue);
    hash_float(hash, color.alpha);
}

inline void hash_affine(std::uint32_t& hash, const vellum::graphics::Affine2D& m) {
    for (const float value : {m.a, m.b, m.c, m.d, m.tx, m.ty}) hash_float(hash, value);
}

inline void hash_geometry(std::uint32_t& hash, const vellum::graphics::PathGeometry* path) {
    hash_u32(hash, path != nullptr ? 1U : 0U);
    if (path == nullptr) return;
    for (const auto verb : path->verbs) hash_u32(hash, static_cast<std::uint32_t>(verb));
    for (const float value : path->values) hash_float(hash, value);
    hash_u32(hash, static_cast<std::uint32_t>(path->fill_rule));
    hash_float(hash, path->stroke.width);
    hash_u32(hash, static_cast<std::uint32_t>(path->stroke.join));
    hash_u32(hash, static_cast<std::uint32_t>(path->stroke.cap));
    hash_float(hash, path->stroke.miter_limit);
    for (const float value : path->stroke.dash_array) hash_float(hash, value);
    hash_float(hash, path->stroke.dash_offset);
}

inline void hash_paint(std::uint32_t& hash, const vellum::graphics::VectorPaint& paint) {
    hash_color(hash, paint.color);
    hash_u32(hash, paint.gradient ? 1U : 0U);
    if (!paint.gradient) return;
    const auto& g = *paint.gradient;
    hash_u32(hash, static_cast<std::uint32_t>(g.kind));
    hash_u32(hash, static_cast<std::uint32_t>(g.spread));
    hash_u32(hash, static_cast<std::uint32_t>(g.interpolation));
    hash_string(hash, g.color_space);
    for (const float value : {g.angle_degrees, g.center_x, g.center_y, g.radius_x, g.radius_y,
                              g.focal_x, g.focal_y, g.start_radius, g.end_radius}) {
        hash_float(hash, value);
    }
    for (const auto& stop : g.stops) {
        hash_float(hash, stop.position);
        hash_color(hash, stop.color);
    }
    hash_affine(hash, paint.gradient_transform);
}

// The asset's whole display list, so a replaced asset changes the digest
// even when its node keeps its id and box.
inline void hash_vector(std::uint32_t& hash, const vellum::graphics::VectorAsset& asset) {
    hash_string(hash, asset.content_hash);
    for (const float value : {asset.view_box.x, asset.view_box.y, asset.view_box.width,
                              asset.view_box.height}) {
        hash_float(hash, value);
    }
    for (const auto& op : asset.ops) {
        hash_u32(hash, static_cast<std::uint32_t>(op.kind));
        hash_affine(hash, op.transform);
        hash_geometry(hash, op.path.get());
        hash_paint(hash, op.fill);
        hash_paint(hash, op.stroke);
        hash_float(hash, op.opacity);
        hash_geometry(hash, op.clip.get());
    }
}

}  // namespace digest_detail

/// A digest of everything in the commands that changes what the shell draws,
/// so a proof can tell two frames apart. An opacity of 1 adds nothing, so
/// opaque frames keep the digests they had before opacity was carried.
[[nodiscard]] inline std::uint32_t command_digest(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    using namespace digest_detail;
    std::uint32_t hash = 2166136261U;
    for (const auto& command : commands) {
        hash_u32(hash, static_cast<std::uint32_t>(command.kind));
        hash_string(hash, command.node_id);
        hash_float(hash, command.bounds.x);
        hash_float(hash, command.bounds.y);
        hash_float(hash, command.bounds.width);
        hash_float(hash, command.bounds.height);
        hash_float(hash, command.corner_radius);
        hash_string(hash, command.text);
        hash_float(hash, command.font_size);
        hash_float(hash, command.fill.red);
        hash_float(hash, command.fill.green);
        hash_float(hash, command.fill.blue);
        hash_float(hash, command.fill.alpha);
        if (command.opacity != 1.0F) {
            hash_u32(hash, 0x6F706163U);
            hash_float(hash, command.opacity);
        }
        if (command.vector) hash_vector(hash, *command.vector);
    }
    return hash;
}

}  // namespace vellum::web
