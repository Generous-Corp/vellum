#pragma once

#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
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
///
/// A `begin_clip` and its `end_clip` bracket the commands a node's clip
/// limits. A `begin_mask` and its `end_mask` bracket a mask node's subtree;
/// they always sit at the end of a `begin_layer` group holding the masked
/// siblings, and the mask's coverage multiplies that group before it is
/// composited. Clips, layers and masks nest properly.
struct PaintCommand final {
    enum class Kind {
        rectangle,
        text,
        mesh,
        /// Starts an offscreen group for the node `node_id`. `bounds` is the
        /// frame-space union of its members' transformed paint extents, and
        /// the group may be clipped to it unless `layer_unbounded` is set.
        begin_layer,
        /// Composites the innermost open group with its `begin_layer`'s opacity.
        end_layer,
        path,
        /// Draws `image_asset` as `image` places it within `bounds`, over
        /// `fill`, clipped to `corner_radii`; without an asset it draws
        /// `kMissingImagePlaceholder` over the box.
        image,
        /// Intersects the clip with `clip_rect` and `corner_radii`, or with
        /// the area `path` fills when it is set, both in `bounds` coordinates
        /// mapped by `transform`, until the matching `end_clip`.
        begin_clip,
        /// Restores the clip in effect before the matching `begin_clip`.
        end_clip,
        /// Starts rendering the mask node `node_id`; the commands up to the
        /// matching `end_mask` draw its coverage rather than visible paint.
        begin_mask,
        /// Multiplies the enclosing group by the mask's coverage, taken by
        /// `mask_mode`.
        end_mask,
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
    /// Path geometry and stroke, relative to `bounds.x`/`bounds.y`. The fill
    /// paint is `fill` and `fill_gradients`, with gradients resolved against
    /// `path_gradient_box`.
    std::shared_ptr<const PathGeometry> path;
    /// Maps `bounds` coordinates into the frame: every ancestor's transform
    /// and the node's own, each applied about its node's origin.
    Affine2D transform{};
    /// The alpha multiplier for this command's own paint. On a `begin_layer`
    /// it is the group's opacity. Opacity an enclosing layer applies is not
    /// included; `effective_opacities` gives the product.
    float opacity = 1.0F;
    /// The resolved per-corner radii; see `resolved_corner_radii`.
    CornerRadii corner_radii{};
    /// On a `begin_layer`, set when a member can paint outside `bounds`,
    /// which text can, so the group needs the whole frame.
    bool layer_unbounded = false;
    /// On a `begin_layer`, set when the group must stay offscreen whatever
    /// its opacity: an isolated node, or the siblings a mask applies to.
    bool layer_isolated = false;
    /// On a `begin_clip` without `path`, the clip rectangle in `bounds`
    /// coordinates; `corner_radii` gives its corners.
    Rect clip_rect{};
    /// On a `begin_mask`, how the mask's rendering becomes coverage.
    MaskMode mask_mode = MaskMode::alpha;
    /// What an `image` command draws.
    std::optional<ImageReference> image;
    /// The asset `Scene::images` holds for `image`, or null when it is missing.
    std::shared_ptr<const ImageAsset> image_asset;
};

[[nodiscard]] constexpr std::string_view to_string(PaintCommand::Kind kind) noexcept {
    switch (kind) {
    case PaintCommand::Kind::rectangle: return "rectangle";
    case PaintCommand::Kind::text: return "text";
    case PaintCommand::Kind::mesh: return "mesh";
    case PaintCommand::Kind::path: return "path";
    case PaintCommand::Kind::image: return "image";
    case PaintCommand::Kind::begin_layer: return "begin_layer";
    case PaintCommand::Kind::end_layer: return "end_layer";
    case PaintCommand::Kind::begin_clip: return "begin_clip";
    case PaintCommand::Kind::end_clip: return "end_clip";
    case PaintCommand::Kind::begin_mask: return "begin_mask";
    case PaintCommand::Kind::end_mask: return "end_mask";
    }
    return "unknown";
}

/// The absolute box a path command's fill gradients resolve against: the
/// command's layout `bounds` when it has area, otherwise the path's
/// `path_control_bounds` moved to the command's origin. Empty when neither
/// box has area, in which case gradients draw nothing.
[[nodiscard]] std::optional<Rect> path_gradient_box(const PaintCommand& command);

/// The area a command can paint, in its `bounds` coordinates before
/// `transform`: the box grown by each shadow's offset, spread, and 1.5 times
/// its blur radius, a mesh's clipped vertex extent, or a path's
/// `path_control_bounds` outset by `path_stroke_outset`. Text may overflow its
/// box, so its extent is the box with `bounded` false.
struct PaintExtent final {
    Rect rect{};
    bool bounded = true;
};

[[nodiscard]] PaintExtent paint_extent(const PaintCommand& command);

/// Whether the command draws content, as opposed to opening or closing a layer.
[[nodiscard]] constexpr bool draws_content(PaintCommand::Kind kind) noexcept {
    return kind == PaintCommand::Kind::rectangle || kind == PaintCommand::Kind::text ||
           kind == PaintCommand::Kind::mesh || kind == PaintCommand::Kind::path ||
           kind == PaintCommand::Kind::image;
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
/// refuses such a scene outright. A scene that fails
/// `scene_fields_match_version` yields no commands. A path node emits its
/// command even when it has no geometry or its geometry fails `path_is_valid`,
/// so the renderer refuses it rather than silently skipping it; a valid path
/// with no commands emits nothing. An image node always emits its command,
/// with the asset `find_image_asset` resolves, or none when it is missing.
///
/// A node whose opacity is below one and whose subtree draws more than one
/// command is wrapped in a `begin_layer`/`end_layer` pair; when it draws
/// exactly one, its opacity is folded into that command's. A node whose
/// opacity is zero or less contributes nothing.
///
/// An isolated node is always wrapped in a layer, with `layer_isolated` set. A
/// node whose clip `clips_own_paint` wraps its layer, paint and subtree in a
/// `begin_clip`/`end_clip` pair; any other clip wraps only its subtree, after
/// the node's own paint and inside its layer. A node with mask children wraps
/// its other children in a `layer_isolated` layer named `node_id + "/masked"`
/// and ends that layer with one `begin_mask`/`end_mask` pair per mask child, in
/// child order, holding the mask's own commands; a mask child whose subtree
/// draws nothing still emits its pair, which hides the siblings entirely. A
/// node whose clip fails `clip_is_valid` contributes nothing, and a scene whose
/// root is a mask yields no commands.
[[nodiscard]] std::vector<PaintCommand> make_paint_commands(const Scene& scene);

/// Whether a clip can be applied as given: its rectangle and radii are
/// finite, and its path, when set, passes `path_is_valid`. On failure
/// `reason`, when non-null, says which rule failed.
[[nodiscard]] bool clip_is_valid(const NodeClip& clip, std::string* reason = nullptr);

/// The first node, in depth-first order, whose clip fails `clip_is_valid`,
/// with `reason` set to the rule that failed.
[[nodiscard]] const SceneNode* find_invalid_clip(const SceneNode& node, std::string& reason);

/// A clip's area in absolute coordinates before any transform, for a node
/// whose box starts at `absolute_x`, `absolute_y`: the rectangle, or the
/// path's `path_control_bounds`. Empty sizes are clamped to zero.
[[nodiscard]] Rect clip_extent(const NodeClip& clip, float absolute_x, float absolute_y);

/// The paint commands one scene node and its subtree contribute: the
/// half-open index range [`begin`, `end`) of the command stream, which holds
/// the node's own commands, its descendants' and, when the node is faded as a
/// group, its `begin_layer`/`end_layer` pair. A subtree's commands are always
/// contiguous because lowering is depth first.
struct PaintCommandRange final {
    const SceneNode* node = nullptr;
    std::size_t begin = 0;
    std::size_t end = 0;
};

/// `make_paint_commands`, also reporting each contributing node's range in
/// depth-first preorder, so a node's range precedes and contains those of its
/// descendants. Nodes that contribute nothing for themselves or their subtree
/// (unknown kind, non-finite compositing, zero opacity) have no range; a
/// node that draws nothing but is kept, such as an empty group, has an empty
/// one. `ranges`, when non-null, is cleared first; `node` points into `scene`.
[[nodiscard]] std::vector<PaintCommand> make_paint_commands(
    const Scene& scene, std::vector<PaintCommandRange>* ranges);

/// Whether a mesh can be drawn as given: every index names a vertex, the index
/// count is a multiple of three, every position and texture coordinate is
/// finite, and a texture's pixel buffer matches its dimensions. On failure
/// `reason`, when non-null, says which rule failed.
[[nodiscard]] bool mesh_is_valid(const TriangleMesh& mesh, std::string* reason = nullptr);

/// What a consumer loses drawing `scene`, as diagnostics against node ids:
/// a `kSceneVersionMismatchCode` error for an unsupported scene version or
/// the first node that `find_version_mismatch` reports (the scene is then
/// refused, so nothing else is listed), otherwise a `kImageAssetMissingCode`
/// warning for each image node whose asset `Scene::images` does not hold.
[[nodiscard]] std::vector<LoweringDiagnostic> scene_loss_diagnostics(const Scene& scene);

}  // namespace vellum::graphics
