#pragma once

#include <pulp/canvas/canvas.hpp>
#include <string>
#include <vector>
#include <memory>
#include <cstdint>

namespace pulp::canvas {

// Parsed SVG image — can be rendered to any Canvas at any size
class SvgImage {
public:
    SvgImage() = default;
    ~SvgImage();

    SvgImage(SvgImage&& other) noexcept;
    SvgImage& operator=(SvgImage&& other) noexcept;

    SvgImage(const SvgImage&) = delete;
    SvgImage& operator=(const SvgImage&) = delete;

    // Load from SVG string
    static SvgImage from_string(const std::string& svg_data);

    // Load from file
    static SvgImage from_file(const std::string& path);

    // Check if loaded
    bool is_valid() const { return image_ != nullptr; }

    // Original dimensions
    float width() const;
    float height() const;

    // Rasterize to RGBA pixel buffer at a given size
    std::vector<uint8_t> rasterize(int width, int height) const;

    // Render into a Canvas (draws paths using Canvas primitives)
    void render(Canvas& canvas, float x, float y, float width, float height) const;

private:
    void* image_ = nullptr; // NSVGimage*
};

} // namespace pulp::canvas
