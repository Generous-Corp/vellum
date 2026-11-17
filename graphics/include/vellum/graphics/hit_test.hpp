#pragma once

#include <vellum/graphics/scene.hpp>

#include <optional>
#include <string>
#include <unordered_set>

namespace vellum::graphics {

/// The id of a `SceneNode`.
using NodeId = std::string;

/// How `pick` chooses among the nodes under a point.
struct PickOptions final {
    /// How far from a path's or vector's drawn fill or stroke a point still
    /// hits, in frame pixels, whatever the transform. It is checked by testing
    /// the point and 16 points on two rings of this radius around it. Boxes
    /// (rectangles, text, images, plots) and clips are tested exactly.
    float tolerance = 0.5F;
    /// The ids of the nodes that may be returned, borrowed for the call. When
    /// set, the topmost hittable node under the point decides the result: it
    /// is returned if it is in the set, otherwise the nearest ancestor in the
    /// set is returned (so a label inside a button picks the button), and
    /// otherwise nothing is, because that node covers whatever lies beneath
    /// it. A node in the set is hittable even when it paints nothing. When
    /// null, the result is the topmost node that paints something.
    const std::unordered_set<NodeId>* interactive = nullptr;
};

/// The node under `point`, given in frame (logical pixel) coordinates, the
/// space of `Scene::root`'s parent. Tracks are evaluated at `scene.clock`
/// first, so a node is hit where it is drawn.
///
/// The walk visits nodes front to back in paint order: a node's children from
/// last to first, then the node itself. Each node's full transform, composed
/// with its ancestors', is inverted to map the point into the node's own
/// coordinates, so rotated, skewed and scaled nodes hit by their drawn shape.
///
/// What is hittable (curves and arcs are flattened to within a twentieth of a
/// frame pixel):
/// - `path` nodes by their geometry: the filled area under the path's fill
///   rule when the node has a visible fill, and the stroke outline (width,
///   butt, square and round caps, round and bevel joins; miter tips count as
///   bevels and dashes are ignored) when it has a visible stroke. A path
///   that paints neither is hit by its box only when it is interactive.
/// - `vector` nodes by the fill and stroke geometry of their path operations,
///   under each operation's and group's transform, and inside each
///   enclosing group's clip.
/// - `mesh` nodes by their triangles, inside the mesh clip when it is set.
/// - every other kind by its box, with its resolved corner radii. Only nodes
///   that paint something (a rectangle with a visible fill or gradient, text,
///   an image or a plot) are hittable. A group or custom node draws nothing,
///   and a transparent rectangle draws nothing visible, so each is hittable
///   only when it is interactive. Box shadows and effects are not hittable.
///
/// What is not hittable: a node with opacity zero, a non-finite transform or
/// clip, an unknown kind, or a transform with a zero determinant, and none of
/// its descendants.
///
/// Clips: a node's clip (a rectangle with radii, or a path under its fill
/// rule) limits its subtree, and its own paint too when `clips_own_paint`.
/// A point outside the clip misses.
///
/// Masks: a mask node is never hit itself. Its siblings are hit only where
/// the point lies inside the geometry that the mask node's own subtree would
/// hit as painted content, for every mask child of the parent. The mask's
/// alpha or luminance value is not sampled: a point that lands on a painted
/// shape of the mask counts as covered, and the parent's own paint is not
/// masked, as when drawing.
[[nodiscard]] std::optional<NodeId> pick(
    const Scene& scene, Point point, const PickOptions& options = {});

}  // namespace vellum::graphics
