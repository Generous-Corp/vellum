#pragma once

#include <cstdint>

namespace vellum::graphics::testing {

/// How a plot column summarises the slots it covers. Production keeps the
/// minimum and maximum; the others exist so tests can show what that buys.
enum class PlotDecimation : std::uint8_t {
    /// The minimum and maximum of the column's slots.
    min_max,
    /// Only the column's first slot, which drops peaks between columns.
    first_sample,
    /// One rectangle per slot without decimation, so the geometry grows with
    /// the feed rather than the box.
    per_slot,
};

/// The plot rules tests can turn off. Every rule is on in production.
struct PlotRules final {
    /// A line column reaches the last value of the column before it.
    bool join_columns = true;
    /// Sample damage covers the old spans as well as the new ones.
    bool previous_spans = true;
    /// Sample damage covers every column whose span changed, rather than only
    /// the columns of the newest slot.
    bool every_changed_column = true;
    /// Sample damage grows by the radius of the effects around the plot.
    bool grow_by_effects = true;
};

/// Sets the decimation and rules for every plot in the process.
void set_plot_decimation(PlotDecimation decimation) noexcept;
[[nodiscard]] PlotDecimation plot_decimation() noexcept;
void set_plot_rules(const PlotRules& rules) noexcept;
[[nodiscard]] PlotRules plot_rules() noexcept;

}  // namespace vellum::graphics::testing
