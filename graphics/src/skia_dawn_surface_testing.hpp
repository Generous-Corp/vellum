#pragma once

#include <cstdint>

namespace vellum::graphics::testing {

/// Which content the Skia surface redraws whole when partial damage meets it.
/// Every rule is on in production; tests turn one off to show what it guards.
struct PartialRedrawRules final {
    bool enclose_paths = true;
    bool enclose_own_layers = true;
    bool enclose_interpolated_fills = true;
};

/// Sets the rules for every surface in the process.
void set_partial_redraw_rules(const PartialRedrawRules& rules) noexcept;
[[nodiscard]] PartialRedrawRules partial_redraw_rules() noexcept;

/// Sets Graphite's internal multisample count for surfaces created afterwards.
/// Production uses 1.
void set_internal_sample_count(std::uint32_t count) noexcept;
[[nodiscard]] std::uint32_t internal_sample_count() noexcept;

}  // namespace vellum::graphics::testing
