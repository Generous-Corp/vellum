#pragma once

#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string_view>
#include <vector>

namespace vellum::graphics {

/// A half-open rectangle in integer device pixels: `left <= x < right`.
struct PixelRect final {
    std::int32_t left = 0;
    std::int32_t top = 0;
    std::int32_t right = 0;
    std::int32_t bottom = 0;

    [[nodiscard]] constexpr std::int64_t width() const noexcept {
        return right > left ? static_cast<std::int64_t>(right) - left : 0;
    }
    [[nodiscard]] constexpr std::int64_t height() const noexcept {
        return bottom > top ? static_cast<std::int64_t>(bottom) - top : 0;
    }
    /// Saturates at the largest `int64_t` instead of overflowing.
    [[nodiscard]] constexpr std::int64_t area() const noexcept {
        const std::int64_t w = width();
        const std::int64_t h = height();
        if (w == 0 || h == 0) return 0;
        constexpr std::int64_t largest = 9223372036854775807;
        return h > largest / w ? largest : w * h;
    }
    [[nodiscard]] constexpr bool empty() const noexcept { return right <= left || bottom <= top; }

    friend constexpr bool operator==(const PixelRect&, const PixelRect&) = default;
};

/// The overlap of two rectangles; empty when they do not overlap.
[[nodiscard]] PixelRect intersection(const PixelRect& a, const PixelRect& b) noexcept;

/// The smallest rectangle containing both; an empty input is ignored.
[[nodiscard]] PixelRect bounding_union(const PixelRect& a, const PixelRect& b) noexcept;

[[nodiscard]] bool overlaps(const PixelRect& a, const PixelRect& b) noexcept;

/// The coordinate range a damage region works in; rectangles entering a
/// region are clamped to [-kMaxPixelCoordinate, kMaxPixelCoordinate].
inline constexpr std::int32_t kMaxPixelCoordinate = 1 << 30;

/// Converts a logical rectangle to the device pixels it touches at `scale`,
/// rounding outward and clamping each edge to +/-kMaxPixelCoordinate.
/// Non-finite input yields `std::nullopt`.
[[nodiscard]] std::optional<PixelRect> to_pixel_rect(const Rect& logical, float scale) noexcept;

/// The device-pixel frame of a scene at `scale`, rounding the size up.
///
/// A render target may round its size differently (the Skia/Dawn surface
/// rounds to nearest), so consumers clip damage to their actual target.
[[nodiscard]] PixelRect frame_rect(const Scene& scene, float scale) noexcept;

/// The largest rectangle budget a region accepts; coalescing costs about the
/// cube of the budget per insertion.
inline constexpr std::size_t kMaxDamageRects = 64;

/// Limits that keep damage tracking cheap and bounded.
struct DamagePolicy final {
    /// Largest number of rectangles a region holds before it merges them,
    /// clamped to [1, kMaxDamageRects].
    std::size_t max_rects = 16;
    /// Fraction of the frame area at or above which a region becomes the whole frame.
    double full_repaint_fraction = 0.5;
    /// Device pixels added around every damaged rectangle to cover anti-aliased edges.
    std::int32_t antialias_margin = 1;
};

/// Damage telemetry for one region.
struct DamageStats final {
    std::int64_t area_pixels = 0;
    std::int64_t frame_area_pixels = 0;
    double frame_fraction = 0.0;
    /// Rectangles submitted with `add`, after clipping to the frame.
    std::size_t input_rects = 0;
    /// The largest rectangle count reached before a coalescing pass, or the
    /// current count when none ran.
    std::size_t rects_before_coalescing = 0;
    std::size_t rects_after_coalescing = 0;
    std::size_t coalescing_passes = 0;
    /// The threshold turned accumulated damage into the whole frame.
    bool collapsed_to_full_frame = false;
    /// `add_full_frame` requested the whole frame (a first commit, a canvas
    /// change, a reload or an unknown node), independent of the threshold.
    bool explicit_full_frame = false;
};

/// A set of non-overlapping device-pixel rectangles clipped to a frame.
///
/// The list never exceeds `DamagePolicy::max_rects`: when it would, the
/// cheapest pairs are merged into their bounding boxes. Once the damaged area
/// reaches `full_repaint_fraction` of the frame the region becomes the frame.
class DamageRegion final {
public:
    DamageRegion() = default;
    explicit DamageRegion(PixelRect frame, DamagePolicy policy = {});

    void add(const PixelRect& rect);
    void add(const DamageRegion& other);
    void add_full_frame();
    /// Intersections clear both full-frame flags unless the result is still the whole frame.
    void intersect(const PixelRect& rect);
    void intersect(const DamageRegion& other);
    void clear();

    [[nodiscard]] const std::vector<PixelRect>& rects() const noexcept { return rects_; }
    [[nodiscard]] const PixelRect& frame() const noexcept { return frame_; }
    [[nodiscard]] const DamagePolicy& policy() const noexcept { return policy_; }
    [[nodiscard]] bool empty() const noexcept { return rects_.empty(); }
    [[nodiscard]] bool is_full_frame() const noexcept;
    [[nodiscard]] bool intersects(const PixelRect& rect) const noexcept;
    [[nodiscard]] std::int64_t area() const noexcept;
    [[nodiscard]] PixelRect bounds() const noexcept;
    [[nodiscard]] DamageStats stats() const noexcept;

private:
    void append_disjoint(const PixelRect& rect);
    void coalesce();
    void apply_threshold();
    void clear_full_flags_unless_full() noexcept;

    PixelRect frame_{};
    DamagePolicy policy_{};
    std::vector<PixelRect> rects_;
    std::size_t input_rects_ = 0;
    std::size_t peak_before_coalescing_ = 0;
    std::size_t coalescing_passes_ = 0;
    bool collapsed_ = false;
    bool explicit_full_ = false;
};

/// A scene node visited with its absolute logical bounds.
struct AbsoluteNode final {
    const SceneNode* node = nullptr;
    const SceneNode* parent = nullptr;
    Rect absolute_bounds{};
    std::size_t depth = 0;
};

/// Walks every node depth-first in paint order, accumulating parent offsets the
/// same way paint-command lowering does.
void visit_absolute_nodes(const Scene& scene, const std::function<void(const AbsoluteNode&)>& visit);

/// Extension point for effects and measurements that change where a node's
/// damage lands. The defaults expand nothing, clip nothing and estimate text
/// conservatively.
class DamagePropagation {
public:
    virtual ~DamagePropagation() = default;

    /// Logical pixels by which an effect on `node` (for example a blur
    /// kernel) spreads its damage beyond its painted bounds.
    [[nodiscard]] virtual float effect_radius(const SceneNode& node) const;

    /// An absolute logical clip that bounds the damage of `node` and its
    /// descendants, or `std::nullopt` for none.
    [[nodiscard]] virtual std::optional<Rect> clip_bounds(
        const SceneNode& node, const Rect& absolute_bounds) const;

    /// The absolute logical area the glyphs of a text node can cover. Text is
    /// laid out without wrapping and painted unclipped at the box origin, so
    /// the default extends to the right edge of `frame` and adds 1.4 times the
    /// largest font size per line break, plus one font size of overhang on
    /// every side. A renderer-backed override can measure exactly, for
    /// example with `SkiaDawnSurface::measure_text`.
    [[nodiscard]] virtual Rect text_extent(
        const SceneNode& node, const Rect& absolute_bounds, const Rect& frame) const;
};

/// The absolute logical area a node paints by itself, including shadows,
/// text extent and mesh geometry, before effect radius and clips. Empty for
/// nodes that paint nothing. `frame` is the scene's logical frame.
[[nodiscard]] std::optional<Rect> node_paint_bounds(
    const SceneNode& node, const Rect& absolute_bounds, const Rect& frame,
    const DamagePropagation& propagation);

/// Whether two nodes with the same identity paint differently in place.
/// Floats compare bitwise, so a NaN changing to a different NaN is a change.
/// Likewise +0.0 and -0.0 compare as different.
///
/// Meshes compare by pointer: a `TriangleMesh` or `MeshTexture` must not be
/// mutated once a scene references it; replace the pointer to change it.
[[nodiscard]] bool paint_differs(const SceneNode& previous, const SceneNode& next);

/// Damage between two scenes, keyed by node id (nodes with an empty or
/// repeated id are keyed by position). Added, removed, moved, resized,
/// restyled, reparented and reordered nodes damage both their old and new
/// areas. A changed frame size, background or `scene_version` damages the
/// whole frame.
[[nodiscard]] DamageRegion compute_scene_damage(
    const Scene& previous, const Scene& next, float scale,
    DamagePolicy policy = {}, const DamagePropagation* propagation = nullptr);

/// The newest scene schema version whose every paint-affecting field the
/// damage model understands. Partial redraw is enabled only for scene
/// versions the damage model understands; a newer scene repaints fully until
/// the damage model learns its fields and this constant is raised.
inline constexpr std::uint32_t kDamageModelSceneVersion = 1U;

/// Whether a scene of `version` may be redrawn from its damage alone.
[[nodiscard]] constexpr bool partial_redraw_supported(std::uint32_t version) noexcept {
    return version <= kDamageModelSceneVersion;
}

/// Why the authoring host asked for a redraw (`invalidate.reason`).
enum class InvalidationReason {
    state,
    timer,
    promise,
    service,
    reload,
};

[[nodiscard]] std::optional<InvalidationReason> parse_invalidation_reason(std::string_view reason) noexcept;

/// Renderer-owned damage state across frames.
///
/// The tracker keeps per-node records of the last committed scene (key,
/// parent, absolute bounds, damage area and paint fields), not the scene.
class DamageTracker final {
public:
    explicit DamageTracker(DamagePolicy policy = {},
                           std::shared_ptr<const DamagePropagation> propagation = nullptr);

    /// Diffs `next` against the last committed scene, accumulates the damage
    /// and commits `next`. The first scene, a new scale, frame size,
    /// background or `scene_version` damages the whole frame.
    void commit(const Scene& next, float scale);

    /// Damages every node with `node_id`, and their descendants, in the
    /// committed scene. An unknown id damages the whole frame. Before the
    /// first commit it does nothing, because that commit repaints everything.
    void mark_dirty(std::string_view node_id);

    /// Damages the whole committed frame for any reason. Before the first
    /// commit it does nothing.
    void mark_dirty_all(InvalidationReason reason);

    /// Handles a host `invalidate` message. `reload`, or any reason without a
    /// node id, calls `mark_dirty_all`; otherwise it calls `mark_dirty`.
    void invalidate(InvalidationReason reason, std::string_view node_id = {});

    /// The accumulated damage since the last call; resets the tracker's damage.
    [[nodiscard]] DamageRegion take_damage();

    [[nodiscard]] const DamageRegion& pending_damage() const noexcept { return pending_; }
    [[nodiscard]] const DamageStats& last_stats() const noexcept { return last_stats_; }
    [[nodiscard]] bool has_scene() const noexcept { return committed_ != nullptr; }

    class Snapshot;

private:
    DamagePolicy policy_;
    std::shared_ptr<const DamagePropagation> propagation_;
    std::shared_ptr<const Snapshot> committed_;
    DamageRegion pending_;
    DamageStats last_stats_{};
};

}  // namespace vellum::graphics
