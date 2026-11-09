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
///
/// `bounds` stays in scene-absolute coordinates with no transform applied, and
/// `transform` maps those coordinates into the frame. A `begin_layer` and its
/// matching `end_layer` bracket the commands of a subtree that is composited
/// as one group and then drawn with the layer's `opacity`; layers nest.
struct PaintCommand final {
    enum class Kind {
        rectangle,
        text,
        mesh,
        /// Starts an offscreen group. `node_id`, `bounds`, and `transform`
        /// describe the node that owns it; the group is not clipped to them.
        begin_layer,
        /// Composites the innermost open group with its `begin_layer`'s opacity.
        end_layer,
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
    /// Maps `bounds` coordinates into the frame: every ancestor's transform
    /// and the node's own, each applied about its node's origin.
    Affine2D transform{};
    /// The alpha multiplier for this command's own paint. On a `begin_layer`
    /// it is the group's opacity. Opacity an enclosing layer applies is not
    /// included; `effective_opacities` gives the product.
    float opacity = 1.0F;
    /// The resolved per-corner radii; see `resolved_corner_radii`.
    CornerRadii corner_radii{};
};

/// Whether the command draws content, as opposed to opening or closing a layer.
[[nodiscard]] constexpr bool draws_content(PaintCommand::Kind kind) noexcept {
    return kind == PaintCommand::Kind::rectangle || kind == PaintCommand::Kind::text ||
           kind == PaintCommand::Kind::mesh;
}

/// The axis-aligned box that contains `rect` once mapped by `transform`.
[[nodiscard]] Rect transformed_bounds(const Affine2D& transform, const Rect& rect) noexcept;

/// For each command, its opacity multiplied by the opacity of every layer that
/// encloses it: the opacity the command would have if layers were flattened.
/// A backend that cannot composite groups multiplies this into its paint.
[[nodiscard]] std::vector<float> effective_opacities(
    const std::vector<PaintCommand>& commands);

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
/// build does not know, or whose compositing fails `compositing_is_finite`,
/// contributes nothing for itself or its subtree; `SkiaDawnSurface::render`
/// refuses such a scene outright. In a version 1 scene the transform,
/// opacity, and per-corner radii keep their defaults whatever they hold.
///
/// A node whose opacity is below one and whose subtree draws more than one
/// command is wrapped in a `begin_layer`/`end_layer` pair; when it draws
/// exactly one, its opacity is folded into that command's. A node whose
/// opacity is zero or less contributes nothing.
[[nodiscard]] std::vector<PaintCommand> make_paint_commands(const Scene& scene);

/// Whether a mesh can be drawn as given: every index names a vertex, the index
/// count is a multiple of three, every position and texture coordinate is
/// finite, and a texture's pixel buffer matches its dimensions. On failure
/// `reason`, when non-null, says which rule failed.
[[nodiscard]] bool mesh_is_valid(const TriangleMesh& mesh, std::string* reason = nullptr);

}  // namespace vellum::graphics
