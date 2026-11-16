#pragma once

#include <vellum/graphics/scene.hpp>

#include <string>

namespace vellum::graphics {

/// The displacement and speed, in progress and progress per second, below
/// which a spring counts as settled. From `spring_settle_time` on it is
/// exactly at its target.
inline constexpr double kSpringRestThreshold = 1.0e-4;

/// Whether any node of `scene` has a track.
[[nodiscard]] bool scene_has_tracks(const Scene& scene) noexcept;

/// The progress `easing` gives a fraction `elapsed` in [0, 1] of its segment,
/// for the linear, cubic-bezier and steps kinds. A cubic bezier can return
/// values outside [0, 1]. A spring is timed in seconds instead; see
/// `spring_progress`.
[[nodiscard]] double eased_progress(const Easing& easing, double elapsed) noexcept;

/// The progress of a spring `seconds` after its keyframe: 0 at release,
/// approaching 1, and exactly 1 from `spring_settle_time` on. It can
/// overshoot 1 before then when the spring is underdamped.
[[nodiscard]] double spring_progress(const Easing& easing, double seconds) noexcept;

/// The time after release from which the spring's displacement and speed stay
/// below `kSpringRestThreshold`. Not finite for constants that never settle.
[[nodiscard]] double spring_settle_time(const Easing& easing) noexcept;

/// Whether `track` can be evaluated on `node`: it has keyframes with finite,
/// strictly increasing times; its values are finite and its colours lie in
/// [0, 1]; each easing it uses has valid constants; a spring segment lasts at
/// least the spring's settle time; and it addresses a property the node has
/// (a stroke on a path node with geometry, an existing shadow, gradient stop
/// or text run). On failure `reason`, when non-null, says which rule failed.
[[nodiscard]] bool track_is_valid(
    const SceneNode& node, const AnimationTrack& track, std::string* reason = nullptr);

/// The first node, in depth-first order, with a track that fails
/// `track_is_valid` or two tracks for the same property and address, with
/// `reason` set to the rule that failed.
[[nodiscard]] const SceneNode* find_invalid_tracks(const SceneNode& node, std::string& reason);

/// `scene` with every track evaluated at `scene.clock` and written into the
/// property it animates, and every node's tracks removed. The result depends
/// only on the scene, so the same scene and clock give the same result. A
/// track that fails `track_is_valid` is not applied; `accept_scene` refuses a
/// scene that has one. Without tracks the result equals `scene`.
[[nodiscard]] Scene evaluate_tracks(const Scene& scene);

}  // namespace vellum::graphics
