#pragma once

#include <vellum/graphics/color.hpp>

#include <cstddef>
#include <cmath>
#include <cstdint>
#include <memory>
#include <optional>
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

struct GradientStop final {
    float position = 0.0F;
    Color color{};
};

struct LinearGradient final {
    float angle_degrees = 180.0F;
    bool repeating = false;
    /// Length in logical pixels of one repeating cycle. Zero uses the full
    /// resolved paint-box line.
    float repeat_length = 0.0F;
    std::vector<GradientStop> stops;
};

struct BoxShadow final {
    float offset_x = 0.0F;
    float offset_y = 0.0F;
    float blur_radius = 0.0F;
    float spread_radius = 0.0F;
    Color color = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
};

struct TextStyle final {
    std::string font_family = "Inter";
    int font_weight = 400;
    float font_size = 14.0F;
    float letter_spacing = 0.0F;
    Color color = Color::hex(0x111827);
    bool underline = false;
    bool strikethrough = false;
};

struct TextRun final {
    std::string text;
    TextStyle style;
};

/// Premultiplied RGBA8 texels, row-major, top row first.
struct MeshTexture final {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::uint8_t> premultiplied_rgba;
};

/// One vertex of a triangle mesh. The position is relative to the owning
/// node's origin. `u` and `v` are normalized texture coordinates. The colour
/// is premultiplied RGBA8 packed with red in the high byte; with a texture it
/// multiplies the sampled texel.
struct MeshVertex final {
    float x = 0.0F;
    float y = 0.0F;
    float u = 0.0F;
    float v = 0.0F;
    std::uint32_t premultiplied_rgba = 0xFFFFFFFFU;
};

/// An indexed triangle list with an optional texture and clip rectangle.
///
/// Positions are final: a producer that applies a projective transform does
/// so per vertex before building the mesh, so colours and texture coordinates
/// interpolate linearly in output space. Texels are sampled nearest-neighbour
/// with clamped edges, and the mesh is drawn without anti-aliasing.
struct TriangleMesh final {
    std::vector<MeshVertex> vertices;
    /// Three indices per triangle.
    std::vector<std::uint32_t> indices;
    std::shared_ptr<const MeshTexture> texture;
    bool clip_enabled = false;
    /// Clip rectangle relative to the owning node's origin.
    Rect clip{};
};

/// One command of a `PathGeometry`. Each verb consumes a fixed number of
/// values from `PathGeometry::values`, given by `path_verb_arity`.
enum class PathVerb : std::uint8_t {
    /// x, y: starts a new subpath.
    move,
    /// x, y.
    line,
    /// Control x, y, then end x, y.
    quadratic,
    /// First control x, y, second control x, y, then end x, y.
    cubic,
    /// An SVG elliptical arc: radius x, radius y, x-axis rotation in degrees,
    /// large-arc flag, sweep flag (each 0 or 1), then end x, y.
    arc,
    /// Closes the current subpath; consumes nothing.
    close,
};

[[nodiscard]] constexpr std::size_t path_verb_arity(PathVerb verb) noexcept {
    switch (verb) {
    case PathVerb::move:
    case PathVerb::line: return 2U;
    case PathVerb::quadratic: return 4U;
    case PathVerb::cubic: return 6U;
    case PathVerb::arc: return 7U;
    case PathVerb::close: return 0U;
    }
    return 0U;
}

enum class FillRule : std::uint8_t {
    nonzero,
    evenodd,
};

enum class StrokeJoin : std::uint8_t {
    miter,
    round,
    bevel,
};

enum class StrokeCap : std::uint8_t {
    butt,
    round,
    square,
};

/// How a path's outline is stroked. A zero width or a transparent colour
/// draws no stroke.
struct PathStroke final {
    Color color = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    float width = 0.0F;
    StrokeJoin join = StrokeJoin::miter;
    StrokeCap cap = StrokeCap::butt;
    /// At least 1, as in SVG.
    float miter_limit = 4.0F;
    /// Alternating dash and gap lengths; empty draws a solid stroke.
    std::vector<float> dash_array;
    float dash_offset = 0.0F;
};

/// The geometry and stroke of a `path` node. Coordinates are absolute within
/// the path and relative to the owning node's origin; relative and shorthand
/// SVG commands are resolved when the path is built, so a scene never stores
/// path data strings.
///
/// The fill paint is the node's `fill` and `fill_gradients`. A gradient
/// resolves against the node's layout box, or against
/// `path_control_bounds` when that box has no area, like SVG's
/// objectBoundingBox. The stroke is a single colour; stroke gradients are not
/// supported. The SVG paint defaults (a black fill, no stroke, a stroke width
/// of 1) belong to the authoring layer: a C++ caller building a node gets the
/// transparent `SceneNode::fill` and the defaults below.
struct PathGeometry final {
    std::vector<PathVerb> verbs;
    /// The verbs' values, concatenated in order.
    std::vector<float> values;
    FillRule fill_rule = FillRule::nonzero;
    PathStroke stroke;
    bool antialias = true;
};

/// How an image fills its node's box.
enum class ImageFit : std::uint8_t {
    /// Stretches the source to the box, as CSS `object-fit: fill`.
    fill,
    /// Scales the source uniformly to fit inside the box and centres it.
    contain,
    /// Scales the source uniformly to cover the box, centres it, and crops
    /// what falls outside.
    cover,
    /// Draws the source at one logical pixel per image pixel, centred and
    /// cropped to the box.
    none,
};

enum class ImageSampling : std::uint8_t {
    nearest,
    linear,
};

/// Decoded image pixels, named by the content hash of the asset they were
/// decoded from. Like a mesh texture, an asset must not be mutated once a
/// scene references it; replace the pointer to change it. Keep the same
/// object from frame to frame: a renderer may remember work per object (the
/// Skia surface hashes an asset's texels once per object), so rebuilding an
/// unchanged asset every frame costs that work every frame.
struct ImageAsset final {
    /// `sha256:` followed by 64 lowercase hexadecimal digits, the DesignIR
    /// asset convention (see `is_content_hash`).
    std::string content_hash;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    /// Premultiplied RGBA8 texels, row-major, top row first.
    std::vector<std::uint8_t> premultiplied_rgba;
};

/// What an `image` node draws: the asset named by `content_hash` in
/// `Scene::images`, cropped to `source_rect` and fitted into the node's box.
/// A reference whose asset is not in the scene draws
/// `kMissingImagePlaceholder` over the box instead.
struct ImageReference final {
    std::string content_hash;
    /// The part of the image to draw, in image pixels. Absent draws the whole
    /// image; a rectangle reaching past the image is clipped to it.
    std::optional<Rect> source_rect;
    ImageFit fit = ImageFit::fill;
    ImageSampling sampling = ImageSampling::linear;
};

/// A point in logical pixels.
struct Point final {
    float x = 0.0F;
    float y = 0.0F;
};

/// A two-dimensional affine transform. It maps a point (x, y) to
/// (a * x + c * y + tx, b * x + d * y + ty); the six values are in the order
/// of CSS `matrix(a, b, c, d, tx, ty)`. The default is the identity.
struct Affine2D final {
    float a = 1.0F;
    float b = 0.0F;
    float c = 0.0F;
    float d = 1.0F;
    float tx = 0.0F;
    float ty = 0.0F;
};

[[nodiscard]] constexpr Affine2D translation(float x, float y) noexcept {
    return {.tx = x, .ty = y};
}

/// The transform that applies `inner` first and then `outer`.
[[nodiscard]] constexpr Affine2D concat(const Affine2D& outer, const Affine2D& inner) noexcept {
    return {
        .a = outer.a * inner.a + outer.c * inner.b,
        .b = outer.b * inner.a + outer.d * inner.b,
        .c = outer.a * inner.c + outer.c * inner.d,
        .d = outer.b * inner.c + outer.d * inner.d,
        .tx = outer.a * inner.tx + outer.c * inner.ty + outer.tx,
        .ty = outer.b * inner.tx + outer.d * inner.ty + outer.ty,
    };
}

[[nodiscard]] constexpr Point apply(const Affine2D& transform, Point point) noexcept {
    return {
        transform.a * point.x + transform.c * point.y + transform.tx,
        transform.b * point.x + transform.d * point.y + transform.ty,
    };
}

[[nodiscard]] constexpr bool is_identity(const Affine2D& transform) noexcept {
    return transform.a == 1.0F && transform.b == 0.0F && transform.c == 0.0F &&
           transform.d == 1.0F && transform.tx == 0.0F && transform.ty == 0.0F;
}

[[nodiscard]] inline bool is_finite(const Affine2D& transform) noexcept {
    return std::isfinite(transform.a) && std::isfinite(transform.b) &&
           std::isfinite(transform.c) && std::isfinite(transform.d) &&
           std::isfinite(transform.tx) && std::isfinite(transform.ty);
}

/// Corner radii in logical pixels, clockwise from the top-left corner. Each
/// corner is circular. Radii that together exceed a side are scaled down
/// proportionally when drawn, as CSS `border-radius` is.
struct CornerRadii final {
    float top_left = 0.0F;
    float top_right = 0.0F;
    float bottom_right = 0.0F;
    float bottom_left = 0.0F;
};

[[nodiscard]] constexpr CornerRadii uniform_corner_radii(float radius) noexcept {
    return {radius, radius, radius, radius};
}

[[nodiscard]] constexpr bool is_uniform(const CornerRadii& radii) noexcept {
    return radii.top_left == radii.top_right && radii.top_left == radii.bottom_right &&
           radii.top_left == radii.bottom_left;
}

[[nodiscard]] constexpr bool is_rounded(const CornerRadii& radii) noexcept {
    return radii.top_left > 0.0F || radii.top_right > 0.0F ||
           radii.bottom_right > 0.0F || radii.bottom_left > 0.0F;
}

[[nodiscard]] inline bool is_finite(const CornerRadii& radii) noexcept {
    return std::isfinite(radii.top_left) && std::isfinite(radii.top_right) &&
           std::isfinite(radii.bottom_right) && std::isfinite(radii.bottom_left);
}

/// The scene schema version this build produces.
///
/// The version is a single integer. Bump it when a change can alter rendered
/// output: a new `SceneNode::Kind`, a new field that changes how a scene is
/// drawn, a new meaning for an existing field, or a default that alters
/// existing output. Keep it when a change only adds metadata that no renderer
/// draws. A consumer accepts only the versions it knows and fails closed on
/// any other, so a scene built for a newer schema is never drawn with parts
/// silently missing.
///
/// Version 2 adds `SceneNode::transform`, `opacity`, and `corner_radii`. A
/// version 1 scene is still accepted when none of its nodes sets them; one
/// that does fails `scene_fields_match_version` and is refused.
///
/// Version 3 adds the `path` node kind. A version 1 or 2 scene is still
/// accepted when it has no path node; one that does fails
/// `scene_fields_match_version` and is refused.
///
/// Version 4 adds the `image` node kind and `Scene::images`. A scene of an
/// earlier version is still accepted when it has no image node; one that does
/// fails `scene_fields_match_version` and is refused.
///
/// Claimed versions, in order:
/// version 4: image nodes; version 5: clip, mask and isolation on groups (reserved)
inline constexpr std::uint32_t kSceneVersion = 4U;

/// The oldest scene schema version this build still consumes.
inline constexpr std::uint32_t kMinSupportedSceneVersion = 1U;

[[nodiscard]] constexpr bool scene_version_is_supported(std::uint32_t version) noexcept {
    return version >= kMinSupportedSceneVersion && version <= kSceneVersion;
}


/// A deliberately small retained scene used by the first extraction proof.
///
/// Nodes carry stable semantic identities so import, interaction, and capture
/// can address the same object. The scene is renderer-independent; Skia/Dawn
/// is one consumer rather than the owner of application state.
///
/// `Kind` is closed for a given `kSceneVersion`. A consumer that meets a kind
/// it does not know refuses the scene rather than treating the node as a
/// `group`; producers report authored content they cannot express as a known
/// kind through `LoweringDiagnostic` instead of emitting it.
struct SceneNode final {
    enum class Kind {
        group,
        rectangle,
        text,
        custom,
        mesh,
        path,
        image,
    };

    std::string id;
    Kind kind = Kind::group;
    Rect bounds{};
    Color fill = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
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
    std::string custom_component;
    std::string custom_properties_json;
    std::vector<SceneNode> children;
    /// The geometry of a `mesh` node.
    std::shared_ptr<const TriangleMesh> mesh;
    /// A transform applied about the node's origin, the top-left corner of
    /// `bounds`, in addition to the `bounds` offset. It applies to the node's
    /// own paint and to its whole subtree, composing with every ancestor's.
    /// Absent is the identity.
    std::optional<Affine2D> transform;
    /// The opacity of the node and its subtree as one group. Values outside
    /// [0, 1] are clamped. Overlapping descendants are composited together
    /// first and then faded, so they do not show through one another.
    float opacity = 1.0F;
    /// Per-corner radii. When present they replace `corner_radius`, which
    /// remains the uniform shorthand.
    std::optional<CornerRadii> corner_radii;
    /// The geometry of a `path` node. A path node without it is refused. Like
    /// a mesh, it must not be mutated once a scene references it; replace the
    /// pointer to change it.
    std::shared_ptr<const PathGeometry> path;
    /// What an `image` node draws. An image node without it, or with
    /// children, is refused. The node's `fill` paints behind the image, and
    /// its corner radii clip both.
    std::optional<ImageReference> image;
};

/// The corner radii a node is drawn with: `corner_radii` when present,
/// otherwise `corner_radius` on every corner. Negative radii resolve to zero.
[[nodiscard]] constexpr CornerRadii resolved_corner_radii(const SceneNode& node) noexcept {
    const auto radii = node.corner_radii.value_or(uniform_corner_radii(node.corner_radius));
    const auto positive = [](float value) { return value > 0.0F ? value : 0.0F; };
    return {
        positive(radii.top_left),
        positive(radii.top_right),
        positive(radii.bottom_right),
        positive(radii.bottom_left),
    };
}

/// Whether a node sets a field version 2 introduced: a non-identity
/// transform, an opacity other than one, or per-corner radii.
[[nodiscard]] constexpr bool uses_version_2_fields(const SceneNode& node) noexcept {
    return (node.transform && !is_identity(*node.transform)) || node.opacity != 1.0F ||
           node.corner_radii.has_value();
}

/// The first node, in depth-first order, that `uses_version_2_fields`.
[[nodiscard]] inline const SceneNode* find_version_2_fields(const SceneNode& node) noexcept {
    if (uses_version_2_fields(node)) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_version_2_fields(child); found != nullptr) return found;
    }
    return nullptr;
}

/// Whether a node's transform, opacity, and corner radii are all finite.
[[nodiscard]] inline bool compositing_is_finite(const SceneNode& node) noexcept {
    return std::isfinite(node.opacity) &&
           (!node.transform || is_finite(*node.transform)) &&
           (!node.corner_radii || is_finite(*node.corner_radii));
}

/// The first node, in depth-first order, whose transform, opacity, or corner
/// radii are not finite.
[[nodiscard]] inline const SceneNode* find_non_finite_compositing(
    const SceneNode& node) noexcept {
    if (!compositing_is_finite(node)) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_non_finite_compositing(child); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

[[nodiscard]] constexpr bool is_known_kind(SceneNode::Kind kind) noexcept {
    switch (kind) {
    case SceneNode::Kind::group:
    case SceneNode::Kind::rectangle:
    case SceneNode::Kind::text:
    case SceneNode::Kind::custom:
    case SceneNode::Kind::mesh:
    case SceneNode::Kind::path:
    case SceneNode::Kind::image:
        return true;
    }
    return false;
}

/// The first node, in depth-first order, whose kind fails `is_known_kind`.
[[nodiscard]] inline const SceneNode* find_unknown_kind(const SceneNode& node) noexcept {
    if (!is_known_kind(node.kind)) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_unknown_kind(child); found != nullptr) {
            return found;
        }
    }
    return nullptr;
}

struct Scene final {
    float width = 0.0F;
    float height = 0.0F;
    Color background = Color::hex(0x111827);
    SceneNode root;
    std::uint32_t scene_version = kSceneVersion;
    /// The decoded assets image nodes name by content hash. Each hash appears
    /// at most once.
    std::vector<std::shared_ptr<const ImageAsset>> images;
};

/// The first node, in depth-first order, of a kind version 3 introduced: a
/// `path` node.
[[nodiscard]] inline const SceneNode* find_version_3_kinds(const SceneNode& node) noexcept {
    if (node.kind == SceneNode::Kind::path) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_version_3_kinds(child); found != nullptr) return found;
    }
    return nullptr;
}

/// The first node, in depth-first order, of a kind version 4 introduced: an
/// `image` node.
[[nodiscard]] inline const SceneNode* find_version_4_kinds(const SceneNode& node) noexcept {
    if (node.kind == SceneNode::Kind::image) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_version_4_kinds(child); found != nullptr) return found;
    }
    return nullptr;
}

/// Whether every field and kind the scene uses exists at its version: a
/// version 1 scene sets no field that `uses_version_2_fields` reports, a
/// version 1 or 2 scene has no node that `find_version_3_kinds` reports, and
/// a scene before version 4 has no node that `find_version_4_kinds` reports.
[[nodiscard]] inline bool scene_fields_match_version(const Scene& scene) noexcept {
    return (scene.scene_version >= 2U || find_version_2_fields(scene.root) == nullptr) &&
           (scene.scene_version >= 3U || find_version_3_kinds(scene.root) == nullptr) &&
           (scene.scene_version >= 4U || find_version_4_kinds(scene.root) == nullptr);
}

/// The first node, in depth-first order, that its scene's version cannot
/// carry: a field or kind newer than `scene.scene_version`, or a kind outside
/// `is_known_kind`. Null when `scene_fields_match_version` holds and every
/// kind is known.
[[nodiscard]] inline const SceneNode* find_version_mismatch(const Scene& scene) noexcept {
    if (const auto* unknown = find_unknown_kind(scene.root); unknown != nullptr) return unknown;
    if (scene.scene_version < 2U) {
        if (const auto* found = find_version_2_fields(scene.root); found != nullptr) return found;
    }
    if (scene.scene_version < 3U) {
        if (const auto* found = find_version_3_kinds(scene.root); found != nullptr) return found;
    }
    if (scene.scene_version < 4U) {
        if (const auto* found = find_version_4_kinds(scene.root); found != nullptr) return found;
    }
    return nullptr;
}

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
