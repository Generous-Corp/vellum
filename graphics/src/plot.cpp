#include "plot_testing.hpp"

#include <vellum/graphics/plot.hpp>

#include <algorithm>
#include <atomic>
#include <bit>
#include <cmath>
#include <cstddef>
#include <limits>

namespace vellum::graphics {
namespace {

std::atomic<std::uint8_t> decimation_mode{
    static_cast<std::uint8_t>(testing::PlotDecimation::min_max)};
std::atomic<bool> join_columns{true};
std::atomic<bool> previous_spans{true};
std::atomic<bool> every_changed_column{true};

constexpr double kGridLimit = 1 << 30;

bool same_float(float a, float b) noexcept {
    return std::bit_cast<std::uint32_t>(a) == std::bit_cast<std::uint32_t>(b);
}

bool same_color(const Color& a, const Color& b) noexcept {
    return same_float(a.red, b.red) && same_float(a.green, b.green) &&
           same_float(a.blue, b.blue) && same_float(a.alpha, b.alpha);
}

bool finite_color(const Color& color) noexcept {
    return std::isfinite(color.red) && std::isfinite(color.green) &&
           std::isfinite(color.blue) && std::isfinite(color.alpha);
}

std::int32_t grid_edge(double value) noexcept {
    if (!std::isfinite(value)) return 0;
    return static_cast<std::int32_t>(std::clamp(std::round(value), -kGridLimit, kGridLimit));
}

// A half-open run of device rows, or of columns for a horizontal meter.
struct Span final {
    std::int32_t begin = 0;
    std::int32_t end = 0;

    [[nodiscard]] bool empty() const noexcept { return end <= begin; }
    friend bool operator==(const Span& a, const Span& b) noexcept {
        return (a.empty() && b.empty()) || (a.begin == b.begin && a.end == b.end);
    }
};

Span unite(const Span& a, const Span& b) noexcept {
    if (a.empty()) return b;
    if (b.empty()) return a;
    return {std::min(a.begin, b.begin), std::max(a.end, b.end)};
}

// Maps values onto device pixels along one axis. `low` is where `value_min`
// lands and `high` where `value_max` does.
struct Axis final {
    std::int32_t low = 0;
    std::int32_t high = 0;
    double minimum = 0.0;
    double maximum = 1.0;

    [[nodiscard]] double fraction(float value) const noexcept {
        const double t = (static_cast<double>(value) - minimum) / (maximum - minimum);
        if (std::isnan(t)) return 0.0;
        return std::clamp(t, 0.0, 1.0);
    }
    // The pixel edge a value falls on.
    [[nodiscard]] std::int32_t edge(float value) const noexcept {
        return low + static_cast<std::int32_t>(
                         std::lround(fraction(value) * static_cast<double>(high - low)));
    }
    // The pixel a value falls in, kept inside the axis.
    [[nodiscard]] std::int32_t cell(float value) const noexcept {
        const std::int32_t lo = std::min(low, high);
        const std::int32_t hi = std::max(low, high);
        if (hi <= lo) return lo;
        const double at = static_cast<double>(low) +
                          fraction(value) * static_cast<double>(high - low);
        const auto pixel = static_cast<std::int32_t>(std::floor(at));
        return std::clamp(pixel, lo, hi - 1);
    }
};

Axis value_axis(const PlotSpec& plot, const PlotGrid& grid) noexcept {
    return {grid.bottom, grid.top, plot.value_min, plot.value_max};
}

struct Column final {
    Span fill;
    Span line;
    std::uint64_t first_slot = 0;
    std::uint64_t end_slot = 0;
};

float slot_value(const PlotSpec& plot, std::uint64_t slot) noexcept {
    return slot < plot.samples.size() ? plot.samples[slot]
                                      : std::numeric_limits<float>::quiet_NaN();
}

std::int32_t device_width(float logical, float scale) noexcept {
    return std::max<std::int32_t>(1, static_cast<std::int32_t>(std::lround(
                                         static_cast<double>(logical) * scale)));
}

Span fill_span(const Axis& axis, float low, float high, float baseline) noexcept {
    const std::int32_t base = axis.edge(baseline);
    const std::int32_t a = axis.edge(high);
    const std::int32_t b = axis.edge(low);
    return {std::min({a, b, base}), std::max({a, b, base})};
}

std::vector<Column> plot_columns(const PlotSpec& plot, const PlotGrid& grid, float scale) {
    const std::int64_t width = static_cast<std::int64_t>(grid.right) - grid.left;
    if (width <= 0 || width > kMaxPlotColumns || grid.bottom <= grid.top ||
        plot.slot_count == 0U) {
        return {};
    }
    const auto count = static_cast<std::uint64_t>(width);
    const std::uint64_t slots = plot.slot_count;
    const Axis axis = value_axis(plot, grid);
    const auto mode = static_cast<testing::PlotDecimation>(decimation_mode.load());
    std::vector<Column> columns(count);
    for (std::uint64_t c = 0; c < count; ++c) {
        columns[c].first_slot = c * slots / count;
        columns[c].end_slot = std::max(columns[c].first_slot + 1U, (c + 1U) * slots / count);
    }

    if (plot.style == PlotStyle::bar && slots <= count) {
        const std::int32_t gap = std::max<std::int32_t>(
            0, static_cast<std::int32_t>(std::lround(static_cast<double>(plot.bar_gap) * scale)));
        for (std::uint64_t slot = 0; slot < slots; ++slot) {
            const std::uint64_t begin = slot * count / slots;
            const std::uint64_t end = (slot + 1U) * count / slots;
            if (end <= begin) continue;
            const auto drawn = std::max<std::uint64_t>(
                1U, end - begin > static_cast<std::uint64_t>(gap) ? end - begin - gap : 1U);
            const float value = slot_value(plot, slot);
            for (std::uint64_t c = begin; c < end; ++c) {
                columns[c].first_slot = slot;
                columns[c].end_slot = slot + 1U;
                if (c < begin + drawn && !std::isnan(value)) {
                    columns[c].fill = fill_span(axis, value, value, plot.baseline);
                }
            }
        }
        return columns;
    }

    const std::int32_t thickness = device_width(plot.line_width, scale);
    const std::int32_t above = (thickness - 1) / 2;
    const std::int32_t below = thickness - 1 - above;
    const bool join = join_columns.load();
    bool previous = false;
    float previous_last = 0.0F;
    for (auto& column : columns) {
        bool any = false;
        float low = 0.0F;
        float high = 0.0F;
        float last = 0.0F;
        for (std::uint64_t slot = column.first_slot; slot < column.end_slot; ++slot) {
            const float value = slot_value(plot, slot);
            if (std::isnan(value)) continue;
            if (!any) {
                low = high = last = value;
                any = true;
                if (mode == testing::PlotDecimation::first_sample) break;
                continue;
            }
            low = std::min(low, value);
            high = std::max(high, value);
            last = value;
        }
        if (!any) {
            previous = false;
            continue;
        }
        if (plot.style == PlotStyle::line) {
            std::int32_t top = axis.cell(high);
            std::int32_t bottom = axis.cell(low);
            if (join && previous) {
                const std::int32_t joined = axis.cell(previous_last);
                top = std::min(top, joined);
                bottom = std::max(bottom, joined);
            }
            column.line = {std::max(grid.top, top - above), std::min(grid.bottom, bottom + below + 1)};
        } else {
            column.fill = fill_span(axis, low, high, plot.baseline);
        }
        previous = true;
        previous_last = last;
    }
    return columns;
}

Rect logical(std::int32_t left, std::int32_t top, std::int32_t right, std::int32_t bottom,
             float scale) noexcept {
    const double s = scale;
    return {static_cast<float>(left / s), static_cast<float>(top / s),
            static_cast<float>((static_cast<double>(right) - left) / s),
            static_cast<float>((static_cast<double>(bottom) - top) / s)};
}

struct Meter final {
    Span level;
    Span peak;
};

// A level meter along its growth axis: rows for a vertical meter, columns
// for a horizontal one.
Meter meter_spans(const PlotSpec& plot, const PlotGrid& grid, float scale) {
    const bool vertical = plot.orientation == PlotOrientation::vertical;
    const Axis axis = vertical ? Axis{grid.bottom, grid.top, plot.value_min, plot.value_max}
                               : Axis{grid.left, grid.right, plot.value_min, plot.value_max};
    const std::int32_t lo = std::min(axis.low, axis.high);
    const std::int32_t hi = std::max(axis.low, axis.high);
    Meter meter;
    const float level = slot_value(plot, 0U);
    if (!std::isnan(level)) {
        const std::int32_t edge = axis.edge(level);
        meter.level = {std::min(edge, axis.low), std::max(edge, axis.low)};
    }
    const float peak = slot_value(plot, 1U);
    if (!std::isnan(peak) && plot.peak_width > 0.0F && hi > lo) {
        const std::int32_t thickness = std::min(device_width(plot.peak_width, scale), hi - lo);
        const std::int32_t edge = axis.edge(peak);
        const std::int32_t start = std::clamp(edge - thickness / 2, lo, hi - thickness);
        meter.peak = {start, start + thickness};
    }
    return meter;
}

Rect meter_rect(const Span& span, const PlotSpec& plot, const PlotGrid& grid, float scale) {
    if (plot.orientation == PlotOrientation::vertical) {
        return logical(grid.left, span.begin, grid.right, span.end, scale);
    }
    return logical(span.begin, grid.top, span.end, grid.bottom, scale);
}

void merge_runs(const std::vector<Column>& columns, const PlotGrid& grid, float scale,
                bool line, std::vector<PlotRect>& output) {
    const auto span_of = [line](const Column& column) { return line ? column.line : column.fill; };
    std::size_t index = 0;
    while (index < columns.size()) {
        const Span span = span_of(columns[index]);
        if (span.empty()) {
            ++index;
            continue;
        }
        std::size_t end = index + 1U;
        while (end < columns.size() && span_of(columns[end]) == span) ++end;
        output.push_back({logical(grid.left + static_cast<std::int32_t>(index), span.begin,
                                  grid.left + static_cast<std::int32_t>(end), span.end, scale),
                          line ? PlotLayer::line : PlotLayer::fill});
        index = end;
    }
}

// One rectangle per slot, for the decimation control only.
void per_slot_geometry(const PlotSpec& plot, const PlotGrid& grid, float scale,
                       std::vector<PlotRect>& output) {
    const Axis axis = value_axis(plot, grid);
    const double width = static_cast<double>(grid.right) - grid.left;
    const double s = scale;
    for (std::uint64_t slot = 0; slot < plot.slot_count; ++slot) {
        const float value = slot_value(plot, slot);
        if (std::isnan(value)) continue;
        const double x0 = grid.left + width * static_cast<double>(slot) / plot.slot_count;
        const double x1 = grid.left + width * static_cast<double>(slot + 1U) / plot.slot_count;
        const Span span = plot.style == PlotStyle::line
                              ? Span{axis.cell(value), axis.cell(value) + 1}
                              : fill_span(axis, value, value, plot.baseline);
        output.push_back({{static_cast<float>(x0 / s), static_cast<float>(span.begin / s),
                           static_cast<float>((x1 - x0) / s),
                           static_cast<float>((span.end - span.begin) / s)},
                          plot.style == PlotStyle::line ? PlotLayer::line : PlotLayer::fill});
    }
}

std::vector<Rect> column_damage(const PlotSpec& before, const PlotSpec& after,
                                const PlotGrid& grid, float scale) {
    const auto was = plot_columns(before, grid, scale);
    const auto now = plot_columns(after, grid, scale);
    std::vector<char> changed(now.size(), 0);
    for (std::size_t c = 0; c < now.size(); ++c) {
        changed[c] = c >= was.size() || !(was[c].fill == now[c].fill) ||
                     !(was[c].line == now[c].line);
    }
    if (!every_changed_column.load()) {
        std::uint64_t newest = 0;
        bool found = false;
        for (std::uint64_t slot = after.samples.size(); slot > 0U; --slot) {
            if (!std::isnan(after.samples[slot - 1U])) {
                newest = slot - 1U;
                found = true;
                break;
            }
        }
        for (std::size_t c = 0; c < now.size(); ++c) {
            const bool covers = found && now[c].first_slot <= newest && newest < now[c].end_slot;
            if (!covers) changed[c] = 0;
        }
    }
    const bool old_spans = previous_spans.load();
    std::vector<Rect> damage;
    std::size_t index = 0;
    while (index < now.size()) {
        if (changed[index] == 0) {
            ++index;
            continue;
        }
        std::size_t end = index;
        Span rows;
        while (end < now.size() && changed[end] != 0) {
            rows = unite(rows, unite(now[end].fill, now[end].line));
            if (old_spans && end < was.size()) rows = unite(rows, unite(was[end].fill, was[end].line));
            ++end;
        }
        if (!rows.empty()) {
            damage.push_back(logical(grid.left + static_cast<std::int32_t>(index), rows.begin,
                                     grid.left + static_cast<std::int32_t>(end), rows.end, scale));
        }
        index = end;
    }
    return damage;
}

}  // namespace

namespace testing {

void set_plot_decimation(PlotDecimation decimation) noexcept {
    decimation_mode.store(static_cast<std::uint8_t>(decimation));
}

PlotDecimation plot_decimation() noexcept {
    return static_cast<PlotDecimation>(decimation_mode.load());
}

void set_plot_rules(const PlotRules& rules) noexcept {
    join_columns.store(rules.join_columns);
    previous_spans.store(rules.previous_spans);
    every_changed_column.store(rules.every_changed_column);
}

PlotRules plot_rules() noexcept {
    return {.join_columns = join_columns.load(),
            .previous_spans = previous_spans.load(),
            .every_changed_column = every_changed_column.load()};
}

}  // namespace testing

bool plot_is_valid(const PlotSpec& plot, std::string* reason) {
    const auto fail = [reason](const char* why) {
        if (reason != nullptr) *reason = why;
        return false;
    };
    switch (plot.style) {
    case PlotStyle::line:
    case PlotStyle::area:
        if (plot.shape != PlotChannelShape::scalar && plot.shape != PlotChannelShape::vector) {
            return fail("a line or area plot reads a scalar or vector channel");
        }
        break;
    case PlotStyle::bar:
        if (plot.shape != PlotChannelShape::vector) return fail("a bar plot reads a vector channel");
        break;
    case PlotStyle::level_meter:
        if (plot.shape != PlotChannelShape::meter) {
            return fail("a level meter reads a meter channel");
        }
        break;
    default:
        return fail("the plot style is unknown");
    }
    if (plot.history != PlotHistory::scroll && plot.history != PlotHistory::sweep) {
        return fail("the plot history is unknown");
    }
    if (plot.orientation != PlotOrientation::vertical &&
        plot.orientation != PlotOrientation::horizontal) {
        return fail("the plot orientation is unknown");
    }
    if (plot.style == PlotStyle::level_meter) {
        if (plot.samples.size() > 2U) return fail("a level meter holds a level and a peak");
    } else {
        if (plot.slot_count == 0U || plot.slot_count > kMaxPlotSlots) {
            return fail("the slot count is not between 1 and kMaxPlotSlots");
        }
        if (plot.samples.size() > plot.slot_count) return fail("there are more samples than slots");
    }
    if (!std::isfinite(plot.value_min) || !std::isfinite(plot.value_max) ||
        plot.value_min == plot.value_max) {
        return fail("the value range is not finite or is empty");
    }
    if (!std::isfinite(plot.baseline)) return fail("the baseline is not finite");
    if (!finite_color(plot.line_color) || !finite_color(plot.fill_color) ||
        !finite_color(plot.peak_color)) {
        return fail("a plot colour is not finite");
    }
    if (!std::isfinite(plot.line_width) || plot.line_width < 0.0F ||
        !std::isfinite(plot.bar_gap) || plot.bar_gap < 0.0F ||
        !std::isfinite(plot.peak_width) || plot.peak_width < 0.0F) {
        return fail("a plot width or gap is negative or not finite");
    }
    return true;
}

const SceneNode* find_invalid_plot(const SceneNode& node, std::string& reason) {
    if (node.kind == SceneNode::Kind::plot) {
        if (!node.plot) {
            reason = "it has no plot spec";
            return &node;
        }
        if (!node.children.empty()) {
            reason = "a plot node has no children";
            return &node;
        }
        if (!plot_is_valid(*node.plot, &reason)) return &node;
    }
    for (const auto& child : node.children) {
        if (const auto* found = find_invalid_plot(child, reason); found != nullptr) return found;
    }
    return nullptr;
}

bool plot_layout_equal(const PlotSpec& a, const PlotSpec& b) noexcept {
    return a.style == b.style && a.channel == b.channel && a.shape == b.shape &&
           a.audio_channel == b.audio_channel && a.slot_count == b.slot_count &&
           a.history == b.history && a.orientation == b.orientation &&
           same_float(a.value_min, b.value_min) && same_float(a.value_max, b.value_max) &&
           same_float(a.baseline, b.baseline) && same_color(a.line_color, b.line_color) &&
           same_color(a.fill_color, b.fill_color) && same_color(a.peak_color, b.peak_color) &&
           same_float(a.line_width, b.line_width) && same_float(a.bar_gap, b.bar_gap) &&
           same_float(a.peak_width, b.peak_width);
}

bool plot_specs_equal(const PlotSpec& a, const PlotSpec& b) noexcept {
    return plot_layout_equal(a, b) &&
           std::equal(a.samples.begin(), a.samples.end(), b.samples.begin(), b.samples.end(),
                      [](float left, float right) { return same_float(left, right); });
}

PlotGrid plot_grid(const Rect& box, float scale) noexcept {
    const double s = scale;
    const double x = box.x;
    const double y = box.y;
    if (!std::isfinite(s) || !(s > 0.0)) return {};
    PlotGrid grid{grid_edge(x * s), grid_edge(y * s),
                  grid_edge((x + std::max(0.0F, box.width)) * s),
                  grid_edge((y + std::max(0.0F, box.height)) * s)};
    grid.right = std::max(grid.right, grid.left);
    grid.bottom = std::max(grid.bottom, grid.top);
    return grid;
}

Rect plot_grid_rect(const Rect& box, float scale) noexcept {
    const auto grid = plot_grid(box, scale);
    if (!std::isfinite(scale) || !(scale > 0.0F)) return {box.x, box.y, 0.0F, 0.0F};
    return logical(grid.left, grid.top, grid.right, grid.bottom, scale);
}

PlotGeometry plot_geometry(const PlotSpec& plot, const Color& background, const Rect& box,
                           float scale) {
    PlotGeometry geometry;
    if (!plot_is_valid(plot) || !std::isfinite(scale) || !(scale > 0.0F)) return geometry;
    const auto grid = plot_grid(box, scale);
    geometry.columns = static_cast<std::uint32_t>(grid.right - grid.left);
    geometry.rows = static_cast<std::uint32_t>(grid.bottom - grid.top);
    if (geometry.columns == 0U || geometry.rows == 0U) return geometry;
    if (finite_color(background) && background.alpha > 0.0F) {
        geometry.rects.push_back(
            {logical(grid.left, grid.top, grid.right, grid.bottom, scale), PlotLayer::background});
    }
    if (plot.style == PlotStyle::level_meter) {
        const auto meter = meter_spans(plot, grid, scale);
        if (!meter.level.empty()) {
            geometry.rects.push_back({meter_rect(meter.level, plot, grid, scale), PlotLayer::fill});
        }
        if (!meter.peak.empty()) {
            geometry.rects.push_back({meter_rect(meter.peak, plot, grid, scale), PlotLayer::peak});
        }
        return geometry;
    }
    if (testing::plot_decimation() == testing::PlotDecimation::per_slot) {
        per_slot_geometry(plot, grid, scale, geometry.rects);
        return geometry;
    }
    const auto columns = plot_columns(plot, grid, scale);
    merge_runs(columns, grid, scale, false, geometry.rects);
    merge_runs(columns, grid, scale, true, geometry.rects);
    return geometry;
}

std::vector<Rect> plot_sample_damage(const PlotSpec& before, const PlotSpec& after,
                                     const Rect& box, float scale) {
    if (!std::isfinite(scale) || !(scale > 0.0F)) return {};
    const auto grid = plot_grid(box, scale);
    if (grid.right <= grid.left || grid.bottom <= grid.top) return {};
    if (!plot_is_valid(before) || !plot_is_valid(after) || !plot_layout_equal(before, after)) {
        return {logical(grid.left, grid.top, grid.right, grid.bottom, scale)};
    }
    if (after.style != PlotStyle::level_meter) return column_damage(before, after, grid, scale);
    const auto was = meter_spans(before, grid, scale);
    const auto now = meter_spans(after, grid, scale);
    const bool old_spans = previous_spans.load();
    std::vector<Rect> damage;
    if (!(was.level == now.level)) {
        Span band = now.level;
        if (old_spans) {
            // The two levels share their start, so what changed is the part
            // of the longer one past the shorter one.
            if (was.level.empty() || now.level.empty()) {
                band = unite(was.level, now.level);
            } else if (was.level.begin == now.level.begin) {
                band = {std::min(was.level.end, now.level.end), std::max(was.level.end, now.level.end)};
            } else {
                band = {std::min(was.level.begin, now.level.begin),
                        std::max(was.level.begin, now.level.begin)};
            }
        }
        if (!band.empty()) damage.push_back(meter_rect(band, after, grid, scale));
    }
    if (!(was.peak == now.peak)) {
        if (!now.peak.empty()) damage.push_back(meter_rect(now.peak, after, grid, scale));
        if (old_spans && !was.peak.empty()) damage.push_back(meter_rect(was.peak, after, grid, scale));
    }
    return damage;
}

}  // namespace vellum::graphics
