#pragma once

namespace vellum::graphics::testing {

/// Which animation rules apply. Every rule is on in production; tests turn
/// one off to show what it guards.
struct AnimationRules final {
    /// Damage diffs the evaluated scene rather than the static fields.
    bool evaluate_for_damage = true;
    /// A spring is exactly at its target from its settle time on.
    bool settle_springs = true;
};

void set_animation_rules(const AnimationRules& rules) noexcept;
[[nodiscard]] AnimationRules animation_rules() noexcept;

}  // namespace vellum::graphics::testing
