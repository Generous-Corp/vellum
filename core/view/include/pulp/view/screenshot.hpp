#pragma once

#include <pulp/view/view.hpp>
#include <string>
#include <vector>
#include <cstdint>

namespace pulp::view {

enum class ScreenshotBackend {
    default_backend,
    coregraphics,
    skia,
};

// Render a view tree to a PNG image buffer (headless, no window needed)
// Returns empty vector on failure or unsupported platform
std::vector<uint8_t> render_to_png(
    View& root,
    uint32_t width,
    uint32_t height,
    float scale = 2.0f,  // Retina scale factor
    ScreenshotBackend backend = ScreenshotBackend::default_backend
);

// Render a view tree to a PNG file
bool render_to_file(
    View& root,
    uint32_t width,
    uint32_t height,
    const std::string& output_path,
    float scale = 2.0f,
    ScreenshotBackend backend = ScreenshotBackend::default_backend
);

} // namespace pulp::view
