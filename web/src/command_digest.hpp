#pragma once

#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/plot.hpp>
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

inline void hash_gradient(std::uint32_t& hash, const vellum::graphics::LinearGradient& g) {
    hash_u32(hash, static_cast<std::uint32_t>(g.kind));
    hash_u32(hash, g.repeating ? 1U : 0U);
    hash_u32(hash, static_cast<std::uint32_t>(g.spread));
    hash_u32(hash, static_cast<std::uint32_t>(g.interpolation));
    hash_string(hash, g.color_space);
    for (const float value : {g.angle_degrees, g.repeat_length, g.center_x, g.center_y, g.radius_x,
                              g.radius_y, g.focal_x, g.focal_y, g.start_radius, g.end_radius}) {
        hash_float(hash, value);
    }
    hash_u32(hash, static_cast<std::uint32_t>(g.stops.size()));
    for (const auto& stop : g.stops) {
        hash_float(hash, stop.position);
        hash_color(hash, stop.color);
    }
}

inline void hash_paint(std::uint32_t& hash, const vellum::graphics::VectorPaint& paint) {
    hash_color(hash, paint.color);
    hash_u32(hash, paint.gradient ? 1U : 0U);
    if (!paint.gradient) return;
    hash_gradient(hash, *paint.gradient);
    hash_affine(hash, paint.gradient_transform);
}

inline void hash_rect(std::uint32_t& hash, const vellum::graphics::Rect& r) {
    for (const float value : {r.x, r.y, r.width, r.height}) hash_float(hash, value);
}

inline void hash_text_style(std::uint32_t& hash, const vellum::graphics::TextStyle& style) {
    hash_string(hash, style.font_family);
    hash_u32(hash, static_cast<std::uint32_t>(style.font_weight));
    hash_float(hash, style.font_size);
    hash_float(hash, style.letter_spacing);
    hash_color(hash, style.color);
    hash_u32(hash, (style.underline ? 1U : 0U) | (style.strikethrough ? 2U : 0U));
}

inline void hash_plot(std::uint32_t& hash, const vellum::graphics::PlotSpec& plot) {
    hash_u32(hash, static_cast<std::uint32_t>(plot.style));
    hash_string(hash, plot.channel);
    hash_u32(hash, static_cast<std::uint32_t>(plot.shape));
    hash_u32(hash, plot.audio_channel);
    hash_u32(hash, plot.slot_count);
    hash_u32(hash, static_cast<std::uint32_t>(plot.history));
    hash_u32(hash, static_cast<std::uint32_t>(plot.orientation));
    for (const float value : {plot.value_min, plot.value_max, plot.baseline, plot.line_width,
                              plot.bar_gap, plot.peak_width}) {
        hash_float(hash, value);
    }
    hash_color(hash, plot.line_color);
    hash_color(hash, plot.fill_color);
    hash_color(hash, plot.peak_color);
    hash_u32(hash, static_cast<std::uint32_t>(plot.samples.size()));
    for (const float value : plot.samples) hash_float(hash, value);
}

inline void hash_mesh(std::uint32_t& hash, const vellum::graphics::TriangleMesh& mesh) {
    for (const auto& v : mesh.vertices) {
        for (const float value : {v.x, v.y, v.u, v.v}) hash_float(hash, value);
        hash_u32(hash, v.premultiplied_rgba);
    }
    hash_u32(hash, static_cast<std::uint32_t>(mesh.indices.size()));
    for (const auto index : mesh.indices) hash_u32(hash, index);
    hash_u32(hash, mesh.clip_enabled ? 1U : 0U);
    hash_rect(hash, mesh.clip);
    hash_u32(hash, mesh.texture ? 1U : 0U);
    if (!mesh.texture) return;
    hash_u32(hash, mesh.texture->width);
    hash_u32(hash, mesh.texture->height);
    for (const auto byte : mesh.texture->premultiplied_rgba) hash_byte(hash, byte);
}

inline void hash_effect(std::uint32_t& hash, const vellum::graphics::NodeEffect& effect) {
    hash_u32(hash, static_cast<std::uint32_t>(effect.kind));
    for (const float value : {effect.sigma, effect.offset_x, effect.offset_y}) hash_float(hash, value);
    hash_color(hash, effect.color);
    for (const float value : effect.matrix) hash_float(hash, value);
    hash_string(hash, effect.material);
    for (const auto& value : effect.values) {
        hash_string(hash, value.name);
        hash_u32(hash, static_cast<std::uint32_t>(value.type));
        for (const float number : value.value) hash_float(hash, number);
    }
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

// Every other field that changes what a command draws. Each is hashed only
// when it differs from its default, behind a tag, so a command that sets none
// of them keeps the digest it had before the field was covered.
inline void hash_extras(std::uint32_t& hash, const vellum::graphics::PaintCommand& command) {
    using vellum::graphics::Affine2D;
    const vellum::graphics::PaintCommand defaults;
    const Affine2D identity{};
    const auto& t = command.transform;
    if (t.a != identity.a || t.b != identity.b || t.c != identity.c || t.d != identity.d ||
        t.tx != identity.tx || t.ty != identity.ty) {
        hash_u32(hash, 0x78666D74U);
        hash_affine(hash, t);
    }
    if (!command.fill_gradients.empty()) {
        hash_u32(hash, 0x67726164U);
        hash_u32(hash, static_cast<std::uint32_t>(command.fill_gradients.size()));
        for (const auto& gradient : command.fill_gradients) hash_gradient(hash, gradient);
    }
    if (!command.box_shadows.empty()) {
        hash_u32(hash, 0x73686477U);
        for (const auto& shadow : command.box_shadows) {
            for (const float value : {shadow.offset_x, shadow.offset_y, shadow.blur_radius,
                                      shadow.spread_radius}) {
                hash_float(hash, value);
            }
            hash_color(hash, shadow.color);
        }
    }
    const auto& r = command.corner_radii;
    if (r.top_left != 0.0F || r.top_right != 0.0F || r.bottom_right != 0.0F ||
        r.bottom_left != 0.0F) {
        hash_u32(hash, 0x72616469U);
        for (const float value : {r.top_left, r.top_right, r.bottom_right, r.bottom_left}) {
            hash_float(hash, value);
        }
    }
    if (command.font_family != defaults.font_family || command.font_weight != defaults.font_weight ||
        command.letter_spacing != 0.0F || command.underline || command.strikethrough) {
        hash_u32(hash, 0x74737479U);
        hash_string(hash, command.font_family);
        hash_u32(hash, static_cast<std::uint32_t>(command.font_weight));
        hash_float(hash, command.letter_spacing);
        hash_u32(hash, (command.underline ? 1U : 0U) | (command.strikethrough ? 2U : 0U));
    }
    if (!command.text_runs.empty()) {
        hash_u32(hash, 0x72756E73U);
        for (const auto& run : command.text_runs) {
            hash_string(hash, run.text);
            hash_text_style(hash, run.style);
        }
    }
    if (command.mesh) {
        hash_u32(hash, 0x6D657368U);
        hash_mesh(hash, *command.mesh);
    }
    if (command.path) {
        hash_u32(hash, 0x70617468U);
        hash_geometry(hash, command.path.get());
    }
    if (command.layer_unbounded || command.layer_isolated) {
        hash_u32(hash, 0x6C617972U);
        hash_u32(hash, (command.layer_unbounded ? 1U : 0U) | (command.layer_isolated ? 2U : 0U));
    }
    if (command.blend_mode != defaults.blend_mode) {
        hash_u32(hash, 0x626C6E64U);
        hash_u32(hash, static_cast<std::uint32_t>(command.blend_mode));
    }
    if (command.kind == vellum::graphics::PaintCommand::Kind::begin_clip) {
        hash_u32(hash, 0x636C6970U);
        hash_rect(hash, command.clip_rect);
    }
    if (command.mask_mode != defaults.mask_mode) {
        hash_u32(hash, 0x6D61736BU);
        hash_u32(hash, static_cast<std::uint32_t>(command.mask_mode));
    }
    if (command.image) {
        hash_u32(hash, 0x696D6167U);
        hash_string(hash, command.image->content_hash);
        hash_u32(hash, command.image->source_rect ? 1U : 0U);
        if (command.image->source_rect) hash_rect(hash, *command.image->source_rect);
        hash_u32(hash, static_cast<std::uint32_t>(command.image->fit));
        hash_u32(hash, static_cast<std::uint32_t>(command.image->sampling));
    }
    if (!command.effects.empty()) {
        hash_u32(hash, 0x65666678U);
        hash_u32(hash, static_cast<std::uint32_t>(command.effects.size()));
        for (const auto& effect : command.effects) hash_effect(hash, effect);
        hash_rect(hash, command.effect_box);
    }
    if (command.plot) {
        hash_u32(hash, 0x706C6F74U);
        hash_plot(hash, *command.plot);
    }
    if (command.plot_background) hash_u32(hash, 0x70626B67U);
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
        hash_extras(hash, command);
    }
    return hash;
}

}  // namespace vellum::web
