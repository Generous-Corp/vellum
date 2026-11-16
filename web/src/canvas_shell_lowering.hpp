#pragma once

#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <algorithm>
#include <cstddef>
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

/// A plot the canvas shell does not draw, because its composed transform is
/// not the identity; a warning. An untransformed plot is drawn exactly, as
/// the rectangles of its `plot_geometry`.
inline constexpr std::string_view kPlotNotDrawnCode = "VELLUM_WEB_PLOT_NOT_DRAWN";

/// One piece of a flattened plot: a rectangle and the colour the plot's
/// rectangles composite to inside it.
struct CanvasShellPlotPiece final {
    vellum::graphics::Rect rect{};
    vellum::graphics::Color color{};
};

/// `top` drawn source-over onto `bottom`, both straight alpha.
[[nodiscard]] inline vellum::graphics::Color canvas_shell_over(
    const vellum::graphics::Color& top, const vellum::graphics::Color& bottom) noexcept {
    const float alpha = top.alpha + bottom.alpha * (1.0F - top.alpha);
    if (!(alpha > 0.0F)) return vellum::graphics::Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    const auto mix = [&](float t, float b) {
        return (t * top.alpha + b * bottom.alpha * (1.0F - top.alpha)) / alpha;
    };
    return {mix(top.red, bottom.red), mix(top.green, bottom.green), mix(top.blue, bottom.blue),
            alpha};
}

/// Flattens rectangles drawn in order into disjoint pieces, each carrying the
/// colour its overlapping rectangles composite to, so drawing every piece once
/// with the plot's opacity composites the plot as one group, as the native
/// renderer does through its opacity layer.
[[nodiscard]] inline std::vector<CanvasShellPlotPiece> canvas_shell_flatten(
    const std::vector<CanvasShellPlotPiece>& drawn) {
    using vellum::graphics::Rect;
    const auto overlap = [](const Rect& a, const Rect& b) {
        return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height &&
               b.y < a.y + a.height;
    };
    // `from` without `cut`, as up to four disjoint rectangles.
    const auto subtract = [](const Rect& from, const Rect& cut) {
        std::vector<Rect> pieces;
        const float left = std::max(from.x, cut.x);
        const float right = std::min(from.x + from.width, cut.x + cut.width);
        const float top = std::max(from.y, cut.y);
        const float bottom = std::min(from.y + from.height, cut.y + cut.height);
        if (top > from.y) pieces.push_back({from.x, from.y, from.width, top - from.y});
        if (bottom < from.y + from.height) {
            pieces.push_back({from.x, bottom, from.width, from.y + from.height - bottom});
        }
        if (left > from.x) pieces.push_back({from.x, top, left - from.x, bottom - top});
        if (right < from.x + from.width) {
            pieces.push_back({right, top, from.x + from.width - right, bottom - top});
        }
        return pieces;
    };
    std::vector<CanvasShellPlotPiece> pieces;
    for (const auto& next : drawn) {
        if (!(next.rect.width > 0.0F) || !(next.rect.height > 0.0F)) continue;
        std::vector<CanvasShellPlotPiece> kept;
        std::vector<Rect> uncovered{next.rect};
        for (const auto& piece : pieces) {
            if (!overlap(piece.rect, next.rect)) {
                kept.push_back(piece);
                continue;
            }
            for (const auto& rest : subtract(piece.rect, next.rect)) {
                kept.push_back({rest, piece.color});
            }
            const float left = std::max(piece.rect.x, next.rect.x);
            const float top = std::max(piece.rect.y, next.rect.y);
            const Rect shared{left, top,
                              std::min(piece.rect.x + piece.rect.width,
                                       next.rect.x + next.rect.width) - left,
                              std::min(piece.rect.y + piece.rect.height,
                                       next.rect.y + next.rect.height) - top};
            kept.push_back({shared, canvas_shell_over(next.color, piece.color)});
            std::vector<Rect> remaining;
            for (const auto& open : uncovered) {
                if (!overlap(open, shared)) {
                    remaining.push_back(open);
                    continue;
                }
                for (const auto& rest : subtract(open, shared)) remaining.push_back(rest);
            }
            uncovered = std::move(remaining);
        }
        for (const auto& open : uncovered) kept.push_back({open, next.color});
        pieces = std::move(kept);
    }
    return pieces;
}

/// Replaces each untransformed `plot` command with rectangle commands that
/// draw its `plot_geometry` at `scale`, so the shell draws plots with the
/// rectangles it already draws. The rectangles are flattened into disjoint
/// pieces (`canvas_shell_flatten`) that each carry the plot's opacity, so an
/// overlap such as a fill over the background, or a peak over a meter level,
/// is blended once, as one group. A gradient background, which lowers to a
/// `node_id + "/background"` rectangle just before the plot, is flattened
/// with the plot's rectangles: its colour joins the pieces and the rectangle
/// keeps only its gradients, which the shell does not draw. When that
/// rectangle and the plot are all an opacity layer holds, the layer's
/// opacity moves onto the pieces, which composite as the layer would. A
/// transformed plot keeps its command, which the shell skips and
/// `canvas_shell_loses` counts.
[[nodiscard]] inline std::vector<vellum::graphics::PaintCommand> canvas_shell_expand_plots(
    std::vector<vellum::graphics::PaintCommand> commands, float scale = 1.0F) {
    using vellum::graphics::PaintCommand;
    using vellum::graphics::PlotLayer;
    std::vector<PaintCommand> output;
    output.reserve(commands.size());
    for (std::size_t index = 0; index < commands.size(); ++index) {
        auto& command = commands[index];
        if (command.kind != PaintCommand::Kind::plot || !command.plot ||
            !vellum::graphics::is_identity(command.transform)) {
            output.push_back(std::move(command));
            continue;
        }
        const auto& plot = *command.plot;
        std::vector<CanvasShellPlotPiece> drawn;
        float opacity = command.opacity;
        if (!output.empty() && output.back().kind == PaintCommand::Kind::rectangle &&
            output.back().node_id == command.node_id + "/background" &&
            vellum::graphics::is_identity(output.back().transform)) {
            auto& background = output.back();
            if (background.fill.alpha > 0.0F) drawn.push_back({background.bounds, background.fill});
            background.fill.alpha = 0.0F;
            const std::size_t layer = output.size() - 2U;
            const bool alone = output.size() >= 2U &&
                               output[layer].kind == PaintCommand::Kind::begin_layer &&
                               output[layer].node_id == command.node_id &&
                               output[layer].blend_mode == vellum::graphics::BlendMode::normal &&
                               output[layer].effects.empty() && !output[layer].layer_isolated &&
                               index + 1U < commands.size() &&
                               commands[index + 1U].kind == PaintCommand::Kind::end_layer &&
                               commands[index + 1U].node_id == command.node_id;
            if (alone) {
                opacity *= output[layer].opacity;
                background.opacity *= output[layer].opacity;
                output.erase(output.begin() + static_cast<std::ptrdiff_t>(layer));
                ++index;
            }
        }
        const auto geometry = vellum::graphics::plot_geometry(plot, command.fill, command.bounds,
                                                              scale);
        drawn.reserve(drawn.size() + geometry.rects.size());
        for (const auto& piece : geometry.rects) {
            auto color = command.fill;
            if (piece.layer == PlotLayer::fill) color = plot.fill_color;
            if (piece.layer == PlotLayer::line) color = plot.line_color;
            if (piece.layer == PlotLayer::peak) color = plot.peak_color;
            if (!(color.alpha > 0.0F)) continue;
            drawn.push_back({piece.rect, color});
        }
        for (const auto& piece : canvas_shell_flatten(drawn)) {
            output.push_back({
                .kind = PaintCommand::Kind::rectangle,
                .node_id = command.node_id,
                .bounds = piece.rect,
                .fill = piece.color,
                .opacity = opacity,
            });
        }
    }
    return output;
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
/// already a loss. `canvas_shell_gradient_diagnostics` names each of them. A
/// plot left after `canvas_shell_expand_plots` is not drawn and counts once.
[[nodiscard]] inline bool canvas_shell_loses(
    const vellum::graphics::PaintCommand& command) noexcept {
    using Kind = vellum::graphics::PaintCommand::Kind;
    if (command.kind == Kind::begin_layer || command.kind == Kind::mesh ||
        command.kind == Kind::path || command.kind == Kind::image ||
        command.kind == Kind::begin_clip || command.kind == Kind::begin_mask ||
        command.kind == Kind::plot) {
        return true;
    }
    if (!vellum::graphics::draws_content(command.kind)) return false;
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
    /// warnings, one `kEffectNotDrawnCode` warning for each effect it does
    /// not draw, and one `kPlotNotDrawnCode` warning for each plot it does not
    /// draw.
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
    lowering.commands = canvas_shell_expand_plots(vellum::graphics::make_paint_commands(scene));
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
    for (auto& diagnostic : canvas_shell_effect_diagnostics(
             lowering.commands, canvas_shell_masked(lowering.commands))) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    lowering.filters = canvas_shell_filters(lowering.commands, lowering.color_matrices);
    const auto masked = canvas_shell_masked(lowering.commands);
    for (std::size_t index = 0; index < lowering.commands.size(); ++index) {
        const auto& command = lowering.commands[index];
        if (masked[index] || command.kind != vellum::graphics::PaintCommand::Kind::plot) continue;
        lowering.diagnostics.push_back({
            .severity = vellum::graphics::LoweringDiagnostic::Severity::warning,
            .disposition = vellum::graphics::LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(kPlotNotDrawnCode),
            .node_id = command.node_id,
            .property = "transform",
            .message = "plot " + command.node_id +
                       " was not drawn: the canvas shell draws plots only untransformed",
        });
    }
    return lowering;
}

}  // namespace vellum::web
