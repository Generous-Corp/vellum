#include <vellum/graphics/capture_stats.hpp>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <unordered_set>

namespace vellum::graphics {

CaptureStats analyze_capture_rgba(
    std::span<const std::uint8_t> rgba,
    std::uint32_t width,
    std::uint32_t height) {
    const auto pixel_count = static_cast<std::size_t>(width) * height;
    if (width == 0 || height == 0 || rgba.size() != pixel_count * 4U) {
        throw std::invalid_argument("RGBA capture size does not match its dimensions");
    }

    CaptureStats stats{};
    stats.pixel_count = pixel_count;
    std::unordered_set<std::uint32_t> colors;
    colors.reserve(std::min<std::size_t>(pixel_count, 4096U));
    const auto background =
        (static_cast<std::uint32_t>(rgba[0]) << 24U) |
        (static_cast<std::uint32_t>(rgba[1]) << 16U) |
        (static_cast<std::uint32_t>(rgba[2]) << 8U) |
        rgba[3];
    double luminance_sum = 0.0;
    double luminance_square_sum = 0.0;

    for (std::size_t index = 0; index < pixel_count; ++index) {
        const auto offset = index * 4U;
        const auto color =
            (static_cast<std::uint32_t>(rgba[offset]) << 24U) |
            (static_cast<std::uint32_t>(rgba[offset + 1]) << 16U) |
            (static_cast<std::uint32_t>(rgba[offset + 2]) << 8U) |
            rgba[offset + 3];
        colors.insert(color);
        stats.opaque_pixels += rgba[offset + 3] >= 250U ? 1U : 0U;
        stats.non_background_pixels += color != background ? 1U : 0U;
        const double luminance =
            0.2126 * rgba[offset] + 0.7152 * rgba[offset + 1] +
            0.0722 * rgba[offset + 2];
        luminance_sum += luminance;
        luminance_square_sum += luminance * luminance;
    }
    stats.unique_colors = colors.size();
    const double mean = luminance_sum / static_cast<double>(pixel_count);
    const double variance = std::max(
        0.0, luminance_square_sum / static_cast<double>(pixel_count) - mean * mean);
    stats.luminance_standard_deviation = std::sqrt(variance);
    return stats;
}

bool passes_content_floor(const CaptureStats& stats) noexcept {
    if (stats.pixel_count == 0) {
        return false;
    }
    const auto opaque_ratio =
        static_cast<double>(stats.opaque_pixels) / static_cast<double>(stats.pixel_count);
    const auto content_ratio =
        static_cast<double>(stats.non_background_pixels) /
        static_cast<double>(stats.pixel_count);
    return stats.unique_colors >= 8 &&
           stats.luminance_standard_deviation >= 4.0 &&
           opaque_ratio >= 0.90 && content_ratio >= 0.05;
}

}  // namespace vellum::graphics
