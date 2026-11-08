#pragma once

#include <vellum/graphics/scene.hpp>

#include <string>
#include <vector>

namespace vellum::graphics {

/// A renderer-neutral, absolute-position paint operation.
///
/// Scene traversal belongs to the shared C++ graphics core. Platform backends
/// consume this stable command stream instead of independently walking the
/// retained tree and reimplementing parent-coordinate accumulation.
struct PaintCommand final {
    enum class Kind {
        rectangle,
        text,
        mesh,
    };

    Kind kind = Kind::rectangle;
    std::string node_id;
    Rect bounds{};
    Color fill{};
    std::vector<LinearGradient> fill_gradients;
    std::vector<BoxShadow> box_shadows;
    float corner_radius = 0.0F;
    std::string text;
    float font_size = 14.0F;
    std::string font_family = "Inter";
    int font_weight = 400;
    float letter_spacing = 0.0F;
    bool underline = false;
    bool strikethrough = false;
    std::vector<TextRun> text_runs;
    /// Mesh geometry, relative to `bounds.x`/`bounds.y`.
    std::shared_ptr<const TriangleMesh> mesh;
    /// The `Scene::scene_version` this command was produced from.
    std::uint32_t scene_version = kSceneVersion;
};

struct ResolvedLinearGradient final {
    float start_x = 0.0F;
    float start_y = 0.0F;
    float end_x = 0.0F;
    float end_y = 0.0F;
    bool repeating = false;
    float repeat_length = 0.0F;
    std::vector<GradientStop> stops;
};

/// Resolve CSS-compatible linear-gradient angles against the actual paint box.
/// Zero degrees points up and 90 degrees points right. The line crosses the
/// box center and spans the projection of all four corners.
[[nodiscard]] ResolvedLinearGradient resolve_linear_gradient(
    const Rect& bounds, const LinearGradient& gradient);

/// Flatten a scene into absolute paint commands. A scene whose version fails
/// `scene_version_is_supported` yields no commands, and a node of a kind this
/// build does not know contributes nothing for itself or its subtree.
[[nodiscard]] std::vector<PaintCommand> make_paint_commands(const Scene& scene);

/// Whether a mesh can be drawn as given: every index names a vertex, the index
/// count is a multiple of three, every position and texture coordinate is
/// finite, and a texture's pixel buffer matches its dimensions. On failure
/// `reason`, when non-null, says which rule failed.
[[nodiscard]] bool mesh_is_valid(const TriangleMesh& mesh, std::string* reason = nullptr);

}  // namespace vellum::graphics
