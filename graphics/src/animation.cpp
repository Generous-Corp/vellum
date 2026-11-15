#include <vellum/graphics/animation.hpp>

#include "animation_testing.hpp"

#include <algorithm>
#include <atomic>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <numbers>
#include <set>
#include <tuple>
#include <utility>

namespace vellum::graphics {
namespace {

std::atomic<bool> evaluate_for_damage_rule{true};
std::atomic<bool> settle_springs_rule{true};

bool any_tracks(const SceneNode& node) noexcept {
    if (!node.tracks.empty()) return true;
    return std::any_of(node.children.begin(), node.children.end(),
                       [](const SceneNode& child) { return any_tracks(child); });
}

double cubic(double first, double second, double s) noexcept {
    const double inverse = 1.0 - s;
    return 3.0 * inverse * inverse * s * first + 3.0 * inverse * s * s * second + s * s * s;
}

double cubic_slope(double first, double second, double s) noexcept {
    const double inverse = 1.0 - s;
    return 3.0 * inverse * inverse * first + 6.0 * inverse * s * (second - first) +
           3.0 * s * s * (1.0 - second);
}

double cubic_bezier(const Easing& easing, double elapsed) noexcept {
    const double x1 = easing.x1;
    const double x2 = easing.x2;
    double s = elapsed;
    bool solved = false;
    for (int iteration = 0; iteration < 8; ++iteration) {
        const double error = cubic(x1, x2, s) - elapsed;
        if (std::fabs(error) < 1.0e-12) {
            solved = true;
            break;
        }
        const double slope = cubic_slope(x1, x2, s);
        if (std::fabs(slope) < 1.0e-9) break;
        s -= error / slope;
        if (s < 0.0 || s > 1.0) break;
    }
    if (!solved) {
        double low = 0.0;
        double high = 1.0;
        s = elapsed;
        for (int iteration = 0; iteration < 64; ++iteration) {
            s = 0.5 * (low + high);
            if (cubic(x1, x2, s) < elapsed) low = s;
            else high = s;
        }
    }
    return cubic(easing.y1, easing.y2, s);
}

double steps(const Easing& easing, double elapsed) noexcept {
    const auto count = static_cast<double>(easing.steps);
    double step = std::floor(elapsed * count);
    if (easing.step_position == StepPosition::jump_start ||
        easing.step_position == StepPosition::jump_both) {
        step += 1.0;
    }
    double jumps = count;
    if (easing.step_position == StepPosition::jump_both) jumps = count + 1.0;
    if (easing.step_position == StepPosition::jump_none) jumps = count - 1.0;
    step = std::clamp(step, 0.0, jumps);
    return step / jumps;
}

struct Spring final {
    double omega = 0.0;
    double zeta = 0.0;
    double velocity = 0.0;
};

Spring spring_of(const Easing& easing) noexcept {
    const double stiffness = easing.stiffness;
    const double mass = easing.mass;
    return {
        .omega = std::sqrt(stiffness / mass),
        .zeta = static_cast<double>(easing.damping) / (2.0 * std::sqrt(stiffness * mass)),
        .velocity = easing.initial_velocity,
    };
}

bool critically_damped(const Spring& spring) noexcept {
    return std::fabs(spring.zeta - 1.0) <= 1.0e-6;
}

// The displacement from the target, starting at -1 with the spring's velocity.
double displacement(const Spring& spring, double t) noexcept {
    constexpr double start = -1.0;
    const double omega = spring.omega;
    const double zeta = spring.zeta;
    if (critically_damped(spring)) {
        const double b = spring.velocity + omega * start;
        return std::exp(-omega * t) * (start + b * t);
    }
    if (zeta < 1.0) {
        const double damped = omega * std::sqrt(1.0 - zeta * zeta);
        const double b = (spring.velocity + zeta * omega * start) / damped;
        return std::exp(-zeta * omega * t) *
               (start * std::cos(damped * t) + b * std::sin(damped * t));
    }
    const double root = std::sqrt(zeta * zeta - 1.0);
    const double slow = -omega * (zeta - root);
    const double fast = -omega * (zeta + root);
    const double c2 = (spring.velocity - slow * start) / (fast - slow);
    const double c1 = start - c2;
    return c1 * std::exp(slow * t) + c2 * std::exp(fast * t);
}

bool finite(const Color& color) noexcept {
    return std::isfinite(color.red) && std::isfinite(color.green) && std::isfinite(color.blue) &&
           std::isfinite(color.alpha);
}

bool same_bits(const Color& a, const Color& b) noexcept {
    return std::bit_cast<std::uint32_t>(a.red) == std::bit_cast<std::uint32_t>(b.red) &&
           std::bit_cast<std::uint32_t>(a.green) == std::bit_cast<std::uint32_t>(b.green) &&
           std::bit_cast<std::uint32_t>(a.blue) == std::bit_cast<std::uint32_t>(b.blue) &&
           std::bit_cast<std::uint32_t>(a.alpha) == std::bit_cast<std::uint32_t>(b.alpha);
}

bool unit(float value) noexcept { return value >= 0.0F && value <= 1.0F; }

bool unit(const Color& color) noexcept {
    return unit(color.red) && unit(color.green) && unit(color.blue) && unit(color.alpha);
}

bool easing_is_valid(const Easing& easing, std::string& reason) {
    switch (easing.kind) {
    case EasingKind::linear: return true;
    case EasingKind::cubic_bezier:
        if (!std::isfinite(easing.x1) || !std::isfinite(easing.y1) ||
            !std::isfinite(easing.x2) || !std::isfinite(easing.y2)) {
            reason = "a cubic bezier has a non-finite control point";
            return false;
        }
        if (!unit(easing.x1) || !unit(easing.x2)) {
            reason = "a cubic bezier x value lies outside [0, 1]";
            return false;
        }
        return true;
    case EasingKind::steps:
        if (easing.steps < 1U ||
            (easing.step_position == StepPosition::jump_none && easing.steps < 2U)) {
            reason = "steps easing needs at least one step, or two with jump-none";
            return false;
        }
        if (static_cast<std::uint8_t>(easing.step_position) >
            static_cast<std::uint8_t>(StepPosition::jump_both)) {
            reason = "steps easing has an unknown step position";
            return false;
        }
        return true;
    case EasingKind::spring:
        if (!std::isfinite(easing.stiffness) || !std::isfinite(easing.damping) ||
            !std::isfinite(easing.mass) || !std::isfinite(easing.initial_velocity) ||
            easing.stiffness <= 0.0F || easing.damping <= 0.0F || easing.mass <= 0.0F) {
            reason = "a spring needs positive finite stiffness, damping and mass and a finite "
                     "velocity";
            return false;
        }
        if (!std::isfinite(spring_settle_time(easing))) {
            reason = "a spring with these constants does not settle";
            return false;
        }
        return true;
    }
    reason = "a keyframe has an unknown easing kind";
    return false;
}

bool addresses_existing(const SceneNode& node, const AnimationTrack& track, std::string& reason) {
    switch (track.property) {
    case AnimatedProperty::opacity:
    case AnimatedProperty::translate_x:
    case AnimatedProperty::translate_y:
    case AnimatedProperty::scale_x:
    case AnimatedProperty::scale_y:
    case AnimatedProperty::rotation_degrees:
    case AnimatedProperty::fill:
        return true;
    case AnimatedProperty::stroke_color:
        if (node.kind != SceneNode::Kind::path || !node.path) {
            reason = "a stroke colour track needs a path node with geometry";
            return false;
        }
        return true;
    case AnimatedProperty::shadow_color:
        if (track.index >= node.box_shadows.size()) {
            reason = "a shadow colour track names shadow " + std::to_string(track.index) +
                     ", which the node does not have";
            return false;
        }
        return true;
    case AnimatedProperty::gradient_stop_color:
        if (track.index >= node.fill_gradients.size() ||
            track.stop >= node.fill_gradients[track.index].stops.size()) {
            reason = "a gradient stop track names gradient " + std::to_string(track.index) +
                     " stop " + std::to_string(track.stop) + ", which the node does not have";
            return false;
        }
        return true;
    case AnimatedProperty::text_run_color:
        if (track.index >= node.text_runs.size()) {
            reason = "a text run colour track names run " + std::to_string(track.index) +
                     ", which the node does not have";
            return false;
        }
        return true;
    }
    reason = "a track animates an unknown property";
    return false;
}

std::tuple<std::uint8_t, std::uint32_t, std::uint32_t> address(const AnimationTrack& track) {
    const bool indexed = track.property == AnimatedProperty::shadow_color ||
                         track.property == AnimatedProperty::gradient_stop_color ||
                         track.property == AnimatedProperty::text_run_color;
    return {static_cast<std::uint8_t>(track.property), indexed ? track.index : 0U,
            track.property == AnimatedProperty::gradient_stop_color ? track.stop : 0U};
}

// The segment `clock` falls in and its progress, or the held keyframe when
// the clock lies outside every segment.
struct Sample final {
    std::size_t from = 0;
    std::size_t to = 0;
    double progress = 0.0;
};

Sample sample(const AnimationTrack& track, double clock) noexcept {
    const auto& keys = track.keyframes;
    if (keys.size() == 1U || !(clock >= keys.front().time)) return {};
    if (clock >= keys.back().time) return {.from = keys.size() - 1U, .to = keys.size() - 1U};
    const auto next = std::upper_bound(
        keys.begin(), keys.end(), clock,
        [](double value, const AnimationKeyframe& key) { return value < key.time; });
    const auto to = static_cast<std::size_t>(next - keys.begin());
    const auto from = to - 1U;
    const auto& easing = keys[from].easing;
    const double elapsed = clock - keys[from].time;
    const double progress = easing.kind == EasingKind::spring
        ? spring_progress(easing, elapsed)
        : eased_progress(easing, elapsed / (keys[to].time - keys[from].time));
    return {.from = from, .to = to, .progress = progress};
}

float scalar_at(const AnimationTrack& track, double clock) noexcept {
    const auto at = sample(track, clock);
    const float first = track.keyframes[at.from].value;
    const float last = track.keyframes[at.to].value;
    if (at.from == at.to || at.progress == 0.0) return first;
    if (at.progress == 1.0) return last;
    const double first_wide = first;
    return static_cast<float>(first_wide + (static_cast<double>(last) - first_wide) * at.progress);
}

Color color_at(const AnimationTrack& track, double clock) noexcept {
    const auto at = sample(track, clock);
    const Color first = track.keyframes[at.from].color;
    const Color last = track.keyframes[at.to].color;
    if (at.from == at.to || at.progress == 0.0) return first;
    if (at.progress == 1.0) return last;
    const auto mix = [&](double a, double b) { return a + (b - a) * at.progress; };
    const double alpha = std::clamp(mix(first.alpha, last.alpha), 0.0, 1.0);
    if (alpha <= 0.0) return Color{0.0F, 0.0F, 0.0F, 0.0F};
    const auto channel = [&](float a, float b) {
        const double premultiplied = mix(static_cast<double>(a) * first.alpha,
                                         static_cast<double>(b) * last.alpha);
        return static_cast<float>(std::clamp(premultiplied / alpha, 0.0, 1.0));
    };
    return {channel(first.red, last.red), channel(first.green, last.green),
            channel(first.blue, last.blue), static_cast<float>(alpha)};
}

void apply_tracks(SceneNode& node, double clock) {
    if (!node.tracks.empty()) {
        float translate_x = 0.0F;
        float translate_y = 0.0F;
        float scale_x = 1.0F;
        float scale_y = 1.0F;
        float rotation = 0.0F;
        bool transformed = false;
        for (const auto& track : node.tracks) {
            if (!track_is_valid(node, track)) continue;
            switch (track.property) {
            case AnimatedProperty::opacity: node.opacity = scalar_at(track, clock); break;
            case AnimatedProperty::translate_x:
                translate_x = scalar_at(track, clock);
                transformed = true;
                break;
            case AnimatedProperty::translate_y:
                translate_y = scalar_at(track, clock);
                transformed = true;
                break;
            case AnimatedProperty::scale_x:
                scale_x = scalar_at(track, clock);
                transformed = true;
                break;
            case AnimatedProperty::scale_y:
                scale_y = scalar_at(track, clock);
                transformed = true;
                break;
            case AnimatedProperty::rotation_degrees:
                rotation = scalar_at(track, clock);
                transformed = true;
                break;
            case AnimatedProperty::fill: node.fill = color_at(track, clock); break;
            case AnimatedProperty::stroke_color: {
                const Color color = color_at(track, clock);
                if (same_bits(color, node.path->stroke.color)) break;
                auto stroked = std::make_shared<PathGeometry>(*node.path);
                stroked->stroke.color = color;
                node.path = std::move(stroked);
                break;
            }
            case AnimatedProperty::shadow_color:
                node.box_shadows[track.index].color = color_at(track, clock);
                break;
            case AnimatedProperty::gradient_stop_color:
                node.fill_gradients[track.index].stops[track.stop].color = color_at(track, clock);
                break;
            case AnimatedProperty::text_run_color:
                node.text_runs[track.index].style.color = color_at(track, clock);
                break;
            }
        }
        if (transformed) {
            const double radians = static_cast<double>(rotation) * std::numbers::pi / 180.0;
            const float cosine = rotation == 0.0F ? 1.0F : static_cast<float>(std::cos(radians));
            const float sine = rotation == 0.0F ? 0.0F : static_cast<float>(std::sin(radians));
            const Affine2D animated{
                .a = cosine * scale_x,
                .b = sine * scale_x,
                .c = -sine * scale_y,
                .d = cosine * scale_y,
                .tx = translate_x,
                .ty = translate_y,
            };
            if (!is_identity(animated)) {
                node.transform = concat(node.transform.value_or(Affine2D{}), animated);
            }
        }
        node.tracks.clear();
    }
    for (auto& child : node.children) apply_tracks(child, clock);
}

}  // namespace

bool scene_has_tracks(const Scene& scene) noexcept { return any_tracks(scene.root); }

double eased_progress(const Easing& easing, double elapsed) noexcept {
    if (elapsed <= 0.0) return easing.kind == EasingKind::steps ? steps(easing, 0.0) : 0.0;
    if (elapsed >= 1.0) return 1.0;
    switch (easing.kind) {
    case EasingKind::linear: return elapsed;
    case EasingKind::cubic_bezier: return cubic_bezier(easing, elapsed);
    case EasingKind::steps: return steps(easing, elapsed);
    case EasingKind::spring: return elapsed;
    }
    return elapsed;
}

double spring_progress(const Easing& easing, double seconds) noexcept {
    if (seconds <= 0.0) return 0.0;
    if (settle_springs_rule.load(std::memory_order_relaxed) &&
        seconds >= spring_settle_time(easing)) {
        return 1.0;
    }
    return 1.0 + displacement(spring_of(easing), seconds);
}

double spring_settle_time(const Easing& easing) noexcept {
    const auto spring = spring_of(easing);
    constexpr double start = -1.0;
    const double omega = spring.omega;
    const double zeta = spring.zeta;
    double bound = 0.0;
    double rate = 0.0;
    if (critically_damped(spring)) {
        const double b = spring.velocity + omega * start;
        // t e^(-omega t) <= 2 / (e omega) e^(-omega t / 2).
        const double reach = 2.0 / (std::numbers::e * omega);
        bound = std::max(std::fabs(start) + std::fabs(b) * reach,
                         std::fabs(b - omega * start) + omega * std::fabs(b) * reach);
        rate = omega / 2.0;
    } else if (zeta < 1.0) {
        const double damped = omega * std::sqrt(1.0 - zeta * zeta);
        const double b = (spring.velocity + zeta * omega * start) / damped;
        bound = std::hypot(start, b) * std::max(1.0, omega);
        rate = zeta * omega;
    } else {
        const double root = std::sqrt(zeta * zeta - 1.0);
        const double slow = -omega * (zeta - root);
        const double fast = -omega * (zeta + root);
        const double c2 = (spring.velocity - slow * start) / (fast - slow);
        const double c1 = start - c2;
        bound = std::max(std::fabs(c1) + std::fabs(c2),
                         std::fabs(c1 * slow) + std::fabs(c2 * fast));
        rate = -slow;
    }
    if (!std::isfinite(bound) || !std::isfinite(rate) || rate <= 0.0) {
        return std::numeric_limits<double>::infinity();
    }
    if (bound <= kSpringRestThreshold) return 0.0;
    return std::log(bound / kSpringRestThreshold) / rate;
}

bool track_is_valid(const SceneNode& node, const AnimationTrack& track, std::string* reason) {
    std::string local;
    std::string& why = reason != nullptr ? *reason : local;
    if (track.keyframes.empty()) {
        why = "a track has no keyframes";
        return false;
    }
    const bool color = is_color_property(track.property);
    for (std::size_t index = 0; index < track.keyframes.size(); ++index) {
        const auto& key = track.keyframes[index];
        if (!std::isfinite(key.time)) {
            why = "keyframe " + std::to_string(index) + " has a non-finite time";
            return false;
        }
        if (index > 0U && !(key.time > track.keyframes[index - 1U].time)) {
            why = "keyframe " + std::to_string(index) + " does not come after the one before it";
            return false;
        }
        if (color ? !finite(key.color) || !unit(key.color) : !std::isfinite(key.value)) {
            why = color ? "keyframe " + std::to_string(index) +
                              " has a colour channel outside [0, 1]"
                        : "keyframe " + std::to_string(index) + " has a non-finite value";
            return false;
        }
        if (index + 1U == track.keyframes.size()) break;
        if (!easing_is_valid(key.easing, why)) return false;
        if (key.easing.kind == EasingKind::spring &&
            track.keyframes[index + 1U].time - key.time < spring_settle_time(key.easing)) {
            why = "keyframe " + std::to_string(index + 1U) +
                  " comes before the spring from keyframe " + std::to_string(index) +
                  " settles";
            return false;
        }
    }
    return addresses_existing(node, track, why);
}

const SceneNode* find_invalid_tracks(const SceneNode& node, std::string& reason) {
    std::set<std::tuple<std::uint8_t, std::uint32_t, std::uint32_t>> seen;
    for (const auto& track : node.tracks) {
        if (!track_is_valid(node, track, &reason)) return &node;
        if (!seen.insert(address(track)).second) {
            reason = "two tracks animate the same property";
            return &node;
        }
    }
    for (const auto& child : node.children) {
        if (const auto* found = find_invalid_tracks(child, reason); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

Scene evaluate_tracks(const Scene& scene) {
    Scene evaluated = scene;
    if (scene_has_tracks(scene)) apply_tracks(evaluated.root, scene.clock);
    return evaluated;
}

namespace testing {

void set_animation_rules(const AnimationRules& rules) noexcept {
    evaluate_for_damage_rule.store(rules.evaluate_for_damage);
    settle_springs_rule.store(rules.settle_springs);
}

AnimationRules animation_rules() noexcept {
    return {.evaluate_for_damage = evaluate_for_damage_rule.load(),
            .settle_springs = settle_springs_rule.load()};
}

}  // namespace testing

}  // namespace vellum::graphics
