#pragma once

#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/scene.hpp>

#include <string>
#include <vector>

namespace vellum::graphics {

/// Whether a renderer draws a scene, and what it reports. Every backend
/// decides through `accept_scene`, so a scene one backend refuses is refused
/// by all of them with the same reason and diagnostics.
struct SceneAcceptance final {
    bool accepted = false;
    /// Why the scene is refused; empty when it is accepted.
    std::string reason;
    /// For an accepted scene, `scene_loss_diagnostics`. For a refused scene,
    /// only the refusal: its `kSceneVersionMismatchCode` error, or a
    /// `kInvalidImageCode` error for an undrawable image or image table, or
    /// the effect error (`kInvalidEffectCode`, `kUnknownMaterialCode` or
    /// `kInvalidMaterialValueCode`) for an effect that cannot be drawn.
    /// Other refusals (a non-finite transform, an undrawable path) leave it
    /// empty and report through `reason`.
    std::vector<LoweringDiagnostic> diagnostics;
};

/// Decides whether `scene` can be drawn. It is refused for an unsupported
/// version, an unknown kind, a field or kind newer than its version, a
/// non-finite transform, opacity or corner radius, a root marked as a mask, a
/// clip that fails `clip_is_valid`, an effect `find_invalid_effect` reports,
/// an undrawable path, an
/// image node without a valid reference or with children, or an image table
/// that `scene_images_are_valid` rejects, or a token table or reference that
/// `find_invalid_token_reference` reports. A scene with tracks is also refused
/// for a non-finite clock or a track `find_invalid_tracks` reports, and every
/// rule after the version checks applies to the scene as `evaluate_tracks`
/// draws it, so a track that drives a transform to infinity is refused.
[[nodiscard]] SceneAcceptance accept_scene(const Scene& scene);

}  // namespace vellum::graphics
