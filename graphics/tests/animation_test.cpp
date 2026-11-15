#include "animation_testing.hpp"

#include <vellum/graphics/animation.hpp>
#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace hooks = vellum::graphics::testing;

int failures = 0;

void check(bool ok, const std::string& what) {
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str());
    if (!ok) ++failures;
}

bool near(double value, double expected, double tolerance) {
    return std::fabs(value - expected) <= tolerance;
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

Scene scene_with(std::vector<SceneNode> children, double clock = 0.0) {
    Scene scene;
    scene.width = 200.0F;
    scene.height = 100.0F;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group, .bounds = {0, 0, 200, 100}};
    scene.root.children = std::move(children);
    scene.clock = clock;
    return scene;
}

const SceneNode& evaluated_node(const Scene& scene, const std::string& id) {
    static Scene holder;
    holder = evaluate_tracks(scene);
    return *find_node(holder, id);
}

Easing spring(float stiffness, float damping, float mass = 1.0F, float velocity = 0.0F) {
    return {.kind = EasingKind::spring, .stiffness = stiffness, .damping = damping, .mass = mass,
            .initial_velocity = velocity};
}

// An independent oracle for the spring: fourth-order Runge-Kutta on
// m x'' + c x' + k x = 0 from x = -1, x' = v, with progress 1 + x.
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

void easing_oracles() {
    check(eased_progress({}, 0.3) == 0.3, "linear easing returns elapsed time");
    const Easing ease{.kind = EasingKind::cubic_bezier, .x1 = 0.25F, .y1 = 0.1F, .x2 = 0.25F,
                      .y2 = 1.0F};
    check(near(eased_progress(ease, 0.5), 0.8024033877, 1.0e-6),
          "cubic-bezier ease at half time matches the CSS value 0.8024");
    const Easing ease_in{.kind = EasingKind::cubic_bezier, .x1 = 0.42F, .y1 = 0.0F, .x2 = 1.0F,
                         .y2 = 1.0F};
    check(near(eased_progress(ease_in, 0.5), 0.3153568, 1.0e-5),
          "cubic-bezier ease-in at half time matches the CSS value 0.3154");
    const Easing back{.kind = EasingKind::cubic_bezier, .x1 = 0.3F, .y1 = 1.8F, .x2 = 0.7F,
                      .y2 = 1.8F};
    check(eased_progress(back, 0.5) > 1.0, "a cubic bezier can overshoot");
    const auto stepped = [](std::uint32_t steps, StepPosition position) {
        return Easing{.kind = EasingKind::steps, .steps = steps, .step_position = position};
    };
    check(eased_progress(stepped(4, StepPosition::jump_end), 0.6) == 0.5,
          "steps(4, jump-end) at 0.6 is 0.5");
    check(eased_progress(stepped(4, StepPosition::jump_start), 0.6) == 0.75,
          "steps(4, jump-start) at 0.6 is 0.75");
    check(eased_progress(stepped(3, StepPosition::jump_none), 0.6) == 0.5,
          "steps(3, jump-none) at 0.6 is 0.5");
    check(eased_progress(stepped(3, StepPosition::jump_both), 0.1) == 0.25,
          "steps(3, jump-both) at 0.1 is 0.25");
}

void spring_oracles() {
    const Easing under = spring(100.0F, 10.0F);
    const Easing critical = spring(100.0F, 20.0F);
    const Easing over = spring(100.0F, 40.0F);
    const Easing launched = spring(170.0F, 26.0F, 1.0F, 4.0F);
    for (const auto& [name, easing] : std::vector<std::pair<std::string, Easing>>{
             {"underdamped", under}, {"critically damped", critical},
             {"overdamped", over}, {"launched", launched}}) {
        bool agrees = true;
        for (const double t : {0.05, 0.13, 0.31, 0.6}) {
            agrees = agrees && near(spring_progress(easing, t), integrated_spring(easing, t), 1.0e-7);
        }
        check(agrees, name + " spring matches a numerically integrated oscillator");
        const double settle = spring_settle_time(easing);
        check(std::isfinite(settle) && settle > 0.0, name + " spring has a finite settle time");
        check(spring_progress(easing, settle) == 1.0 && spring_progress(easing, settle + 5.0) == 1.0,
              name + " spring is exactly at its target from its settle time on");
        bool quiet = true;
        for (double t = settle; t < settle + 2.0; t += 0.01) {
            quiet = quiet && std::fabs(integrated_spring(easing, t) - 1.0) < kSpringRestThreshold;
        }
        check(quiet, name + " spring stays within the rest threshold after its settle time");
    }
    double peak = 0.0;
    for (double t = 0.0; t < 1.0; t += 0.005) peak = std::max(peak, spring_progress(under, t));
    check(peak > 1.1, "an underdamped spring overshoots its target");
    bool monotonic = true;
    double previous = 0.0;
    for (double t = 0.0; t < 1.0; t += 0.005) {
        const double value = spring_progress(critical, t);
        monotonic = monotonic && value >= previous && value <= 1.0;
        previous = value;
    }
    check(monotonic, "a critically damped spring from rest approaches without overshoot");
    {
        RulesOverride off({.settle_springs = false});
        check(spring_progress(under, spring_settle_time(under)) != 1.0,
              "negative control: without settling a spring never reaches its target exactly");
    }
}

void evaluation() {
    auto fader = box("fader", {10, 10, 40, 40}, Color::hex(0xFFFFFF));
    fader.tracks.push_back(track(AnimatedProperty::opacity, {key(1.0, 0.0F), key(3.0, 1.0F)}));
    for (const auto& [clock, expected] :
         std::vector<std::pair<double, float>>{{0.0, 0.0F}, {1.0, 0.0F}, {2.0, 0.5F},
                                               {2.5, 0.75F}, {3.0, 1.0F}, {9.0, 1.0F}}) {
        const auto& node = evaluated_node(scene_with({fader}, clock), "fader");
        check(node.opacity == expected && node.tracks.empty(),
              "opacity track at clock " + std::to_string(clock) + " is " +
                  std::to_string(expected));
    }

    auto mover = box("mover", {0, 0, 10, 10}, Color::hex(0xFFFFFF));
    mover.transform = translation(5.0F, 0.0F);
    mover.tracks.push_back(track(AnimatedProperty::translate_x, {key(0.0, 0.0F), key(1.0, 20.0F)}));
    mover.tracks.push_back(
        track(AnimatedProperty::rotation_degrees, {key(0.0, 90.0F), key(1.0, 90.0F)}));
    mover.tracks.push_back(track(AnimatedProperty::scale_y, {key(0.0, 2.0F), key(1.0, 2.0F)}));
    const auto& moved = evaluated_node(scene_with({mover}, 0.5), "mover");
    const auto point = apply(*moved.transform, {1.0F, 1.0F});
    check(near(point.x, 5.0 + 10.0 - 2.0, 1.0e-5) && near(point.y, 1.0, 1.0e-5),
          "transform channels compose translate, rotate then scale inside the node transform");

    auto tint = box("tint", {0, 0, 10, 10}, Color::hex(0xFF0000));
    tint.tracks.push_back(track(AnimatedProperty::fill,
                                {color_key(0.0, Color::rgba(1, 0, 0, 1)),
                                 color_key(1.0, Color::rgba(0, 0, 1, 0))}));
    const auto& tinted = evaluated_node(scene_with({tint}, 0.5), "tint");
    check(tinted.fill.red == 1.0F && tinted.fill.blue == 0.0F && tinted.fill.alpha == 0.5F,
          "colours interpolate premultiplied: red fading to clear blue stays red");

    auto styled = box("styled", {0, 0, 10, 10}, Color::hex(0x000000));
    styled.box_shadows.push_back({.blur_radius = 2.0F, .color = Color::hex(0x000000)});
    styled.fill_gradients.push_back({.stops = {{0.0F, Color::hex(0x000000)},
                                               {1.0F, Color::hex(0x000000)}}});
    styled.tracks.push_back(track(AnimatedProperty::shadow_color,
                                  {color_key(0.0, Color::hex(0x000000)),
                                   color_key(1.0, Color::hex(0xFFFFFF))}));
    styled.tracks.push_back(track(AnimatedProperty::gradient_stop_color,
                                  {color_key(0.0, Color::hex(0x000000)),
                                   color_key(1.0, Color::hex(0x00FF00))},
                                  0U, 1U));
    const auto& restyled = evaluated_node(scene_with({styled}, 0.5), "styled");
    check(restyled.box_shadows[0].color.red == 0.5F &&
              restyled.fill_gradients[0].stops[1].color.green == 0.5F &&
              restyled.fill_gradients[0].stops[0].color.green == 0.0F,
          "shadow and gradient stop colour tracks write the addressed slot only");

    auto geometry = std::make_shared<PathGeometry>();
    geometry->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close};
    geometry->values = {0, 0, 10, 0, 10, 10};
    geometry->stroke = {.color = Color::hex(0x000000), .width = 2.0F};
    SceneNode outline{.id = "outline", .kind = SceneNode::Kind::path, .bounds = {0, 0, 10, 10}};
    outline.path = geometry;
    outline.tracks.push_back(track(AnimatedProperty::stroke_color,
                                   {color_key(0.0, Color::hex(0x000000)),
                                    color_key(1.0, Color::hex(0x0000FF))}));
    const auto& stroked = evaluated_node(scene_with({outline}, 1.0), "outline");
    check(stroked.path->stroke.color.blue == 1.0F && geometry->stroke.color.blue == 0.0F &&
              stroked.path->values == geometry->values,
          "a stroke colour track replaces the geometry without mutating the authored one");

    SceneNode label{.id = "label", .kind = SceneNode::Kind::text, .bounds = {0, 0, 50, 20}};
    label.text_runs = {{.text = "a"}, {.text = "b"}};
    label.tracks.push_back(track(AnimatedProperty::text_run_color,
                                 {color_key(0.0, Color::hex(0x000000)),
                                  color_key(1.0, Color::hex(0xFF0000))},
                                 1U));
    const auto& relabelled = evaluated_node(scene_with({label}, 1.0), "label");
    check(relabelled.text_runs[1].style.color.red == 1.0F &&
              relabelled.text_runs[0].style.color.red != 1.0F,
          "a text run colour track writes its run only");
}

// Every drawn field of every command, as bits, so two lowerings compare
// exactly.
std::vector<std::uint32_t> fingerprint(const Scene& scene) {
    std::vector<std::uint32_t> out;
    const auto f = [&](float value) { out.push_back(std::bit_cast<std::uint32_t>(value)); };
    const auto color = [&](const Color& value) {
        f(value.red);
        f(value.green);
        f(value.blue);
        f(value.alpha);
    };
    const auto rect = [&](const Rect& value) {
        f(value.x);
        f(value.y);
        f(value.width);
        f(value.height);
    };
    for (const auto& command : make_paint_commands(scene)) {
        out.push_back(static_cast<std::uint32_t>(command.kind));
        for (const char c : command.node_id) out.push_back(static_cast<unsigned char>(c));
        rect(command.bounds);
        color(command.fill);
        for (const auto& gradient : command.fill_gradients) {
            f(gradient.angle_degrees);
            for (const auto& stop : gradient.stops) {
                f(stop.position);
                color(stop.color);
            }
        }
        for (const auto& shadow : command.box_shadows) {
            f(shadow.offset_x);
            f(shadow.offset_y);
            f(shadow.blur_radius);
            f(shadow.spread_radius);
            color(shadow.color);
        }
        f(command.corner_radius);
        for (const auto& run : command.text_runs) color(run.style.color);
        if (command.path) {
            color(command.path->stroke.color);
            f(command.path->stroke.width);
            for (const float value : command.path->values) f(value);
        }
        const auto& t = command.transform;
        for (const float value : {t.a, t.b, t.c, t.d, t.tx, t.ty}) f(value);
        f(command.opacity);
        const auto& r = command.corner_radii;
        for (const float value : {r.top_left, r.top_right, r.bottom_right, r.bottom_left}) f(value);
        rect(command.clip_rect);
        out.push_back((command.layer_unbounded ? 1U : 0U) | (command.layer_isolated ? 2U : 0U));
    }
    return out;
}

bool same_bits(const Scene& a, const Scene& b) { return fingerprint(a) == fingerprint(b); }

Scene busy_scene(double clock) {
    auto a = box("a", {10, 10, 20, 20}, Color::hex(0xFF0000));
    a.tracks.push_back(track(AnimatedProperty::rotation_degrees,
                             {key(0.0, 0.0F, {.kind = EasingKind::cubic_bezier, .x1 = 0.3F,
                                              .y1 = 1.4F, .x2 = 0.6F, .y2 = 1.0F}),
                              key(2.0, 180.0F)}));
    a.tracks.push_back(track(AnimatedProperty::opacity,
                             {key(0.0, 0.2F, spring(120.0F, 9.0F)), key(4.0, 1.0F)}));
    a.tracks.push_back(track(AnimatedProperty::fill,
                             {color_key(0.0, Color::hex(0xFF0000),
                                        {.kind = EasingKind::steps, .steps = 5U}),
                              color_key(1.5, Color::hex(0x00FF00, 0.5F))}));
    return scene_with({a}, clock);
}

void determinism() {
    bool replay = true;
    bool sensitive = false;
    for (int frame = 0; frame < 240; ++frame) {
        const double clock = frame / 60.0;
        replay = replay && same_bits(busy_scene(clock), busy_scene(clock));
        sensitive = sensitive || !same_bits(busy_scene(clock), busy_scene(clock + 0.001));
    }
    check(replay, "the same scene at the same clock lowers to bitwise identical commands");
    check(sensitive, "control: a different clock lowers to different commands");
}

void ranges_point_into_the_authored_scene() {
    const auto scene = busy_scene(0.7);
    std::vector<PaintCommandRange> ranges;
    const auto commands = make_paint_commands(scene, &ranges);
    bool authored = !ranges.empty();
    for (const auto& range : ranges) {
        authored = authored && range.node == find_node(scene, range.node->id);
    }
    check(authored, "paint command ranges of a scene with tracks name its authored nodes");
    const auto evaluated = evaluate_tracks(scene);
    check(!commands.empty() && commands.back().opacity == find_node(evaluated, "a")->opacity &&
              commands.back().opacity != find_node(scene, "a")->opacity,
          "commands carry the evaluated opacity rather than the static one");
}

void refused(const Scene& scene, const std::string& fragment, const std::string& what) {
    const auto acceptance = accept_scene(scene);
    check(!acceptance.accepted && acceptance.reason.find(fragment) != std::string::npos,
          what + " (" + acceptance.reason + ")");
}

void accepted(const Scene& scene, const std::string& what) {
    const auto acceptance = accept_scene(scene);
    check(acceptance.accepted, what + " (" + acceptance.reason + ")");
}

void validation() {
    const auto with_track = [](AnimationTrack value, SceneNode node = box("n", {0, 0, 10, 10},
                                                                         Color::hex(0xFFFFFF))) {
        node.tracks.push_back(std::move(value));
        return scene_with({std::move(node)}, 0.5);
    };
    const auto opacity = [&](std::vector<AnimationKeyframe> keys) {
        return with_track(track(AnimatedProperty::opacity, std::move(keys)));
    };
    accepted(opacity({key(0.0, 0.0F), key(1.0, 1.0F)}), "control: a valid opacity track is accepted");
    refused(opacity({}), "no keyframes", "a track without keyframes is refused");
    refused(opacity({key(1.0, 0.0F), key(1.0, 1.0F)}), "does not come after",
            "keyframe times that do not increase are refused");
    refused(opacity({key(0.0, 0.0F), key(std::nan(""), 1.0F)}), "non-finite time",
            "a non-finite keyframe time is refused");
    refused(opacity({key(0.0, 0.0F), key(1.0, std::numeric_limits<float>::infinity())}),
            "non-finite value", "a non-finite keyframe value is refused");
    refused(with_track(track(AnimatedProperty::fill, {color_key(0.0, Color{2.0F, 0, 0, 1})})),
            "outside [0, 1]", "a colour channel outside [0, 1] is refused");
    refused(opacity({key(0.0, 0.0F, {.kind = EasingKind::cubic_bezier, .x1 = 1.5F}),
                     key(1.0, 1.0F)}),
            "x value", "a cubic bezier x outside [0, 1] is refused");
    refused(opacity({key(0.0, 0.0F, {.kind = EasingKind::steps, .steps = 0U}), key(1.0, 1.0F)}),
            "at least one step", "steps easing without steps is refused");
    refused(opacity({key(0.0, 0.0F, {.kind = EasingKind::steps, .steps = 1U,
                                     .step_position = StepPosition::jump_none}),
                     key(1.0, 1.0F)}),
            "two with jump-none", "steps(1, jump-none) is refused");
    refused(opacity({key(0.0, 0.0F, spring(100.0F, 0.0F)), key(10.0, 1.0F)}), "positive finite",
            "an undamped spring is refused");
    const double settle = spring_settle_time(spring(100.0F, 10.0F));
    refused(opacity({key(0.0, 0.0F, spring(100.0F, 10.0F)), key(settle * 0.5, 1.0F)}),
            "before the spring", "a keyframe that cuts a spring short is refused");
    accepted(opacity({key(0.0, 0.0F, spring(100.0F, 10.0F)), key(settle, 1.0F)}),
             "control: a keyframe at the settle time is accepted");
    refused(with_track(track(AnimatedProperty::stroke_color, {color_key(0.0, Color{})})),
            "path node", "a stroke colour track on a rectangle is refused");
    refused(with_track(track(AnimatedProperty::shadow_color, {color_key(0.0, Color{})}, 1U)),
            "shadow 1", "a shadow colour track past the shadows is refused");
    refused(with_track(track(AnimatedProperty::gradient_stop_color, {color_key(0.0, Color{})})),
            "gradient 0", "a gradient stop track past the gradients is refused");
    refused(with_track(track(AnimatedProperty::text_run_color, {color_key(0.0, Color{})})),
            "run 0", "a text run colour track past the runs is refused");
    {
        auto node = box("n", {0, 0, 10, 10}, Color::hex(0xFFFFFF));
        node.tracks.push_back(track(AnimatedProperty::opacity, {key(0.0, 0.0F)}));
        node.tracks.push_back(track(AnimatedProperty::opacity, {key(0.0, 1.0F)}));
        refused(scene_with({node}), "same property", "two tracks of one property are refused");
        node.tracks[1].property = AnimatedProperty::translate_x;
        accepted(scene_with({node}), "control: tracks of two properties are accepted");
    }
    {
        auto scene = opacity({key(0.0, 0.0F), key(1.0, 1.0F)});
        scene.clock = std::numeric_limits<double>::infinity();
        refused(scene, "clock is not finite", "a non-finite clock with tracks is refused");
        scene.root.children[0].tracks.clear();
        accepted(scene, "control: a non-finite clock without tracks changes nothing");
    }
    {
        const Easing overshoot{.kind = EasingKind::cubic_bezier, .x1 = 0.5F, .y1 = 3.0e38F,
                               .x2 = 0.5F, .y2 = 1.0F};
        auto scene = with_track(track(AnimatedProperty::scale_x,
                                      {key(0.0, -3.0e38F, overshoot), key(1.0, 3.0e38F)}));
        refused(scene, "non-finite transform",
                "a track that drives the transform past float range is refused as drawn");
        scene.root.children[0].tracks[0].keyframes[0].easing = {};
        accepted(scene, "control: the same range eased linearly is accepted");
    }
}

void version_refusal() {
    auto animated = box("animated", {0, 0, 10, 10}, Color::hex(0xFFFFFF));
    animated.tracks.push_back(track(AnimatedProperty::opacity, {key(0.0, 0.0F), key(1.0, 1.0F)}));
    auto scene = scene_with({animated});
    scene.scene_version = 11U;
    accepted(scene, "a version 11 scene with tracks is accepted");
    for (const std::uint32_t version : {10U, 7U, 2U}) {
        scene.scene_version = version;
        const auto acceptance = accept_scene(scene);
        check(!acceptance.accepted && acceptance.diagnostics.size() == 1U &&
                  acceptance.diagnostics[0].code == kSceneVersionMismatchCode &&
                  acceptance.diagnostics[0].property == "tracks" &&
                  acceptance.diagnostics[0].node_id == "animated",
              "a version " + std::to_string(version) +
                  " scene with tracks is refused with VELLUM_SCENE_VERSION_MISMATCH");
        check(make_paint_commands(scene).empty(),
              "a version " + std::to_string(version) + " scene with tracks lowers to nothing");
    }
    auto legacy = box("legacy", {0, 0, 10, 10}, Color::hex(0xFFFFFF));
    legacy.opacity = 0.5F;
    legacy.transform = translation(3.0F, 4.0F);
    legacy.clip = NodeClip{.rect = {0, 0, 5, 5}};
    legacy.isolate = true;
    auto older = scene_with({legacy});
    older.scene_version = 10U;
    older.clock = 3.0;
    accepted(older, "control: a version 10 scene with older fields and a clock is accepted");
    older.root.children[0].tracks = animated.tracks;
    older.scene_version = 4U;
    const auto acceptance = accept_scene(older);
    check(!acceptance.accepted && acceptance.diagnostics.size() == 1U &&
              acceptance.diagnostics[0].property == "clip",
          "an older scene names the oldest field it lacks first");
    {
        auto orb = box("orb", {0, 0, 10, 10}, Color::hex(0x000000));
        orb.fill_gradients.push_back({.kind = GradientKind::conic,
                                      .stops = {{0.0F, Color::hex(0x000000)},
                                                {1.0F, Color::hex(0xFFFFFF)}}});
        orb.tracks.push_back(track(AnimatedProperty::gradient_stop_color,
                                   {color_key(0.0, Color::hex(0x000000)),
                                    color_key(1.0, Color::hex(0xFF0000))},
                                   0U, 1U));
        auto gradient_scene = scene_with({orb}, 0.5);
        gradient_scene.scene_version = 11U;
        accepted(gradient_scene, "a conic gradient with an animated stop is accepted at 11");
        gradient_scene.scene_version = 10U;
        const auto refusal = accept_scene(gradient_scene);
        check(!refusal.accepted && refusal.diagnostics.size() == 1U &&
                  refusal.diagnostics[0].property == "tracks",
              "at version 10 the same node is refused for its tracks, not its gradient");
        gradient_scene.scene_version = 7U;
        const auto older_refusal = accept_scene(gradient_scene);
        check(!older_refusal.accepted &&
                  older_refusal.reason.find("gradient vocabulary") != std::string::npos,
              "at version 7 it is refused for the gradient vocabulary first");
        const auto stop = evaluate_tracks(scene_with({orb}, 0.5));
        check(find_node(stop, "orb")->fill_gradients[0].kind == GradientKind::conic &&
                  find_node(stop, "orb")->fill_gradients[0].stops[1].color.red == 0.5F,
              "a stop track keeps the gradient's kind and writes its stop");
    }
    check(kSceneVersion >= 11U && kDamageModelSceneVersion >= 11U &&
              partial_redraw_supported(8U) && partial_redraw_supported(11U) &&
              !partial_redraw_supported(kDamageModelSceneVersion + 1U),
          "this build produces version 11 and redraws it from damage");
}

DamagePolicy exact_policy() {
    return {.max_rects = 16, .full_repaint_fraction = 2.0, .antialias_margin = 0};
}

PixelRect pixels(Rect rect) { return *to_pixel_rect(rect, 1.0F); }

bool covers(const DamageRegion& region, const PixelRect& rect) {
    DamageRegion probe = region;
    probe.intersect(rect);
    return probe.area() == rect.area();
}

Scene damage_scene(double clock) {
    auto mover = box("mover", {10, 10, 20, 20}, Color::hex(0xFF0000));
    mover.tracks.push_back(
        track(AnimatedProperty::translate_x, {key(0.0, 0.0F), key(1.0, 100.0F)}));
    auto resting = box("resting", {150, 60, 20, 20}, Color::hex(0x00FF00));
    resting.tracks.push_back(track(AnimatedProperty::fill,
                                   {color_key(0.0, Color::hex(0x000000)),
                                    color_key(0.25, Color::hex(0x00FF00))}));
    auto still = box("still", {150, 10, 20, 20}, Color::hex(0x0000FF));
    auto settling = box("settling", {60, 60, 20, 20}, Color::hex(0xFFFFFF));
    settling.tracks.push_back(
        track(AnimatedProperty::translate_y, {key(0.0, 0.0F, spring(200.0F, 12.0F)),
                                              key(10.0, 10.0F)}));
    return scene_with({mover, resting, still, settling}, clock);
}

void damage() {
    DamageTracker tracker(exact_policy());
    tracker.commit(damage_scene(0.5), 1.0F);
    (void)tracker.take_damage();
    tracker.commit(damage_scene(0.6), 1.0F);
    const auto moved = tracker.take_damage();
    const auto old_box = pixels({60, 10, 20, 20});
    const auto new_box = pixels({70, 10, 20, 20});
    const double settle = spring_settle_time(spring(200.0F, 12.0F));
    check(covers(moved, old_box) && covers(moved, new_box),
          "a moving track damages its node's old and new boxes");
    check(!moved.intersects(pixels({150, 60, 20, 20})) &&
              !moved.intersects(pixels({150, 10, 20, 20})),
          "a track at rest and a node without tracks are not damaged");
    check(settle < 2.5, "the settling spring settles within two and a half seconds");

    DamageTracker resting(exact_policy());
    resting.commit(damage_scene(10.0), 1.0F);
    (void)resting.take_damage();
    bool quiet = true;
    for (int frame = 1; frame < 30; ++frame) {
        resting.commit(damage_scene(10.0 + frame / 60.0), 1.0F);
        quiet = quiet && resting.take_damage().empty();
    }
    check(quiet, "every track at rest damages nothing, frame after frame");

    const auto settled_pair = [&](double first, double second) {
        return compute_scene_damage(damage_scene(first), damage_scene(second), 1.0F,
                                    exact_policy());
    };
    check(!settled_pair(settle * 0.5, settle * 0.5 + 0.02).intersects(pixels({150, 60, 20, 20})) &&
              settled_pair(settle * 0.5, settle * 0.5 + 0.02).intersects(pixels({60, 60, 20, 30})),
          "a spring still moving damages its node");
    {
        // The mover and fill track are at rest from t = 1; only the spring
        // remains, and from its settle time it is exactly at its target.
        const double after = std::max(settle, 1.0) + 0.1;
        check(settled_pair(after, after + 0.05).empty(),
              "a settled spring damages nothing");
        RulesOverride off({.settle_springs = false});
        check(!settled_pair(after, after + 0.05).empty(),
              "negative control: an unsettled spring keeps damaging after its settle time");
    }
    {
        RulesOverride off({.evaluate_for_damage = false});
        DamageTracker blind(exact_policy());
        blind.commit(damage_scene(0.5), 1.0F);
        (void)blind.take_damage();
        blind.commit(damage_scene(0.6), 1.0F);
        check(blind.take_damage().empty(),
              "negative control: damage of static fields misses the moving track");
    }
}

}  // namespace

int main() {
    easing_oracles();
    spring_oracles();
    evaluation();
    determinism();
    ranges_point_into_the_authored_scene();
    validation();
    version_refusal();
    damage();
    if (failures != 0) {
        std::printf("%d animation checks failed\n", failures);
        return 1;
    }
    std::printf("all animation checks passed\n");
    return 0;
}
