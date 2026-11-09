#pragma once
// Compares two RGBA8 images the way this module's renderers are checked
// against each other: the mean absolute channel difference, and the fraction
// of pixels whose every channel is within a bound. A GPU renderer and
// SoftRenderer, drawing the same document, differ only in edge coverage and
// texture filtering, and are held to kMaxMeanDifference and kMinWithinFraction
// at kPerChannelBound.
//
// Only documents both renderers draw are comparable: SoftRenderer draws no
// clip masks, layers, filters or shaders, so a document using any of them
// differs by design. Pass both images in the same channel order and alpha
// convention (SoftRenderer's pixels() are premultiplied RGBA8, top row first).
//
// Header-only, no dependencies.

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <vector>

namespace vellum::rmlui {

struct ImageDifference {
    double mean = 0.0;   // mean absolute channel difference, 0..255
    double within = 1.0; // fraction of pixels with every channel within the bound
};

inline constexpr int kPerChannelBound = 8;
inline constexpr double kMaxMeanDifference = 2.0;
inline constexpr double kMinWithinFraction = 0.995;

// Two width x height images whose rows start row_bytes apart (padding past
// width * 4 bytes is ignored, as in a GPU readback with an aligned stride).
// Empty, null or short-row images compare as entirely different.
inline ImageDifference compare_rgba(const std::uint8_t* a, std::size_t a_row_bytes,
                                    const std::uint8_t* b, std::size_t b_row_bytes,
                                    std::uint32_t width, std::uint32_t height,
                                    int per_channel_bound = kPerChannelBound) {
    const std::size_t row = static_cast<std::size_t>(width) * 4u;
    if (!a || !b || width == 0 || height == 0 || a_row_bytes < row || b_row_bytes < row)
        return {255.0, 0.0};
    std::uint64_t sum = 0;
    std::size_t close = 0;
    for (std::uint32_t y = 0; y < height; ++y) {
        const std::uint8_t* pa = a + static_cast<std::size_t>(y) * a_row_bytes;
        const std::uint8_t* pb = b + static_cast<std::size_t>(y) * b_row_bytes;
        for (std::size_t i = 0; i < row; i += 4) {
            bool ok = true;
            for (std::size_t c = 0; c < 4; ++c) {
                const int delta = std::abs(int(pa[i + c]) - int(pb[i + c]));
                sum += static_cast<std::uint64_t>(delta);
                ok = ok && delta <= per_channel_bound;
            }
            close += ok ? 1u : 0u;
        }
    }
    const double pixels = static_cast<double>(width) * static_cast<double>(height);
    return {static_cast<double>(sum) / (pixels * 4.0), static_cast<double>(close) / pixels};
}

// Two tightly packed images of the same size. Different sizes, empty images
// or a partial pixel compare as entirely different.
inline ImageDifference compare_rgba(const std::vector<std::uint8_t>& a,
                                    const std::vector<std::uint8_t>& b,
                                    int per_channel_bound = kPerChannelBound) {
    if (a.size() != b.size() || a.empty() || a.size() % 4u != 0)
        return {255.0, 0.0};
    const auto width = static_cast<std::uint32_t>(a.size() / 4u);
    return compare_rgba(a.data(), a.size(), b.data(), b.size(), width, 1u, per_channel_bound);
}

inline bool within_renderer_tolerance(const ImageDifference& d) {
    return d.mean <= kMaxMeanDifference && d.within >= kMinWithinFraction;
}

} // namespace vellum::rmlui
