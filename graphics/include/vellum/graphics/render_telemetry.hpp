#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace vellum::graphics {

/// The most a renderer cache may hold. A cache evicts its least recently used
/// entries until it is within both limits, and refuses an entry larger than
/// `max_bytes` outright. A zero `max_entries` or `max_bytes` turns the cache
/// off: every lookup misses and nothing is kept.
struct CacheBudget final {
    std::size_t max_entries = 0;
    std::size_t max_bytes = 0;
};

/// Budgets for the renderer's resource caches. Every cache returns exactly
/// what it would build, so output never depends on the budgets; they trade
/// memory for the CPU and upload work a hit saves.
struct RenderCacheBudgets final {
    /// Shaped and laid-out text paragraphs, keyed by every run's text and
    /// style (colour included) and the font directory. Bytes are an estimate:
    /// Skia does not report a paragraph's size.
    CacheBudget paragraphs{512, 16U * 1024U * 1024U};
    /// Linear-gradient shaders, keyed by their exact end points, colours,
    /// positions and tile mode.
    CacheBudget gradients{256, 1024U * 1024U};
    /// GPU textures uploaded from mesh textures, keyed by size and the
    /// SHA-256 of the texels. Bytes are the texture's RGBA8 size.
    CacheBudget textures{64, 64U * 1024U * 1024U};
};

/// Counters for one cache. The lifetime counters run from the surface's
/// creation; the `frame_` counters cover only the most recent frame.
struct CacheStats final {
    std::uint64_t hits = 0;
    std::uint64_t misses = 0;
    /// Entries evicted to stay within the budget.
    std::uint64_t evictions = 0;
    /// Entries dropped by a trim: a settled resize, a device loss, or a
    /// budget change.
    std::uint64_t trimmed = 0;
    /// Entries refused because they alone exceed the byte budget, or because
    /// the cache is off.
    std::uint64_t refused = 0;
    std::uint64_t frame_hits = 0;
    std::uint64_t frame_misses = 0;
    std::size_t entries = 0;
    std::size_t bytes = 0;
};

/// Hits over lookups, or zero before the first lookup.
[[nodiscard]] double hit_rate(const CacheStats& stats) noexcept;
/// Hits over lookups in the most recent frame, or zero without lookups.
[[nodiscard]] double frame_hit_rate(const CacheStats& stats) noexcept;

/// Why the renderer last trimmed its caches.
enum class CacheTrimReason {
    none,
    /// A resize settled: the size held for `kSettleFrames` frames. Entries no
    /// frame has used since the resize are dropped.
    resize_settled,
    /// The Dawn device was lost. Every entry is dropped.
    device_lost,
    /// `set_cache_budgets` lowered a budget.
    budget_changed,
};

[[nodiscard]] const char* to_string(CacheTrimReason reason) noexcept;

/// Renderer telemetry for the most recent frame, beside `GpuEvidence`.
struct RenderTelemetry final {
    /// Frames drawn since the surface was created.
    std::uint64_t frame = 0;
    bool full_repaint = false;
    std::string full_repaint_reason;
    std::int64_t damage_area_pixels = 0;
    std::int64_t frame_area_pixels = 0;
    double damage_fraction = 0.0;
    std::size_t paint_commands_total = 0;
    std::size_t paint_commands_recorded = 0;
    /// Offscreen layers (`saveLayer`) the frame opened: group-opacity layers
    /// and paint that cannot fold its opacity, counted per damage rectangle.
    std::size_t layer_count = 0;
    CacheStats paragraphs;
    CacheStats gradients;
    CacheStats textures;
    /// GPU bytes held by the texture cache.
    std::size_t atlas_bytes = 0;
    /// Graphite's elapsed GPU time for the frame, once it has finished on the
    /// GPU and when the device supports timestamp queries.
    bool gpu_time_available = false;
    double gpu_milliseconds = 0.0;
    double cpu_milliseconds = 0.0;
    /// The resize state has settled (see `CacheTrimReason::resize_settled`).
    bool resize_settled = false;
    std::uint64_t trims = 0;
    CacheTrimReason last_trim = CacheTrimReason::none;
};

/// A JSON object holding every `RenderTelemetry` field, for capture evidence.
[[nodiscard]] std::string to_json(const RenderTelemetry& telemetry);

}  // namespace vellum::graphics
