#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

namespace vellum::graphics {

struct CaptureStats final {
    std::size_t pixel_count = 0;
    std::size_t unique_colors = 0;
    std::size_t opaque_pixels = 0;
    std::size_t non_background_pixels = 0;
    double luminance_standard_deviation = 0.0;
};

[[nodiscard]] std::optional<std::size_t> checked_image_byte_count(
    std::uint32_t width,
    std::uint32_t height,
    std::size_t bytes_per_pixel) noexcept;

[[nodiscard]] CaptureStats analyze_capture_rgba(
    std::span<const std::uint8_t> rgba,
    std::uint32_t width,
    std::uint32_t height);

[[nodiscard]] bool passes_content_floor(const CaptureStats& stats) noexcept;

}  // namespace vellum::graphics
