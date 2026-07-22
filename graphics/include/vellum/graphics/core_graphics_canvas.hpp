#pragma once

#include <vellum/graphics/color.hpp>

#include <cstddef>
#include <string>
#include <vector>

#if !defined(__APPLE__)
#error "CoreGraphicsCanvas is available only on Apple platforms"
#endif

#include <CoreGraphics/CoreGraphics.h>

namespace vellum::graphics {

class CoreGraphicsCanvas final {
public:
    CoreGraphicsCanvas(CGContextRef context, float width, float height) noexcept;

    void set_fill_color(Color color) noexcept;
    void set_font(std::string family, float size);
    void set_fill_gradient_linear(
        float x0,
        float y0,
        float x1,
        float y1,
        const Color* colors,
        const float* positions,
        std::size_t count);
    void clear_fill_gradient() noexcept;

    void fill_rect(float x, float y, float width, float height);
    void fill_rounded_rect(
        float x, float y, float width, float height, float radius);
    void fill_text(const std::string& text, float x, float baseline_y);

    [[nodiscard]] float width() const noexcept { return width_; }
    [[nodiscard]] float height() const noexcept { return height_; }

private:
    struct LinearGradient {
        CGPoint start{};
        CGPoint end{};
        std::vector<Color> colors;
        std::vector<CGFloat> positions;
    };

    void apply_fill() const noexcept;
    void fill_current_path();

    CGContextRef context_ = nullptr;
    float width_ = 0.0F;
    float height_ = 0.0F;
    Color fill_ = Color::rgba(0.0F, 0.0F, 0.0F, 1.0F);
    std::string font_family_ = "Helvetica";
    float font_size_ = 14.0F;
    LinearGradient gradient_;
};

}  // namespace vellum::graphics
