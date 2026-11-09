#pragma once

#include <vellum/graphics/paint_command.hpp>

#include <cstddef>
#include <vector>

namespace vellum::web {

/// Whether the canvas shell draws `command` with a loss it reports. The shell
/// has no group compositing, so a `begin_layer` is flattened to per-command
/// alpha and counts once; it does not draw meshes or paths and counts each one
/// as a loss; and it draws a transformed or per-corner command as its
/// untransformed box with the uniform radius.
/// Gradients and shadows, which the shell also does not draw, are not counted.
[[nodiscard]] inline bool canvas_shell_loses(
    const vellum::graphics::PaintCommand& command) noexcept {
    using Kind = vellum::graphics::PaintCommand::Kind;
    if (command.kind == Kind::begin_layer || command.kind == Kind::mesh ||
        command.kind == Kind::path) {
        return true;
    }
    if (!vellum::graphics::draws_content(command.kind)) return false;
    return !vellum::graphics::is_identity(command.transform) ||
           !vellum::graphics::is_uniform(command.corner_radii);
}

[[nodiscard]] inline int canvas_shell_loss_count(
    const std::vector<vellum::graphics::PaintCommand>& commands) noexcept {
    int losses = 0;
    for (const auto& command : commands) {
        if (canvas_shell_loses(command)) ++losses;
    }
    return losses;
}

}  // namespace vellum::web
