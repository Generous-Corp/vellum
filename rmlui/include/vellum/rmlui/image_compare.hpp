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

// Images of different sizes, or empty ones, compare as entirely different.
inline ImageDifference compare_rgba(const std::vector<std::uint8_t>& a,
                                    const std::vector<std::uint8_t>& b,
                                    int per_channel_bound = kPerChannelBound) {
    if (a.size() != b.size() || a.empty() || a.size() % 4u != 0)
        return {255.0, 0.0};
    std::uint64_t sum = 0;
    std::size_t close = 0;
    const std::size_t pixels = a.size() / 4u;
    for (std::size_t p = 0; p < pixels; ++p) {
        bool ok = true;
        for (std::size_t c = 0; c < 4; ++c) {
            const int delta = std::abs(int(a[p * 4 + c]) - int(b[p * 4 + c]));
            sum += static_cast<std::uint64_t>(delta);
            ok = ok && delta <= per_channel_bound;
        }
        close += ok ? 1u : 0u;
    }
    return {static_cast<double>(sum) / static_cast<double>(a.size()),
            static_cast<double>(close) / static_cast<double>(pixels)};
}

inline bool within_renderer_tolerance(const ImageDifference& d) {
    return d.mean <= kMaxMeanDifference && d.within >= kMinWithinFraction;
}

} // namespace vellum::rmlui
