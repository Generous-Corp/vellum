#pragma once

#include <vellum/graphics/color.hpp>

#include <array>
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

/// A clip shape in the owning node's coordinates, relative to its origin.
///
/// Without `path` the clip is `rect` with `radii`, each corner circular and
/// scaled down like `CornerRadii` when the radii exceed a side; a negative
/// width or height clips everything. With `path` the clip is the area the
/// path fills under its fill rule; its stroke is ignored, and a path with no
/// commands clips everything. Clip edges are anti-aliased.
///
/// With `clips_own_paint`, the default, the clip limits the node's own paint
/// as well as its subtree, like CSS `clip-path`; without it only the subtree
/// is clipped and the node's own fill and shadows stay whole, like CSS
/// `overflow: hidden`.
struct NodeClip final {
    Rect rect{};
    CornerRadii radii{};
    bool clips_own_paint = true;
    /// Like a path node's geometry, it must not be mutated once a scene
    /// references it; replace the pointer to change it.
    std::shared_ptr<const PathGeometry> path;
};

/// How a mask node's rendered subtree becomes coverage for its siblings.
enum class MaskMode : std::uint8_t {
    /// The mask's alpha.
    alpha,
    /// The mask's luminance multiplied by its alpha, as CSS `mask-mode:
    /// luminance` and SVG `<mask>` define it (Rec. 709 coefficients).
    luminance,
};

/// How a node's composited group meets what lies beneath it.
///
/// `normal` is the default and draws no group of its own. Every other mode
/// composites the node and its subtree offscreen as one group, as `isolate`
/// does, and then combines that group with the backdrop by the mode's formula:
/// the separable and non-separable blend modes of the W3C Compositing and
/// Blending specification, and the Porter-Duff operators of the canvas
/// `globalCompositeOperation` list. `source_over` is the same arithmetic as
/// `normal` but always draws the group offscreen.
///
/// The backdrop is whatever the enclosing group holds, or the frame. A mode
/// that empties the backdrop wherever the group draws nothing, such as
/// `destination_in`, `clear` or `source` (see `blend_mode_clears_backdrop`),
/// does so across the whole backdrop, as the canvas operators of the same name
/// do, not only within the group's paint extent.
///
/// Tier 1 modes are required on every backend. Tier 2 modes are required on
/// Skia Graphite and optional elsewhere; a backend without one draws the group
/// as `normal` and reports `kBlendModeFallbackCode`.
enum class BlendMode : std::uint8_t {
    // Tier 1.
    normal,
    multiply,
    screen,
    overlay,
    darken,
    lighten,
    difference,
    exclusion,
    source_over,
    destination_in,
    destination_out,
    // Tier 2.
    hue,
    saturation,
    color,
    luminosity,
    color_dodge,
    color_burn,
    hard_light,
    soft_light,
    clear,
    source,
    destination,
    source_in,
    source_out,
    source_atop,
    destination_over,
    destination_atop,
    exclusive_or,
    plus,
};

inline constexpr std::array<BlendMode, 29> kAllBlendModes{
    BlendMode::normal, BlendMode::multiply, BlendMode::screen, BlendMode::overlay,
    BlendMode::darken, BlendMode::lighten, BlendMode::difference, BlendMode::exclusion,
    BlendMode::source_over, BlendMode::destination_in, BlendMode::destination_out,
    BlendMode::hue, BlendMode::saturation, BlendMode::color, BlendMode::luminosity,
    BlendMode::color_dodge, BlendMode::color_burn, BlendMode::hard_light, BlendMode::soft_light,
    BlendMode::clear, BlendMode::source, BlendMode::destination, BlendMode::source_in,
    BlendMode::source_out, BlendMode::source_atop, BlendMode::destination_over,
    BlendMode::destination_atop, BlendMode::exclusive_or, BlendMode::plus,
};

[[nodiscard]] constexpr bool is_known_blend_mode(BlendMode mode) noexcept {
    return static_cast<std::uint8_t>(mode) <= static_cast<std::uint8_t>(BlendMode::plus);
}

/// Whether the mode leaves nothing of the backdrop where the group draws
/// nothing, so a group using it empties the whole backdrop outside its own
/// paint: `clear`, `source`, `source_in`, `source_out`, `destination_in` and
/// `destination_atop`.
[[nodiscard]] constexpr bool blend_mode_clears_backdrop(BlendMode mode) noexcept {
    switch (mode) {
    case BlendMode::clear:
    case BlendMode::source:
    case BlendMode::source_in:
    case BlendMode::source_out:
    case BlendMode::destination_in:
    case BlendMode::destination_atop: return true;
    default: return false;
    }
}

/// 1 for the modes every backend must draw, 2 for the rest, 0 for a value
/// outside the enumeration.
[[nodiscard]] constexpr int blend_mode_tier(BlendMode mode) noexcept {
    if (!is_known_blend_mode(mode)) return 0;
    return static_cast<std::uint8_t>(mode) <= static_cast<std::uint8_t>(BlendMode::destination_out)
        ? 1
        : 2;
}

/// The mode's name as DesignIR and the authoring `mixBlendMode` style spell
/// it: lowercase with underscores.
[[nodiscard]] constexpr std::string_view to_string(BlendMode mode) noexcept {
    switch (mode) {
    case BlendMode::normal: return "normal";
    case BlendMode::multiply: return "multiply";
    case BlendMode::screen: return "screen";
    case BlendMode::overlay: return "overlay";
    case BlendMode::darken: return "darken";
    case BlendMode::lighten: return "lighten";
    case BlendMode::difference: return "difference";
    case BlendMode::exclusion: return "exclusion";
    case BlendMode::source_over: return "source_over";
    case BlendMode::destination_in: return "destination_in";
    case BlendMode::destination_out: return "destination_out";
    case BlendMode::hue: return "hue";
    case BlendMode::saturation: return "saturation";
    case BlendMode::color: return "color";
    case BlendMode::luminosity: return "luminosity";
    case BlendMode::color_dodge: return "color_dodge";
    case BlendMode::color_burn: return "color_burn";
    case BlendMode::hard_light: return "hard_light";
    case BlendMode::soft_light: return "soft_light";
    case BlendMode::clear: return "clear";
    case BlendMode::source: return "source";
    case BlendMode::destination: return "destination";
    case BlendMode::source_in: return "source_in";
    case BlendMode::source_out: return "source_out";
    case BlendMode::source_atop: return "source_atop";
    case BlendMode::destination_over: return "destination_over";
    case BlendMode::destination_atop: return "destination_atop";
    case BlendMode::exclusive_or: return "xor";
    case BlendMode::plus: return "plus";
    }
    return "unknown";
}

/// The mode a name spells, with `-` accepted for `_` as CSS writes it, or
/// nothing for an unknown name.
[[nodiscard]] constexpr std::optional<BlendMode> parse_blend_mode(
    std::string_view name) noexcept {
    for (const auto mode : kAllBlendModes) {
        const auto canonical = to_string(mode);
        if (canonical.size() != name.size()) continue;
        bool same = true;
        for (std::size_t index = 0; index < name.size() && same; ++index) {
            const char given = name[index] == '-' ? '_' : name[index];
            same = given == canonical[index];
        }
        if (same) return mode;
    }
    return std::nullopt;
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
/// Version 5 adds `SceneNode::clip`, `mask`, and `isolate`. A scene of an
/// earlier version is still accepted when none of its nodes sets them; one
/// that does fails `scene_fields_match_version` and is refused.
///
/// Version 6 adds `SceneNode::blend_mode`. A scene of an earlier version is
/// still accepted when none of its nodes sets it; one that does fails
/// `scene_fields_match_version` and is refused.
///
/// Claimed versions, in order:
/// version 4: image nodes; version 5: clip, mask and isolation on groups;
/// version 6: blend modes and DesignIR compositing fields; version 7: token
/// table (reserved)
inline constexpr std::uint32_t kSceneVersion = 6U;

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
    /// Clips the node's own paint and its whole subtree. The clip is in the
    /// node's coordinates, so it moves with `transform`.
    std::optional<NodeClip> clip;
    /// Marks the node as a mask for its siblings. The node and its subtree
    /// are not drawn; they are rendered offscreen and their coverage, by
    /// `MaskMode`, multiplies the parent's other children, which are
    /// composited together first. Several mask children multiply. The
    /// parent's own paint is not masked. The root cannot be a mask.
    std::optional<MaskMode> mask;
    /// Composites the node and its subtree offscreen as one group even at
    /// full opacity, so the group meets what lies beneath it only once.
    bool isolate = false;
    /// How the node's group combines with what lies beneath it. Anything but
    /// `normal` draws the node and its subtree as one offscreen group, faded
    /// by `opacity`, and then blends that group over the backdrop. The
    /// backdrop is whatever the enclosing group holds, so an ancestor that
    /// fades or isolates contains the blend.
    BlendMode blend_mode = BlendMode::normal;
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

/// Whether a node sets a field version 5 introduced: a clip, a mask mode,
/// or isolation.
[[nodiscard]] constexpr bool uses_version_5_fields(const SceneNode& node) noexcept {
    return node.clip.has_value() || node.mask.has_value() || node.isolate;
}

/// The first node, in depth-first order, that `uses_version_5_fields`.
[[nodiscard]] inline const SceneNode* find_version_5_fields(const SceneNode& node) noexcept {
    if (uses_version_5_fields(node)) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_version_5_fields(child); found != nullptr) return found;
    }
    return nullptr;
}

/// Whether a node sets a field version 6 introduced: a blend mode other than
/// `normal`.
[[nodiscard]] constexpr bool uses_version_6_fields(const SceneNode& node) noexcept {
    return node.blend_mode != BlendMode::normal;
}

/// The first node, in depth-first order, that `uses_version_6_fields`.
[[nodiscard]] inline const SceneNode* find_version_6_fields(const SceneNode& node) noexcept {
    if (uses_version_6_fields(node)) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_version_6_fields(child); found != nullptr) return found;
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

/// The first node, in depth-first order, whose blend mode is outside
/// `BlendMode`.
[[nodiscard]] inline const SceneNode* find_unknown_blend_mode(const SceneNode& node) noexcept {
    if (!is_known_blend_mode(node.blend_mode)) return &node;
    for (const auto& child : node.children) {
        if (const auto* found = find_unknown_blend_mode(child); found != nullptr) return found;
    }
    return nullptr;
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
/// version 1 or 2 scene has no node that `find_version_3_kinds` reports, a
/// scene before version 4 has no node that `find_version_4_kinds` reports,
/// a scene before version 5 sets no field that `uses_version_5_fields`
/// reports, and a scene before version 6 sets no field that
/// `uses_version_6_fields` reports.
[[nodiscard]] inline bool scene_fields_match_version(const Scene& scene) noexcept {
    return (scene.scene_version >= 2U || find_version_2_fields(scene.root) == nullptr) &&
           (scene.scene_version >= 3U || find_version_3_kinds(scene.root) == nullptr) &&
           (scene.scene_version >= 4U || find_version_4_kinds(scene.root) == nullptr) &&
           (scene.scene_version >= 5U || find_version_5_fields(scene.root) == nullptr) &&
           (scene.scene_version >= 6U || find_version_6_fields(scene.root) == nullptr);
}

/// The first node, in depth-first order, that its scene's version cannot
/// carry: a field or kind newer than `scene.scene_version`, or a kind outside
/// `is_known_kind`. Null when `scene_fields_match_version` holds and every
/// kind and blend mode is known.
[[nodiscard]] inline const SceneNode* find_version_mismatch(const Scene& scene) noexcept {
    if (const auto* unknown = find_unknown_kind(scene.root); unknown != nullptr) return unknown;
    if (const auto* unknown = find_unknown_blend_mode(scene.root); unknown != nullptr) {
        return unknown;
    }
    if (scene.scene_version < 2U) {
        if (const auto* found = find_version_2_fields(scene.root); found != nullptr) return found;
    }
    if (scene.scene_version < 3U) {
        if (const auto* found = find_version_3_kinds(scene.root); found != nullptr) return found;
    }
    if (scene.scene_version < 4U) {
        if (const auto* found = find_version_4_kinds(scene.root); found != nullptr) return found;
    }
    if (scene.scene_version < 5U) {
        if (const auto* found = find_version_5_fields(scene.root); found != nullptr) return found;
    }
    if (scene.scene_version < 6U) {
        if (const auto* found = find_version_6_fields(scene.root); found != nullptr) return found;
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
