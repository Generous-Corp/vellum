#pragma once

#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
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
[[nodiscard]] inline bool canvas_shell_loses(
    const vellum::graphics::PaintCommand& command) noexcept {
    using Kind = vellum::graphics::PaintCommand::Kind;
    if (command.kind == Kind::begin_layer || command.kind == Kind::mesh ||
        command.kind == Kind::path || command.kind == Kind::image ||
        command.kind == Kind::begin_clip || command.kind == Kind::begin_mask) {
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
    for (auto& diagnostic : canvas_shell_effect_diagnostics(
             lowering.commands, canvas_shell_masked(lowering.commands))) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
    lowering.filters = canvas_shell_filters(lowering.commands, lowering.color_matrices);
    return lowering;
}

}  // namespace vellum::web
