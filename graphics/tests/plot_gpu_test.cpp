#include "../src/plot_testing.hpp"
#include "../src/skia_dawn_surface_test_access.hpp"
#include "../src/skia_dawn_surface_testing.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/plot_feed.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/value/in_memory_value_feed.hpp>

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;
namespace hooks = vellum::graphics::testing;
using vellum::value::ChannelShape;
using vellum::value::InMemoryValueFeed;

int failures = 0;
bool gpu_missing = false;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

constexpr float kWidth = 320.0F;
constexpr float kHeight = 200.0F;
constexpr float kNaN = std::numeric_limits<float>::quiet_NaN();
const Color kBackground = Color::hex(0x0B1020);
const Color kPlotFill = Color::hex(0x111827);
const Color kLine = Color::hex(0xF97316);
const Color kArea = Color::hex(0x22C55E);
const Color kPeak = Color::hex(0xF8FAFC);

std::string describe(const FrameStats& stats) {
    std::ostringstream out;
    out << "full=" << stats.full_repaint << " reason=" << to_string(stats.full_repaint_reason)
        << " damage_px=" << stats.damage_area_pixels << '/' << stats.frame_area_pixels
        << " draws=" << stats.paint_command_draws << " cpu_ms=" << stats.cpu_milliseconds;
    return out.str();
}

std::unique_ptr<SkiaDawnSurface> make_surface(float scale, bool layer_cache = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(kWidth),
         .height = static_cast<std::uint32_t>(kHeight),
         .scale = scale},
        &error);
    if (!surface) {
        gpu_missing = true;
        std::cout << "SKIP no GPU surface: " << error << '\n';
        return nullptr;
    }
    if (!layer_cache) {
        LayerCachePolicy policy;
        policy.enabled = false;
        surface->set_layer_cache_policy(policy);
    }
    return surface;
}

struct Color8 final {
    int r = 0;
    int g = 0;
    int b = 0;
    int a = 0;
};

struct Capture final {
    bool ok = false;
    std::string error;
    harness::CaptureResult result;

    [[nodiscard]] Color8 at(std::uint32_t x, std::uint32_t y) const;
};

Color8 Capture::at(std::uint32_t x, std::uint32_t y) const {
    if (x >= result.pixel_width || y >= result.pixel_height) return {-1, -1, -1, -1};
    const auto offset = (static_cast<std::size_t>(y) * result.pixel_width + x) * 4U;
    return {result.rgba[offset], result.rgba[offset + 1U], result.rgba[offset + 2U],
            result.rgba[offset + 3U]};
}

bool is(const Color8& pixel, const Color& color) {
    return pixel.r == color.red8() && pixel.g == color.green8() && pixel.b == color.blue8() &&
           pixel.a == 255;
}

std::string text(const Color8& pixel) {
    return std::to_string(pixel.r) + "," + std::to_string(pixel.g) + "," +
           std::to_string(pixel.b) + "," + std::to_string(pixel.a);
}

Capture capture(SkiaDawnSurface& surface) {
    Capture out;
    out.result.evidence = surface.evidence();
    out.ok = surface.capture_rgba(out.result.rgba, out.result.pixel_width,
                                  out.result.pixel_height, &out.error);
    out.result.ok = out.ok;
    return out;
}

bool identical(SkiaDawnSurface& a, SkiaDawnSurface& b, std::string& message) {
    const auto left = capture(a);
    const auto right = capture(b);
    if (!left.ok || !right.ok) {
        message = left.error + right.error;
        return false;
    }
    const auto verdict = harness::assert_identical(left.result, right.result);
    message = verdict.message;
    return static_cast<bool>(verdict);
}

struct PlotOverride final {
    PlotOverride(hooks::PlotDecimation decimation, hooks::PlotRules rules)
        : saved_decimation(hooks::plot_decimation()), saved_rules(hooks::plot_rules()) {
        hooks::set_plot_decimation(decimation);
        hooks::set_plot_rules(rules);
    }
    ~PlotOverride() {
        hooks::set_plot_decimation(saved_decimation);
        hooks::set_plot_rules(saved_rules);
    }
    PlotOverride(const PlotOverride&) = delete;
    PlotOverride& operator=(const PlotOverride&) = delete;
    hooks::PlotDecimation saved_decimation;
    hooks::PlotRules saved_rules;
};

struct RulesOverride final {
    explicit RulesOverride(const hooks::PartialRedrawRules& rules)
        : saved(hooks::partial_redraw_rules()) {
        hooks::set_partial_redraw_rules(rules);
    }
    ~RulesOverride() { hooks::set_partial_redraw_rules(saved); }
    RulesOverride(const RulesOverride&) = delete;
    RulesOverride& operator=(const RulesOverride&) = delete;
    hooks::PartialRedrawRules saved;
};

Scene empty_scene() {
    Scene scene;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.background = kBackground;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    return scene;
}

SceneNode plot_node(std::string id, Rect bounds, PlotSpec spec) {
    SceneNode node{.id = std::move(id), .kind = SceneNode::Kind::plot, .bounds = bounds,
                   .fill = kPlotFill};
    node.plot = std::make_shared<PlotSpec>(std::move(spec));
    return node;
}

PlotSpec spec_of(PlotStyle style, PlotChannelShape shape, std::uint32_t slots,
                 std::vector<float> samples, std::string channel = {}) {
    PlotSpec spec;
    spec.style = style;
    spec.shape = shape;
    spec.slot_count = slots;
    spec.samples = std::move(samples);
    spec.channel = std::move(channel);
    spec.line_color = kLine;
    spec.fill_color = kArea;
    spec.peak_color = kPeak;
    return spec;
}

SceneNode* find_mutable(SceneNode& node, const std::string& id) {
    if (node.id == id) return &node;
    for (auto& child : node.children) {
        if (auto* found = find_mutable(child, id); found != nullptr) return found;
    }
    return nullptr;
}

void set_samples(Scene& scene, const std::string& id, std::vector<float> samples) {
    auto* node = find_mutable(scene.root, id);
    auto spec = std::make_shared<PlotSpec>(*node->plot);
    spec->samples = std::move(samples);
    node->plot = std::move(spec);
}

// The dense scope: 2000 slots of 0.25 with one spike to 1 at slot 1234 and
// one dip to 0 at slot 1500, over a 100 by 60 box at (20, 10).
const Rect kScopeBox{20.0F, 10.0F, 100.0F, 60.0F};

Scene oracle_scene() {
    Scene scene = empty_scene();
    std::vector<float> samples(2000, 0.25F);
    samples[1234] = 1.0F;
    samples[1500] = 0.0F;
    scene.root.children.push_back(plot_node(
        "scope", kScopeBox, spec_of(PlotStyle::line, PlotChannelShape::vector, 2000, samples)));
    scene.root.children.push_back(plot_node(
        "area", {140.0F, 10.0F, 40.0F, 40.0F},
        spec_of(PlotStyle::area, PlotChannelShape::vector, 4, {0.5F, 0.5F, 0.5F, 0.5F})));
    auto meter = spec_of(PlotStyle::level_meter, PlotChannelShape::meter, 0, {0.5F, 0.75F});
    meter.peak_width = 2.0F;
    scene.root.children.push_back(plot_node("meter", {200.0F, 10.0F, 10.0F, 80.0F}, meter));
    return scene;
}

// Pixels computed from the plot rules by hand, not from `plot_geometry`.
void pixel_oracles() {
    for (const float scale : {1.0F, 2.0F}) {
        const std::string at = "@" + std::to_string(static_cast<int>(scale)) + " ";
        auto surface = make_surface(scale);
        if (!surface) return;
        std::string error;
        if (!expect(surface->render(oracle_scene(), &error), at + "the oracle scene renders",
                    error)) {
            return;
        }
        const auto shot = capture(*surface);
        if (!expect(shot.ok, at + "the oracle scene is captured", shot.error)) return;
        const auto s = static_cast<std::uint32_t>(scale);
        const std::uint32_t left = 20U * s;
        const std::uint32_t top = 10U * s;
        const std::uint32_t columns = 100U * s;
        const std::uint32_t rows = 60U * s;
        // Column c covers slots [c * 2000 / columns, (c + 1) * 2000 / columns).
        const std::uint32_t spike = 1234U * columns / 2000U;
        const std::uint32_t dip = 1500U * columns / 2000U;
        const std::uint32_t quiet = 300U * columns / 2000U;
        // 0.25 falls in row floor(0.75 * rows).
        const std::uint32_t level = top + (rows * 3U) / 4U;
        expect(is(shot.at(left + spike, top), kLine),
               at + "the decimated spike reaches the plot's top row in its column",
               text(shot.at(left + spike, top)));
        expect(is(shot.at(left + spike, level), kLine),
               at + "the spike column joins the steady level", text(shot.at(left + spike, level)));
        expect(is(shot.at(left + dip, top + rows - 1U), kLine),
               at + "the decimated dip reaches the plot's bottom row",
               text(shot.at(left + dip, top + rows - 1U)));
        // A one-logical-pixel line is `s` device rows from the value's row.
        expect(is(shot.at(left + quiet, level), kLine) &&
                   is(shot.at(left + quiet, level + s - 1U), kLine) &&
                   is(shot.at(left + quiet, level - 1U), kPlotFill) &&
                   is(shot.at(left + quiet, level + s), kPlotFill),
               at + "a steady column is one logical pixel of line over the plot fill",
               text(shot.at(left + quiet, level + s)));
        expect(is(shot.at(left + spike + 2U, top), kPlotFill) &&
                   is(shot.at(left - 1U, level), kBackground) &&
                   is(shot.at(left + columns, level), kBackground),
               at + "the plot paints only its box");
        const std::uint32_t area_left = 140U * s;
        const std::uint32_t area_top = 10U * s;
        const std::uint32_t half = 20U * s;
        expect(is(shot.at(area_left + 1U, area_top + half - 1U), kPlotFill) &&
                   is(shot.at(area_left + 1U, area_top + half), kArea) &&
                   is(shot.at(area_left + 1U, area_top + 2U * half - 1U), kArea),
               at + "an area at half fills exactly the lower half",
               text(shot.at(area_left + 1U, area_top + half)));
        const std::uint32_t meter_left = 200U * s;
        const std::uint32_t meter_top = 10U * s;
        const std::uint32_t meter_rows = 80U * s;
        const std::uint32_t level_edge = meter_top + meter_rows / 2U;
        const std::uint32_t peak_edge = meter_top + meter_rows / 4U;
        const std::uint32_t tick = 2U * s;
        expect(is(shot.at(meter_left + 3U, level_edge), kArea) &&
                   is(shot.at(meter_left + 3U, level_edge - 1U), kPlotFill),
               at + "a meter at half fills from its bottom to its middle",
               text(shot.at(meter_left + 3U, level_edge)));
        expect(is(shot.at(meter_left + 3U, peak_edge - tick / 2U), kPeak) &&
                   is(shot.at(meter_left + 3U, peak_edge + tick / 2U - 1U), kPeak) &&
                   is(shot.at(meter_left + 3U, peak_edge - tick / 2U - 1U), kPlotFill) &&
                   is(shot.at(meter_left + 3U, peak_edge + tick / 2U), kPlotFill),
               at + "the peak tick is centred on the peak and two logical pixels thick",
               text(shot.at(meter_left + 3U, peak_edge)));

        PlotOverride off(hooks::PlotDecimation::first_sample, {});
        if (!surface->render(oracle_scene(), &error)) return;
        const auto lossy = capture(*surface);
        expect(lossy.ok && is(lossy.at(left + spike, top), kPlotFill),
               at + "negative control: first-sample decimation loses the spike's pixels",
               text(lossy.at(left + spike, top)));
    }
}

struct SweepCount final {
    int differing = 0;
    int partial = 0;
    int edges = 0;
    std::string first;
};

SweepCount sweep_damage_edges(const Scene& scene, float scale, bool layer_cache) {
    SweepCount count;
    auto full = make_surface(scale);
    auto partial = make_surface(scale, layer_cache);
    if (!full || !partial) return {.differing = -1};
    std::string error;
    if (!full->render(scene, &error)) return {.differing = -1, .first = error};
    const PixelRect frame = frame_rect(scene, scale);
    for (const bool vertical : {true, false}) {
        const std::int32_t extent = vertical ? frame.right : frame.bottom;
        for (std::int32_t edge = 1; edge < extent; ++edge) {
            DamageRegion damage(frame, {.full_repaint_fraction = 2.0});
            damage.add(vertical ? PixelRect{edge, 0, frame.right, frame.bottom}
                                : PixelRect{0, edge, frame.right, frame.bottom});
            if (!partial->render(scene, &error) || !partial->render(scene, damage, &error)) {
                return {.differing = -1, .first = error};
            }
            ++count.edges;
            if (!partial->frame_stats().full_repaint) ++count.partial;
            std::string message;
            if (!identical(*full, *partial, message)) {
                if (count.differing == 0) {
                    count.first = std::string(vertical ? "x=" : "y=") + std::to_string(edge) +
                                  ": " + message;
                }
                ++count.differing;
            }
        }
    }
    return count;
}

std::string sweep_detail(const SweepCount& count) {
    return std::to_string(count.differing) + " of " + std::to_string(count.edges) +
           " edges differ, " + std::to_string(count.partial) + " partial; first " + count.first;
}

Scene sweep_scene() {
    Scene scene = oracle_scene();
    std::vector<float> bars{0.9F, 0.2F, 0.6F, 0.4F, 1.0F, 0.1F};
    auto bar = spec_of(PlotStyle::bar, PlotChannelShape::vector, 6, bars);
    bar.bar_gap = 1.5F;
    scene.root.children.push_back(plot_node("bars", {20.5F, 90.25F, 61.0F, 50.0F}, bar));
    auto horizontal = spec_of(PlotStyle::level_meter, PlotChannelShape::meter, 0, {0.6F, 0.8F});
    horizontal.orientation = PlotOrientation::horizontal;
    scene.root.children.push_back(plot_node("hmeter", {100.0F, 150.0F, 90.0F, 12.0F}, horizontal));
    std::vector<float> wave(300);
    for (std::size_t i = 0; i < wave.size(); ++i) wave[i] = 0.5F + 0.45F * std::sin(0.11F * i);
    auto thick = spec_of(PlotStyle::line, PlotChannelShape::scalar, 300, wave);
    thick.line_width = 2.5F;
    auto turned = plot_node("turned", {230.0F, 100.0F, 70.0F, 40.0F}, thick);
    turned.transform = Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F, .tx = 40.0F};
    scene.root.children.push_back(std::move(turned));
    return scene;
}

bool render_both(SkiaDawnSurface& full, SkiaDawnSurface& partial, const Scene& scene);

// A plot over a radial gradient background: the background is enclosed whole
// as an interpolated fill, so sweeps and sample updates stay exact.
Scene gradient_plot_scene() {
    Scene scene = oracle_scene();
    auto* scope = find_mutable(scene.root, "scope");
    scope->fill_gradients = {{.kind = GradientKind::radial,
                              .stops = {{.position = 0.0F, .color = Color::hex(0x1E293B)},
                                        {.position = 1.0F, .color = Color::hex(0x020617)}}}};
    return scene;
}

void gradient_plot() {
    const auto count = sweep_damage_edges(gradient_plot_scene(), 2.0F, false);
    expect(count.differing == 0 && count.partial > 0,
           "a damage edge swept across a plot on a radial gradient leaves every frame "
           "byte-identical",
           sweep_detail(count));
    auto full = make_surface(2.0F);
    auto partial = make_surface(2.0F);
    if (!full || !partial) return;
    Scene scene = gradient_plot_scene();
    if (!render_both(*full, *partial, scene)) return;
    int partial_frames = 0;
    for (int step = 0; step < 6; ++step) {
        std::vector<float> samples(2000, 0.25F + 0.1F * static_cast<float>(step));
        samples[100U * static_cast<std::size_t>(step)] = 1.0F;
        set_samples(scene, "scope", samples);
        if (!render_both(*full, *partial, scene)) return;
        if (!partial->frame_stats().full_repaint) ++partial_frames;
        std::string message;
        expect(identical(*full, *partial, message),
               "a sample update over a gradient background is byte-identical, step " +
                   std::to_string(step),
               message);
    }
    expect(partial_frames > 0, "sample updates over a gradient background take the partial path");
}

// A plot inside a blurred group: each sample update redraws exactly on the
// partial path. The renderer redraws a damaged effect group whole, so the
// damage growth by the blur's radius is checked against the damage model in
// plot_test.cpp rather than here.
void plot_under_blur() {
    const auto run = [](bool grow) {
        PlotOverride rules(hooks::PlotDecimation::min_max, {.grow_by_effects = grow});
        auto full = make_surface(2.0F);
        auto partial = make_surface(2.0F);
        if (!full || !partial) return std::pair<int, int>{-1, -1};
        Scene scene = oracle_scene();
        auto* scope = find_mutable(scene.root, "scope");
        SceneNode group{.id = "fx", .kind = SceneNode::Kind::group,
                        .bounds = {0.0F, 0.0F, kWidth, kHeight}};
        group.effects = {{.kind = EffectKind::blur, .sigma = 3.0F}};
        group.children.push_back(*scope);
        std::erase_if(scene.root.children, [](const SceneNode& node) { return node.id == "scope"; });
        scene.root.children.push_back(std::move(group));
        if (!render_both(*full, *partial, scene)) return std::pair<int, int>{-1, -1};
        int differing = 0;
        int partial_frames = 0;
        for (int step = 0; step < 6; ++step) {
            std::vector<float> samples(2000, 0.25F);
            samples[300U * static_cast<std::size_t>(step) + 50U] = 1.0F;
            set_samples(scene, "scope", samples);
            if (!render_both(*full, *partial, scene)) return std::pair<int, int>{-1, -1};
            if (!partial->frame_stats().full_repaint) ++partial_frames;
            std::string message;
            if (!identical(*full, *partial, message)) ++differing;
        }
        return std::pair<int, int>{differing, partial_frames};
    };
    const auto [differing, partial_frames] = run(true);
    expect(differing == 0 && partial_frames > 0,
           "sample updates of a plot under a blur redraw byte-identically on the partial path",
           std::to_string(differing) + " differ, " + std::to_string(partial_frames) + " partial");
}

void sweeps() {
    const Scene scene = sweep_scene();
    for (const float scale : {1.0F, 2.0F}) {
        for (const bool cached : {false, true}) {
            const std::string label = std::string(cached ? "cache on @" : "cache off @") +
                                      std::to_string(static_cast<int>(scale)) + " ";
            const auto count = sweep_damage_edges(scene, scale, cached);
            if (count.differing < 0 && gpu_missing) return;
            expect(count.differing == 0 && count.partial > 0,
                   label + "a damage edge swept across every plot kind leaves every frame "
                           "byte-identical",
                   sweep_detail(count));
        }
    }
    {
        RulesOverride off({.enclose_paths = false, .enclose_own_layers = false,
                           .enclose_interpolated_fills = false, .enclose_images = false,
                           .enclose_clips = false, .enclose_group_layers = false});
        const auto bare = sweep_damage_edges(scene, 2.0F, false);
        expect(bare.differing == 0 && bare.partial > 0,
               "plots reproduce through a partial clip with every enclosure rule off",
               sweep_detail(bare));
    }
    Scene control = empty_scene();
    auto path = std::make_shared<PathGeometry>();
    path->verbs = {PathVerb::move, PathVerb::cubic, PathVerb::close};
    path->values = {10.0F, 80.0F, 60.0F, -20.0F, 140.0F, 160.0F, 200.0F, 40.0F};
    path->stroke = {.color = kLine, .width = 3.0F};
    control.root.children.push_back({.id = "curve", .kind = SceneNode::Kind::path,
                                     .bounds = {20.3F, 20.7F, 200.0F, 120.0F},
                                     .fill = kArea, .path = path});
    RulesOverride off({.enclose_paths = false});
    const auto cut = sweep_damage_edges(control, 2.0F, false);
    expect(cut.differing > 0 && cut.partial > 0,
           "control: the sweep does catch an anti-aliased path cut without enclosure",
           sweep_detail(cut));
}

bool render_both(SkiaDawnSurface& full, SkiaDawnSurface& partial, const Scene& scene) {
    std::string error;
    const bool full_ok = full.render(scene, &error);
    const bool partial_ok = full_ok && partial.render_incremental(scene, &error);
    if (!full_ok || !partial_ok) expect(false, "both paths render", error);
    return full_ok && partial_ok;
}

const Rect kHistoryBox{10.0F, 20.0F, 120.0F, 50.0F};
const Rect kMeterBox{150.0F, 20.0F, 12.0F, 100.0F};
const Rect kWaveBox{180.0F, 20.0F, 128.0F, 60.0F};

Scene feed_scene() {
    Scene scene = empty_scene();
    scene.root.children.push_back(
        {.id = "label", .kind = SceneNode::Kind::rectangle, .bounds = {10.0F, 150.0F, 80.0F, 20.0F},
         .fill = Color::hex(0x334155)});
    auto history = spec_of(PlotStyle::line, PlotChannelShape::scalar, 120, {}, "level");
    history.line_width = 1.5F;
    scene.root.children.push_back(plot_node("history", kHistoryBox, history));
    auto meter = spec_of(PlotStyle::level_meter, PlotChannelShape::meter, 0, {}, "meter");
    scene.root.children.push_back(plot_node("meter", kMeterBox, meter));
    auto wave = spec_of(PlotStyle::area, PlotChannelShape::vector, 512, {}, "wave");
    wave.baseline = 0.5F;
    scene.root.children.push_back(plot_node("wave", kWaveBox, wave));
    return scene;
}

std::int64_t box_pixels(const Rect& box, float scale, std::int32_t margin) {
    const auto w = static_cast<std::int64_t>(std::ceil(box.width * scale)) + 2 * margin;
    const auto h = static_cast<std::int64_t>(std::ceil(box.height * scale)) + 2 * margin;
    return w * h;
}

struct FeedRun final {
    int frames = 0;
    int partial = 0;
    int differing = 0;
    int over_budget = 0;
    std::string first;
};

// Drives the feed scene through `ticks` ticks of `publish`, comparing each
// incremental frame with a full one and its damage area with `budget`.
template <typename Publish>
FeedRun run_feed(int ticks, InMemoryValueFeed& feed, PlotFeedSampler& sampler,
                 Scene& scene, SkiaDawnSurface& full, SkiaDawnSurface& partial,
                 std::int64_t budget, Publish publish) {
    FeedRun run;
    for (int tick = 0; tick < ticks; ++tick) {
        publish(tick);
        const auto report = sampler.sample(feed, scene);
        if (report.updated.empty()) continue;
        if (!render_both(full, partial, scene)) return {.differing = -1};
        const auto stats = partial.frame_stats();
        ++run.frames;
        if (!stats.full_repaint) ++run.partial;
        if (stats.full_repaint || stats.damage_area_pixels > budget) {
            if (run.over_budget == 0 && run.first.empty()) {
                run.first = "tick " + std::to_string(tick) + " " + describe(stats);
            }
            ++run.over_budget;
        }
        std::string message;
        if (!identical(full, partial, message)) {
            if (run.differing == 0) run.first = "tick " + std::to_string(tick) + ": " + message;
            ++run.differing;
        }
    }
    return run;
}

std::string run_detail(const FeedRun& run) {
    return std::to_string(run.differing) + " of " + std::to_string(run.frames) + " differ, " +
           std::to_string(run.partial) + " partial, " + std::to_string(run.over_budget) +
           " over budget; first " + run.first;
}

float wave_value(int tick, std::size_t index) {
    return 0.5F + 0.4F * std::sin(0.05F * static_cast<float>(index) + 0.3F * tick);
}

void feed_updates() {
    for (const float scale : {1.0F, 2.0F}) {
        const std::string at = "@" + std::to_string(static_cast<int>(scale)) + " ";
        InMemoryValueFeed feed({{.name = "level", .shape = ChannelShape::scalar},
                                {.name = "meter", .shape = ChannelShape::meter, .neutral = 0.0F,
                                 .capacity = 2},
                                {.name = "wave", .shape = ChannelShape::vector, .capacity = 512}});
        PlotFeedSampler sampler({.stale_after_ticks = 4});
        Scene scene = feed_scene();
        auto full = make_surface(scale);
        auto partial = make_surface(scale, true);
        if (!full || !partial || !render_both(*full, *partial, scene)) return;
        const auto level = [&](int tick) {
            feed.publish_scalar(0, 0.5F + 0.45F * std::sin(0.21F * static_cast<float>(tick)));
        };
        // One logical column per slot: a new sample damages its column, the
        // antialias margin each side, and a line thickness of rows.
        const auto column_budget = static_cast<std::int64_t>(
            (std::ceil(scale) + 2.0F) * (kHistoryBox.height * scale + 2.0F));
        const auto filling = run_feed(120, feed, sampler, scene, *full, *partial,
                                      column_budget, level);
        expect(filling.differing == 0 && filling.partial == filling.frames &&
                   filling.frames == 120 && filling.over_budget == 0,
               at + "each sample filling the history damages only its column",
               run_detail(filling));
        const auto scrolling = run_feed(40, feed, sampler, scene, *full, *partial,
                                        box_pixels(kHistoryBox, scale, 1), [&](int tick) {
                                            level(120 + tick);
                                        });
        expect(scrolling.differing == 0 && scrolling.partial == scrolling.frames &&
                   scrolling.frames == 40 && scrolling.over_budget == 0,
               at + "a scrolling history damages its scroll region and nothing else",
               run_detail(scrolling));
        const auto quiet = run_feed(6, feed, sampler, scene, *full, *partial,
                                    box_pixels(kHistoryBox, scale, 1), [](int) {});
        expect(quiet.differing == 0 && quiet.frames == 1 && quiet.over_budget == 0,
               at + "a history gone stale takes one neutral sample within its plot",
               run_detail(quiet));
        const float resting_rms[]{0.5F};
        const float resting_peak[]{0.65F};
        feed.publish_meter(1, resting_rms, resting_peak);
        sampler.sample(feed, scene);
        if (!render_both(*full, *partial, scene)) return;
        const auto meters = run_feed(60, feed, sampler, scene, *full, *partial,
                                     box_pixels(kMeterBox, scale, 1) / 2, [&](int tick) {
                                         const float rms[]{0.5F + 0.3F * std::sin(0.4F * tick)};
                                         const float peak[]{std::min(1.0F, rms[0] + 0.15F)};
                                         feed.publish_meter(1, rms, peak);
                                     });
        expect(meters.differing == 0 && meters.partial == meters.frames && meters.frames > 50 &&
                   meters.over_budget == 0,
               at + "a meter update damages the band between levels and the peak ticks",
               run_detail(meters));
        std::vector<float> block(512);
        const auto waves = run_feed(30, feed, sampler, scene, *full, *partial,
                                    box_pixels(kWaveBox, scale, 1), [&](int tick) {
                                        for (std::size_t i = 0; i < block.size(); ++i) {
                                            block[i] = wave_value(tick, i);
                                        }
                                        feed.publish_vector(2, block);
                                    });
        expect(waves.differing == 0 && waves.partial == waves.frames && waves.frames == 30 &&
                   waves.over_budget == 0,
               at + "a vector block damages only its plot", run_detail(waves));
        const auto stale = run_feed(8, feed, sampler, scene, *full, *partial,
                                    box_pixels(kWaveBox, scale, 1) +
                                        box_pixels(kMeterBox, scale, 1),
                                    [](int) {});
        expect(stale.differing == 0 && stale.frames >= 1,
               at + "channels gone stale fall to neutral and redraw exactly",
               run_detail(stale));

        // The same scroll without the scroll region, and the same meter fall
        // without the old spans, leave stale pixels.
        {
            PlotOverride off(hooks::PlotDecimation::min_max, {.every_changed_column = false});
            const auto cut = run_feed(10, feed, sampler, scene, *full, *partial,
                                      box_pixels(kHistoryBox, scale, 1),
                                      [&](int tick) { level(400 + tick); });
            expect(cut.differing > 0 && cut.partial > 0,
                   at + "negative control: damaging only the newest column leaves the "
                        "scrolled history stale",
                   run_detail(cut));
        }
        if (!render_both(*full, *partial, scene)) return;
        {
            partial->invalidate(InvalidationReason::reload);
            if (!render_both(*full, *partial, scene)) return;
            PlotOverride off(hooks::PlotDecimation::min_max, {.previous_spans = false});
            const auto cut = run_feed(10, feed, sampler, scene, *full, *partial,
                                      box_pixels(kMeterBox, scale, 1), [&](int tick) {
                                          const float rms[]{0.9F - 0.08F * tick};
                                          const float peak[]{0.95F - 0.08F * tick};
                                          feed.publish_meter(1, rms, peak);
                                      });
            expect(cut.differing > 0 && cut.partial > 0,
                   at + "negative control: without the old spans a falling meter leaves "
                        "stale pixels",
                   run_detail(cut));
        }
    }
}

// A plot rebound to a feed, then a channel, that has never published drops
// its old samples, and the incremental frame matches a full redraw.
void rebind_to_unpublished() {
    for (const float scale : {1.0F, 2.0F}) {
        const std::string at = "@" + std::to_string(static_cast<int>(scale)) + " ";
        InMemoryValueFeed live({{.name = "wave", .shape = ChannelShape::vector, .capacity = 512}});
        InMemoryValueFeed silent({{.name = "wave", .shape = ChannelShape::vector, .capacity = 512},
                                  {.name = "other", .shape = ChannelShape::vector,
                                   .capacity = 512}});
        Scene scene = feed_scene();
        PlotFeedSampler sampler;
        std::vector<float> block(512);
        for (std::size_t i = 0; i < block.size(); ++i) block[i] = wave_value(3, i);
        live.publish_vector(0, block);
        sampler.sample(live, scene);
        auto full = make_surface(scale);
        auto partial = make_surface(scale, true);
        if (!full || !partial || !render_both(*full, *partial, scene)) return;
        const auto check = [&](const std::string& what) {
            if (!render_both(*full, *partial, scene)) return;
            const auto stats = partial->frame_stats();
            const auto* wave = find_mutable(scene.root, "wave");
            std::string message;
            const bool same = identical(*full, *partial, message);
            expect(wave->plot->samples.empty() && !stats.full_repaint &&
                       stats.damage_area_pixels > 0 &&
                       stats.damage_area_pixels <= box_pixels(kWaveBox, scale, 1) && same,
                   at + what, describe(stats) + " " + message);
        };
        sampler.sample(silent, scene);
        check("switching to an unpublished feed clears the plot byte-identically");
        live.publish_vector(0, block);
        sampler.sample(live, scene);
        if (!render_both(*full, *partial, scene)) return;
        auto* wave = find_mutable(scene.root, "wave");
        auto rebound = std::make_shared<PlotSpec>(*wave->plot);
        rebound->channel = "other";
        wave->plot = rebound;
        sampler.sample(silent, scene);
        check("rebinding to an unpublished channel clears the plot byte-identically");
    }
}

void large_feed() {
    constexpr std::size_t samples = 1U << 20U;
    InMemoryValueFeed feed({{.name = "dense", .shape = ChannelShape::vector,
                             .capacity = samples}});
    Scene scene = empty_scene();
    scene.root.children.push_back(plot_node(
        "dense", {16.0F, 16.0F, 256.0F, 128.0F},
        spec_of(PlotStyle::line, PlotChannelShape::vector, samples, {}, "dense")));
    PlotFeedSampler sampler;
    std::vector<float> block(samples);
    auto full = make_surface(2.0F);
    auto partial = make_surface(2.0F, true);
    if (!full || !partial) return;
    for (int round = 0; round < 3; ++round) {
        for (std::size_t i = 0; i < samples; ++i) {
            block[i] = 0.5F + 0.45F * std::sin(0.0007F * static_cast<float>(i) + round) +
                       ((i % 65521U) == static_cast<std::size_t>(round) ? 0.05F : 0.0F);
        }
        feed.publish_vector(0, block);
        sampler.sample(feed, scene);
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = full->frame_stats();
        const auto geometry = plot_geometry(*scene.root.children[0].plot, kPlotFill,
                                            scene.root.children[0].bounds, 2.0F);
        expect(stats.paint_command_draws <= 1U && geometry.columns == 512U &&
                   geometry.rects.size() <= geometry.columns + 1U,
               "a million-sample block draws one command of at most one span per column",
               describe(stats) + " rects=" + std::to_string(geometry.rects.size()));
        std::string message;
        expect(identical(*full, *partial, message),
               "a million-sample block redraws incrementally byte-identical", message);
    }
}

void version_gate() {
    auto surface = make_surface(1.0F);
    if (!surface) return;
    Scene older = oracle_scene();
    older.scene_version = 11U;
    std::string error;
    expect(!surface->render(older, &error) && surface->diagnostics().size() == 1U &&
               surface->diagnostics()[0].code == kSceneVersionMismatchCode &&
               surface->diagnostics()[0].node_id == "scope",
           "a version 11 scene with a plot is refused with a version mismatch", error);
    Scene legal = older;
    legal.root.children.clear();
    legal.root.children.push_back({.id = "box", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {4.0F, 4.0F, 20.0F, 20.0F},
                                   .fill = kArea, .opacity = 0.5F, .isolate = true});
    expect(surface->render(legal, &error), "a version 11 scene without a plot still draws",
           error);

    auto full = make_surface(1.0F);
    auto gated = make_surface(1.0F);
    auto current = make_surface(1.0F);
    if (!full || !gated || !current) return;
    SkiaDawnSurfaceTestAccess::set_damage_model_version(*gated, 11U);
    Scene scene = oracle_scene();
    if (!render_both(*full, *gated, scene) || !render_both(*full, *current, scene)) return;
    std::vector<float> moved(2000, 0.6F);
    set_samples(scene, "scope", moved);
    if (!render_both(*full, *gated, scene)) return;
    if (!render_both(*full, *current, scene)) return;
    const auto fallback = gated->frame_stats();
    const auto taken = current->frame_stats();
    expect(!taken.full_repaint && taken.damage_area_pixels > 0,
           "a version 12 plot update takes the partial path at the damage gate", describe(taken));
    expect(fallback.full_repaint &&
               fallback.full_repaint_reason == FullRepaintReason::damage_model_version,
           "negative control: a damage model before version 12 repaints plots fully",
           describe(fallback));
    std::string message;
    expect(identical(*full, *current, message), "the gated plot update is byte-identical",
           message);
}

}  // namespace

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "FAIL device bootstrap: " << error << '\n';
        return 1;
    }
    pixel_oracles();
    if (gpu_missing) return 77;
    sweeps();
    gradient_plot();
    plot_under_blur();
    feed_updates();
    rebind_to_unpublished();
    large_feed();
    version_gate();
    if (failures != 0) {
        std::cerr << failures << " plot GPU check(s) failed\n";
        return 1;
    }
    std::cout << "plot GPU checks passed\n";
    return 0;
}
