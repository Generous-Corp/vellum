#pragma once

#include <vellum/graphics/paint_command.hpp>

#include <chrono>
#include <cstdint>
#include <vector>

namespace vellum::graphics::testing {

/// Which content the Skia surface redraws whole when partial damage meets it.
/// Every rule is on in production; tests turn one off to show what it guards.
struct PartialRedrawRules final {
    bool enclose_paths = true;
    bool enclose_own_layers = true;
    bool enclose_interpolated_fills = true;
    bool enclose_images = true;
    bool enclose_clips = true;
    bool enclose_group_layers = true;
    bool enclose_vector_assets = true;
    bool enclose_effects = true;
    bool enclose_backdrops = true;
};

/// Sets the rules for every surface in the process.
void set_partial_redraw_rules(const PartialRedrawRules& rules) noexcept;
[[nodiscard]] PartialRedrawRules partial_redraw_rules() noexcept;

/// Enables solid rectangle batches in full frames. Partial frames and
/// commands inside clips, masks or layers always use individual draws.
void set_rectangle_batching(bool enabled) noexcept;
[[nodiscard]] bool rectangle_batching_enabled() noexcept;

struct RectangleBatchCounters final {
    std::uint64_t batches = 0;
    std::uint64_t rectangles = 0;
};

/// Process-lifetime recording counters, including frames that fail to submit.
[[nodiscard]] RectangleBatchCounters rectangle_batch_counters() noexcept;

/// Sets Graphite's internal multisample count for surfaces created afterwards.
/// Production uses 1.
void set_internal_sample_count(std::uint32_t count) noexcept;
[[nodiscard]] std::uint32_t internal_sample_count() noexcept;

/// Recreation attempts a lost device gets in a row before recovery stops.
inline constexpr std::uint32_t kDeviceRecoveryAttempts = 6;
/// The wait after the first failed attempt; each later failure doubles it, up
/// to `kDeviceRecoveryMaxBackoff`.
inline constexpr std::chrono::milliseconds kDeviceRecoveryFirstBackoff{100};
inline constexpr std::chrono::milliseconds kDeviceRecoveryMaxBackoff{1000};

/// The clock device recovery reads to space its attempts; null restores the
/// steady clock.
using RecoveryClock = std::chrono::steady_clock::time_point (*)();
void set_recovery_clock(RecoveryClock clock) noexcept;

/// Makes the next `count` device recreations fail before they request an
/// adapter.
void fail_device_recreations(std::uint32_t count) noexcept;

/// Makes the next `count` device requests fail after the adapter was found,
/// the first creation of a device included.
void fail_device_requests(std::uint32_t count) noexcept;

/// Makes the next `count` plot draw operations fail. This is a test-only
/// control for proving callers report a renderer failure instead of skipping
/// the remaining pixel oracles.
void fail_plot_draws(std::uint32_t count) noexcept;

/// Makes the next `count` replacement devices lost as soon as they are created.
void lose_recreated_devices(std::uint32_t count) noexcept;

/// Makes the next `count` rebuilds of a surface after a device loss fail. A
/// surface retries its rebuild on the same backoff and limit as recreation.
void fail_surface_rebuilds(std::uint32_t count) noexcept;

/// Makes the device lost while each of the next `count` frames is submitted.
void lose_device_during_frames(std::uint32_t count) noexcept;

/// Applies the surface's rule that a layer holding a plot is unbounded below
/// scale 0.25, to commands lowered without a scale.
void unbound_plot_layers_for_scale(std::vector<PaintCommand>& commands, float scale);

}  // namespace vellum::graphics::testing
