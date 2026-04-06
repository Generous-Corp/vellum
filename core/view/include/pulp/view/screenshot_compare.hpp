#pragma once

/// @file screenshot_compare.hpp
/// Compare two PNG screenshots for visual similarity.
/// Used by the design import pipeline to validate that generated Pulp code
/// renders close to the original design.

#include <string>
#include <vector>
#include <cstdint>

namespace pulp::view {

/// Result of comparing two screenshots.
struct CompareResult {
    bool valid = false;           ///< True if comparison completed successfully
    float similarity = 0.0f;     ///< 0.0 = completely different, 1.0 = identical
    uint32_t total_pixels = 0;   ///< Total pixels compared
    uint32_t diff_pixels = 0;    ///< Pixels that differ beyond threshold
    float mean_error = 0.0f;     ///< Mean per-channel error (0-255 scale)
    std::string error;           ///< Error message if comparison failed

    /// Returns true if similarity is above the given threshold (default 0.85)
    bool passes(float threshold = 0.85f) const { return valid && similarity >= threshold; }
};

/// Bounding box of changed pixels between two screenshots.
struct DiffBounds {
    bool valid = false;
    uint32_t x = 0;
    uint32_t y = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t diff_pixels = 0;
};

/// Compare two PNG images for visual similarity.
/// @param reference_png  Raw PNG bytes of the reference (source design) image
/// @param rendered_png   Raw PNG bytes of the rendered Pulp output
/// @param tolerance      Per-channel color tolerance (0-255). Pixels within
///                       this tolerance are considered matching. Default 32
///                       allows for minor rendering differences (antialiasing,
///                       theme color variations, font rendering).
CompareResult compare_screenshots(
    const std::vector<uint8_t>& reference_png,
    const std::vector<uint8_t>& rendered_png,
    uint8_t tolerance = 32
);

/// Compare two PNG files for visual similarity.
CompareResult compare_screenshot_files(
    const std::string& reference_path,
    const std::string& rendered_path,
    uint8_t tolerance = 32
);

/// Generate a visual diff image highlighting differences between two PNGs.
/// Returns empty vector on failure.
/// Matching pixels are dimmed, differing pixels are highlighted in red.
std::vector<uint8_t> generate_diff_image(
    const std::vector<uint8_t>& reference_png,
    const std::vector<uint8_t>& rendered_png,
    uint8_t tolerance = 32
);

/// Crop a rectangular region from a PNG image and return it as a new PNG.
/// Returns an empty vector if decoding fails or the requested region does not
/// intersect the source image.
std::vector<uint8_t> crop_png(
    const std::vector<uint8_t>& png,
    uint32_t x,
    uint32_t y,
    uint32_t width,
    uint32_t height
);

/// Compute the bounding box of pixels that differ beyond the given tolerance.
/// Returns an invalid result when decoding fails or no pixels differ.
DiffBounds diff_bounds(
    const std::vector<uint8_t>& reference_png,
    const std::vector<uint8_t>& rendered_png,
    uint8_t tolerance = 32
);

} // namespace pulp::view
