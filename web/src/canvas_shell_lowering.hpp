#pragma once

#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/graphics/vector_asset.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace vellum::web {

/// A mask the canvas shell cannot composite; a warning. The shell draws the
/// masked siblings unmasked and does not draw the mask's own commands.
inline constexpr std::string_view kMaskNotCompositedCode = "VELLUM_WEB_MASK_NOT_COMPOSITED";
inline constexpr std::string_view kGradientNotExpressibleCode = "VELLUM_WEB_GRADIENT_NOT_EXPRESSIBLE";
/// A compiled vector asset the canvas shell draws with an approximation it
/// names: group opacity over overlapping paint, a gradient spread,
/// interpolation, colour space or kind canvas 2D lacks, or a stroke gradient
/// under a transform that is not a similarity.
inline constexpr std::string_view kVectorApproximatedCode = "VELLUM_WEB_VECTOR_APPROXIMATED";

namespace canvas_vector_detail {

inline void number(std::string& out, double value) {
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%.7g", std::isfinite(value) ? value : 0.0);
    out += buffer;
}

inline void numbers(std::string& out, std::initializer_list<double> values) {
    out += '[';
    bool first = true;
    for (const double value : values) {
        if (!first) out += ',';
        first = false;
        number(out, value);
    }
    out += ']';
}

inline void matrix(std::string& out, const vellum::graphics::Affine2D& m) {
    numbers(out, {m.a, m.b, m.c, m.d, m.tx, m.ty});
}

// SVG path data with absolute commands, which Path2D accepts as given.
inline std::string path_data(const vellum::graphics::PathGeometry& path) {
    using vellum::graphics::PathVerb;
    std::string out;
    std::size_t offset = 0;
    for (const auto verb : path.verbs) {
        const float* v = path.values.data() + offset;
        const auto arity = vellum::graphics::path_verb_arity(verb);
        offset += arity;
        switch (verb) {
        case PathVerb::move: out += 'M'; break;
        case PathVerb::line: out += 'L'; break;
        case PathVerb::quadratic: out += 'Q'; break;
        case PathVerb::cubic: out += 'C'; break;
        case PathVerb::arc: out += 'A'; break;
        case PathVerb::close: out += 'Z'; break;
        }
        for (std::size_t index = 0; index < arity; ++index) {
            if (index > 0U) out += ' ';
            number(out, v[index]);
        }
    }
    return out;
}

inline void quoted(std::string& out, std::string_view text) {
    out += '"';
    for (const char character : text) {
        if (character == '"' || character == '\\') out += '\\';
        out += character;
    }
    out += '"';
}

inline bool similarity(const vellum::graphics::Affine2D& m, double& scale) {
    const double ab = std::hypot(m.a, m.b);
    const double cd = std::hypot(m.c, m.d);
    const double dot = static_cast<double>(m.a) * m.c + static_cast<double>(m.b) * m.d;
    scale = std::sqrt(std::abs(static_cast<double>(m.a) * m.d - static_cast<double>(m.b) * m.c));
    const double tolerance = 1e-5 * std::max(1.0, std::max(ab, cd));
    return std::abs(ab - cd) <= tolerance && std::abs(dot) <= tolerance * std::max(1.0, ab);
}

inline vellum::graphics::Point map(const vellum::graphics::Affine2D& m, double x, double y) {
    return vellum::graphics::apply(m, {static_cast<float>(x), static_cast<float>(y)});
}

inline void stops(std::string& out, const vellum::graphics::LinearGradient& gradient,
                  float alpha) {
    out += ",\"stops\":[";
    bool first = true;
    for (const auto& stop : gradient.stops) {
        if (!first) out += ',';
        first = false;
        numbers(out, {stop.position, stop.color.red, stop.color.green, stop.color.blue,
                      stop.color.alpha * alpha});
    }
    out += ']';
}

// Writes `paint` as a canvas paint in the operation's coordinates. Linear
// gradients map exactly under any affine gradient transform, as do radial
// ones under a similarity; a fill radial gradient under any other transform
// is drawn in gradient space (`space`), which only a fill can use.
inline void paint(std::string& out, const vellum::graphics::VectorPaint& source, float alpha,
                  bool fill, std::vector<std::string>& losses, const std::string& where) {
    using vellum::graphics::GradientKind;
    using vellum::graphics::GradientSpread;
    if (!source.gradient) {
        out += "{\"color\":";
        numbers(out, {source.color.red, source.color.green, source.color.blue,
                      source.color.alpha * alpha});
        out += '}';
        return;
    }
    const auto& gradient = *source.gradient;
    if (gradient.spread != GradientSpread::pad || gradient.repeating) {
        losses.push_back(where + " gradient spread is drawn as pad; canvas 2D only pads");
    }
    if (!vellum::graphics::gradient_uses_legacy_interpolation(gradient)) {
        losses.push_back(where +
                         " gradient interpolation or colour space is drawn as sRGB by canvas 2D");
    }
    const auto& m = source.gradient_transform;
    if (gradient.kind == GradientKind::conic) {
        losses.push_back(where + " conic gradient is drawn as its first stop's colour");
        const auto& first = gradient.stops.front().color;
        out += "{\"color\":";
        numbers(out, {first.red, first.green, first.blue, first.alpha * alpha});
        out += '}';
        return;
    }
    if (gradient.kind == GradientKind::linear) {
        const auto resolved =
            vellum::graphics::resolve_linear_gradient({0.0F, 0.0F, 1.0F, 1.0F}, gradient);
        const double dx = resolved.end_x - resolved.start_x;
        const double dy = resolved.end_y - resolved.start_y;
        const double length = dx * dx + dy * dy;
        const double determinant = static_cast<double>(m.a) * m.d - static_cast<double>(m.b) * m.c;
        // The ramp parameter is ((inverse(m) * p - start) . d) / |d|^2, a linear
        // function of p whose gradient is inverse(A)^T d / |d|^2.
        const double rx = (m.d * dx - m.b * dy) / determinant / length;
        const double ry = (-m.c * dx + m.a * dy) / determinant / length;
        const double r2 = rx * rx + ry * ry;
        const auto start = map(m, resolved.start_x, resolved.start_y);
        out += "{\"linear\":";
        numbers(out, {start.x, start.y, start.x + rx / r2, start.y + ry / r2});
        stops(out, gradient, alpha);
        out += '}';
        return;
    }
    double x0 = 0.0;
    double y0 = 0.0;
    double r0 = 0.0;
    double x1 = 0.0;
    double y1 = 0.0;
    double r1 = 0.0;
    vellum::graphics::Affine2D space = m;
    if (gradient.kind == GradientKind::radial) {
        const float rx = gradient.radius_x > 0.0F ? gradient.radius_x : 0.5F;
        const float ry = gradient.radius_y > 0.0F ? gradient.radius_y : 0.5F;
        space = vellum::graphics::concat(
            m, {.a = rx, .d = ry, .tx = gradient.center_x, .ty = gradient.center_y});
        r1 = 1.0;
    } else {
        x0 = gradient.focal_x;
        y0 = gradient.focal_y;
        r0 = gradient.start_radius;
        x1 = gradient.center_x;
        y1 = gradient.center_y;
        r1 = gradient.end_radius;
    }
    double scale = 1.0;
    if (similarity(space, scale)) {
        const auto start = map(space, x0, y0);
        const auto end = map(space, x1, y1);
        out += "{\"radial\":";
        numbers(out, {start.x, start.y, r0 * scale, end.x, end.y, r1 * scale});
    } else if (fill) {
        out += "{\"radial\":";
        numbers(out, {x0, y0, r0, x1, y1, r1});
        out += ",\"space\":";
        matrix(out, space);
    } else {
        losses.push_back(where +
                         " radial stroke gradient under a non-uniform transform is drawn with "
                         "circular rings");
        const auto start = map(space, x0, y0);
        const auto end = map(space, x1, y1);
        out += "{\"radial\":";
        numbers(out, {start.x, start.y, r0 * scale, end.x, end.y, r1 * scale});
    }
    stops(out, gradient, alpha);
    out += '}';
}

}  // namespace canvas_vector_detail

/// What the canvas shell draws for one `vector` command: a JSON plan the
/// shell's `drawVector` executes with a 2D context, and each approximation
/// it makes. The plan's `matrix` maps the asset's view box into the frame,
/// and its `steps` are, in order: `save` (with an `alpha` that multiplies
/// the global alpha and an optional `transform`), `clip` (path data and fill
/// rule), `restore`, `fill` and `stroke` (path data, an optional
/// `transform`, and a paint of `color`, `linear` endpoints or `radial`
/// circles with `stops`, drawn in the optional `space` coordinates).
struct CanvasVectorPlan final {
    std::string json;
    std::vector<std::string> losses;
};

[[nodiscard]] inline CanvasVectorPlan lower_vector_for_canvas(
    const vellum::graphics::PaintCommand& command, float alpha) {
    using vellum::graphics::VectorOp;
    namespace detail = canvas_vector_detail;
    CanvasVectorPlan plan;
    if (!command.vector || !vellum::graphics::vector_asset_is_valid(*command.vector)) {
        plan.json = "{\"steps\":[]}";
        return plan;
    }
    const auto& asset = *command.vector;
    auto& out = plan.json;
    out = "{\"id\":";
    detail::quoted(out, command.node_id);
    out += ",\"matrix\":";
    detail::matrix(out, vellum::graphics::concat(
        command.transform, vellum::graphics::vector_view_box_transform(command.bounds,
                                                                       asset.view_box)));
    out += ",\"alpha\":";
    detail::number(out, alpha);
    out += ",\"steps\":[";
    bool first = true;
    const auto step = [&] {
        if (!first) out += ',';
        first = false;
    };
    // The paints inside each open group, to tell when its opacity, which the
    // shell flattens into each paint, changes how overlapping paint blends.
    struct Open final {
        std::size_t index = 0;
        float opacity = 1.0F;
        std::size_t paints = 0;
    };
    std::vector<Open> open;
    for (std::size_t index = 0; index < asset.ops.size(); ++index) {
        const auto& op = asset.ops[index];
        const std::string where = "operation " + std::to_string(index);
        if (op.kind == VectorOp::Kind::push_group) {
            step();
            out += "{\"op\":\"save\",\"alpha\":";
            detail::number(out, op.opacity);
            if (!vellum::graphics::is_identity(op.transform)) {
                out += ",\"transform\":";
                detail::matrix(out, op.transform);
            }
            out += '}';
            if (op.clip) {
                step();
                out += "{\"op\":\"clip\",\"d\":";
                detail::quoted(out, detail::path_data(*op.clip));
                out += op.clip->fill_rule == vellum::graphics::FillRule::evenodd
                           ? ",\"rule\":\"evenodd\"}"
                           : ",\"rule\":\"nonzero\"}";
            }
            open.push_back({index, op.opacity, 0U});
            continue;
        }
        if (op.kind == VectorOp::Kind::pop_group) {
            step();
            out += "{\"op\":\"restore\"}";
            const auto closed = open.back();
            open.pop_back();
            if (closed.opacity < 1.0F && closed.paints > 1U) {
                plan.losses.push_back("group at operation " + std::to_string(closed.index) +
                                      " has opacity over overlapping paint, which canvas 2D "
                                      "applies to each paint");
            }
            if (!open.empty()) open.back().paints += closed.paints;
            continue;
        }
        const bool fills = vellum::graphics::vector_op_fills(op);
        const bool strokes = vellum::graphics::vector_op_strokes(op);
        if (!fills && !strokes) continue;
        const std::size_t paints = (fills ? 1U : 0U) + (strokes ? 1U : 0U);
        if (!open.empty()) open.back().paints += paints;
        const auto data = detail::path_data(*op.path);
        if (fills) {
            step();
            out += "{\"op\":\"fill\",\"d\":";
            detail::quoted(out, data);
            out += op.path->fill_rule == vellum::graphics::FillRule::evenodd
                       ? ",\"rule\":\"evenodd\""
                       : ",\"rule\":\"nonzero\"";
            if (!vellum::graphics::is_identity(op.transform)) {
                out += ",\"transform\":";
                detail::matrix(out, op.transform);
            }
            out += ",\"paint\":";
            detail::paint(out, op.fill, 1.0F, true, plan.losses, where + " fill");
            out += '}';
        }
        if (strokes) {
            const auto& stroke = op.path->stroke;
            step();
            out += "{\"op\":\"stroke\",\"d\":";
            detail::quoted(out, data);
            if (!vellum::graphics::is_identity(op.transform)) {
                out += ",\"transform\":";
                detail::matrix(out, op.transform);
            }
            out += ",\"width\":";
            detail::number(out, stroke.width);
            constexpr std::array<const char*, 3> joins{"miter", "round", "bevel"};
            constexpr std::array<const char*, 3> caps{"butt", "round", "square"};
            out += ",\"join\":\"";
            out += joins[static_cast<std::size_t>(stroke.join)];
            out += "\",\"cap\":\"";
            out += caps[static_cast<std::size_t>(stroke.cap)];
            out += "\",\"miter\":";
            detail::number(out, stroke.miter_limit);
            if (!stroke.dash_array.empty()) {
                out += ",\"dash\":[";
                for (std::size_t dash = 0; dash < stroke.dash_array.size(); ++dash) {
                    if (dash > 0U) out += ',';
                    detail::number(out, stroke.dash_array[dash]);
                }
                out += "],\"dashOffset\":";
                detail::number(out, stroke.dash_offset);
            }
            out += ",\"paint\":";
            detail::paint(out, op.stroke, 1.0F, false, plan.losses, where + " stroke");
            out += '}';
        }
    }
    out += "]}";
    return plan;
}

/// An effect the canvas shell does not draw; a warning. The shell draws blur
/// and colour matrix effects through the canvas `filter`; a drop shadow, a
/// backdrop blur and a material are drawn without.
inline constexpr std::string_view kEffectNotDrawnCode = "VELLUM_WEB_EFFECT_NOT_DRAWN";

/// A colour matrix the shell installs as an SVG `feColorMatrix` filter that
/// its canvas filter names by `id`.
struct CanvasShellColorMatrix final {
    std::string id;
    /// The matrix's twenty entries, space separated, in SVG order.
    std::string values;
};

/// The CSS filter function for one effect the shell draws, or empty. A blur is
/// `blur()` with its sigma as the standard deviation in CSS pixels; a colour
/// matrix is a `url()` naming the matrix appended to `matrices`.
[[nodiscard]] inline std::string canvas_shell_filter_function(
    const vellum::graphics::NodeEffect& effect,
    std::vector<CanvasShellColorMatrix>& matrices) {
    using vellum::graphics::EffectKind;
    if (effect.kind == EffectKind::blur) {
        return "blur(" + std::to_string(effect.sigma) + "px)";
    }
    if (effect.kind != EffectKind::color_matrix) return {};
    CanvasShellColorMatrix matrix{.id = "vellum-color-matrix-" + std::to_string(matrices.size())};
    for (const float entry : effect.matrix) {
        if (!matrix.values.empty()) matrix.values += ' ';
        matrix.values += std::to_string(entry);
    }
    auto reference = "url(#" + matrix.id + ")";
    matrices.push_back(std::move(matrix));
    return reference;
}

/// For each command, the canvas filter the shell draws it with: the blur and
/// colour matrix effects of every effect layer around it, innermost first,
/// or empty for none. The shell has no group compositing, so it filters each
/// member on its own rather than the group as a whole. Colour matrices are
/// appended to `matrices`.
[[nodiscard]] inline std::vector<std::string> canvas_shell_filters(
    const std::vector<vellum::graphics::PaintCommand>& commands,
    std::vector<CanvasShellColorMatrix>& matrices) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    std::vector<std::string> filters;
    filters.reserve(commands.size());
    std::vector<std::string> open;
    const auto current = [&] {
        std::string css;
        for (auto layer = open.rbegin(); layer != open.rend(); ++layer) {
            if (layer->empty()) continue;
            if (!css.empty()) css += ' ';
            css += *layer;
        }
        return css;
    };
    for (const auto& command : commands) {
        if (command.kind == Kind::begin_layer) {
            std::string chain;
            for (const auto& effect : command.effects) {
                const auto function = canvas_shell_filter_function(effect, matrices);
                if (function.empty()) continue;
                if (!chain.empty()) chain += ' ';
                chain += function;
            }
            open.push_back(std::move(chain));
            filters.push_back({});
            continue;
        }
        if (command.kind == Kind::end_layer) {
            if (!open.empty()) open.pop_back();
            filters.push_back({});
            continue;
        }
        filters.push_back(vellum::graphics::draws_content(command.kind) ? current() : std::string{});
    }
    return filters;
}

/// One warning for each effect the shell does not draw, naming its node.
[[nodiscard]] inline std::vector<vellum::graphics::LoweringDiagnostic>
canvas_shell_effect_diagnostics(const std::vector<vellum::graphics::PaintCommand>& commands,
                                const std::vector<bool>& masked) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    using vellum::graphics::EffectKind;
    std::vector<vellum::graphics::LoweringDiagnostic> output;
    for (std::size_t index = 0; index < commands.size(); ++index) {
        const auto& command = commands[index];
        if (masked[index] || command.kind != Kind::begin_layer) continue;
        for (const auto& effect : command.effects) {
            const char* name = effect.kind == EffectKind::drop_shadow     ? "drop shadow"
                               : effect.kind == EffectKind::backdrop_blur ? "backdrop blur"
                               : effect.kind == EffectKind::material      ? "material"
                                                                          : nullptr;
            if (name == nullptr) continue;
            std::string what = name;
            if (effect.kind == EffectKind::material) what += " " + effect.material;
            output.push_back({
                .severity = vellum::graphics::LoweringDiagnostic::Severity::warning,
                .disposition = vellum::graphics::LoweringDiagnostic::Disposition::lowered,
                .code = std::string(kEffectNotDrawnCode),
                .node_id = command.node_id,
                .property = "effects",
                .message = "the " + what + " of " + command.node_id +
                           " was not drawn: the canvas shell draws only blur and colour "
                           "matrix effects",
            });
        }
    }
    return output;
}

/// Whether the canvas shell draws `command` with a loss it reports. The shell
/// composites only Tier 1 blended groups (see `canvas_shell_composites`, which
/// `canvas_shell_loss_count` exempts); any other `begin_layer` is flattened to
/// per-command alpha, its blur and colour matrix effects to per-command
/// filters, and counts once; it does not draw meshes or paths and counts each one
/// as a loss; it draws an image as `kMissingImagePlaceholder` over its box and
/// counts it as a loss; and it draws a transformed or per-corner command as
/// its untransformed box with the uniform radius. It does not clip, so each
/// `begin_clip` counts once; and it cannot mask, so it skips every command
/// from a `begin_mask` to its `end_mask` and counts the mask once, drawing
/// the masked siblings unmasked rather than drawing the mask as paint.
/// Shadows and plain linear fill gradients, which the shell has never drawn,
/// are not counted, so scenes without the version 8 gradient vocabulary keep
/// their loss counts. A fill gradient that uses that vocabulary counts once
/// for its command; a gradient stroke counts through its path, which is
/// already a loss. `canvas_shell_gradient_diagnostics` names each of them.
/// A compiled vector asset is drawn through `lower_vector_for_canvas` and
/// counts once when that plan approximates anything.
[[nodiscard]] inline bool canvas_shell_loses(
    const vellum::graphics::PaintCommand& command) noexcept {
    using Kind = vellum::graphics::PaintCommand::Kind;
    if (command.kind == Kind::begin_layer || command.kind == Kind::mesh ||
        command.kind == Kind::path || command.kind == Kind::image ||
        command.kind == Kind::begin_clip || command.kind == Kind::begin_mask) {
        return true;
    }
    if (!vellum::graphics::draws_content(command.kind)) return false;
    if (command.kind == vellum::graphics::PaintCommand::Kind::vector) {
        return !lower_vector_for_canvas(command, 1.0F).losses.empty();
    }
    return std::any_of(command.fill_gradients.begin(), command.fill_gradients.end(),
                       vellum::graphics::gradient_uses_version_8) ||
           !vellum::graphics::is_identity(command.transform) ||
           !vellum::graphics::is_uniform(command.corner_radii);
}

/// For each command, whether it lies inside a mask, between a `begin_mask`
/// and its `end_mask`, where the shell draws nothing. The outermost mask
/// brackets are not inside; a nested mask's are, so each mask counts once.
[[nodiscard]] inline std::vector<bool> canvas_shell_masked(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    std::vector<bool> masked;
    masked.reserve(commands.size());
    std::size_t depth = 0;
    for (const auto& command : commands) {
        if (command.kind == Kind::end_mask && depth > 0U) --depth;
        masked.push_back(depth > 0U);
        if (command.kind == Kind::begin_mask) ++depth;
    }
    return masked;
}



/// One warning for each mask the shell counts as a loss, naming the mask node
/// and why it was not applied. A mask nested inside another mask is skipped
/// with it and, like its count, has no report of its own.
[[nodiscard]] inline std::vector<vellum::graphics::LoweringDiagnostic>
canvas_shell_mask_diagnostics(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    using vellum::graphics::MaskMode;
    const auto masked = canvas_shell_masked(commands);
    std::vector<vellum::graphics::LoweringDiagnostic> output;
    for (std::size_t index = 0; index < commands.size(); ++index) {
        const auto& command = commands[index];
        if (masked[index] || command.kind != Kind::begin_mask) continue;
        const char* mode = command.mask_mode == MaskMode::luminance ? "luminance" : "alpha";
        output.push_back({
            .severity = vellum::graphics::LoweringDiagnostic::Severity::warning,
            .disposition = vellum::graphics::LoweringDiagnostic::Disposition::lowered,
            .code = std::string(kMaskNotCompositedCode),
            .node_id = command.node_id,
            .property = "mask",
            .message = std::string("mask ") + command.node_id + " (" + mode +
                       ") was not composited: the canvas shell has no group compositing, "
                       "so its siblings were drawn unmasked",
        });
    }
    return output;
}

/// The canvas `globalCompositeOperation` that draws `mode`, or nothing for a
/// mode the shell does not draw. The shell implements Tier 1 only: `normal`
/// and `source_over` are `source-over`, and every Tier 2 mode, which the canvas
/// may or may not offer, falls back to `normal` with a
/// `kBlendModeFallbackCode` warning (see `canvas_shell_blend_diagnostics`).
[[nodiscard]] constexpr std::optional<std::string_view> canvas_shell_blend_operation(
    vellum::graphics::BlendMode mode) noexcept {
    using vellum::graphics::BlendMode;
    switch (mode) {
    case BlendMode::normal:
    case BlendMode::source_over: return "source-over";
    case BlendMode::multiply: return "multiply";
    case BlendMode::screen: return "screen";
    case BlendMode::overlay: return "overlay";
    case BlendMode::darken: return "darken";
    case BlendMode::lighten: return "lighten";
    case BlendMode::difference: return "difference";
    case BlendMode::exclusion: return "exclusion";
    case BlendMode::destination_in: return "destination-in";
    case BlendMode::destination_out: return "destination-out";
    default: break;
    }
    return std::nullopt;
}

/// What the canvas backend can do beyond plain drawing.
struct CanvasShellCapabilities final {
    /// The backend has `blendBegin(operation, alpha)` and `blendEnd()`, which
    /// draw a group offscreen and composite it once. A backend written before
    /// them has neither; the shell then draws every blended group as `normal`
    /// and reports each one.
    bool group_compositing = true;
};

/// How the shell draws each command of a scene.
struct CanvasShellPaintPlan final {
    /// For a `begin_layer` the shell composites as a group, the canvas
    /// operation it is blended with when its `end_layer` is reached; empty for
    /// every other command.
    std::vector<std::string_view> group_operations;
    /// The alpha each command is drawn with. A drawing command takes its own
    /// opacity times that of every layer between it and the nearest enclosing
    /// composited group, whose own opacity is applied once, when the group is
    /// composited; a composited `begin_layer` holds that group alpha. Without
    /// a composited group this is `effective_opacities`.
    std::vector<float> opacities;
};

/// Whether the shell composites the `begin_layer` `command` as a group: a
/// Tier 1 mode other than `normal` on a backend with group compositing. Every
/// other layer is flattened to per-command alpha.
[[nodiscard]] inline bool canvas_shell_composites(
    const vellum::graphics::PaintCommand& command,
    const CanvasShellCapabilities& capabilities) noexcept {
    return command.kind == vellum::graphics::PaintCommand::Kind::begin_layer &&
           capabilities.group_compositing &&
           command.blend_mode != vellum::graphics::BlendMode::normal &&
           canvas_shell_blend_operation(command.blend_mode).has_value();
}

[[nodiscard]] inline CanvasShellPaintPlan canvas_shell_paint_plan(
    const std::vector<vellum::graphics::PaintCommand>& commands,
    const CanvasShellCapabilities& capabilities = {}) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    CanvasShellPaintPlan plan;
    plan.group_operations.reserve(commands.size());
    plan.opacities.reserve(commands.size());
    std::vector<float> enclosing_before;
    float enclosing = 1.0F;
    for (const auto& command : commands) {
        if (command.kind == Kind::end_layer) {
            if (!enclosing_before.empty()) {
                enclosing = enclosing_before.back();
                enclosing_before.pop_back();
            }
            plan.opacities.push_back(enclosing);
            plan.group_operations.emplace_back();
            continue;
        }
        plan.opacities.push_back(enclosing * command.opacity);
        if (command.kind != Kind::begin_layer) {
            plan.group_operations.emplace_back();
            continue;
        }
        enclosing_before.push_back(enclosing);
        if (canvas_shell_composites(command, capabilities)) {
            plan.group_operations.push_back(*canvas_shell_blend_operation(command.blend_mode));
            enclosing = 1.0F;
        } else {
            plan.group_operations.emplace_back();
            enclosing *= command.opacity;
        }
    }
    return plan;
}

/// One warning for each blended layer the shell draws as `normal`: a Tier 2
/// mode, which the shell does not implement, or any mode on a backend without
/// group compositing. Each names the node and why.
[[nodiscard]] inline std::vector<vellum::graphics::LoweringDiagnostic>
canvas_shell_blend_diagnostics(const std::vector<vellum::graphics::PaintCommand>& commands,
                               const CanvasShellCapabilities& capabilities = {}) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    const auto masked = canvas_shell_masked(commands);
    std::vector<vellum::graphics::LoweringDiagnostic> output;
    for (std::size_t index = 0; index < commands.size(); ++index) {
        const auto& command = commands[index];
        if (masked[index] || command.kind != Kind::begin_layer ||
            command.blend_mode == vellum::graphics::BlendMode::normal ||
            canvas_shell_composites(command, capabilities)) {
            continue;
        }
        const std::string mode(vellum::graphics::to_string(command.blend_mode));
        const bool tier_two = !canvas_shell_blend_operation(command.blend_mode).has_value();
        output.push_back({
            .severity = vellum::graphics::LoweringDiagnostic::Severity::warning,
            .disposition = vellum::graphics::LoweringDiagnostic::Disposition::lowered,
            .code = std::string(vellum::graphics::kBlendModeFallbackCode),
            .node_id = command.node_id,
            .property = "blendMode",
            .message = tier_two
                ? "blend mode " + mode + " of " + command.node_id +
                      " is a Tier 2 mode the canvas shell does not draw; the group was drawn "
                      "as normal"
                : "blend mode " + mode + " of " + command.node_id +
                      " needs group compositing, which the canvas backend lacks "
                      "(no blendBegin and blendEnd); the group was drawn as normal",
        });
    }
    return output;
}

/// Reports each gradient paint in the version 8 vocabulary that the shell
/// draws as its base colour: every fill gradient that uses it and every
/// gradient stroke. Plain linear fill gradients are not reported, as before
/// version 8. A path may carry both a fill and a stroke gradient, so each is
/// reported though the path counts as one loss.
[[nodiscard]] inline std::vector<vellum::graphics::LoweringDiagnostic>
canvas_shell_gradient_diagnostics(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    using Kind = vellum::graphics::GradientKind;
    std::vector<vellum::graphics::LoweringDiagnostic> output;
    const auto report = [&](const vellum::graphics::PaintCommand& command,
                            const vellum::graphics::LinearGradient& gradient,
                            std::string property) {
        std::string form = "gradient";
        switch (gradient.kind) {
        case Kind::linear: form = "linear gradient"; break;
        case Kind::radial:
            {
                const float radius_x =
                    (gradient.radius_x > 0.0F ? gradient.radius_x : 0.5F) * command.bounds.width;
                const float radius_y =
                    (gradient.radius_y > 0.0F ? gradient.radius_y : 0.5F) * command.bounds.height;
                const bool elliptical = radius_x != radius_y;
                form = elliptical ? "elliptical radial gradient" : "radial gradient";
            }
            break;
        case Kind::two_circle_radial: form = "two-circle radial gradient"; break;
        case Kind::conic: form = "conic gradient"; break;
        }
        output.push_back({
            .severity = vellum::graphics::LoweringDiagnostic::Severity::warning,
            .disposition = vellum::graphics::LoweringDiagnostic::Disposition::lowered,
            .code = std::string(kGradientNotExpressibleCode),
            .node_id = command.node_id,
            .property = std::move(property),
            .message = "canvas 2D shell cannot express " + form +
                       "; the base colour was drawn",
        });
    };
    // A command inside a mask is not drawn at all, so it has no base colour
    // to report, matching `canvas_shell_loss_count`.
    const auto masked = canvas_shell_masked(commands);
    for (std::size_t index = 0; index < commands.size(); ++index) {
        const auto& command = commands[index];
        if (masked[index] || !vellum::graphics::draws_content(command.kind)) continue;
        for (const auto& gradient : command.fill_gradients) {
            if (vellum::graphics::gradient_uses_version_8(gradient)) {
                report(command, gradient, "gradient");
            }
        }
        if (command.kind == vellum::graphics::PaintCommand::Kind::path && command.path &&
            command.path->stroke.gradient) {
            report(command, *command.path->stroke.gradient, "strokeGradient");
        }
    }
    return output;
}

/// One `kVectorApproximatedCode` warning for each approximation the shell
/// makes drawing a compiled vector asset outside a mask.
[[nodiscard]] inline std::vector<vellum::graphics::LoweringDiagnostic>
canvas_shell_vector_diagnostics(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    const auto masked = canvas_shell_masked(commands);
    std::vector<vellum::graphics::LoweringDiagnostic> output;
    for (std::size_t index = 0; index < commands.size(); ++index) {
        const auto& command = commands[index];
        if (masked[index] || command.kind != vellum::graphics::PaintCommand::Kind::vector) {
            continue;
        }
        for (auto& loss : lower_vector_for_canvas(command, 1.0F).losses) {
            output.push_back({
                .severity = vellum::graphics::LoweringDiagnostic::Severity::warning,
                .disposition = vellum::graphics::LoweringDiagnostic::Disposition::lowered,
                .code = std::string(kVectorApproximatedCode),
                .node_id = command.node_id,
                .property = "vector",
                .message = "vector " + command.node_id + ": " + std::move(loss),
            });
        }
    }
    return output;
}

/// The commands the shell draws with a loss: each `canvas_shell_loses`
/// outside a mask, except a layer the shell composites as a group, which it
/// draws exactly. A blended layer drawn as `normal` instead is counted, once.
[[nodiscard]] inline int canvas_shell_loss_count(
    const std::vector<vellum::graphics::PaintCommand>& commands,
    const CanvasShellCapabilities& capabilities = {}) {
    const auto masked = canvas_shell_masked(commands);
    int losses = 0;
    for (std::size_t index = 0; index < commands.size(); ++index) {
        if (masked[index] || canvas_shell_composites(commands[index], capabilities)) continue;
        if (canvas_shell_loses(commands[index])) ++losses;
    }
    return losses;
}

/// What the canvas shell draws for a scene and what it reports. The shell
/// accepts a scene through `accept_scene`, the decision the native renderer
/// makes, so a scene the native renderer refuses draws nothing here and
/// carries the same reason and diagnostics.
struct CanvasShellLowering final {
    bool accepted = false;
    /// Why the scene is refused; empty when it is accepted.
    std::string reason;
    /// `SceneAcceptance::diagnostics`: the refusal of a refused scene, or the
    /// warnings of an accepted one followed by one `kMaskNotCompositedCode`
    /// warning for each mask the shell skips, one `kBlendModeFallbackCode`
    /// warning for each blended group drawn as `normal`, the gradient
    /// warnings, and one `kEffectNotDrawnCode` warning for each effect it does
    /// not draw.
    std::vector<vellum::graphics::LoweringDiagnostic> diagnostics;
    /// The commands the shell draws; empty for a refused scene.
    std::vector<vellum::graphics::PaintCommand> commands;
    /// `canvas_shell_filters(commands)`, one per command.
    std::vector<std::string> filters;
    /// The colour matrices `filters` names.
    std::vector<CanvasShellColorMatrix> color_matrices;
    /// `canvas_shell_loss_count(commands)`; zero for a refused scene, whose
    /// loss is its refusal.
    int losses = 0;
};

[[nodiscard]] inline CanvasShellLowering lower_for_canvas_shell(
    const vellum::graphics::Scene& scene, const CanvasShellCapabilities& capabilities = {}) {
    auto acceptance = vellum::graphics::accept_scene(scene);
    CanvasShellLowering lowering{
        .accepted = acceptance.accepted,
        .reason = std::move(acceptance.reason),
        .diagnostics = std::move(acceptance.diagnostics),
    };
    if (!lowering.accepted) return lowering;
    lowering.commands = vellum::graphics::make_paint_commands(scene);
    lowering.losses = canvas_shell_loss_count(lowering.commands, capabilities);
    for (auto& diagnostic : canvas_shell_mask_diagnostics(lowering.commands)) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    for (auto& diagnostic : canvas_shell_blend_diagnostics(lowering.commands, capabilities)) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    for (auto& diagnostic : canvas_shell_gradient_diagnostics(lowering.commands)) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    for (auto& diagnostic : canvas_shell_vector_diagnostics(lowering.commands)) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    for (auto& diagnostic : canvas_shell_effect_diagnostics(
             lowering.commands, canvas_shell_masked(lowering.commands))) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    lowering.filters = canvas_shell_filters(lowering.commands, lowering.color_matrices);
    return lowering;
}

}  // namespace vellum::web
