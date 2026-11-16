#include "../src/animation_testing.hpp"
#include "../src/skia_dawn_surface_test_access.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/animation.hpp>
#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;
namespace hooks = vellum::graphics::testing;

int failures = 0;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

constexpr float kWidth = 200.0F;
constexpr float kHeight = 140.0F;

std::string describe(const FrameStats& stats) {
    std::ostringstream out;
    out << "full=" << stats.full_repaint << " reason=" << to_string(stats.full_repaint_reason)
        << " damage_px=" << stats.damage_area_pixels << '/' << stats.frame_area_pixels;
    return out.str();
}

std::unique_ptr<SkiaDawnSurface> make_surface(float scale, bool layer_cache = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(kWidth),
         .height = static_cast<std::uint32_t>(kHeight),
         .scale = scale},
        &error);
    expect(surface != nullptr, "offscreen surface is created", error);
    if (surface && !layer_cache) {
        LayerCachePolicy policy;
        policy.enabled = false;
        surface->set_layer_cache_policy(policy);
    }
    return surface;
}

struct Capture final {
    bool ok = false;
    std::string error;
    harness::CaptureResult result;
};

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

struct RulesOverride final {
    explicit RulesOverride(const hooks::AnimationRules& rules) : saved(hooks::animation_rules()) {
        hooks::set_animation_rules(rules);
    }
    ~RulesOverride() { hooks::set_animation_rules(saved); }
    RulesOverride(const RulesOverride&) = delete;
    RulesOverride& operator=(const RulesOverride&) = delete;
    hooks::AnimationRules saved;
};

AnimationKeyframe key(double time, float value, Easing easing = {}) {
    return {.time = time, .value = value, .easing = easing};
}

AnimationKeyframe color_key(double time, Color color, Easing easing = {}) {
    return {.time = time, .color = color, .easing = easing};
}

AnimationTrack track(AnimatedProperty property, std::vector<AnimationKeyframe> keys,
                     std::uint32_t index = 0U, std::uint32_t stop = 0U) {
    return {.property = property, .index = index, .stop = stop, .keyframes = std::move(keys)};
}

SceneNode box(std::string id, Rect bounds, Color fill) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = fill};
}

const Easing kSpring{.kind = EasingKind::spring, .stiffness = 170.0F, .damping = 26.0F};

// One node per animated property, each with its own timing, so a frame mixes
// moving tracks with tracks at rest.
Scene animated_scene(double clock) {
    Scene scene;
    scene.scene_version = kDamageModelSceneVersion;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.background = Color::hex(0x000000);
    scene.clock = clock;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    auto& children = scene.root.children;

    auto fade = box("fade", {10, 10, 30, 30}, Color::hex(0xFFFFFF));
    fade.tracks.push_back(track(AnimatedProperty::opacity, {key(0.0, 0.0F), key(1.0, 1.0F)}));
    children.push_back(fade);

    auto tint = box("tint", {50, 10, 30, 30}, Color::hex(0xFF0000));
    tint.tracks.push_back(track(AnimatedProperty::fill,
                                {color_key(0.0, Color::hex(0xFF0000)),
                                 color_key(1.0, Color::hex(0x0000FF))}));
    children.push_back(tint);

    auto stepped = box("stepped", {90, 10, 30, 30}, Color::hex(0xFFFFFF));
    stepped.tracks.push_back(track(
        AnimatedProperty::opacity,
        {key(0.0, 0.0F, {.kind = EasingKind::steps, .steps = 4U}), key(1.0, 1.0F)}));
    children.push_back(stepped);

    auto eased = box("eased", {130, 10, 30, 30}, Color::hex(0xFFFFFF));
    eased.tracks.push_back(track(
        AnimatedProperty::opacity,
        {key(0.0, 0.0F, {.kind = EasingKind::cubic_bezier, .x1 = 0.25F, .y1 = 0.1F,
                         .x2 = 0.25F, .y2 = 1.0F}),
         key(1.0, 1.0F)}));
    children.push_back(eased);

    auto slide = box("slide", {10, 60, 20, 20}, Color::hex(0x00FF00));
    slide.tracks.push_back(
        track(AnimatedProperty::translate_x, {key(0.5, 0.0F), key(1.5, 40.0F)}));
    children.push_back(slide);

    auto spring = box("spring", {100, 60, 30, 30}, Color::hex(0xFFFFFF));
    spring.tracks.push_back(track(AnimatedProperty::opacity,
                                  {key(0.0, 0.0F, kSpring),
                                   key(spring_settle_time(kSpring), 1.0F)}));
    children.push_back(spring);

    auto turn = box("turn", {170, 60, 20, 20}, Color::hex(0xFFFFFF));
    turn.tracks.push_back(
        track(AnimatedProperty::rotation_degrees, {key(1.0, 0.0F), key(2.0, 90.0F)}));
    children.push_back(turn);

    auto glow = box("glow", {14, 104, 24, 16}, Color::hex(0x334155));
    glow.box_shadows.push_back({.blur_radius = 4.0F, .spread_radius = 2.0F,
                                .color = Color::hex(0x000000)});
    glow.tracks.push_back(track(AnimatedProperty::shadow_color,
                                {color_key(0.3, Color::hex(0x000000)),
                                 color_key(1.3, Color::hex(0xF59E0B))}));
    children.push_back(glow);

    auto ramp = box("ramp", {56, 100, 40, 24}, Color::hex(0x000000));
    ramp.fill_gradients.push_back({.angle_degrees = 90.0F,
                                   .stops = {{0.0F, Color::hex(0x1D4ED8)},
                                             {1.0F, Color::hex(0x1D4ED8)}}});
    ramp.tracks.push_back(track(AnimatedProperty::gradient_stop_color,
                                {color_key(0.8, Color::hex(0x1D4ED8)),
                                 color_key(1.8, Color::hex(0xF43F5E))},
                                0U, 1U));
    children.push_back(ramp);

    auto orb = box("orb", {100, 100, 24, 24}, Color::hex(0x000000));
    orb.fill_gradients.push_back({.kind = GradientKind::radial,
                                  .spread = GradientSpread::reflect,
                                  .stops = {{0.0F, Color::hex(0xFDE68A)},
                                            {1.0F, Color::hex(0x7C3AED)}}});
    orb.tracks.push_back(track(AnimatedProperty::gradient_stop_color,
                               {color_key(0.4, Color::hex(0x7C3AED)),
                                color_key(1.6, Color::hex(0x10B981))},
                               0U, 1U));
    children.push_back(orb);

    auto geometry = std::make_shared<PathGeometry>();
    geometry->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::line,
                       PathVerb::close};
    geometry->values = {0, 0, 30, 0, 30, 20, 0, 20};
    geometry->stroke = {.color = Color::hex(0x22D3EE), .width = 3.0F};
    SceneNode outline{.id = "outline", .kind = SceneNode::Kind::path,
                      .bounds = {110, 102, 30, 20}, .fill = Color::hex(0x111827)};
    outline.path = geometry;
    outline.tracks.push_back(track(AnimatedProperty::stroke_color,
                                   {color_key(0.2, Color::hex(0x22D3EE)),
                                    color_key(0.9, Color::hex(0xA3E635))}));
    children.push_back(outline);

    SceneNode label{.id = "label", .kind = SceneNode::Kind::text,
                    .bounds = {150, 100, 46, 24}};
    label.text_runs = {{.text = "Ab", .style = {.font_size = 14.0F}},
                       {.text = "c", .style = {.font_size = 14.0F}}};
    label.tracks.push_back(track(AnimatedProperty::text_run_color,
                                 {color_key(1.2, Color::hex(0xFFFFFF)),
                                  color_key(2.2, Color::hex(0xEF4444))},
                                 1U));
    children.push_back(label);
    return scene;
}

struct Pixel final {
    int red = 0;
    int green = 0;
    int blue = 0;
};

Pixel pixel(const Capture& frame, float x, float y, float scale) {
    const auto px = static_cast<std::uint32_t>(x * scale);
    const auto py = static_cast<std::uint32_t>(y * scale);
    const std::size_t index = (static_cast<std::size_t>(py) * frame.result.pixel_width + px) * 4U;
    return {frame.result.rgba[index], frame.result.rgba[index + 1U], frame.result.rgba[index + 2U]};
}

bool near(const Pixel& value, const Pixel& expected, int tolerance = 1) {
    return std::abs(value.red - expected.red) <= tolerance &&
           std::abs(value.green - expected.green) <= tolerance &&
           std::abs(value.blue - expected.blue) <= tolerance;
}

std::string text(const Pixel& value) {
    return std::to_string(value.red) + "," + std::to_string(value.green) + "," +
           std::to_string(value.blue);
}

Pixel gray(double fraction) {
    const int level = static_cast<int>(std::lround(fraction * 255.0));
    return {level, level, level};
}

// An independent oracle for the spring: fourth-order Runge-Kutta on
// m x'' + c x' + k x = 0 from x = -1 at rest, with progress 1 + x.
double integrated_spring(const Easing& easing, double seconds) {
    const double k = easing.stiffness;
    const double c = easing.damping;
    const double m = easing.mass;
    double x = -1.0;
    double v = easing.initial_velocity;
    const int steps = 20000;
    const double h = seconds / steps;
    const auto accel = [&](double position, double speed) { return (-k * position - c * speed) / m; };
    for (int step = 0; step < steps; ++step) {
        const double k1x = v;
        const double k1v = accel(x, v);
        const double k2x = v + 0.5 * h * k1v;
        const double k2v = accel(x + 0.5 * h * k1x, v + 0.5 * h * k1v);
        const double k3x = v + 0.5 * h * k2v;
        const double k3v = accel(x + 0.5 * h * k2x, v + 0.5 * h * k2v);
        const double k4x = v + h * k3v;
        const double k4v = accel(x + h * k3x, v + h * k3v);
        x += h / 6.0 * (k1x + 2.0 * k2x + 2.0 * k3x + k4x);
        v += h / 6.0 * (k1v + 2.0 * k2v + 2.0 * k3v + k4v);
    }
    return 1.0 + x;
}

// Pixel oracles: values computed from the easing definitions, independent of
// the renderer, checked at chosen clock values.
void pixel_oracles(float scale) {
    const std::string size = "scale " + std::to_string(static_cast<int>(scale)) + ": ";
    auto surface = make_surface(scale);
    if (!surface) return;
    const auto at = [&](double clock) {
        std::string error;
        Capture frame;
        if (!expect(surface->render(animated_scene(clock), &error),
                    size + "frame at clock " + std::to_string(clock) + " renders", error)) {
            return frame;
        }
        return capture(*surface);
    };
    const auto start = at(0.0);
    if (!start.ok) return;
    expect(near(pixel(start, 65, 25, scale), {255, 0, 0}, 0),
           size + "control: an opaque red fill reads 255,0,0",
           text(pixel(start, 65, 25, scale)));
    expect(near(pixel(start, 25, 25, scale), {0, 0, 0}, 0),
           size + "a fade at clock 0 shows the background", text(pixel(start, 25, 25, scale)));

    const auto quarter = at(0.25);
    expect(near(pixel(quarter, 25, 25, scale), gray(0.25)),
           size + "linear opacity at clock 0.25 draws 25% white",
           text(pixel(quarter, 25, 25, scale)));
    expect(near(pixel(quarter, 105, 25, scale), gray(0.25)),
           size + "steps(4) opacity at clock 0.25 draws its first step",
           text(pixel(quarter, 105, 25, scale)));

    const auto half = at(0.5);
    expect(near(pixel(half, 65, 25, scale), {128, 0, 128}),
           size + "a red to blue fill at clock 0.5 draws 128,0,128",
           text(pixel(half, 65, 25, scale)));
    expect(near(pixel(half, 145, 25, scale), gray(0.8024033877)),
           size + "ease opacity at clock 0.5 draws 80.24% white",
           text(pixel(half, 145, 25, scale)));
    expect(near(pixel(half, 105, 25, scale), gray(0.5)),
           size + "steps(4) opacity at clock 0.5 draws its second step",
           text(pixel(half, 105, 25, scale)));
    expect(near(pixel(half, 25, 25, scale), gray(0.25), 3) == false,
           size + "negative control: the clock 0.25 oracle fails at clock 0.5",
           text(pixel(half, 25, 25, scale)));

    const double spring_clock = 0.12;
    const auto springing = at(spring_clock);
    const double progress = std::clamp(integrated_spring(kSpring, spring_clock), 0.0, 1.0);
    expect(progress > 0.2 && progress < 0.95 &&
               near(pixel(springing, 115, 75, scale), gray(progress)),
           size + "a spring mid flight draws its progress as opacity",
           text(pixel(springing, 115, 75, scale)) + " expected " + text(gray(progress)));

    const auto one = at(1.0);
    expect(near(pixel(one, 40, 70, scale), {0, 255, 0}, 0) &&
               near(pixel(one, 12, 70, scale), {0, 0, 0}, 0),
           size + "a slide halfway through moves the box 20 pixels right",
           text(pixel(one, 40, 70, scale)) + " / " + text(pixel(one, 12, 70, scale)));

    const auto settled = at(3.0);
    expect(near(pixel(settled, 115, 75, scale), {255, 255, 255}, 0),
           size + "a settled spring draws its exact target",
           text(pixel(settled, 115, 75, scale)));
    expect(near(pixel(settled, 160, 70, scale), {255, 255, 255}, 0) &&
               near(pixel(settled, 180, 70, scale), {0, 0, 0}, 0),
           size + "a quarter turn about the node origin swings the box left of it",
           text(pixel(settled, 160, 70, scale)) + " / " + text(pixel(settled, 180, 70, scale)));
    expect(near(pixel(settled, 65, 25, scale), {0, 0, 255}, 0) &&
               near(pixel(settled, 25, 25, scale), {255, 255, 255}, 0),
           size + "tracks past their last keyframe hold their last value",
           text(pixel(settled, 65, 25, scale)));
}

// Two fresh surfaces fed the same clocks produce the same frames, byte for
// byte; a run on shifted clocks does not.
void replay(float scale) {
    const std::string size = "scale " + std::to_string(static_cast<int>(scale)) + ": ";
    auto first = make_surface(scale);
    auto second = make_surface(scale);
    auto shifted = make_surface(scale);
    if (!first || !second || !shifted) return;
    int differing = 0;
    int shifted_differing = 0;
    int frames = 0;
    for (int frame = 0; frame <= 75; ++frame) {
        const double clock = frame / 30.0;
        std::string error;
        if (!first->render_incremental(animated_scene(clock), &error) ||
            !second->render_incremental(animated_scene(clock), &error) ||
            !shifted->render_incremental(animated_scene(clock + 0.004), &error)) {
            expect(false, size + "replay frame renders", error);
            return;
        }
        ++frames;
        std::string message;
        if (!identical(*first, *second, message)) ++differing;
        if (!identical(*first, *shifted, message)) ++shifted_differing;
    }
    expect(frames == 76 && differing == 0,
           size + "two runs over the same clocks are byte-identical every frame",
           std::to_string(differing) + " of " + std::to_string(frames) + " differ");
    expect(shifted_differing > 0,
           size + "control: a run four milliseconds behind differs",
           std::to_string(shifted_differing) + " frames differ");
}

struct SweepCount final {
    int differing = 0;
    int partial = 0;
    int damaged_partial = 0;
    int resting_quiet = 0;
    int resting = 0;
    std::string first;
};

// Renders the animation on a full-repaint surface and an incremental one and
// compares every frame. The last frames run with every track at rest.
SweepCount sweep(float scale, bool layer_cache, std::uint32_t damage_model = 0U) {
    SweepCount count;
    auto full = make_surface(scale, layer_cache);
    auto partial = make_surface(scale, layer_cache);
    if (!full || !partial) return {.differing = -1};
    if (damage_model != 0U) {
        SkiaDawnSurfaceTestAccess::set_damage_model_version(*partial, damage_model);
    }
    for (int frame = 0; frame <= 80; ++frame) {
        const double clock = frame <= 70 ? frame / 30.0 : 3.0 + frame / 60.0;
        const auto scene = animated_scene(clock);
        std::string error;
        if (!full->render(scene, &error) || !partial->render_incremental(scene, &error)) {
            expect(false, "sweep frame renders", error);
            return {.differing = -1};
        }
        const auto stats = partial->frame_stats();
        if (!stats.full_repaint) ++count.partial;
        if (!stats.full_repaint && stats.damage_area_pixels > 0) ++count.damaged_partial;
        if (frame > 71) {
            ++count.resting;
            if (!stats.full_repaint && stats.damage_area_pixels == 0) ++count.resting_quiet;
        }
        std::string message;
        if (!identical(*full, *partial, message)) {
            if (count.differing == 0) {
                count.first = "clock " + std::to_string(clock) + ": " + message + " " +
                              describe(stats);
            }
            ++count.differing;
        }
    }
    return count;
}

void partial_redraw(float scale, bool layer_cache) {
    const std::string label = "scale " + std::to_string(static_cast<int>(scale)) +
                              (layer_cache ? " cached: " : ": ");
    const auto count = sweep(scale, layer_cache);
    expect(count.differing == 0 && count.partial > 0 && count.damaged_partial > 0,
           label + "animated frames redrawn from damage match full redraws",
           std::to_string(count.differing) + " differ, " + std::to_string(count.partial) +
               " partial, " + std::to_string(count.damaged_partial) + " with damage; " +
               count.first);
    expect(count.resting > 0 && count.resting_quiet == count.resting,
           label + "frames with every track at rest damage nothing",
           std::to_string(count.resting_quiet) + " of " + std::to_string(count.resting));
    {
        RulesOverride blind({.evaluate_for_damage = false});
        const auto stale = sweep(scale, layer_cache);
        expect(stale.differing > 0 && stale.partial > 0,
               label + "negative control: damage from static fields leaves stale frames",
               std::to_string(stale.differing) + " differ, " + std::to_string(stale.partial) +
                   " partial");
    }
    const auto gated = sweep(scale, layer_cache, 10U);
    expect(gated.differing == 0 && gated.partial == 0,
           label + "control: below the damage gate every animated frame repaints fully",
           std::to_string(gated.partial) + " partial");
}

void refusal_keeps_frame() {
    auto surface = make_surface(1.0F);
    if (!surface) return;
    std::string error;
    if (!expect(surface->render(animated_scene(0.5), &error), "version 11 scene renders", error)) {
        return;
    }
    const auto before = capture(*surface);
    auto older = animated_scene(0.75);
    older.scene_version = 10U;
    expect(!surface->render(older, &error) && !surface->diagnostics().empty() &&
               surface->diagnostics()[0].code == kSceneVersionMismatchCode &&
               surface->diagnostics()[0].property == "tracks",
           "a version 10 scene with tracks is refused with VELLUM_SCENE_VERSION_MISMATCH",
           error);
    const auto after = capture(*surface);
    expect(before.ok && after.ok &&
               static_cast<bool>(harness::assert_identical(before.result, after.result)),
           "a refused scene keeps the last frame");
}

}  // namespace

Scene token_scene(double clock) {
    Scene scene;
    scene.scene_version = kDamageModelSceneVersion;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.background = Color::hex(0x000000);
    scene.clock = clock;
    scene.tokens = {{"main.color.accent", "primitive", Color::hex(0xFF0000)}};
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    auto tint = box("tint", {50, 10, 30, 30}, Color::hex(0xFF0000));
    tint.fill_token = 0U;
    tint.tracks.push_back(track(AnimatedProperty::fill,
                                {color_key(0.0, Color::hex(0xFF0000)),
                                 color_key(1.0, Color::hex(0x0000FF))}));
    scene.root.children.push_back(std::move(tint));
    return scene;
}

void animated_token_fill(float scale) {
    const std::string size = "scale " + std::to_string(static_cast<int>(scale)) + ": ";
    auto surface = make_surface(scale);
    if (!surface) return;
    std::string error;
    if (!expect(surface->render(token_scene(0.0), &error),
                size + "a token-driven fill at clock 0 renders", error)) {
        return;
    }
    const auto start = capture(*surface);
    expect(start.ok && near(pixel(start, 65, 25, scale), {255, 0, 0}, 0),
           size + "a token-driven fill at rest draws its receipt colour",
           text(pixel(start, 65, 25, scale)));
    if (!expect(surface->render(token_scene(0.5), &error),
                size + "a token-driven fill animated to another colour renders", error)) {
        return;
    }
    const auto half = capture(*surface);
    expect(half.ok && near(pixel(half, 65, 25, scale), {128, 0, 128}),
           size + "render draws the evaluated token fill at clock 0.5",
           text(pixel(half, 65, 25, scale)));
    if (!expect(surface->render_incremental(token_scene(1.0), &error),
                size + "an incremental frame of an animated token fill renders", error)) {
        return;
    }
    const auto end = capture(*surface);
    expect(end.ok && near(pixel(end, 65, 25, scale), {0, 0, 255}),
           size + "render_incremental draws the evaluated token fill at clock 1",
           text(pixel(end, 65, 25, scale)));
    auto stale = token_scene(0.5);
    stale.root.children[0].fill = Color::hex(0x00FF00);
    expect(!surface->render(stale, &error),
           size + "control: a stale authored token fill is still refused");
}

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cout << "FAIL device bootstrap: " << error << '\n';
        return 1;
    }
    pixel_oracles(1.0F);
    pixel_oracles(2.0F);
    replay(1.0F);
    replay(2.0F);
    partial_redraw(1.0F, false);
    partial_redraw(2.0F, false);
    partial_redraw(2.0F, true);
    refusal_keeps_frame();
    animated_token_fill(1.0F);
    animated_token_fill(2.0F);
    if (failures != 0) {
        std::cout << failures << " animation GPU checks failed\n";
        return 1;
    }
    std::cout << "all animation GPU checks passed\n";
    return 0;
}
