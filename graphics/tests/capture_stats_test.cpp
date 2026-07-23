#include <vellum/graphics/capture_stats.hpp>

#include <cstdint>
#include <vector>

int main() {
    using vellum::graphics::analyze_capture_rgba;
    using vellum::graphics::passes_content_floor;

    constexpr std::uint32_t width = 32;
    constexpr std::uint32_t height = 24;
    std::vector<std::uint8_t> blank(width * height * 4U, 0U);
    if (passes_content_floor(analyze_capture_rgba(blank, width, height))) {
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
