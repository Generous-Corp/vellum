#pragma once

#include <vellum/graphics/paint_command.hpp>

#include <cstddef>
#include <vector>

namespace vellum::web {

/// Whether the canvas shell draws `command` with a loss it reports. The shell
/// has no group compositing, so a `begin_layer` is flattened to per-command
/// alpha and counts once; it does not draw meshes or paths and counts each one
/// as a loss; and it draws a transformed or per-corner command as its
/// untransformed box with the uniform radius. It does not clip, so each
/// `begin_clip` counts once; and it cannot mask, so it skips every command
/// from a `begin_mask` to its `end_mask` and counts the mask once, drawing
/// the masked siblings unmasked rather than drawing the mask as paint.
/// Gradients and shadows, which the shell also does not draw, are not counted.
[[nodiscard]] inline bool canvas_shell_loses(
    const vellum::graphics::PaintCommand& command) noexcept {
    using Kind = vellum::graphics::PaintCommand::Kind;
    if (command.kind == Kind::begin_layer || command.kind == Kind::mesh ||
        command.kind == Kind::path || command.kind == Kind::begin_clip ||
        command.kind == Kind::begin_mask) {
        return true;
    }
    if (!vellum::graphics::draws_content(command.kind)) return false;
    return !vellum::graphics::is_identity(command.transform) ||
           !vellum::graphics::is_uniform(command.corner_radii);
}

/// For each command, whether it lies inside a mask, between a `begin_mask`
/// and its `end_mask`, where the shell draws nothing. The mask brackets
/// themselves are not inside.
[[nodiscard]] inline std::vector<bool> canvas_shell_masked(
    const std::vector<vellum::graphics::PaintCommand>& commands) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    std::vector<bool> masked;
    masked.reserve(commands.size());
    std::size_t depth = 0;
    for (const auto& command : commands) {
        if (command.kind == Kind::end_mask && depth > 0U) --depth;
        masked.push_back(depth > 0U && command.kind != Kind::begin_mask);
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

}  // namespace vellum::web
