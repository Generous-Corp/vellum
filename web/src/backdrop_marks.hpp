#pragma once

// A frame's backdrop: marks drawn behind every scene command with additive
// ("lighter") blending, for a view whose background is a field of many small
// marks (the Superconvolver editor's acoustic field). Thousands of marks per
// frame are too many for one scene node each, so a view hands the whole set
// over as one array of floats.
//
// The array is a sequence of records of `kBackdropHeader` floats:
//   dot   [0, x, y, radius, red, green, blue, alpha, 0]
//   glow  [1, x, y, radius, red, green, blue, alpha, middle_alpha]
//         a radial gradient from alpha at the centre through middle_alpha at
//         0.38 of the radius to transparent at the edge
//   line  [2, count, width, 0, red, green, blue, alpha, 0]
//         followed by `count` x, y pairs, stroked as one open polyline
// in CSS pixels, with colour channels from 0 to 1.

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

#include <bit>

namespace vellum::web {

inline constexpr std::size_t kBackdropHeader = 9;
// The most floats one frame's backdrop may hold: 4 MiB of marks.
inline constexpr std::size_t kMaxBackdropFloats = std::size_t{1} << 20;
// The most points one line may have.
inline constexpr std::size_t kMaxBackdropLinePoints = 4096;

enum class BackdropMark : std::uint8_t { dot = 0, glow = 1, line = 2 };

namespace backdrop_detail {

inline bool unit(float value) { return value >= 0.0F && value <= 1.0F; }

}  // namespace backdrop_detail

/// The number of marks in `data`, or nothing when any record is malformed:
/// an unknown kind, a value that is not finite, a negative radius or width,
/// a colour channel outside 0 to 1, a line with fewer than two points or
/// more than `kMaxBackdropLinePoints`, a record cut short, or an array past
/// `kMaxBackdropFloats`.
[[nodiscard]] inline std::optional<std::size_t> count_backdrop_marks(
    std::span<const float> data) {
    using backdrop_detail::unit;
    if (data.size() > kMaxBackdropFloats) return std::nullopt;
    for (const float value : data) {
        if (!std::isfinite(value)) return std::nullopt;
    }
    std::size_t marks = 0;
    std::size_t at = 0;
    while (at < data.size()) {
        if (data.size() - at < kBackdropHeader) return std::nullopt;
        const auto record = data.subspan(at, kBackdropHeader);
        if (!unit(record[4]) || !unit(record[5]) || !unit(record[6]) || !unit(record[7])) {
            return std::nullopt;
        }
        const float kind = record[0];
        if (kind == 0.0F || kind == 1.0F) {
            if (record[3] < 0.0F) return std::nullopt;
            if (kind == 0.0F && record[8] != 0.0F) return std::nullopt;
            if (kind == 1.0F && !unit(record[8])) return std::nullopt;
            at += kBackdropHeader;
        } else if (kind == 2.0F) {
            const float count = record[1];
            if (count < 2.0F || count > static_cast<float>(kMaxBackdropLinePoints) ||
                count != std::floor(count) || record[2] < 0.0F) {
                return std::nullopt;
            }
            const std::size_t points = 2U * static_cast<std::size_t>(count);
            if (data.size() - at - kBackdropHeader < points) return std::nullopt;
            at += kBackdropHeader + points;
        } else {
            return std::nullopt;
        }
        ++marks;
    }
    return marks;
}

/// Folds a backdrop into a command digest, so two frames that differ only in
/// their backdrop have different digests. An empty backdrop adds nothing.
[[nodiscard]] inline std::uint32_t backdrop_digest(std::uint32_t hash,
                                                   std::span<const float> data) {
    if (data.empty()) return hash;
    const auto mix = [&hash](std::uint32_t value) {
        for (unsigned shift = 0; shift < 32; shift += 8) {
            hash ^= (value >> shift) & 0xFFU;
            hash *= 16777619U;
        }
    };
    mix(0x62647270U);
    mix(static_cast<std::uint32_t>(data.size()));
    for (const float value : data) mix(std::bit_cast<std::uint32_t>(value));
    return hash;
}

}  // namespace vellum::web
