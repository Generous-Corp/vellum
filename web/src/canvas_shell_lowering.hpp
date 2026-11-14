#pragma once

#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <cstddef>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace vellum::web {

/// A mask the canvas shell cannot composite; a warning. The shell draws the
/// masked siblings unmasked and does not draw the mask's own commands.
inline constexpr std::string_view kMaskNotCompositedCode = "VELLUM_WEB_MASK_NOT_COMPOSITED";

/// A plot the canvas shell does not draw, because its composed transform is
/// not the identity; a warning. An untransformed plot is drawn exactly, as
/// the rectangles of its `plot_geometry`.
inline constexpr std::string_view kPlotNotDrawnCode = "VELLUM_WEB_PLOT_NOT_DRAWN";

/// Replaces each untransformed `plot` command with one rectangle command per
/// rectangle of its `plot_geometry` at `scale`, carrying the plot's opacity,
/// so the shell draws plots with the rectangles it already draws. A
/// transformed plot keeps its command, which the shell skips and
/// `canvas_shell_loses` counts.
[[nodiscard]] inline std::vector<vellum::graphics::PaintCommand> canvas_shell_expand_plots(
    std::vector<vellum::graphics::PaintCommand> commands, float scale = 1.0F) {
    using vellum::graphics::PaintCommand;
    using vellum::graphics::PlotLayer;
    std::vector<PaintCommand> output;
    output.reserve(commands.size());
    for (auto& command : commands) {
        if (command.kind != PaintCommand::Kind::plot || !command.plot ||
            !vellum::graphics::is_identity(command.transform)) {
            output.push_back(std::move(command));
            continue;
        }
        const auto& plot = *command.plot;
        const auto geometry = vellum::graphics::plot_geometry(plot, command.fill, command.bounds,
                                                              scale);
        for (const auto& piece : geometry.rects) {
            auto color = command.fill;
            if (piece.layer == PlotLayer::fill) color = plot.fill_color;
            if (piece.layer == PlotLayer::line) color = plot.line_color;
            if (piece.layer == PlotLayer::peak) color = plot.peak_color;
            output.push_back({
                .kind = PaintCommand::Kind::rectangle,
                .node_id = command.node_id,
                .bounds = piece.rect,
                .fill = color,
                .opacity = command.opacity,
            });
        }
    }
    return output;
}

/// Whether the canvas shell draws `command` with a loss it reports. The shell
/// has no group compositing, so a `begin_layer` is flattened to per-command
/// alpha and counts once; it does not draw meshes or paths and counts each one
/// as a loss; it draws an image as `kMissingImagePlaceholder` over its box and
/// counts it as a loss; and it draws a transformed or per-corner command as
/// its untransformed box with the uniform radius. It does not clip, so each
/// `begin_clip` counts once; and it cannot mask, so it skips every command
/// from a `begin_mask` to its `end_mask` and counts the mask once, drawing
/// the masked siblings unmasked rather than drawing the mask as paint. A
/// plot left after `canvas_shell_expand_plots` is not drawn and counts once.
/// Gradients and shadows, which the shell also does not draw, are not counted.
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
    return !vellum::graphics::is_identity(command.transform) ||
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

[[nodiscard]] inline int canvas_shell_loss_count(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    const auto masked = canvas_shell_masked(commands);
    int losses = 0;
    for (std::size_t index = 0; index < commands.size(); ++index) {
        if (!masked[index] && canvas_shell_loses(commands[index])) ++losses;
    }
    return losses;
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
    /// warning for each mask the shell skips and one `kPlotNotDrawnCode`
    /// warning for each plot it does not draw.
    std::vector<vellum::graphics::LoweringDiagnostic> diagnostics;
    /// The commands the shell draws; empty for a refused scene.
    std::vector<vellum::graphics::PaintCommand> commands;
    /// `canvas_shell_loss_count(commands)`; zero for a refused scene, whose
    /// loss is its refusal.
    int losses = 0;
};

[[nodiscard]] inline CanvasShellLowering lower_for_canvas_shell(
    const vellum::graphics::Scene& scene) {
    auto acceptance = vellum::graphics::accept_scene(scene);
    CanvasShellLowering lowering{
        .accepted = acceptance.accepted,
        .reason = std::move(acceptance.reason),
        .diagnostics = std::move(acceptance.diagnostics),
    };
    if (!lowering.accepted) return lowering;
    lowering.commands = canvas_shell_expand_plots(vellum::graphics::make_paint_commands(scene));
    lowering.losses = canvas_shell_loss_count(lowering.commands);
    for (auto& diagnostic : canvas_shell_mask_diagnostics(lowering.commands)) {
        lowering.diagnostics.push_back(std::move(diagnostic));
    }
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
