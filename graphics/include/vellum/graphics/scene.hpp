#pragma once

#include <vellum/graphics/color.hpp>

#include <string>
#include <string_view>
#include <vector>

namespace vellum::graphics {

struct Rect final {
    float x = 0.0F;
    float y = 0.0F;
    float width = 0.0F;
    float height = 0.0F;
};

/// A deliberately small retained scene used by the first extraction proof.
///
/// Nodes carry stable semantic identities so import, interaction, and capture
/// can address the same object. The scene is renderer-independent; Skia/Dawn
/// is one consumer rather than the owner of application state.
struct SceneNode final {
    enum class Kind {
        group,
        rectangle,
        text,
    };

    std::string id;
    Kind kind = Kind::group;
    Rect bounds{};
    Color fill = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    float corner_radius = 0.0F;
    std::string text;
    float font_size = 14.0F;
    std::vector<SceneNode> children;
};

struct Scene final {
    float width = 0.0F;
    float height = 0.0F;
    Color background = Color::hex(0x111827);
    SceneNode root;
};

[[nodiscard]] inline const SceneNode* find_node(
    const SceneNode& node, std::string_view id) noexcept {
    if (node.id == id) {
        return &node;
    }
    for (const auto& child : node.children) {
        if (const auto* found = find_node(child, id); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

[[nodiscard]] inline const SceneNode* find_node(
    const Scene& scene, std::string_view id) noexcept {
    return find_node(scene.root, id);
}

}  // namespace vellum::graphics
