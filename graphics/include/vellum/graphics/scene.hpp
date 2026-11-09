#pragma once

#include <vellum/graphics/color.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
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

/// The scene schema version this build produces and consumes.
///
/// The version is a single integer. Adding a field whose default leaves every
/// existing scene rendering exactly as before does not change it. Any other
/// change bumps it: a new `SceneNode::Kind`, a new meaning for an existing
/// field, or a default that alters existing output. A consumer accepts only
/// the versions it knows and fails closed on any other, so a scene built for
/// a newer schema is never drawn with parts silently missing. When a bump
/// lands, producers that still emit the previous version migrate by
/// constructing the new version's equivalent before handing the scene on.
inline constexpr std::uint32_t kSceneVersion = 1U;

[[nodiscard]] constexpr bool scene_version_is_supported(std::uint32_t version) noexcept {
    return version == kSceneVersion;
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
    /// The geometry of a `path` node. Like a mesh, it must not be mutated once
    /// a scene references it; replace the pointer to change it.
    std::shared_ptr<const PathGeometry> path;
};

[[nodiscard]] constexpr bool is_known_kind(SceneNode::Kind kind) noexcept {
    switch (kind) {
    case SceneNode::Kind::group:
    case SceneNode::Kind::rectangle:
    case SceneNode::Kind::text:
    case SceneNode::Kind::custom:
    case SceneNode::Kind::mesh:
    case SceneNode::Kind::path:
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
