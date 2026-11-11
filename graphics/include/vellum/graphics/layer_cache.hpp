#pragma once

#include <vellum/graphics/paint_command.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace vellum::graphics {

/// When a renderer keeps a subtree's paint as a cached image.
///
/// The policy lives in the renderer and is keyed by scene node id; scenes
/// carry no caching hints. A subtree is a candidate when its node is not the
/// scene root, has an id that no other node shares, and its subtree draws at
/// least `min_draws` commands. A candidate is cached when any of these holds:
///
/// - its node is faded as a group (`begin_layer`): such a group is drawn
///   through an offscreen layer anyway, so it is cached from its first frame
///   and an opacity change alone reuses the image;
/// - it draws at least `expensive_draws` commands and drew the same commands
///   on the previous frame;
/// - it drew the same commands as on each of the previous `static_frames`
///   frames.
///
/// The last two triggers apply only with `allow_inexact`, and never when
/// `LayerPlanOptions::exact_only` is set. A subtree whose image is dropped
/// before it is ever drawn from, or that the budget refuses, waits a few frames
/// (doubling on repeats) before it is tried again.
/// Only the outermost cached candidate on any path is drawn from its image.
struct LayerCachePolicy final {
    bool enabled = true;
    /// Allow the static and expensive triggers. Their images composite with
    /// one more rounding of translucent and antialiased pixels than drawing
    /// the commands directly, so a frame can differ from an uncached one by a
    /// level or more per channel and depends on the surface's history. Off by
    /// default, which keeps every capture byte-identical with and without
    /// caching; a faded group's image is exact and is always allowed.
    bool allow_inexact = false;
    /// Consecutive frames a subtree must repeat its commands before it is
    /// cached. Zero disables this trigger.
    std::uint32_t static_frames = 3;
    /// Subtrees drawing fewer commands are never cached.
    std::size_t min_draws = 2;
    /// Subtrees drawing at least this many commands are cached once they
    /// repeat one frame. Zero disables the trigger.
    std::size_t expensive_draws = 32;
    /// Largest number of cached images held at once.
    std::size_t max_layers = 32;
    /// Largest total area of cached images, in device pixels. Zero means twice
    /// the area of the target surface.
    std::int64_t max_pixels = 0;
};

/// Why a subtree is cached.
enum class LayerCacheTrigger {
    opacity,
    expensive,
    static_content,
};

/// Why a cached image was dropped. The values index
/// `LayerCacheStats::invalidations`.
enum class LayerInvalidation : std::size_t {
    /// A command's paint changed: fill, gradient, shadow, text or style.
    content,
    /// A command moved, resized or changed transform, with its paint unchanged.
    bounds,
    /// The subtree gained or lost commands, or a command changed kind.
    structure,
    /// A mesh or mesh texture was replaced.
    resource,
    /// The node no longer contributes to the scene, or it left the target.
    visibility,
    /// An enclosing subtree is now cached instead, or the subtree stopped
    /// qualifying for the policy.
    superseded,
    /// The surface was resized at the same scale.
    resize,
    /// The surface's scale changed.
    scale,
    /// The GPU device was lost; every image is gone with it.
    device_lost,
    /// A frame failed part way, so images built in it may be incomplete.
    frame_failed,
    /// The host invalidated a node the image contains.
    host_invalidate,
    /// The host sent a `reload`.
    reload,
    /// The policy was replaced or disabled caching.
    policy,
};

inline constexpr std::size_t kLayerInvalidationCount =
    static_cast<std::size_t>(LayerInvalidation::policy) + 1U;

[[nodiscard]] std::string_view to_string(LayerInvalidation reason) noexcept;
[[nodiscard]] std::string_view to_string(LayerCacheTrigger trigger) noexcept;

/// Layer-cache telemetry. Per-frame fields describe the most recent frame;
/// `invalidations` counts every drop since the cache was created.
struct LayerCacheStats final {
    /// Subtrees the policy considered this frame.
    std::size_t candidates = 0;
    /// Cached images held after the frame.
    std::size_t layers = 0;
    /// Device pixels those images cover.
    std::int64_t layer_pixels = 0;
    /// Cached images drawn without re-recording their commands.
    std::size_t hits = 0;
    /// Cached images (re)recorded this frame.
    std::size_t builds = 0;
    /// Paint commands drawn from a cached image instead of being recorded.
    std::size_t commands_from_cache = 0;
    /// Candidates the policy chose but the budget refused this frame.
    std::size_t refused_over_budget = 0;
    std::array<std::uint64_t, kLayerInvalidationCount> invalidations{};

    [[nodiscard]] std::uint64_t invalidated(LayerInvalidation reason) const noexcept {
        return invalidations[static_cast<std::size_t>(reason)];
    }
};

/// Whether two paint commands draw identically. Floats compare bitwise and
/// meshes compare by pointer, as `paint_differs` does for scene nodes.
[[nodiscard]] bool paint_commands_equal(const PaintCommand& a, const PaintCommand& b);

/// Which invalidation describes how `next` differs from `previous`: a
/// different count or kind is `structure`, a replaced mesh `resource`, a
/// change confined to `bounds` and `transform` `bounds`, anything else
/// `content`. Equal streams classify as `content`; callers compare first.
[[nodiscard]] LayerInvalidation classify_change(
    const std::vector<PaintCommand>& previous, const std::vector<PaintCommand>& next);

/// Options for one `LayerCache::plan`.
struct LayerPlanOptions final {
    /// False replays a frame (for a capture) without advancing the
    /// static-frame counters.
    bool count_frame = true;
    /// Cache only faded groups, whose image composites exactly as the
    /// uncached group layer does. Static and expensive subtrees composite
    /// with one more rounding of translucent and antialiased pixels, so a
    /// renderer that mixes partial frames with full repaints must keep
    /// them out to stay byte-identical across the two.
    bool exact_only = false;
};

/// The renderer-neutral half of the layer cache: it tracks every candidate
/// subtree across frames, decides which subtrees are drawn from an image and
/// which images must be (re)built or dropped, and keeps the budget. The
/// renderer owns the images, keyed by the same node ids.
///
/// Each frame the renderer calls `plan`, releases the images named by
/// `take_dropped`, then for each planned layer either draws its image or,
/// when `build` is set, reserves budget with `admit` before recording one.
class LayerCache final {
public:
    struct Layer final {
        std::string node_id;
        /// The subtree's commands, [`begin`, `end`).
        std::size_t begin = 0;
        std::size_t end = 0;
        /// The range is a faded group: the image holds the members between
        /// the `begin_layer` and `end_layer`, and is drawn with
        /// `commands[begin].opacity`.
        bool opacity_layer = false;
        LayerCacheTrigger trigger = LayerCacheTrigger::static_content;
        /// The image must be recorded this frame; otherwise the renderer's
        /// image for `node_id` is current.
        bool build = false;
    };

    /// An image the renderer must release, with the reason.
    struct Dropped final {
        std::string node_id;
        LayerInvalidation reason = LayerInvalidation::content;
    };

    explicit LayerCache(LayerCachePolicy policy = {});

    [[nodiscard]] const LayerCachePolicy& policy() const noexcept { return policy_; }

    /// Replaces the policy and drops every image.
    void set_policy(const LayerCachePolicy& policy);

    /// Plans one frame from its paint commands and their subtree ranges
    /// (`make_paint_commands(scene, &ranges)`). `target_pixels` is the
    /// surface's device-pixel area. Layers are returned in command order and
    /// never overlap.
    [[nodiscard]] std::vector<Layer> plan(
        const std::vector<PaintCommand>& commands,
        const std::vector<PaintCommandRange>& ranges, std::int64_t target_pixels,
        LayerPlanOptions options = {});

    /// Reserves `pixels` for the image of planned layer `node_id` before the
    /// renderer records it. Refusal (the budget is full, or `pixels` is not
    /// positive) means the renderer draws the subtree directly this frame.
    [[nodiscard]] bool admit(std::string_view node_id, std::int64_t pixels);

    /// Records that a planned layer was drawn from its existing image.
    void record_hit(std::string_view node_id);

    /// Abandons a planned build that was not recorded; the subtree is drawn
    /// directly this frame.
    void abandon(std::string_view node_id, LayerInvalidation reason);

    /// Drops every image.
    void invalidate_all(LayerInvalidation reason);

    /// Drops every image whose subtree has a command for `node_id`.
    void invalidate_node(std::string_view node_id);

    /// Images dropped since the last call; the renderer releases them.
    [[nodiscard]] std::vector<Dropped> take_dropped();

    [[nodiscard]] bool has_image(std::string_view node_id) const;
    [[nodiscard]] const LayerCacheStats& stats() const noexcept { return stats_; }

private:
    struct Entry final {
        std::uint64_t hash = 0;
        std::uint32_t unchanged_frames = 0;
        std::uint64_t seen_plan = 0;
        bool has_image = false;
        std::int64_t pixels = 0;
        /// The commands the image was recorded from.
        std::vector<PaintCommand> image_commands;
        /// Every node id in the subtree when the image was recorded.
        std::vector<std::string> image_node_ids;
        /// The commands and ids of a planned build, kept until `admit`.
        std::vector<PaintCommand> pending_commands;
        std::vector<std::string> pending_node_ids;
        bool pending_inexact = false;
        /// The image came from a static or expensive trigger.
        bool inexact_image = false;
        std::uint64_t hits_since_build = 0;
        /// Plans to wait after a wasted build, and the plan it ends at.
        std::uint64_t backoff = 0;
        std::uint64_t blocked_until = 0;
    };

    void drop(const std::string& node_id, Entry& entry, LayerInvalidation reason);
    std::int64_t pixel_budget() const noexcept;

    LayerCachePolicy policy_;
    std::unordered_map<std::string, Entry> entries_;
    std::vector<Dropped> dropped_;
    LayerCacheStats stats_{};
    /// Counts `plan` calls.
    std::uint64_t plan_ = 0;
    std::int64_t target_pixels_ = 0;
    std::int64_t reserved_pixels_ = 0;
    std::size_t reserved_layers_ = 0;
};

}  // namespace vellum::graphics
