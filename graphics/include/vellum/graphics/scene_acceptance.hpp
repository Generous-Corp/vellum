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
    /// `kInvalidImageCode` error for an undrawable image or image table.
    /// Other refusals (a non-finite transform, an undrawable path) leave it
    /// empty and report through `reason`.
    std::vector<LoweringDiagnostic> diagnostics;
};

/// Decides whether `scene` can be drawn. It is refused for an unsupported
/// version, an unknown kind, a field or kind newer than its version, a
/// non-finite transform, opacity or corner radius, a root marked as a mask, a
/// clip that fails `clip_is_valid`, an undrawable path, an
/// image node without a valid reference or with children, or an image table
/// that `scene_images_are_valid` rejects, or a token table or reference that
/// `find_invalid_token_reference` reports.
[[nodiscard]] SceneAcceptance accept_scene(const Scene& scene);

}  // namespace vellum::graphics
