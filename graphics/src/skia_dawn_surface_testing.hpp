#pragma once

#include <vellum/graphics/paint_command.hpp>

#include <cstdint>
#include <vector>

namespace vellum::graphics::testing {

/// Which content the Skia surface redraws whole when partial damage meets it.
/// Every rule is on in production; tests turn one off to show what it guards.
struct PartialRedrawRules final {
    bool enclose_paths = true;
    bool enclose_own_layers = true;
    bool enclose_interpolated_fills = true;
    bool enclose_images = true;
    bool enclose_clips = true;
    bool enclose_group_layers = true;
    bool enclose_effects = true;
    bool enclose_backdrops = true;
};

/// Sets the rules for every surface in the process.
void set_partial_redraw_rules(const PartialRedrawRules& rules) noexcept;
[[nodiscard]] PartialRedrawRules partial_redraw_rules() noexcept;

/// Sets Graphite's internal multisample count for surfaces created afterwards.
/// Production uses 1.
void set_internal_sample_count(std::uint32_t count) noexcept;
[[nodiscard]] std::uint32_t internal_sample_count() noexcept;

/// Applies the surface's rule that a layer holding a plot is unbounded below
/// scale 0.25, to commands lowered without a scale.
void unbound_plot_layers_for_scale(std::vector<PaintCommand>& commands, float scale);

}  // namespace vellum::graphics::testing
