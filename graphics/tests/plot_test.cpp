#include "../src/plot_testing.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/plot_feed.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/value/in_memory_value_feed.hpp>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <span>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace hooks = vellum::graphics::testing;
using vellum::value::ChannelShape;
using vellum::value::InMemoryValueFeed;

int failures = 0;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

constexpr float kNaN = std::numeric_limits<float>::quiet_NaN();

struct Override final {
    Override(hooks::PlotDecimation decimation, hooks::PlotRules rules)
        : saved_decimation(hooks::plot_decimation()), saved_rules(hooks::plot_rules()) {
        hooks::set_plot_decimation(decimation);
        hooks::set_plot_rules(rules);
    }
    ~Override() {
        hooks::set_plot_decimation(saved_decimation);
        hooks::set_plot_rules(saved_rules);
    }
    Override(const Override&) = delete;
    Override& operator=(const Override&) = delete;
    hooks::PlotDecimation saved_decimation;
    hooks::PlotRules saved_rules;
};

PlotSpec line_spec(std::uint32_t slots, std::vector<float> samples) {
    PlotSpec spec;
    spec.style = PlotStyle::line;
    spec.shape = PlotChannelShape::vector;
    spec.channel = "scope";
    spec.slot_count = slots;
    spec.samples = std::move(samples);
    return spec;
}

std::string rects(const PlotGeometry& geometry) {
    std::ostringstream out;
    for (const auto& piece : geometry.rects) {
        out << '[' << static_cast<int>(piece.layer) << ' ' << piece.rect.x << ',' << piece.rect.y
            << ' ' << piece.rect.width << 'x' << piece.rect.height << ']';
    }
    return out.str();
}

// The rows a layer covers in one device column of the geometry, as a mask.
std::vector<bool> column_rows(const PlotGeometry& geometry, PlotLayer layer, float column,
                              const Rect& box, float scale) {
    std::vector<bool> rows(geometry.rows, false);
    const float x = (box.x * scale + column + 0.5F) / scale;
    for (const auto& piece : geometry.rects) {
        if (piece.layer != layer || x < piece.rect.x || x >= piece.rect.x + piece.rect.width) {
            continue;
        }
        for (std::uint32_t row = 0; row < geometry.rows; ++row) {
            const float y = (box.y * scale + static_cast<float>(row) + 0.5F) / scale;
            if (y >= piece.rect.y && y < piece.rect.y + piece.rect.height) rows[row] = true;
        }
    }
    return rows;
}

int first_row(const std::vector<bool>& rows) {
    for (std::size_t row = 0; row < rows.size(); ++row) {
        if (rows[row]) return static_cast<int>(row);
    }
    return -1;
}

int row_count(const std::vector<bool>& rows) {
    int count = 0;
    for (const bool row : rows) count += row ? 1 : 0;
    return count;
}

void line_oracle() {
    const Rect box{10.0F, 20.0F, 8.0F, 10.0F};
    // Eight slots over eight columns: each column is one slot. Values map
    // 0 to the bottom row (9) and 1 to the top row (0).
    const auto spec = line_spec(8, {0.0F, 0.0F, 1.0F, 1.0F, 0.5F, kNaN, 0.5F, 0.5F});
    const auto geometry = plot_geometry(spec, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(geometry.columns == 8U && geometry.rows == 10U, "a plot grid is one cell per device pixel");
    const auto rows_at = [&](int column) {
        return column_rows(geometry, PlotLayer::line, static_cast<float>(column), box, 1.0F);
    };
    expect(first_row(rows_at(0)) == 9 && row_count(rows_at(0)) == 1,
           "a minimum sample draws the bottom row", rects(geometry));
    expect(first_row(rows_at(2)) == 0 && row_count(rows_at(2)) == 10,
           "a rise joins the previous column's value, so the line is connected");
    expect(first_row(rows_at(3)) == 0 && row_count(rows_at(3)) == 1,
           "a maximum sample draws the top row");
    expect(first_row(rows_at(4)) == 0 && row_count(rows_at(4)) == 6,
           "a fall to the middle reaches up to the previous value");
    expect(row_count(rows_at(5)) == 0, "a NaN slot draws nothing");
    expect(first_row(rows_at(6)) == 5 && row_count(rows_at(6)) == 1,
           "the line restarts after a gap without a join across it");
    {
        Override off(hooks::PlotDecimation::min_max, {.join_columns = false});
        const auto loose = plot_geometry(spec, Color::rgba(0, 0, 0, 0), box, 1.0F);
        expect(row_count(column_rows(loose, PlotLayer::line, 2.0F, box, 1.0F)) == 1,
               "negative control: without the join a rise leaves a gap in the line");
    }
    auto thick = spec;
    thick.line_width = 3.0F;
    const auto heavy = plot_geometry(thick, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(row_count(column_rows(heavy, PlotLayer::line, 3.0F, box, 1.0F)) == 2,
           "a three pixel line at the top edge keeps the rows inside the box");
    expect(row_count(column_rows(heavy, PlotLayer::line, 7.0F, box, 1.0F)) == 3,
           "a three pixel line is three rows thick");
    const auto doubled = plot_geometry(spec, Color::rgba(0, 0, 0, 0), box, 2.0F);
    expect(doubled.columns == 16U && doubled.rows == 20U, "a scale of two doubles the grid");
}

// A dense feed decimates by minimum and maximum, so a one-sample spike
// survives in its column; a first-sample decimation loses it.
void decimated_peak_oracle() {
    const Rect box{0.0F, 0.0F, 10.0F, 20.0F};
    std::vector<float> samples(1000, 0.25F);
    samples[537] = 1.0F;
    samples[912] = 0.0F;
    const auto spec = line_spec(1000, samples);
    const auto geometry = plot_geometry(spec, Color::rgba(0, 0, 0, 0), box, 1.0F);
    const auto spike = column_rows(geometry, PlotLayer::line, 5.0F, box, 1.0F);
    const auto dip = column_rows(geometry, PlotLayer::line, 9.0F, box, 1.0F);
    const auto quiet = column_rows(geometry, PlotLayer::line, 3.0F, box, 1.0F);
    expect(first_row(spike) == 0, "the column holding slot 537 reaches the top row", rects(geometry));
    expect(dip.back(), "the column holding slot 912 reaches the bottom row");
    expect(first_row(quiet) == 15 && row_count(quiet) == 1,
           "a quiet column draws only its constant value");
    expect(geometry.rects.size() <= 2U * geometry.columns,
           "decimated geometry is bounded by the columns");
    Override off(hooks::PlotDecimation::first_sample, {});
    const auto sampled = plot_geometry(spec, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(first_row(column_rows(sampled, PlotLayer::line, 5.0F, box, 1.0F)) == 15,
           "negative control: first-sample decimation drops the spike");
}

void area_bar_and_meter_oracles() {
    const Rect box{0.0F, 0.0F, 8.0F, 10.0F};
    PlotSpec area = line_spec(8, {0.5F, 0.5F, 0.0F, 1.0F, 0.2F, 0.2F, 0.2F, 0.2F});
    area.style = PlotStyle::area;
    area.baseline = 0.0F;
    auto geometry = plot_geometry(area, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(first_row(column_rows(geometry, PlotLayer::fill, 0.0F, box, 1.0F)) == 5 &&
               row_count(column_rows(geometry, PlotLayer::fill, 0.0F, box, 1.0F)) == 5,
           "an area at half fills the lower half", rects(geometry));
    expect(row_count(column_rows(geometry, PlotLayer::fill, 2.0F, box, 1.0F)) == 0,
           "an area at its baseline fills nothing");
    expect(row_count(column_rows(geometry, PlotLayer::fill, 3.0F, box, 1.0F)) == 10,
           "an area at the maximum fills the column");
    area.baseline = 0.5F;
    geometry = plot_geometry(area, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(first_row(column_rows(geometry, PlotLayer::fill, 2.0F, box, 1.0F)) == 5 &&
               row_count(column_rows(geometry, PlotLayer::fill, 2.0F, box, 1.0F)) == 5,
           "an area below a raised baseline fills down from it");

    PlotSpec bar = line_spec(4, {1.0F, 0.5F, 0.0F, 0.3F});
    bar.style = PlotStyle::bar;
    bar.bar_gap = 1.0F;
    geometry = plot_geometry(bar, Color::hex(0x101010), box, 1.0F);
    expect(!geometry.rects.empty() && geometry.rects.front().layer == PlotLayer::background,
           "a visible background is drawn first");
    expect(row_count(column_rows(geometry, PlotLayer::fill, 0.0F, box, 1.0F)) == 10 &&
               row_count(column_rows(geometry, PlotLayer::fill, 1.0F, box, 1.0F)) == 0,
           "a two-column bar with a one-pixel gap fills one column", rects(geometry));
    expect(row_count(column_rows(geometry, PlotLayer::fill, 2.0F, box, 1.0F)) == 5 &&
               row_count(column_rows(geometry, PlotLayer::fill, 6.0F, box, 1.0F)) == 3,
           "each bar rises to its own value");

    PlotSpec meter;
    meter.style = PlotStyle::level_meter;
    meter.shape = PlotChannelShape::meter;
    meter.samples = {0.5F, 0.9F};
    meter.peak_width = 1.0F;
    geometry = plot_geometry(meter, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(geometry.rects.size() == 2U && geometry.rects[0].layer == PlotLayer::fill &&
               geometry.rects[0].rect.y == 5.0F && geometry.rects[0].rect.height == 5.0F &&
               geometry.rects[1].layer == PlotLayer::peak && geometry.rects[1].rect.y == 1.0F &&
               geometry.rects[1].rect.height == 1.0F && geometry.rects[1].rect.width == 8.0F,
           "a vertical meter fills to its level and ticks at its peak", rects(geometry));
    meter.orientation = PlotOrientation::horizontal;
    geometry = plot_geometry(meter, Color::rgba(0, 0, 0, 0), box, 1.0F);
    expect(geometry.rects.size() == 2U && geometry.rects[0].rect.x == 0.0F &&
               geometry.rects[0].rect.width == 4.0F && geometry.rects[0].rect.height == 10.0F &&
               geometry.rects[1].rect.x == 7.0F,
           "a horizontal meter grows rightwards", rects(geometry));
}

constexpr float kFrameWidth = 400.0F;
constexpr float kFrameHeight = 300.0F;
const Rect kPlotBox{50.0F, 40.0F, 100.0F, 60.0F};

Scene plot_scene(PlotSpec spec, std::uint32_t version = kSceneVersion) {
    Scene scene;
    scene.width = kFrameWidth;
    scene.height = kFrameHeight;
    scene.scene_version = version;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kFrameWidth, kFrameHeight}};
    scene.root.children.push_back({.id = "other", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {250.0F, 200.0F, 40.0F, 30.0F},
                                   .fill = Color::hex(0x334155)});
    SceneNode plot{.id = "plot", .kind = SceneNode::Kind::plot, .bounds = kPlotBox,
                   .fill = Color::hex(0x0F172A)};
    plot.plot = std::make_shared<PlotSpec>(std::move(spec));
    scene.root.children.push_back(std::move(plot));
    return scene;
}

Scene with_samples(const Scene& scene, std::vector<float> samples) {
    Scene next = scene;
    auto spec = std::make_shared<PlotSpec>(*next.root.children[1].plot);
    spec->samples = std::move(samples);
    next.root.children[1].plot = std::move(spec);
    return next;
}

std::string bounds_of(const DamageRegion& region) {
    const auto b = region.bounds();
    std::ostringstream out;
    out << b.left << ',' << b.top << ' ' << b.right << ',' << b.bottom << " area " << region.area()
        << " rects " << region.rects().size();
    return out.str();
}

bool inside_plot(const DamageRegion& region, float scale) {
    const auto b = region.bounds();
    const std::int32_t margin = 1;
    return b.left >= static_cast<std::int32_t>(kPlotBox.x * scale) - margin &&
           b.top >= static_cast<std::int32_t>(kPlotBox.y * scale) - margin &&
           b.right <= static_cast<std::int32_t>((kPlotBox.x + kPlotBox.width) * scale) + margin &&
           b.bottom <= static_cast<std::int32_t>((kPlotBox.y + kPlotBox.height) * scale) + margin;
}

std::vector<float> ramp(std::size_t count, std::size_t offset = 0) {
    std::vector<float> values(count);
    for (std::size_t index = 0; index < count; ++index) {
        values[index] = 0.5F + 0.4F * std::sin(static_cast<float>(index + offset) * 0.37F);
    }
    return values;
}

void damage_per_update() {
    auto spec = line_spec(100, ramp(40));
    spec.shape = PlotChannelShape::scalar;
    const Scene filling = plot_scene(spec);
    auto grown = ramp(41);
    const Scene appended = with_samples(filling, grown);
    for (const float scale : {1.0F, 2.0F}) {
        const auto damage = compute_scene_damage(filling, appended, scale);
        const auto b = damage.bounds();
        const std::int32_t column = static_cast<std::int32_t>((kPlotBox.x + 40.0F) * scale);
        expect(!damage.empty() && inside_plot(damage, scale) && b.left >= column - 1 &&
                   b.right <= column + static_cast<std::int32_t>(scale) + 1,
               "@" + std::to_string(static_cast<int>(scale)) +
                   " a sample appended to a filling history damages only its column",
               bounds_of(damage));
    }

    std::vector<float> full = ramp(100);
    std::vector<float> shifted = ramp(100, 1);
    const Scene before = with_samples(filling, full);
    const Scene scrolled = with_samples(filling, shifted);
    const auto scroll = compute_scene_damage(before, scrolled, 1.0F);
    expect(inside_plot(scroll, 1.0F) && scroll.bounds().width() >= 98,
           "a scrolled history damages the scroll region, every column that moved",
           bounds_of(scroll));
    {
        Override off(hooks::PlotDecimation::min_max, {.every_changed_column = false});
        const auto newest = compute_scene_damage(before, scrolled, 1.0F);
        expect(newest.bounds().width() <= 3,
               "negative control: damaging only the newest column misses the scroll region",
               bounds_of(newest));
    }

    std::vector<float> high = full;
    std::vector<float> low = full;
    high[60] = 1.0F;
    low[60] = 0.0F;
    const auto drop = compute_scene_damage(with_samples(filling, high), with_samples(filling, low),
                                           1.0F);
    const auto top = static_cast<std::int32_t>(kPlotBox.y);
    const auto bottom = static_cast<std::int32_t>(kPlotBox.y + kPlotBox.height);
    expect(drop.bounds().top <= top && drop.bounds().bottom >= bottom &&
               drop.bounds().width() <= 6,
           "a peak that falls damages its old and new spans in its columns", bounds_of(drop));
    {
        Override off(hooks::PlotDecimation::min_max, {.previous_spans = false});
        const auto fresh = compute_scene_damage(with_samples(filling, high),
                                                with_samples(filling, low), 1.0F);
        expect(fresh.bounds().top > top + 5,
               "negative control: without the old spans a fallen peak's top is not damaged",
               bounds_of(fresh));
    }

    const auto same = compute_scene_damage(before, with_samples(filling, full), 1.0F);
    expect(same.empty(), "a new spec with the same samples damages nothing", bounds_of(same));

    Scene recoloured = before;
    auto layout = std::make_shared<PlotSpec>(*recoloured.root.children[1].plot);
    layout->line_color = Color::hex(0xFF0000);
    recoloured.root.children[1].plot = layout;
    const auto restyle = compute_scene_damage(before, recoloured, 1.0F);
    expect(restyle.bounds().width() >= 100 && restyle.bounds().height() >= 60,
           "a layout change damages the whole plot", bounds_of(restyle));

    spec.history = PlotHistory::sweep;
    std::vector<float> ring = ramp(100);
    ring[31] = kNaN;
    std::vector<float> wrote = ring;
    wrote[31] = 0.9F;
    wrote[32] = kNaN;
    const Scene sweep = plot_scene(spec);
    const auto swept = compute_scene_damage(with_samples(sweep, ring), with_samples(sweep, wrote),
                                            1.0F);
    expect(inside_plot(swept, 1.0F) && swept.bounds().width() <= 5,
           "a sweep write damages the written slot and the gap after it", bounds_of(swept));

    PlotSpec meter;
    meter.style = PlotStyle::level_meter;
    meter.shape = PlotChannelShape::meter;
    meter.peak_width = 0.0F;
    meter.samples = {0.8F, kNaN};
    const Scene loud = plot_scene(meter);
    const auto quieter = compute_scene_damage(loud, with_samples(loud, {0.5F, kNaN}), 1.0F);
    const auto band_top = static_cast<std::int32_t>(kPlotBox.y + kPlotBox.height * 0.2F);
    const auto band_bottom = static_cast<std::int32_t>(kPlotBox.y + kPlotBox.height * 0.5F);
    expect(quieter.bounds().top >= band_top - 1 && quieter.bounds().bottom <= band_bottom + 1 &&
               quieter.bounds().top <= band_top && quieter.bounds().bottom >= band_bottom,
           "a falling level damages only the band between the levels", bounds_of(quieter));
    {
        Override off(hooks::PlotDecimation::min_max, {.previous_spans = false});
        const auto fresh = compute_scene_damage(loud, with_samples(loud, {0.5F, kNaN}), 1.0F);
        expect(fresh.bounds().top > band_top + 5,
               "negative control: without the old level the vacated band is not damaged",
               bounds_of(fresh));
    }

    Scene turned = before;
    turned.root.children[1].transform = Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    const auto mapped = compute_scene_damage(turned, with_samples(turned, high), 1.0F);
    expect(!mapped.empty() && mapped.bounds().height() <= 6,
           "sample damage on a turned plot maps through its transform", bounds_of(mapped));
}

void versioning() {
    static_assert(kSceneVersion == 12U);
    static_assert(kDamageModelSceneVersion == 12U);
    const auto spec = line_spec(10, {0.5F});
    const Scene current = plot_scene(spec);
    expect(accept_scene(current).accepted, "a version 12 scene with a plot is accepted");
    for (std::uint32_t version = 6U; version <= 11U; ++version) {
        const Scene older = plot_scene(spec, version);
        const auto refused = accept_scene(older);
        expect(!refused.accepted && refused.diagnostics.size() == 1U &&
                   refused.diagnostics[0].code == kSceneVersionMismatchCode &&
                   refused.diagnostics[0].node_id == "plot" &&
                   refused.reason.find("plot node") != std::string::npos,
               "a version " + std::to_string(version) + " scene with a plot is refused",
               refused.reason);
        expect(make_paint_commands(older).empty(), "a refused plot scene yields no commands");
        Scene legal = older;
        legal.root.children.pop_back();
        auto& other = legal.root.children[0];
        other.opacity = 0.5F;
        other.transform = Affine2D{.tx = 2.0F};
        other.corner_radii = CornerRadii{1.0F, 2.0F, 3.0F, 4.0F};
        other.isolate = true;
        other.clip = NodeClip{.rect = {0.0F, 0.0F, 10.0F, 10.0F}};
        auto path = std::make_shared<PathGeometry>();
        path->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close};
        path->values = {0.0F, 0.0F, 5.0F, 0.0F, 5.0F, 5.0F};
        legal.root.children.push_back({.id = "path", .kind = SceneNode::Kind::path,
                                       .bounds = {10.0F, 10.0F, 5.0F, 5.0F},
                                       .fill = Color::hex(0xFFFFFF), .path = path});
        const auto kept = accept_scene(legal);
        expect(kept.accepted, "a version " + std::to_string(version) +
                                  " scene keeps every field older versions allow",
               kept.reason);
    }
}

// A plot with a radial gradient background needs both features: it is
// accepted at 12, refused at 11 for the plot and at 7 for the gradient, and
// a plain linear gradient adds no requirement of its own.
void plot_with_gradient_fill() {
    Scene scene = plot_scene(line_spec(4, {0.5F}));
    auto& plot = scene.root.children[1];
    plot.fill_gradients = {{.kind = GradientKind::radial,
                            .stops = {{.position = 0.0F, .color = Color::hex(0x111827)},
                                      {.position = 1.0F, .color = Color::hex(0x334155)}}}};
    expect(accept_scene(scene).accepted, "a plot with a radial gradient fill is accepted at 12");
    const auto commands = make_paint_commands(scene);
    expect(commands.size() == 3U && commands[1].kind == PaintCommand::Kind::rectangle &&
               commands[1].node_id == "plot/background" && commands[1].fill_gradients.size() == 1U &&
               commands[2].kind == PaintCommand::Kind::plot && commands[2].fill.alpha == 0.0F,
           "a gradient plot background lowers to a rectangle behind the plot");
    const auto refused_at = [&](std::uint32_t version, const std::string& why) {
        Scene older = scene;
        older.scene_version = version;
        const auto result = accept_scene(older);
        return !result.accepted && result.diagnostics.size() == 1U &&
               result.diagnostics[0].code == kSceneVersionMismatchCode &&
               result.reason.find(why) != std::string::npos;
    };
    expect(refused_at(11U, "plot node"), "the gradient plot at version 11 is refused for the plot");
    expect(refused_at(7U, "gradient vocabulary"),
           "the gradient plot at version 7 is refused for the gradient");
    plot.fill_gradients[0].kind = GradientKind::linear;
    expect(accept_scene(scene).accepted && refused_at(11U, "plot node") &&
               refused_at(7U, "plot node"),
           "a plain linear gradient fill adds no version requirement to a plot");
    Scene recoloured = scene;
    recoloured.root.children[1].fill_gradients[0].angle_degrees = 90.0F;
    const auto damage = compute_scene_damage(scene, recoloured, 1.0F);
    expect(damage.bounds().width() >= 100 && damage.bounds().height() >= 60,
           "a change to a plot's gradient background damages the whole plot", bounds_of(damage));
}

// A blended plot needs both features: accepted at 12, refused at 11 for the
// plot and at 5 for the blend mode, and drawn through a blended layer.
void plot_with_blend_mode() {
    Scene scene = plot_scene(line_spec(4, {0.5F}));
    scene.root.children[1].blend_mode = BlendMode::multiply;
    expect(accept_scene(scene).accepted, "a multiplied plot is accepted at 12");
    const auto commands = make_paint_commands(scene);
    expect(commands.size() == 4U && commands[1].kind == PaintCommand::Kind::begin_layer &&
               commands[1].blend_mode == BlendMode::multiply &&
               commands[2].kind == PaintCommand::Kind::plot,
           "a multiplied plot is drawn through a blended layer");
    const auto refused_at = [&](std::uint32_t version, const std::string& why) {
        Scene older = scene;
        older.scene_version = version;
        const auto result = accept_scene(older);
        return !result.accepted && result.reason.find(why) != std::string::npos;
    };
    expect(refused_at(11U, "plot node"), "the multiplied plot at version 11 is refused for the plot");
    expect(refused_at(5U, "blend mode"), "the multiplied plot at version 5 is refused for the blend");
}

// A plot inside a blurred group needs both features: accepted at 12, refused
// at 11 for the plot and at 9 for the effect. Its sample damage grows by the
// blur's radius, since the blur spreads every changed pixel.
void plot_under_effects() {
    Scene scene = plot_scene(line_spec(100, ramp(100)));
    SceneNode group{.id = "fx", .kind = SceneNode::Kind::group,
                    .bounds = {0.0F, 0.0F, kFrameWidth, kFrameHeight}};
    group.effects = {{.kind = EffectKind::blur, .sigma = 3.0F}};
    group.children.push_back(scene.root.children[1]);
    scene.root.children[1] = group;
    expect(accept_scene(scene).accepted, "a plot inside a blurred group is accepted at 12");
    const auto refused_at = [&](std::uint32_t version, const std::string& why) {
        Scene older = scene;
        older.scene_version = version;
        const auto result = accept_scene(older);
        return !result.accepted && result.reason.find(why) != std::string::npos;
    };
    expect(refused_at(11U, "plot node"), "the blurred plot at version 11 is refused for the plot");
    expect(refused_at(9U, "effects"), "the blurred plot at version 9 is refused for the effect");
    Scene next = scene;
    auto& plot = next.root.children[1].children[0];
    auto spec = std::make_shared<PlotSpec>(*plot.plot);
    spec->samples[50] = 1.0F;
    plot.plot = spec;
    const auto grown = compute_scene_damage(scene, next, 1.0F);
    const float radius = effect_chain_cost(group.effects).total.radius;
    expect(radius > 1.0F && grown.bounds().width() >= static_cast<std::int64_t>(2.0F * radius),
           "sample damage under a blur grows by the blur's radius", bounds_of(grown));
    Override off(hooks::PlotDecimation::min_max, {.grow_by_effects = false});
    const auto narrow = compute_scene_damage(scene, next, 1.0F);
    expect(narrow.bounds().width() < grown.bounds().width(),
           "negative control: without the effect radius the damage is narrower", bounds_of(narrow));
}

void invalid_plots() {
    const auto refused = [](const std::string& name, PlotSpec spec, const std::string& why) {
        const auto result = accept_scene(plot_scene(std::move(spec)));
        expect(!result.accepted && result.diagnostics.size() == 1U &&
                   result.diagnostics[0].code == kInvalidPlotCode &&
                   result.reason.find(why) != std::string::npos,
               name + " is refused", result.reason);
    };
    auto spec = line_spec(10, {});
    spec.slot_count = 0U;
    refused("a plot without slots", spec, "slot count");
    spec = line_spec(2, {1.0F, 2.0F, 3.0F});
    refused("a plot with more samples than slots", spec, "more samples");
    spec = line_spec(2, {});
    spec.shape = PlotChannelShape::meter;
    refused("a line plot of a meter channel", spec, "scalar or vector");
    spec = line_spec(2, {});
    spec.value_max = spec.value_min;
    refused("an empty value range", spec, "value range");
    spec = line_spec(2, {});
    spec.line_width = -1.0F;
    refused("a negative line width", spec, "width");
    Scene bare = plot_scene(line_spec(2, {}));
    bare.root.children[1].plot.reset();
    const auto missing = accept_scene(bare);
    expect(!missing.accepted && missing.diagnostics.size() == 1U &&
               missing.diagnostics[0].code == kInvalidPlotCode,
           "a plot node without a spec is refused", missing.reason);
    Scene parent = plot_scene(line_spec(2, {}));
    parent.root.children[1].children.push_back({.id = "child"});
    expect(!accept_scene(parent).accepted, "a plot node with children is refused");
}

std::shared_ptr<const PlotSpec> plot_of(const Scene& scene) { return scene.root.children[1].plot; }

void sampler() {
    InMemoryValueFeed feed({{.name = "level", .shape = ChannelShape::scalar, .neutral = 0.0F},
                            {.name = "scope", .shape = ChannelShape::vector, .neutral = 0.5F,
                             .capacity = 8},
                            {.name = "meter", .shape = ChannelShape::meter, .neutral = -1.0F,
                             .capacity = 2}});
    auto history = line_spec(4, {});
    history.channel = "level";
    history.shape = PlotChannelShape::scalar;
    Scene scene = plot_scene(history);
    PlotFeedSampler sampler({.stale_after_ticks = 3});
    auto report = sampler.sample(feed, scene);
    expect(report.updated.empty() && plot_of(scene)->samples.empty(),
           "a channel never published draws nothing");
    feed.publish_scalar(0, 0.1F);
    report = sampler.sample(feed, scene);
    expect(report.updated.size() == 1U && plot_of(scene)->samples.size() == 4U &&
               plot_of(scene)->samples[0] == 0.1F && std::isnan(plot_of(scene)->samples[1]),
           "a scalar publish fills the first slot");
    const auto held = plot_of(scene);
    report = sampler.sample(feed, scene);
    expect(report.updated.empty() && plot_of(scene) == held,
           "a tick without a publish keeps the same spec");
    feed.publish_scalar(0, 0.2F);
    feed.publish_scalar(0, 0.3F);
    sampler.sample(feed, scene);
    expect(plot_of(scene)->samples[1] == 0.3F && std::isnan(plot_of(scene)->samples[2]),
           "several publishes between ticks are one sample, the latest");
    feed.publish_scalar(0, 0.3F);
    sampler.sample(feed, scene);
    expect(plot_of(scene)->samples[2] == 0.3F, "a value held steady still scrolls");
    feed.publish_scalar(0, 0.4F);
    sampler.sample(feed, scene);
    feed.publish_scalar(0, 0.5F);
    sampler.sample(feed, scene);
    const std::vector<float> scrolled{0.3F, 0.3F, 0.4F, 0.5F};
    expect(plot_of(scene)->samples == scrolled, "a full history scrolls left");

    Scene removed = scene;
    const SceneNode kept_plot = removed.root.children.back();
    removed.root.children.pop_back();
    sampler.sample(feed, removed);
    removed.root.children.push_back(kept_plot);
    feed.publish_scalar(0, 0.9F);
    sampler.sample(feed, removed);
    expect(plot_of(removed)->samples[0] == 0.9F && std::isnan(plot_of(removed)->samples[1]),
           "a plot that left the scene starts its history afresh when it returns");

    auto ring = history;
    ring.history = PlotHistory::sweep;
    Scene sweep = plot_scene(ring);
    PlotFeedSampler sweeper;
    for (int tick = 0; tick < 5; ++tick) {
        feed.publish_scalar(0, static_cast<float>(tick));
        sweeper.sample(feed, sweep);
    }
    const auto& swept = plot_of(sweep)->samples;
    expect(swept[0] == 4.0F && std::isnan(swept[1]) && swept[2] == 2.0F && swept[3] == 3.0F,
           "a sweep overwrites the oldest slot and leaves a gap after the newest");

    auto scope = line_spec(8, {});
    Scene vectors = plot_scene(scope);
    const std::vector<float> block{0.1F, 0.2F, 0.3F};
    feed.publish_vector(1, block);
    sampler.sample(feed, vectors);
    expect(plot_of(vectors)->samples.size() == 8U && plot_of(vectors)->samples[2] == 0.3F &&
               std::isnan(plot_of(vectors)->samples[3]),
           "a vector block fills the slots from slot 0");
    for (int tick = 0; tick < 2; ++tick) sampler.sample(feed, vectors);
    expect(plot_of(vectors)->samples[0] == 0.1F, "a channel idle for fewer ticks than stale holds");
    sampler.sample(feed, vectors);
    expect(plot_of(vectors)->samples[0] == 0.5F && plot_of(vectors)->samples[7] == 0.5F,
           "a stale vector channel shows its neutral value");
    PlotFeedSampler patient({.stale_after_ticks = 0});
    Scene kept = plot_scene(scope);
    feed.publish_vector(1, block);
    patient.sample(feed, kept);
    for (int tick = 0; tick < 20; ++tick) patient.sample(feed, kept);
    expect(plot_of(kept)->samples[0] == 0.1F,
           "negative control: with staleness off the channel never falls to neutral");

    PlotSpec meter;
    meter.style = PlotStyle::level_meter;
    meter.shape = PlotChannelShape::meter;
    meter.channel = "meter";
    meter.audio_channel = 1;
    Scene metered = plot_scene(meter);
    const std::vector<float> rms{0.1F, 0.6F};
    const std::vector<float> peak{0.2F, 0.9F};
    feed.publish_meter(2, rms, peak);
    sampler.sample(feed, metered);
    expect(plot_of(metered)->samples == std::vector<float>{0.6F, 0.9F},
           "a meter reads the level and peak of its audio channel");
    for (int tick = 0; tick < 3; ++tick) sampler.sample(feed, metered);
    expect(plot_of(metered)->samples == std::vector<float>{-1.0F, -1.0F},
           "a stale meter falls back to the channel's neutral value");

    auto missing = line_spec(4, {});
    missing.channel = "absent";
    Scene unbound = plot_scene(missing);
    report = sampler.sample(feed, unbound);
    expect(report.diagnostics.size() == 1U &&
               report.diagnostics[0].code == kPlotChannelUnboundCode &&
               report.diagnostics[0].node_id == "plot",
           "a plot whose channel the feed lacks is reported");
    auto wrong = history;
    wrong.shape = PlotChannelShape::vector;
    Scene mismatched = plot_scene(wrong);
    report = sampler.sample(feed, mismatched);
    expect(report.diagnostics.size() == 1U, "a channel with the wrong shape is unbound");
}

// A producer thread publishes an increasing count while the render thread
// samples: every sample is a whole published value and the history never
// goes backwards, so no reader sees a torn or reordered publish. A count can
// change before the value it counts is visible, so a value may repeat.
void producer_thread() {
    InMemoryValueFeed feed({{.name = "count", .shape = ChannelShape::scalar}});
    auto spec = line_spec(4096, {});
    spec.channel = "count";
    spec.shape = PlotChannelShape::scalar;
    spec.value_max = 1.0e6F;
    Scene scene = plot_scene(spec);
    PlotFeedSampler sampler({.stale_after_ticks = 0});
    std::atomic<bool> done{false};
    std::thread producer([&] {
        for (int value = 1; value <= 200000; ++value) feed.publish_scalar(0, static_cast<float>(value));
        done.store(true);
    });
    int ticks = 0;
    while (!done.load() || ticks < 2) {
        sampler.sample(feed, scene);
        ++ticks;
    }
    producer.join();
    sampler.sample(feed, scene);
    const auto& samples = plot_of(scene)->samples;
    bool ordered = true;
    float last = 0.0F;
    std::size_t taken = 0;
    for (const float value : samples) {
        if (std::isnan(value)) break;
        ordered = ordered && value == std::floor(value) && value >= last;
        last = value;
        ++taken;
    }
    expect(ordered && taken > 0U, "samples from a producer thread are whole and in order",
           std::to_string(taken) + " samples over " + std::to_string(ticks) + " ticks");
}

void large_feed() {
    constexpr std::uint32_t slots = 1U << 20U;
    std::vector<float> samples(slots);
    for (std::uint32_t index = 0; index < slots; ++index) {
        samples[index] = 0.5F + 0.45F * std::sin(static_cast<float>(index) * 0.001F) +
                         ((index % 7919U) == 0U ? 0.05F : 0.0F);
    }
    const auto spec = line_spec(slots, samples);
    const Rect box{0.0F, 0.0F, 256.0F, 128.0F};
    const auto start = std::chrono::steady_clock::now();
    const auto geometry = plot_geometry(spec, Color::hex(0x000000), box, 1.0F);
    const auto elapsed = std::chrono::duration<double, std::milli>(
                             std::chrono::steady_clock::now() - start).count();
    expect(geometry.columns == 256U && geometry.rects.size() <= 257U,
           "a million-sample feed draws at most one span per column",
           std::to_string(geometry.rects.size()) + " rects in " + std::to_string(elapsed) + " ms");
    const auto huge = plot_geometry(spec, Color::hex(0x000000), {0.0F, 0.0F, 1.0e9F, 10.0F}, 1.0F);
    expect(huge.rects.size() == 1U && huge.rects[0].layer == PlotLayer::background,
           "a plot wider than kMaxPlotColumns draws only its background");
    Override off(hooks::PlotDecimation::per_slot, {});
    const auto naive = plot_geometry(spec, Color::hex(0x000000), box, 1.0F);
    expect(naive.rects.size() > 257U,
           "negative control: without decimation the geometry grows with the feed",
           std::to_string(naive.rects.size()) + " rects");
}

// A feed whose scalar advances on every read and whose count can be set, to
// show the sampler reads a channel once per tick and treats a first look as
// fresh even when the count has wrapped to zero.
class CountingFeed final : public vellum::value::ValueFeed {
public:
    std::uint32_t count = 0;
    float next = 0.0F;
    [[nodiscard]] std::size_t channel_count() const noexcept override { return 1U; }
    [[nodiscard]] vellum::value::ChannelInfo channel(std::size_t) const noexcept override {
        return {.name = "tick", .shape = ChannelShape::scalar};
    }
    bool read_scalar(std::size_t, float& value) noexcept override {
        value = next;
        next += 1.0F;
        return true;
    }
    std::size_t read_meter(std::size_t, std::span<float>, std::span<float>) noexcept override {
        return 0U;
    }
    std::size_t read_vector(std::size_t, std::span<float>) noexcept override { return 0U; }
    std::size_t read_events(std::size_t, std::span<vellum::value::ChannelEvent>) noexcept override {
        return 0U;
    }
    [[nodiscard]] std::uint32_t publish_count(std::size_t) const noexcept override { return count; }
};

void review_fixes() {
    auto spec = line_spec(8, {});
    spec.channel = "tick";
    spec.shape = PlotChannelShape::scalar;
    Scene scene = plot_scene(spec);
    SceneNode twin = scene.root.children[1];
    twin.id = "twin";
    scene.root.children.push_back(twin);
    CountingFeed counting;
    PlotFeedSampler sampler;
    sampler.sample(counting, scene);
    expect(plot_of(scene)->samples[0] == 0.0F,
           "a first look at a channel whose count wrapped to zero still reads its value");
    for (int tick = 0; tick < 4; ++tick) {
        ++counting.count;
        sampler.sample(counting, scene);
    }
    expect(plot_specs_equal(*plot_of(scene), *scene.root.children[2].plot) &&
               plot_of(scene)->samples[4] == 4.0F && counting.next == 5.0F,
           "two plots of one channel read it once per tick and see the same publish");

    Scene twins = plot_scene(line_spec(4, {}));
    twins.root.children.push_back(twins.root.children[1]);
    InMemoryValueFeed feed({{.name = "scope", .shape = ChannelShape::vector, .capacity = 4}});
    const std::vector<float> block{0.3F, 0.4F};
    feed.publish_vector(0, block);
    PlotFeedSampler shared;
    shared.sample(feed, twins);
    expect(twins.root.children[1].plot->samples.size() == 4U &&
               twins.root.children[2].plot->samples.size() == 4U,
           "two plots sharing an id are both fed");

    InMemoryValueFeed first({{.name = "scope", .shape = ChannelShape::vector, .capacity = 4}});
    InMemoryValueFeed second({{.name = "scope", .shape = ChannelShape::vector, .capacity = 4}});
    const std::vector<float> one{0.1F};
    const std::vector<float> two{0.9F};
    first.publish_vector(0, one);
    second.publish_vector(0, two);
    Scene swapped = plot_scene(line_spec(4, {}));
    PlotFeedSampler switching;
    switching.sample(first, swapped);
    switching.sample(second, swapped);
    expect(plot_of(swapped)->samples[0] == 0.9F,
           "a different feed with the same publish count is read afresh");

    InMemoryValueFeed silent({{.name = "scope", .shape = ChannelShape::vector, .capacity = 4},
                              {.name = "other", .shape = ChannelShape::vector, .capacity = 4}});
    const auto shown = plot_of(swapped);
    const auto unpublished = switching.sample(silent, swapped);
    expect(plot_of(swapped)->samples.empty() && unpublished.updated.size() == 1U &&
               !compute_scene_damage(plot_scene(*shown), swapped, 1.0F).empty(),
           "switching to a feed that has never published clears the old samples and damages them");
    switching.sample(second, swapped);
    expect(plot_of(swapped)->samples[0] == 0.9F, "switching back reads the published feed again");
    auto rechanneled = std::make_shared<PlotSpec>(*plot_of(swapped));
    rechanneled->channel = "other";
    swapped.root.children[1].plot = rechanneled;
    const auto quiet_channel = switching.sample(silent, swapped);
    expect(plot_of(swapped)->samples.empty() && quiet_channel.updated.size() == 1U,
           "rebinding to a channel that has never published clears the old samples");
    switching.sample(silent, swapped);
    expect(plot_of(swapped)->samples.empty(), "an unpublished channel keeps drawing nothing");

    InMemoryValueFeed level({{.name = "level", .shape = ChannelShape::scalar, .neutral = 0.0F}});
    auto history = line_spec(4, {});
    history.channel = "level";
    history.shape = PlotChannelShape::scalar;
    Scene quiet = plot_scene(history);
    PlotFeedSampler stale({.stale_after_ticks = 2});
    level.publish_scalar(0, 1.0F);
    for (int tick = 0; tick < 6; ++tick) stale.sample(level, quiet);
    expect(plot_of(quiet)->samples[0] == 1.0F && plot_of(quiet)->samples[1] == 0.0F &&
               std::isnan(plot_of(quiet)->samples[2]),
           "a stale scalar history takes one neutral sample and stops");

    auto meter = PlotSpec{.style = PlotStyle::level_meter, .shape = PlotChannelShape::meter};
    meter.audio_channel = kMaxPlotAudioChannel + 1U;
    expect(!accept_scene(plot_scene(meter)).accepted,
           "a meter beyond kMaxPlotAudioChannel is refused");
    auto wide = line_spec(4, {});
    wide.line_width = std::numeric_limits<float>::max();
    expect(!accept_scene(plot_scene(wide)).accepted, "a line width over kMaxPlotStroke is refused");
    wide.line_width = kMaxPlotStroke;
    wide.samples = {0.5F};
    const auto heavy = plot_geometry(wide, Color::rgba(0, 0, 0, 0), {0.0F, 0.0F, 4.0F, 4.0F}, 4.0F);
    expect(!heavy.rects.empty() && heavy.rects[0].rect.height == 4.0F,
           "the widest line is clamped to the box");
    const Rect vast{-1073741824.0F, -1073741824.0F, 2147483648.0F, 2147483648.0F};
    const auto huge = plot_geometry(line_spec(4, {0.5F}), Color::hex(0x000000), vast, 1.0F);
    const auto grid = plot_grid(vast, 1.0F);
    expect(huge.rects.size() == 1U && grid.right - grid.left == (1 << 30),
           "a box past the grid limit is clamped so its extent fits");
    expect(plot_sample_damage(line_spec(4, {0.5F}), line_spec(4, {0.6F}), vast, 1.0F).empty(),
           "sample damage on a box past the column limit is empty, as it draws no samples");
}

void lowering() {
    const Scene scene = plot_scene(line_spec(4, {0.5F}));
    const auto commands = make_paint_commands(scene);
    expect(commands.size() == 2U && commands[1].kind == PaintCommand::Kind::plot &&
               commands[1].plot == scene.root.children[1].plot &&
               commands[1].bounds.x == kPlotBox.x &&
               paint_extent(commands[1]).rect.width == kPlotBox.width,
           "a plot lowers to one command carrying its spec over its box");
    expect(to_string(PaintCommand::Kind::plot) == "plot" && draws_content(PaintCommand::Kind::plot),
           "a plot command draws content");
}

}  // namespace

int main() {
    line_oracle();
    decimated_peak_oracle();
    area_bar_and_meter_oracles();
    damage_per_update();
    versioning();
    plot_with_gradient_fill();
    plot_with_blend_mode();
    plot_under_effects();
    invalid_plots();
    sampler();
    producer_thread();
    large_feed();
    review_fixes();
    lowering();
    if (failures != 0) {
        std::cerr << failures << " plot check(s) failed\n";
        return 1;
    }
    std::cout << "plot checks passed\n";
    return 0;
}
