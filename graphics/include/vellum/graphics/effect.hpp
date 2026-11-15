#pragma once

#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/scene.hpp>

#include <array>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::graphics {

/// An effect whose parameters cannot be drawn: a non-finite or out-of-range
/// sigma, offset, colour (each channel in [0, 1]) or matrix entry, more than one backdrop effect on a
/// node, or effects on a mask node; always an error.
inline constexpr std::string_view kInvalidEffectCode = "VELLUM_INVALID_EFFECT";
/// A material id the registry does not hold; always an error.
inline constexpr std::string_view kUnknownMaterialCode = "VELLUM_UNKNOWN_MATERIAL";
/// A material value naming no uniform of its material, naming one twice, of
/// the wrong type, or outside the uniform's range; always an error.
inline constexpr std::string_view kInvalidMaterialValueCode = "VELLUM_INVALID_MATERIAL_VALUE";
/// An effect chain over the pass or texture budget; a warning. The node is
/// drawn without its effects.
inline constexpr std::string_view kEffectBudgetExceededCode = "VELLUM_EFFECT_BUDGET_EXCEEDED";
/// A material an application registered rather than Vellum; a warning with
/// the `extension` disposition. Only a host that loads the component which
/// draws it shows it; the native renderer draws the node without it.
inline constexpr std::string_view kMaterialExtensionCode = "VELLUM_MATERIAL_EXTENSION";

/// The reach of a Gaussian blur, in standard deviations, that damage and
/// enclosure use: the GPU writes nothing a partial redraw can see beyond it.
inline constexpr float kEffectBlurReachSigmas = 3.0F;
/// The largest sigma an effect or material may set.
inline constexpr float kMaxEffectSigma = 128.0F;
/// The largest drop-shadow offset, in logical pixels, on either axis.
inline constexpr float kMaxEffectOffset = 4096.0F;
/// The largest magnitude of a colour matrix entry.
inline constexpr float kMaxColorMatrixEntry = 256.0F;
/// The most GPU passes one node's effect chain may take, counting the pass
/// that renders the group itself.
inline constexpr std::uint32_t kEffectPassBudget = 8U;
/// The most intermediate textures one node's effect chain may hold at once,
/// counting the group's own layer.
inline constexpr std::uint32_t kEffectTextureBudget = 4U;

/// What an effect, or a whole chain, declares it costs.
struct EffectCost final {
    /// Logical pixels by which the filter output spreads past the group's
    /// paint, offsets included.
    float radius = 0.0F;
    /// Logical pixels beyond the node's box that a backdrop effect samples.
    float backdrop_reach = 0.0F;
    std::uint32_t passes = 0;
    std::uint32_t textures = 0;
    /// The effect reads what lies beneath the node's box.
    bool backdrop = false;
};

/// How a registered material is drawn.
enum class MaterialRecipe : std::uint8_t {
    /// Blurs the backdrop by the `blur` uniform, scales its saturation by
    /// `saturation`, and lays `tint` over it, all within the node's box.
    frosted_glass,
    /// Drawn by the application's custom component `component`, which a host
    /// expands; the native renderer draws the node without it.
    extension,
};

/// A uniform a material declares: its name, type, default and the closed
/// range every component of its value must fall in.
struct MaterialUniform final {
    std::string name;
    MaterialValueType type = MaterialValueType::number;
    std::array<float, 4> default_value{};
    float minimum = 0.0F;
    float maximum = 1.0F;
};

/// Where a material's definition comes from.
struct MaterialProvenance final {
    /// Who owns the definition: `vellum` for a material Vellum ships, or the
    /// application or module that registered it.
    std::string owner;
    /// The source the definition was vetted from, such as a source file or a
    /// component module path.
    std::string source;
    /// The revision of that source, or empty.
    std::string revision;
};

/// A material: its id, typed uniforms, provenance, cost and recipe. Scenes and
/// DesignIR name a material by `id` and carry values only.
struct MaterialDefinition final {
    std::string id;
    MaterialRecipe recipe = MaterialRecipe::frosted_glass;
    /// `portable` for a material Vellum ships; `extension` for one an
    /// application registered.
    LoweringDiagnostic::Disposition disposition = LoweringDiagnostic::Disposition::portable;
    MaterialProvenance provenance;
    std::vector<MaterialUniform> uniforms;
    /// For an extension, the custom component that draws it.
    std::string component;
    /// Passes and textures beyond the group's own layer.
    std::uint32_t passes = 1;
    std::uint32_t textures = 0;
};

/// The id of the frosted-glass material Vellum ships.
inline constexpr std::string_view kFrostedGlassMaterial = "vellum.frosted-glass";

/// The registry of materials a scene may name. It always holds the materials
/// Vellum ships; an application adds its own with `register_extension`, which
/// the custom component host does when it loads a component that declares a
/// material. Lookups and registration are thread safe.
class MaterialRegistry final {
public:
    /// The process-wide registry every renderer and acceptance check reads.
    [[nodiscard]] static MaterialRegistry& shared();

    MaterialRegistry();

    [[nodiscard]] std::shared_ptr<const MaterialDefinition> find(std::string_view id) const;

    /// Registers an application material. The definition's recipe and
    /// disposition become `extension`; it needs an id outside the `vellum.`
    /// namespace that is not already registered, a component, an owner, and
    /// uniforms with distinct names, finite ranges, and defaults inside them.
    [[nodiscard]] bool register_extension(MaterialDefinition definition,
                                          std::string* error = nullptr);

    /// Removes an application material; Vellum's own cannot be removed. With
    /// `only`, it removes the material only while `only` is the definition
    /// registered under `id`, so an owner cannot remove a later registration.
    bool unregister_extension(std::string_view id, const MaterialDefinition* only = nullptr);

    /// Every registered material, ordered by id.
    [[nodiscard]] std::vector<std::shared_ptr<const MaterialDefinition>> definitions() const;

private:
    mutable std::mutex mutex_;
    std::map<std::string, std::shared_ptr<const MaterialDefinition>, std::less<>> definitions_;
};

/// The value a material's uniform takes from `values`, or its default.
[[nodiscard]] std::array<float, 4> material_uniform_value(
    const MaterialDefinition& material, const std::vector<MaterialValue>& values,
    std::string_view name);

/// Whether `values` set only uniforms `material` declares, each once, with
/// the declared type and every component finite and inside the range. On
/// failure `reason`, when non-null, says which rule failed.
[[nodiscard]] bool material_values_are_valid(const MaterialDefinition& material,
                                             const std::vector<MaterialValue>& values,
                                             std::string* reason = nullptr);

/// Whether an effect can be drawn as given. A material effect must name a
/// registered material with valid values; `code`, when non-null, receives
/// the diagnostic code of the rule that failed.
[[nodiscard]] bool effect_is_valid(const NodeEffect& effect, std::string* reason = nullptr,
                                   std::string_view* code = nullptr);

/// The first node, in depth-first order, with an effect that fails
/// `effect_is_valid`, more than one backdrop effect, or effects on a mask.
[[nodiscard]] const SceneNode* find_invalid_effect(const SceneNode& node, std::string& reason,
                                                   std::string_view& code);

/// What one valid effect declares:
/// - blur: radius `kEffectBlurReachSigmas` sigma, two passes, one texture;
/// - colour matrix: no radius, one pass, no texture;
/// - drop shadow: the blur's reach plus the larger offset, three passes, two
///   textures;
/// - backdrop blur: a backdrop reach of the blur's, three passes, two
///   textures;
/// - frosted glass: the backdrop blur's, plus a saturation and a tint pass;
/// - an extension material: what it registered.
/// An invalid effect costs nothing.
[[nodiscard]] EffectCost effect_cost(const NodeEffect& effect);

/// The cost of a chain: the group's own layer (one pass, one texture) plus
/// each effect's, with radii and backdrop reaches summed.
struct EffectChainCost final {
    EffectCost total;
    /// The chain needs more than `kEffectPassBudget` passes or
    /// `kEffectTextureBudget` textures; the node is drawn without it.
    bool over_budget = false;
};

[[nodiscard]] EffectChainCost effect_chain_cost(const std::vector<NodeEffect>& effects);

/// Whether a chain paints its whole area even where the group is transparent:
/// it holds a colour matrix that gives transparent black a positive alpha.
[[nodiscard]] bool effect_chain_fills_area(const std::vector<NodeEffect>& effects);

/// Whether a node's effects are drawn: it has some, is not a mask, and its
/// chain is within budget.
[[nodiscard]] bool effects_apply(const SceneNode& node);

/// Whether an effect is drawn natively: every kind except an extension
/// material.
[[nodiscard]] bool effect_draws_natively(const NodeEffect& effect);

/// The loss diagnostics effects cause in a scene: a
/// `kEffectBudgetExceededCode` warning for each node whose chain is over
/// budget and a `kMaterialExtensionCode` warning for each extension
/// material a node names.
[[nodiscard]] std::vector<LoweringDiagnostic> effect_loss_diagnostics(const SceneNode& root);

}  // namespace vellum::graphics
