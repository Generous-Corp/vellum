#pragma once

#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
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
    [[nodiscard]] constexpr std::int64_t area() const noexcept { return width() * height(); }
    [[nodiscard]] constexpr bool empty() const noexcept { return right <= left || bottom <= top; }

    friend constexpr bool operator==(const PixelRect&, const PixelRect&) = default;
};

/// The overlap of two rectangles; empty when they do not overlap.
[[nodiscard]] PixelRect intersection(const PixelRect& a, const PixelRect& b) noexcept;

/// The smallest rectangle containing both; an empty input is ignored.
[[nodiscard]] PixelRect bounding_union(const PixelRect& a, const PixelRect& b) noexcept;

[[nodiscard]] bool overlaps(const PixelRect& a, const PixelRect& b) noexcept;

/// Converts a logical rectangle to the device pixels it touches at `scale`,
/// rounding outward. Non-finite input yields `std::nullopt`.
[[nodiscard]] std::optional<PixelRect> to_pixel_rect(const Rect& logical, float scale) noexcept;

/// The device-pixel frame of a scene at `scale`.
[[nodiscard]] PixelRect frame_rect(const Scene& scene, float scale) noexcept;

/// Limits that keep damage tracking cheap and bounded.
struct DamagePolicy final {
    /// Largest number of rectangles a region holds before it merges them.
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
    bool collapsed_to_full_frame = false;
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

    PixelRect frame_{};
    DamagePolicy policy_{};
    std::vector<PixelRect> rects_;
    std::size_t input_rects_ = 0;
    std::size_t peak_before_coalescing_ = 0;
    std::size_t coalescing_passes_ = 0;
    bool collapsed_ = false;
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

/// Extension point for effects that change where a node's damage lands.
///
/// The default expands nothing and clips nothing.
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
};

/// The absolute logical area a node paints by itself, including shadows,
/// text overflow and mesh geometry, before propagation. Empty for nodes that
/// paint nothing.
[[nodiscard]] std::optional<Rect> node_paint_bounds(const SceneNode& node, const Rect& absolute_bounds);

/// Whether two nodes with the same identity paint differently in place.
[[nodiscard]] bool paint_differs(const SceneNode& previous, const SceneNode& next);

/// Damage between two scenes, keyed by node id. Added, removed, moved,
/// resized, restyled and reordered nodes damage both their old and new
/// areas. A changed frame size or background damages the whole frame.
[[nodiscard]] DamageRegion compute_scene_damage(
    const Scene& previous, const Scene& next, float scale,
    DamagePolicy policy = {}, const DamagePropagation* propagation = nullptr);

/// Why the authoring host asked for a redraw (`invalidate.reason`).
enum class InvalidationReason {
    state,
    timer,
    promise,
    service,
    reload,
};

[[nodiscard]] std::optional<InvalidationReason> parse_invalidation_reason(std::string_view reason) noexcept;

/// Renderer-owned damage state across frames, keyed by node id.
class DamageTracker final {
public:
    explicit DamageTracker(DamagePolicy policy = {},
                           std::shared_ptr<const DamagePropagation> propagation = nullptr);

    /// Diffs `next` against the last committed scene, accumulates the damage
    /// and commits `next`. The first scene, or a new scale, damages the whole frame.
    void commit(const Scene& next, float scale);

    /// Damages a node and its descendants in the committed scene. An unknown
    /// node, or no committed scene, damages the whole frame.
    void mark_dirty(std::string_view node_id);

    /// Handles a host invalidation. `reload`, or any reason without a node,
    /// damages the whole frame; otherwise only the named node is damaged.
    void mark_dirty_all(InvalidationReason reason, std::string_view node_id = {});

    /// The accumulated damage since the last call; resets the tracker's damage.
    [[nodiscard]] DamageRegion take_damage();

    [[nodiscard]] const DamageRegion& pending_damage() const noexcept { return pending_; }
    [[nodiscard]] const DamageStats& last_stats() const noexcept { return last_stats_; }
    [[nodiscard]] bool has_scene() const noexcept { return committed_.has_value(); }

private:
    void reset_pending();

    DamagePolicy policy_;
    std::shared_ptr<const DamagePropagation> propagation_;
    std::optional<Scene> committed_;
    float scale_ = 1.0F;
    DamageRegion pending_;
    DamageStats last_stats_{};
};

}  // namespace vellum::graphics
