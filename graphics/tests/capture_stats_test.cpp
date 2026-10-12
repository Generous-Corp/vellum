#include <vellum/graphics/capture_stats.hpp>

#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

int main() {
    using vellum::graphics::analyze_capture_rgba;
    using vellum::graphics::checked_image_byte_count;
    using vellum::graphics::passes_content_floor;

    if (checked_image_byte_count(32U, 24U, 4U) != 3072U ||
        checked_image_byte_count(0U, 24U, 4U).has_value() ||
        checked_image_byte_count(
            2U, 2U, std::numeric_limits<std::size_t>::max()).has_value()) {
        return 1;
    }

    bool rejected_overflow = false;
    try {
        constexpr std::uint32_t overflowing_dimension = 1U << 31U;
        (void)analyze_capture_rgba(
            {}, overflowing_dimension, overflowing_dimension);
    } catch (const std::invalid_argument&) {
        rejected_overflow = true;
    }
    if (!rejected_overflow) {
        return 1;
    }

    constexpr std::uint32_t width = 32;
    constexpr std::uint32_t height = 24;
    std::vector<std::uint8_t> blank(width * height * 4U, 0U);
    if (passes_content_floor(analyze_capture_rgba(blank, width, height))) {
        return 1;
    }

    std::vector<std::uint8_t> corner_content(10U * 10U * 4U, 255U);
    for (std::size_t pixel = 0; pixel < 10U; ++pixel) {
        const auto offset = pixel * 4U;
        corner_content[offset] = 10U;
        corner_content[offset + 1U] = 20U;
        corner_content[offset + 2U] = 30U;
    }
    const auto corner_stats = analyze_capture_rgba(corner_content, 10U, 10U);
    if (corner_stats.non_background_pixels != 10U || corner_stats.unique_colors != 2U) {
        return 1;
    }

    constexpr std::uint32_t sparse_width = 640;
    constexpr std::uint32_t sparse_height = 400;
    std::vector<std::uint8_t> sparse(
        sparse_width * sparse_height * 4U, 255U);
    for (std::size_t pixel = 0; pixel < sparse_width * sparse_height; ++pixel) {
        const auto offset = pixel * 4U;
        sparse[offset] = 15U;
        sparse[offset + 1U] = 23U;
        sparse[offset + 2U] = 42U;
    }
    for (std::size_t pixel = 0; pixel < 1280U; ++pixel) {
        const auto offset = pixel * 4U;
        sparse[offset] = static_cast<std::uint8_t>(
            40U + 20U * (pixel % 5U));
    }
    if (!passes_content_floor(
            analyze_capture_rgba(sparse, sparse_width, sparse_height))) {
        return 1;
    }
    for (std::size_t pixel = 256U; pixel < 1280U; ++pixel) {
        const auto offset = pixel * 4U;
        sparse[offset] = 15U;
    }
    if (passes_content_floor(
            analyze_capture_rgba(sparse, sparse_width, sparse_height))) {
        return 1;
    }

    std::vector<std::uint8_t> content(width * height * 4U, 255U);
    for (std::uint32_t y = 0; y < height; ++y) {
        for (std::uint32_t x = 0; x < width; ++x) {
            const auto offset = static_cast<std::size_t>(y * width + x) * 4U;
            content[offset] = static_cast<std::uint8_t>((x * 17U) & 0xFFU);
            content[offset + 1U] = static_cast<std::uint8_t>((y * 29U) & 0xFFU);
            content[offset + 2U] = static_cast<std::uint8_t>(((x + y) * 11U) & 0xFFU);
        }
    }
    return passes_content_floor(analyze_capture_rgba(content, width, height)) ? 0 : 1;
}
