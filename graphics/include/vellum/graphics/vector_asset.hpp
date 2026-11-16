#pragma once

#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <optional>
#include <string>

namespace vellum::graphics {

/// Whether a compiled vector asset can be drawn as given: a content hash in
/// the `sha256:` form, a finite view box with positive width and height, at
/// most `kMaxVectorOps` operations, groups that balance and nest at most
/// `kMaxVectorGroupDepth` deep, finite transforms, group opacities within
/// [0, 1], clips and paths that pass `path_is_valid`, path geometry whose own
/// stroke colour is transparent and carries no gradient, and paints whose
/// colours are finite and whose gradients have two to 64 stops at finite,
/// non-decreasing positions within [0, 1], no repeat length, no reason from
/// `gradient_inexpressible_reason`, a transparent `color`, and a finite,
/// invertible `gradient_transform`. On failure `reason`, when non-null, says
/// which rule failed.
[[nodiscard]] bool vector_asset_is_valid(const VectorAsset& asset, std::string* reason = nullptr);

/// The transform that stretches `view_box` onto `bounds`.
[[nodiscard]] Affine2D vector_view_box_transform(const Rect& bounds, const Rect& view_box) noexcept;

/// The box a valid asset can paint, in view-box coordinates: the union of
/// every path operation's `path_paint_box` under the transforms of its
/// enclosing groups and its own, ignoring clips. Empty when no operation
/// paints. The box is non-finite, and so unbounded, when any operation's
/// extent is, as for a stroke with a huge miter limit or transforms whose
/// composition overflows float32.
[[nodiscard]] std::optional<Rect> vector_asset_paint_box(const VectorAsset& asset);

/// `vector_asset_paint_box` mapped by `vector_view_box_transform` onto
/// `bounds`.
[[nodiscard]] std::optional<Rect> vector_paint_bounds(const VectorAsset& asset, const Rect& bounds);

/// Whether a path operation's fill or stroke paints anything.
[[nodiscard]] bool vector_op_fills(const VectorOp& op) noexcept;
[[nodiscard]] bool vector_op_strokes(const VectorOp& op) noexcept;

/// The number of fill and stroke operations the asset paints, and whether it
/// opens any group. An asset with one paint operation and no group can take
/// an opacity into its paint exactly.
struct VectorPaintCount final {
    std::size_t paints = 0;
    bool grouped = false;
    bool gradients = false;
};

[[nodiscard]] VectorPaintCount vector_paint_count(const VectorAsset& asset) noexcept;

}  // namespace vellum::graphics
