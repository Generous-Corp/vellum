#include "support/gpu_test_environment.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/animation.hpp>
#include <vellum/graphics/frame_driver.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/value/in_memory_value_feed.hpp>

#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

int failures = 0;
bool surface_failed = false;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

constexpr float kWidth = 200.0F;
constexpr float kHeight = 120.0F;

std::unique_ptr<SkiaDawnSurface> make_surface(float scale) {
    auto created = harness::create_gpu_surface(
        {.width = static_cast<std::uint32_t>(kWidth),
         .height = static_cast<std::uint32_t>(kHeight),
         .scale = scale});
    if (!created.surface) {
        surface_failed = true;
        expect(false, "GPU surface", created.status.message);
        return nullptr;
    }
    auto surface = std::move(created.surface);
    LayerCachePolicy policy;
    policy.enabled = false;
    surface->set_layer_cache_policy(policy);
    return surface;
}

bool grab(SkiaDawnSurface& surface, harness::CaptureResult& out, std::string& error) {
    out.evidence = surface.evidence();
    out.ok = surface.capture_rgba(out.rgba, out.pixel_width, out.pixel_height, &error);
    return out.ok;
}

AnimationKeyframe key(double time, float value, Easing easing = {}) {
    return {.time = time, .value = value, .easing = easing};
}

AnimationKeyframe color_key(double time, Color color) { return {.time = time, .color = color}; }

SceneNode box(std::string id, Rect bounds, Color fill) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = fill};
}

Scene make_scene() {
    Scene scene;
    scene.scene_version = kDamageModelSceneVersion;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.background = Color::hex(0x000000);
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    auto& children = scene.root.children;

    auto fade = box("fade", {10, 10, 30, 30}, Color::hex(0xFFFFFF));
    fade.tracks.push_back({.property = AnimatedProperty::opacity,
                           .keyframes = {key(0.0, 0.0F), key(1.0, 1.0F)}});
    children.push_back(fade);

    auto tint = box("tint", {50, 10, 30, 30}, Color::hex(0xFF0000));
    tint.tracks.push_back({.property = AnimatedProperty::fill,
                           .keyframes = {color_key(0.2, Color::hex(0xFF0000)),
                                         color_key(1.2, Color::hex(0x0000FF))}});
    children.push_back(tint);

    auto slide = box("slide", {10, 60, 20, 20}, Color::hex(0x00FF00));
    slide.tracks.push_back({.property = AnimatedProperty::translate_x,
                            .keyframes = {key(0.5, 0.0F), key(1.5, 80.0F)}});
    children.push_back(slide);

    auto spec = std::make_shared<PlotSpec>();
    spec->style = PlotStyle::line;
    spec->channel = "level";
    spec->shape = PlotChannelShape::scalar;
    spec->slot_count = 24;
    spec->line_width = 2.0F;
    SceneNode plot{.id = "level-plot", .kind = SceneNode::Kind::plot,
                   .bounds = {100, 10, 90, 40}, .fill = Color::hex(0x1E293B)};
    plot.plot = std::move(spec);
    children.push_back(std::move(plot));
    return scene;
}

struct Totals final {
    int frames = 0;
    int differing = 0;
    int partial = 0;
    int moved = 0;
    int render_failures = 0;
    int idle_renders = 0;
    int requested_after_settle = 0;
    bool settled = false;
    std::string first;
};

Totals run(float scale, bool reduced_motion) {
    Totals totals;
    auto full = make_surface(scale);
    auto partial = make_surface(scale);
    if (!full || !partial) return totals;

    vellum::value::InMemoryValueFeed feed(
        {{.name = "level", .shape = vellum::value::ChannelShape::scalar}});
    FrameDriverOptions options;
    options.reduced_motion = reduced_motion;
    options.feed.stale_after_ticks = 4;
    FrameDriver driver(
        [&](const Scene& scene, std::string* error) {
            return partial->render_incremental(scene, error);
        },
        options);
    driver.set_feed(&feed);

    Scene scene = make_scene();
    harness::CaptureResult previous;
    std::string error;
    double now = 900.0;
    bool have_previous = false;
    bool settled = false;
    for (int tick = 0; tick < 120; ++tick) {
        if (tick < 12) feed.publish_scalar(0, 0.1F + 0.07F * static_cast<float>(tick));
        const auto report = driver.frame(scene, now);
        now += 1.0 / 30.0;
        if (settled && report.rendered) ++totals.idle_renders;
        if (!report.render_ok) {
            ++totals.render_failures;
            break;
        }
        if (report.rendered) {
            ++totals.frames;
            if (!partial->frame_stats().full_repaint) ++totals.partial;
            if (!full->render(scene, &error)) {
                ++totals.render_failures;
                break;
            }
            harness::CaptureResult left;
            harness::CaptureResult right;
            if (!grab(*partial, left, error) || !grab(*full, right, error)) {
                ++totals.render_failures;
                break;
            }
            const auto verdict = harness::assert_identical(left, right);
            if (!verdict) {
                if (totals.differing == 0) {
                    totals.first = "clock " + std::to_string(scene.clock) + ": " + verdict.message;
                }
                ++totals.differing;
            }
            if (have_previous && !static_cast<bool>(harness::assert_identical(previous, left))) {
                ++totals.moved;
            }
            previous = std::move(left);
            have_previous = true;
        }
        if (!report.needs_next_frame) {
            settled = true;
            if (tick > 100) break;
        } else if (settled) {
            ++totals.requested_after_settle;
        }
    }
    totals.settled = settled;
    return totals;
}

void animates_and_matches_full_redraw(float scale) {
    const std::string label = "scale " + std::to_string(static_cast<int>(scale)) + ": ";
    const auto totals = run(scale, false);
    if (surface_failed) return;
    expect(totals.render_failures == 0, label + "every driven frame renders");
    expect(totals.frames >= 20, label + "the track and the feed produce a run of frames",
           std::to_string(totals.frames));
    expect(totals.differing == 0, label + "each incremental frame is byte-identical to a full redraw",
           std::to_string(totals.differing) + " differ; " + totals.first);
    expect(totals.partial > 0, label + "frames after the first are partial redraws",
           std::to_string(totals.partial));
    expect(totals.moved >= 15, label + "consecutive frames show motion",
           std::to_string(totals.moved) + " of " + std::to_string(totals.frames));
    expect(totals.settled, label + "the driver reports that no further frame is needed");
    expect(totals.idle_renders == 0 && totals.requested_after_settle == 0,
           label + "once settled the driver renders nothing and requests nothing",
           std::to_string(totals.idle_renders) + " renders, " +
               std::to_string(totals.requested_after_settle) + " requests");
}

void reduced_motion_draws_the_end_state() {
    auto surface = make_surface(1.0F);
    auto reference = make_surface(1.0F);
    if (!surface || !reference) return;
    FrameDriverOptions options;
    options.reduced_motion = true;
    FrameDriver driver(
        [&](const Scene& scene, std::string* error) {
            return surface->render_incremental(scene, error);
        },
        options);
    Scene scene = make_scene();
    scene.root.children.pop_back();
    const auto report = driver.frame(scene, 1.0);
    Scene rest = make_scene();
    rest.root.children.pop_back();
    rest.clock = 100.0;
    std::string error;
    harness::CaptureResult left;
    harness::CaptureResult right;
    const bool ok = report.render_ok && reference->render(rest, &error) &&
                    grab(*surface, left, error) && grab(*reference, right, error);
    const auto verdict = ok ? harness::assert_identical(left, right) : harness::Verdict{};
    expect(ok && static_cast<bool>(verdict) && !report.needs_next_frame,
           "reduced motion draws the settled frame at once and stops",
           error + " " + verdict.message);
}

}  // namespace

int main() {
    const auto environment = harness::prepare_gpu_environment();
    if (!environment.ready()) return harness::report_gpu_status(environment);
    animates_and_matches_full_redraw(1.0F);
    if (surface_failed) {
        std::cout << failures << " frame driver GPU checks failed\n";
        return 1;
    }
    animates_and_matches_full_redraw(2.0F);
    reduced_motion_draws_the_end_state();
    if (failures != 0) {
        std::cout << failures << " frame driver GPU checks failed\n";
        return 1;
    }
    std::cout << "all frame driver GPU checks passed\n";
    return 0;
}
