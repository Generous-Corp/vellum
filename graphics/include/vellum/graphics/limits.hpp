#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>

namespace vellum::graphics {

// Limits that the JavaScript packages enforce too. `tools/limits/generate_limits.mjs`
// reads the declarations between the markers below and writes
// `limits.generated.js` for `@vellum/design-ir` and `@vellum/ui`. Each
// declaration is `inline constexpr TYPE kName = LITERAL;` with a plain numeric
// literal. A drift test fails when a generated file no longer matches.

// vellum-limits-begin

/// The largest `PlotSpec::slot_count` a scene may carry.
inline constexpr std::uint32_t kMaxPlotSlots = 4194304U;
/// The largest `PlotSpec::audio_channel`.
inline constexpr std::uint32_t kMaxPlotAudioChannel = 1024U;
/// The largest line width, bar gap and peak width, in logical pixels.
inline constexpr float kMaxPlotStroke = 4096.0F;
/// The largest finite 32-bit float, as a double.
inline constexpr double kMaxFloat32 = 3.4028234663852886e38;

/// The most operations one compiled vector asset may hold.
inline constexpr std::size_t kMaxVectorOps = 16384U;
/// The deepest nesting of groups in a compiled vector asset.
inline constexpr std::size_t kMaxVectorGroupDepth = 64U;

/// The longest SVG path data string, in bytes.
inline constexpr std::size_t kMaxPathDataBytes = 65536U;
/// The most entries a stroke dash array may hold.
inline constexpr std::size_t kMaxPathDashes = 64U;

/// The most stops one gradient may hold.
inline constexpr std::size_t kMaxGradientStops = 64U;

/// The most nodes a retained tree may hold.
inline constexpr std::size_t kMaxDesignNodes = 100000U;
/// The deepest nesting of a retained tree.
inline constexpr std::size_t kMaxDesignDepth = 256U;
/// The most design tokens one tree may declare.
inline constexpr std::size_t kMaxDesignTokens = 65536U;
/// The longest text input value, in bytes.
inline constexpr std::size_t kMaxTextInputBytes = 65536U;

/// The most animation tracks one node may carry.
inline constexpr std::size_t kMaxTracksPerNode = 32U;
/// The most keyframes one animation track may carry.
inline constexpr std::size_t kMaxKeyframesPerTrack = 256U;
/// The displacement and speed, in progress and progress per second, below
/// which a spring counts as settled.
inline constexpr double kSpringRestThreshold = 1.0e-4;

/// The longest source anchor, in bytes.
inline constexpr std::size_t kMaxSourceAnchorBytes = 1024U;
/// The longest source file name, in bytes.
inline constexpr std::size_t kMaxSourceFileBytes = 4096U;

// vellum-limits-end

static_assert(kMaxFloat32 == static_cast<double>(std::numeric_limits<float>::max()));
static_assert(kMaxPlotSlots == (1U << 22U));
static_assert(kMaxPathDataBytes == 64U * 1024U);

}  // namespace vellum::graphics
