#include <vellum/graphics/effect.hpp>

#include <algorithm>
#include <cmath>
#include <set>
#include <utility>

namespace vellum::graphics {
namespace {

bool unit_color(const Color& color) {
    const auto unit = [](float channel) {
        return std::isfinite(channel) && channel >= 0.0F && channel <= 1.0F;
    };
    return unit(color.red) && unit(color.green) && unit(color.blue) && unit(color.alpha);
}

bool sigma_in_range(float sigma) {
    return std::isfinite(sigma) && sigma >= 0.0F && sigma <= kMaxEffectSigma;
}

std::size_t value_components(MaterialValueType type) {
    switch (type) {
    case MaterialValueType::number: return 1U;
    case MaterialValueType::vector2: return 2U;
    case MaterialValueType::color: return 4U;
    }
    return 0U;
}

bool known_value_type(MaterialValueType type) { return value_components(type) > 0U; }

std::shared_ptr<const MaterialDefinition> frosted_glass() {
    auto definition = std::make_shared<MaterialDefinition>();
    definition->id = std::string(kFrostedGlassMaterial);
    definition->recipe = MaterialRecipe::frosted_glass;
    definition->disposition = LoweringDiagnostic::Disposition::portable;
    definition->provenance = {.owner = "vellum",
                              .source = "graphics/src/effect.cpp",
                              .revision = "1"};
    definition->uniforms = {
        {.name = "blur",
         .type = MaterialValueType::number,
         .default_value = {16.0F, 0.0F, 0.0F, 0.0F},
         .minimum = 0.0F,
         .maximum = 64.0F},
        {.name = "saturation",
         .type = MaterialValueType::number,
         .default_value = {1.4F, 0.0F, 0.0F, 0.0F},
         .minimum = 0.0F,
         .maximum = 4.0F},
        {.name = "tint",
         .type = MaterialValueType::color,
         .default_value = {1.0F, 1.0F, 1.0F, 0.18F},
         .minimum = 0.0F,
         .maximum = 1.0F},
    };
    definition->passes = 5U;
    definition->textures = 2U;
    return definition;
}

bool fail(std::string* reason, std::string message) {
    if (reason != nullptr) *reason = std::move(message);
    return false;
}

bool is_backdrop(const NodeEffect& effect) { return effect_cost(effect).backdrop; }

}  // namespace

MaterialRegistry& MaterialRegistry::shared() {
    static MaterialRegistry registry;
    return registry;
}

MaterialRegistry::MaterialRegistry() {
    auto glass = frosted_glass();
    definitions_.emplace(glass->id, std::move(glass));
}

std::shared_ptr<const MaterialDefinition> MaterialRegistry::find(std::string_view id) const {
    const std::lock_guard lock(mutex_);
    const auto found = definitions_.find(id);
    return found == definitions_.end() ? nullptr : found->second;
}

bool MaterialRegistry::register_extension(MaterialDefinition definition, std::string* error) {
    definition.recipe = MaterialRecipe::extension;
    definition.disposition = LoweringDiagnostic::Disposition::extension;
    if (definition.id.empty() || definition.id.rfind("vellum.", 0) == 0) {
        return fail(error, "an application material needs an id outside the vellum. namespace");
    }
    if (definition.component.empty()) {
        return fail(error, "material " + definition.id + " names no component to draw it");
    }
    if (definition.provenance.owner.empty()) {
        return fail(error, "material " + definition.id + " has no owner in its provenance");
    }
    if (definition.passes == 0U) {
        return fail(error, "material " + definition.id + " must declare at least one pass");
    }
    std::set<std::string, std::less<>> names;
    for (const auto& uniform : definition.uniforms) {
        if (uniform.name.empty() || !names.insert(uniform.name).second) {
            return fail(error, "material " + definition.id + " declares uniform '" +
                                   uniform.name + "' twice or without a name");
        }
        if (!known_value_type(uniform.type) || !std::isfinite(uniform.minimum) ||
            !std::isfinite(uniform.maximum) || uniform.minimum > uniform.maximum) {
            return fail(error, "uniform " + uniform.name + " of material " + definition.id +
                                   " has an unknown type or an invalid range");
        }
        for (std::size_t index = 0; index < value_components(uniform.type); ++index) {
            const float value = uniform.default_value[index];
            if (!std::isfinite(value) || value < uniform.minimum || value > uniform.maximum) {
                return fail(error, "the default of uniform " + uniform.name + " of material " +
                                       definition.id + " is outside its range");
            }
        }
    }
    const std::lock_guard lock(mutex_);
    if (definitions_.contains(definition.id)) {
        return fail(error, "material " + definition.id + " is already registered");
    }
    auto id = definition.id;
    definitions_.emplace(std::move(id),
                         std::make_shared<const MaterialDefinition>(std::move(definition)));
    return true;
}

bool MaterialRegistry::unregister_extension(std::string_view id, const MaterialDefinition* only) {
    const std::lock_guard lock(mutex_);
    const auto found = definitions_.find(id);
    if (found == definitions_.end() || found->second->recipe != MaterialRecipe::extension ||
        (only != nullptr && found->second.get() != only)) {
        return false;
    }
    definitions_.erase(found);
    return true;
}

std::vector<std::shared_ptr<const MaterialDefinition>> MaterialRegistry::definitions() const {
    const std::lock_guard lock(mutex_);
    std::vector<std::shared_ptr<const MaterialDefinition>> output;
    output.reserve(definitions_.size());
    for (const auto& [id, definition] : definitions_) output.push_back(definition);
    return output;
}

std::array<float, 4> material_uniform_value(const MaterialDefinition& material,
                                            const std::vector<MaterialValue>& values,
                                            std::string_view name) {
    for (const auto& value : values) {
        if (value.name == name) return value.value;
    }
    for (const auto& uniform : material.uniforms) {
        if (uniform.name == name) return uniform.default_value;
    }
    return {};
}

bool material_values_are_valid(const MaterialDefinition& material,
                               const std::vector<MaterialValue>& values, std::string* reason) {
    std::set<std::string_view> seen;
    for (const auto& value : values) {
        const auto uniform = std::find_if(material.uniforms.begin(), material.uniforms.end(),
            [&](const MaterialUniform& candidate) { return candidate.name == value.name; });
        if (uniform == material.uniforms.end()) {
            return fail(reason, "material " + material.id + " has no uniform '" + value.name + "'");
        }
        if (!seen.insert(value.name).second) {
            return fail(reason, "uniform " + value.name + " of material " + material.id +
                                    " is set twice");
        }
        if (value.type != uniform->type) {
            return fail(reason, "uniform " + value.name + " of material " + material.id +
                                    " is set with the wrong type");
        }
        for (std::size_t index = 0; index < value_components(value.type); ++index) {
            const float component = value.value[index];
            if (!std::isfinite(component) || component < uniform->minimum ||
                component > uniform->maximum) {
                return fail(reason, "uniform " + value.name + " of material " + material.id +
                                        " is outside its range");
            }
        }
    }
    return true;
}

bool effect_is_valid(const NodeEffect& effect, std::string* reason, std::string_view* code) {
    if (code != nullptr) *code = kInvalidEffectCode;
    switch (effect.kind) {
    case EffectKind::blur:
    case EffectKind::backdrop_blur:
        if (!sigma_in_range(effect.sigma)) {
            return fail(reason, "the blur sigma is not finite or outside [0, 128]");
        }
        return true;
    case EffectKind::drop_shadow:
        if (!sigma_in_range(effect.sigma)) {
            return fail(reason, "the shadow sigma is not finite or outside [0, 128]");
        }
        if (!std::isfinite(effect.offset_x) || !std::isfinite(effect.offset_y) ||
            std::abs(effect.offset_x) > kMaxEffectOffset ||
            std::abs(effect.offset_y) > kMaxEffectOffset) {
            return fail(reason, "the shadow offset is not finite or too large");
        }
        if (!unit_color(effect.color)) {
            return fail(reason, "a shadow colour channel is not finite or outside [0, 1]");
        }
        return true;
    case EffectKind::color_matrix:
        for (const float entry : effect.matrix) {
            if (!std::isfinite(entry) || std::abs(entry) > kMaxColorMatrixEntry) {
                return fail(reason, "a colour matrix entry is not finite or too large");
            }
        }
        return true;
    case EffectKind::material: {
        const auto material = MaterialRegistry::shared().find(effect.material);
        if (!material) {
            if (code != nullptr) *code = kUnknownMaterialCode;
            return fail(reason, "material '" + effect.material + "' is not registered");
        }
        for (const auto& value : effect.values) {
            if (!known_value_type(value.type)) {
                if (code != nullptr) *code = kInvalidMaterialValueCode;
                return fail(reason, "uniform " + value.name + " has an unknown type");
            }
        }
        if (!material_values_are_valid(*material, effect.values, reason)) {
            if (code != nullptr) *code = kInvalidMaterialValueCode;
            return false;
        }
        return true;
    }
    }
    return fail(reason, "the effect kind is not known");
}

const SceneNode* find_invalid_effect(const SceneNode& node, std::string& reason,
                                     std::string_view& code) {
    if (!node.effects.empty()) {
        code = kInvalidEffectCode;
        if (node.mask) {
            reason = "a mask node cannot carry effects";
            return &node;
        }
        std::size_t backdrops = 0;
        for (const auto& effect : node.effects) {
            if (!effect_is_valid(effect, &reason, &code)) return &node;
            if (is_backdrop(effect)) ++backdrops;
        }
        if (backdrops > 1U) {
            code = kInvalidEffectCode;
            reason = "a node carries more than one backdrop effect";
            return &node;
        }
    }
    for (const auto& child : node.children) {
        if (const auto* found = find_invalid_effect(child, reason, code); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

EffectCost effect_cost(const NodeEffect& effect) {
    if (!effect_is_valid(effect)) return {};
    const auto blur_reach = [](float sigma) { return kEffectBlurReachSigmas * sigma; };
    switch (effect.kind) {
    case EffectKind::blur:
        return {.radius = blur_reach(effect.sigma), .passes = 2U, .textures = 1U};
    case EffectKind::color_matrix:
        return {.passes = 1U};
    case EffectKind::drop_shadow:
        return {.radius = blur_reach(effect.sigma) +
                          std::max(std::abs(effect.offset_x), std::abs(effect.offset_y)),
                .passes = 3U,
                .textures = 2U};
    case EffectKind::backdrop_blur:
        return {.backdrop_reach = blur_reach(effect.sigma),
                .passes = 3U,
                .textures = 2U,
                .backdrop = true};
    case EffectKind::material: {
        const auto material = MaterialRegistry::shared().find(effect.material);
        if (!material) return {};
        if (material->recipe == MaterialRecipe::frosted_glass) {
            const float sigma = material_uniform_value(*material, effect.values, "blur")[0];
            return {.backdrop_reach = blur_reach(sigma),
                    .passes = material->passes,
                    .textures = material->textures,
                    .backdrop = true};
        }
        return {.passes = material->passes, .textures = material->textures};
    }
    }
    return {};
}

EffectChainCost effect_chain_cost(const std::vector<NodeEffect>& effects) {
    EffectChainCost chain;
    chain.total = {.passes = 1U, .textures = 1U};
    for (const auto& effect : effects) {
        const auto cost = effect_cost(effect);
        chain.total.radius += cost.radius;
        chain.total.backdrop_reach += cost.backdrop_reach;
        chain.total.passes += cost.passes;
        chain.total.textures += cost.textures;
        chain.total.backdrop = chain.total.backdrop || cost.backdrop;
    }
    chain.over_budget = chain.total.passes > kEffectPassBudget ||
                        chain.total.textures > kEffectTextureBudget;
    return chain;
}

bool effect_chain_fills_area(const std::vector<NodeEffect>& effects) {
    return std::any_of(effects.begin(), effects.end(), [](const NodeEffect& effect) {
        return effect.kind == EffectKind::color_matrix && effect_is_valid(effect) &&
               effect.matrix[19] > 0.0F;
    });
}

bool effects_apply(const SceneNode& node) {
    return !node.effects.empty() && !node.mask && !effect_chain_cost(node.effects).over_budget;
}

bool effect_draws_natively(const NodeEffect& effect) {
    if (effect.kind != EffectKind::material) return true;
    const auto material = MaterialRegistry::shared().find(effect.material);
    return material && material->recipe != MaterialRecipe::extension;
}

namespace {

void collect_effect_losses(const SceneNode& node, std::vector<LoweringDiagnostic>& output) {
    if (!node.effects.empty() && !node.mask) {
        const auto chain = effect_chain_cost(node.effects);
        if (chain.over_budget) {
            output.push_back({
                .severity = LoweringDiagnostic::Severity::warning,
                .disposition = LoweringDiagnostic::Disposition::lowered,
                .code = std::string(kEffectBudgetExceededCode),
                .node_id = node.id,
                .property = "effects",
                .message = "the effects of " + node.id + " need " +
                           std::to_string(chain.total.passes) + " passes and " +
                           std::to_string(chain.total.textures) +
                           " textures, over the budget of " + std::to_string(kEffectPassBudget) +
                           " and " + std::to_string(kEffectTextureBudget) +
                           "; the node was drawn without them",
            });
        }
        for (const auto& effect : node.effects) {
            if (effect.kind != EffectKind::material) continue;
            const auto material = MaterialRegistry::shared().find(effect.material);
            if (!material || material->recipe != MaterialRecipe::extension) continue;
            output.push_back({
                .severity = LoweringDiagnostic::Severity::warning,
                .disposition = LoweringDiagnostic::Disposition::extension,
                .code = std::string(kMaterialExtensionCode),
                .node_id = node.id,
                .property = "effects",
                .message = "material " + material->id + " belongs to " +
                           material->provenance.owner + " and is drawn by component " +
                           material->component +
                           "; a host without that component draws " + node.id + " without it",
            });
        }
    }
    for (const auto& child : node.children) collect_effect_losses(child, output);
}

}  // namespace

std::vector<LoweringDiagnostic> effect_loss_diagnostics(const SceneNode& root) {
    std::vector<LoweringDiagnostic> output;
    collect_effect_losses(root, output);
    return output;
}

}  // namespace vellum::graphics
