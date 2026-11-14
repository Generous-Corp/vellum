#pragma once

namespace vellum::graphics::testing {

void set_transformed_blur_sigmas(float sigmas) noexcept;
[[nodiscard]] float transformed_blur_sigmas() noexcept;

/// Which effect rules the damage model applies. Every rule is on in
/// production; tests turn one off to show what it guards.
struct EffectDamageRules final {
    /// Members of an effect group grow by the chain's radius.
    bool grow_by_radius = true;
    /// A group whose chain fills its area damages the whole area when the
    /// area changes.
    bool fill_area = true;
    /// Damage reaching a backdrop's sample damages the backdrop's box.
    bool propagate_backdrops = true;
};

void set_effect_damage_rules(const EffectDamageRules& rules) noexcept;
[[nodiscard]] EffectDamageRules effect_damage_rules() noexcept;

}  // namespace vellum::graphics::testing
