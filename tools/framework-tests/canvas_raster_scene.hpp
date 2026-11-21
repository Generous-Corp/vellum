#pragma once

#include <cstddef>
#include <string_view>

namespace canvas_raster_scene {

constexpr int width = 96;
constexpr int height = 52;
constexpr std::size_t row_bytes = width * 4;
constexpr std::string_view pam_header =
    "P7\nWIDTH 96\nHEIGHT 52\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n";

template<class Color, class LineCap, class Canvas>
inline bool paint(Canvas& canvas) {
    canvas.set_fill_color(Color::hex(0x172030));
    canvas.fill_rect(0, 0, width, height);

    canvas.set_stroke_color(Color::hex(0x66e7e1));
    canvas.set_line_cap(LineCap::round);
    canvas.set_line_width(2.25f);
    canvas.stroke_arc(20.25f, 19.25f, 13.25f, 2.27f, 5.48f);

    const bool icon_painted = canvas.draw_svg(
        R"(<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">
          <path fill="#dae1eb" fill-rule="evenodd"
                d="M8 .5 A7.5 7.5 0 1 1 8 15.5 A7.5 7.5 0 1 1 8 .5 Z
                   M8 3 L11.5 8 L8 13 L4.5 8 Z"/>
        </svg>)",
        52.375f, 9.75f, 18.0f, 18.0f);

    const Color stops[] = {
        Color::rgba8(238, 103, 132, 112),
        Color::rgba8(38, 229, 200, 203),
        Color::rgba8(142, 115, 248, 146),
    };
    const float positions[] = {0.0f, 0.45f, 1.0f};
    canvas.set_line_cap(LineCap::butt);
    canvas.set_line_width(1.5f);
    canvas.set_blend_mode(Canvas::BlendMode::screen);
    canvas.set_stroke_gradient_linear(6.25f, 0, 90.75f, 0, stops, positions, 3);
    canvas.stroke_line(6.25f, 42.25f, 90.75f, 42.25f);
    return icon_painted;
}

} // namespace canvas_raster_scene
