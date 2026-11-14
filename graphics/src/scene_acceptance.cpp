#include <vellum/graphics/scene_acceptance.hpp>

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>

#include <cmath>
#include <utility>

namespace vellum::graphics {
namespace {

bool path_is_drawable(const PathGeometry& path, const Color& fill, std::string& reason) {
    if (!path_is_valid(path, &reason)) return false;
    if (!std::isfinite(fill.red) || !std::isfinite(fill.green) || !std::isfinite(fill.blue) ||
        !std::isfinite(fill.alpha)) {
        reason = "the fill colour is not finite";
        return false;
    }
    return true;
}

// The first path node, in depth-first order, that cannot be drawn.
const SceneNode* find_undrawable_path(const SceneNode& node, std::string& reason) {
    if (node.kind == SceneNode::Kind::path &&
        (!node.path || !path_is_drawable(*node.path, node.fill, reason))) {
        if (!node.path) reason.clear();
        return &node;
    }
    for (const auto& child : node.children) {
        if (const auto* found = find_undrawable_path(child, reason); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

// The first image node, in depth-first order, that cannot be drawn: one
// without a reference, with an invalid one, or with children.
const SceneNode* find_undrawable_image(const SceneNode& node, std::string& reason) {
    if (node.kind == SceneNode::Kind::image &&
        (!node.image || !node.children.empty() ||
         !image_reference_is_valid(*node.image, &reason))) {
        if (!node.image) reason = "it has no image reference";
        else if (!node.children.empty()) reason = "an image node has no children";
        return &node;
    }
    for (const auto& child : node.children) {
        if (const auto* found = find_undrawable_image(child, reason); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

// Why `scene` is refused, or empty. `image_node` is set to the refused
// image's id, and `image` to true, for an image or image-table refusal.
std::string refusal(const Scene& scene, bool& image, std::string& image_node) {
    if (!scene_version_is_supported(scene.scene_version)) {
        return "scene version " + std::to_string(scene.scene_version) +
               " is not supported by this renderer";
    }
    if (const auto* unknown = find_unknown_kind(scene.root); unknown != nullptr) {
        return "scene contains unsupported node kind " +
               std::to_string(static_cast<int>(unknown->kind)) + " at " + unknown->id;
    }
    if (!scene_fields_match_version(scene)) {
        const auto loss = scene_loss_diagnostics(scene);
        return loss.empty() ? "scene fields do not match its version" : loss.front().message;
    }
    if (const auto* unsupported = find_unsupported_gradient_color_space(scene.root);
        unsupported != nullptr) {
        return "gradient interpolation or colour space is unsupported at " + unsupported->id;
    }
    if (const auto* invalid = find_non_finite_compositing(scene.root); invalid != nullptr) {
        return "scene node " + invalid->id +
               " has a non-finite transform, opacity, or corner radius";
    }
    if (scene.root.mask) return "scene root " + scene.root.id + " cannot be a mask";
    std::string reason;
    if (const auto* clipped = find_invalid_clip(scene.root, reason); clipped != nullptr) {
        return "clip of " + clipped->id + " cannot be applied: " + reason;
    }
    if (const auto* path = find_undrawable_path(scene.root, reason); path != nullptr) {
        return path->path ? "path " + path->id + " cannot be drawn: " + reason
                          : "path " + path->id + " has no geometry";
    }
    if (const auto* undrawable = find_undrawable_image(scene.root, reason);
        undrawable != nullptr) {
        image = true;
        image_node = undrawable->id;
        return "image " + undrawable->id + " cannot be drawn: " + reason;
    }
    if (!scene_images_are_valid(scene, &reason)) {
        image = true;
        return reason;
    }
    return {};
}

}  // namespace

SceneAcceptance accept_scene(const Scene& scene) {
    SceneAcceptance result;
    result.diagnostics = scene_loss_diagnostics(scene);
    bool image = false;
    std::string image_node;
    result.reason = refusal(scene, image, image_node);
    result.accepted = result.reason.empty();
    if (result.accepted) return result;

    const bool version = !result.diagnostics.empty() &&
        result.diagnostics.front().severity == LoweringDiagnostic::Severity::error;
    if (version) {
        result.diagnostics.resize(1U);
        return result;
    }
    result.diagnostics.clear();
    if (image) {
        result.diagnostics.push_back({
            .severity = LoweringDiagnostic::Severity::error,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(kInvalidImageCode),
            .node_id = std::move(image_node),
            .property = "image",
            .message = result.reason,
        });
    }
    return result;
}

}  // namespace vellum::graphics
