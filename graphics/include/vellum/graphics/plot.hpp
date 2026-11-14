#pragma once

#include <vellum/graphics/color.hpp>
#include <vellum/graphics/scene.hpp>

#include <cstdint>
#include <string>
#include <vector>

namespace vellum::graphics {

/// Whether a plot spec can be drawn: its style reads a channel shape it
/// supports (a line or area plot a scalar or vector channel, a bar plot a
/// vector channel, a level meter a meter channel), its slot count is in
/// [1, `kMaxPlotSlots`] unless it is a level meter, it holds no more samples
/// than slots (two for a level meter), its value range is finite and not
/// empty, its baseline, colours and widths are finite, the widths and the
/// bar gap are in [0, `kMaxPlotStroke`], and its audio channel is at most
/// `kMaxPlotAudioChannel`. On failure `reason`, when non-null, says
/// which rule failed.
[[nodiscard]] bool plot_is_valid(const PlotSpec& plot, std::string* reason = nullptr);

/// The first plot node, in depth-first order, that cannot be drawn: one
/// without a spec, with children, or whose spec fails `plot_is_valid`, with
/// `reason` set to the rule that failed.
[[nodiscard]] const SceneNode* find_invalid_plot(const SceneNode& node, std::string& reason);

/// Every field of two specs except `samples` is the same, floats compared
/// bitwise.
[[nodiscard]] bool plot_layout_equal(const PlotSpec& a, const PlotSpec& b) noexcept;

/// `plot_layout_equal`, and the samples are the same bitwise.
[[nodiscard]] bool plot_specs_equal(const PlotSpec& a, const PlotSpec& b) noexcept;

/// What a rectangle of a plot's geometry paints.
enum class PlotLayer : std::uint8_t {
    /// The node's `fill` over the box.
    background,
    /// `PlotSpec::fill_color`.
    fill,
    /// `PlotSpec::line_color`.
    line,
    /// `PlotSpec::peak_color`.
    peak,
};

struct PlotRect final {
    /// Absolute logical coordinates before the node's transform, on device
    /// pixel edges.
    Rect rect{};
    PlotLayer layer = PlotLayer::fill;
};

/// The rectangles a plot draws, in paint order.
struct PlotGeometry final {
    std::vector<PlotRect> rects;
    /// Device-pixel columns across the box.
    std::uint32_t columns = 0;
    /// Device-pixel rows down the box.
    std::uint32_t rows = 0;
};

/// The device-pixel grid of a plot whose absolute box is `box` at `scale`:
/// each edge rounded to the nearest device pixel.
struct PlotGrid final {
    std::int32_t left = 0;
    std::int32_t top = 0;
    std::int32_t right = 0;
    std::int32_t bottom = 0;
};

[[nodiscard]] PlotGrid plot_grid(const Rect& box, float scale) noexcept;

/// The grid as an absolute logical rectangle.
[[nodiscard]] Rect plot_grid_rect(const Rect& box, float scale) noexcept;

/// The widest plot, in device pixels, whose samples are drawn; a wider plot
/// draws only its background.
inline constexpr std::int32_t kMaxPlotColumns = 1 << 16;

/// What a plot draws over `box` at `scale`: the background when `background`
/// is visible, then, for a line, area or bar plot, one span per device-pixel
/// column from the minimum and maximum of the slots the column covers, with
/// neighbouring columns of the same span merged; for a level meter, the
/// level bar and the peak tick. A line column also reaches the last value of
/// the column before it, so the line stays connected. The rectangle count is
/// bounded by the columns, not the slots. An invalid spec draws nothing.
[[nodiscard]] PlotGeometry plot_geometry(const PlotSpec& plot, const Color& background,
                                         const Rect& box, float scale);

/// The absolute logical areas, before the node's transform, that differ
/// between two specs that are `plot_layout_equal` drawn over the same `box`
/// at `scale`: for a line, area or bar plot, each run of columns whose spans
/// changed, from the top of its highest span to the bottom of its lowest,
/// old and new; for a level meter, the band between the old and new level
/// and the old and new peak ticks when the peak moved. New samples at the end
/// of a filling history damage only their columns; a scrolling history
/// damages every column whose span moved.
[[nodiscard]] std::vector<Rect> plot_sample_damage(const PlotSpec& before, const PlotSpec& after,
                                                   const Rect& box, float scale);

}  // namespace vellum::graphics
