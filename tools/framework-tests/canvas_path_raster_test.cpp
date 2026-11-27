// Canvas path drawing rasterized on a real Dawn device: the fill rule of point
// arrays, retained paths and the current path; polygon fills; polyline strokes
// and their joins; degenerate input; and a gradient-stroked SVG path widget.
// Each case reads the pixels back from the GPU and samples them, because a
// recording that carries the right commands can still paint the wrong thing.
// Offscreen only; no window is created. The executable exits 77 (skipped) when
// there is no Dawn adapter.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <vellum/canvas/canvas.hpp>
#include <vellum/canvas/path.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/view/svg_path_widget.hpp>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace {

using vellum::canvas::Canvas;
using vellum::canvas::Color;
using vellum::canvas::FillRule;
using vellum::render::HeadlessSurface;

struct Pixel {
    float r = 0, g = 0, b = 0;
};

// Renders `draw` onto a black WxH surface and returns the colour at (x, y) as
// 0..1 channels. An empty readback samples as the loud failure colour so a
// missing frame cannot read as "background".
template <typename DrawFn>
Pixel render_and_sample(uint32_t w, uint32_t h, DrawFn draw, uint32_t x, uint32_t y) {
    HeadlessSurface::Config cfg;
    cfg.width = w;
    cfg.height = h;
    cfg.clear_r = 0;
    cfg.clear_g = 0;
    cfg.clear_b = 0;
    cfg.clear_a = 255;
    std::string err;
    auto surface = HeadlessSurface::create(cfg, &err);
    if (!surface)
        return {1.0f, 0.0f, 1.0f};
    const auto rgba = surface->render_rgba([&](Canvas& c) { draw(c); });
    if (rgba.empty() || rgba.width != w || rgba.height != h)
        return {1.0f, 0.0f, 1.0f};
    const size_t i = (static_cast<size_t>(y) * w + x) * 4u;
    return {rgba.pixels[i] / 255.0f, rgba.pixels[i + 1] / 255.0f, rgba.pixels[i + 2] / 255.0f};
}

Color rgb(float r, float g, float b) {
    return Color{r, g, b, 1.0f};
}

// Outer then inner square, both wound the same way, as one current path.
void square_ring_path(Canvas& c) {
    c.begin_path();
    c.move_to(6, 6);
    c.line_to(42, 6);
    c.line_to(42, 42);
    c.line_to(6, 42);
    c.close_path();
    c.move_to(18, 18);
    c.line_to(30, 18);
    c.line_to(30, 30);
    c.line_to(18, 30);
    c.close_path();
}

// A ring as ONE point array: an outer circle followed by an inner circle, both
// wound the same direction and starting at angle 0 so the bridge segments are
// collinear and cancel. Under even-odd the centre is inside two contours, so it
// is a hole; under nonzero the windings add and it is filled.
std::vector<Canvas::Point2D> ring_points(float cx, float cy, float r_outer, float r_inner,
                                         int segments = 64) {
    std::vector<Canvas::Point2D> pts;
    pts.reserve(static_cast<size_t>(segments) * 2);
    for (float r : {r_outer, r_inner}) {
        for (int i = 0; i < segments; ++i) {
            const float t = 2.0f * 3.14159265358979f *
                            (static_cast<float>(i) / static_cast<float>(segments));
            pts.push_back({cx + r * std::cos(t), cy + r * std::sin(t)});
        }
    }
    return pts;
}

FRAMEWORK_TEST(the_current_path_fill_rule_changes_the_painted_pixels) {
    auto draw = [](FillRule rule) {
        return [rule](Canvas& c) {
            square_ring_path(c);
            c.set_fill_color(rgb(1, 0, 0));
            c.fill_current_path(rule);
        };
    };
    // Both loops share a winding, so the rule alone decides the centre.
    const auto nonzero = render_and_sample(48, 48, draw(FillRule::nonzero), 24, 24);
    CHECK(nonzero.r > 0.5f);
    CHECK(nonzero.g < 0.25f);
    const auto evenodd = render_and_sample(48, 48, draw(FillRule::evenodd), 24, 24);
    CHECK(evenodd.r < 0.25f);
    CHECK(evenodd.g < 0.25f);
    // The band between the squares is filled under both rules: even-odd punches
    // a hole, it does not blank the shape.
    CHECK(render_and_sample(48, 48, draw(FillRule::nonzero), 10, 24).r > 0.5f);
    CHECK(render_and_sample(48, 48, draw(FillRule::evenodd), 10, 24).r > 0.5f);
}

FRAMEWORK_TEST(fill_path_fills_a_closed_polygon) {
    const Canvas::Point2D tri[] = {{24.0f, 6.0f}, {42.0f, 42.0f}, {6.0f, 42.0f}};
    auto draw = [&](Canvas& c) {
        c.set_fill_color(rgb(0, 1, 0));
        c.fill_path(tri, 3);
    };
    // Inside the triangle is the fill; the top-left corner is background. A
    // fill that silently drew nothing would leave (24,30) black.
    const auto inside = render_and_sample(48, 48, draw, 24, 30);
    CHECK(inside.g > 0.5f);
    CHECK(inside.r < 0.25f);
    CHECK(inside.b < 0.25f);
    CHECK(render_and_sample(48, 48, draw, 3, 3).g < 0.25f);
}

FRAMEWORK_TEST(fill_path_honors_the_fill_rule_on_a_point_array_ring) {
    const auto pts = ring_points(24.0f, 24.0f, 20.0f, 10.0f);
    auto draw = [&](FillRule rule) {
        return [&, rule](Canvas& c) {
            c.set_fill_color(rgb(0, 1, 0));
            c.fill_path(pts.data(), pts.size(), rule);
        };
    };
    CHECK(render_and_sample(48, 48, draw(FillRule::evenodd), 24, 24).g < 0.25f);
    CHECK(render_and_sample(48, 48, draw(FillRule::nonzero), 24, 24).g > 0.5f);
    // The ring band (r = 15 above the centre) is filled under both rules.
    CHECK(render_and_sample(48, 48, draw(FillRule::evenodd), 24, 9).g > 0.5f);
    CHECK(render_and_sample(48, 48, draw(FillRule::nonzero), 24, 9).g > 0.5f);
    // Outside the outer circle stays background under both.
    CHECK(render_and_sample(48, 48, draw(FillRule::evenodd), 2, 2).g < 0.25f);
    CHECK(render_and_sample(48, 48, draw(FillRule::nonzero), 2, 2).g < 0.25f);
}

// The retained-Path overload reaches Skia through its own converter, so it
// needs its own proof: a fill rule that never reaches the path renders a ring
// as a solid disc with no error anywhere.
FRAMEWORK_TEST(fill_path_honors_the_fill_rule_on_a_two_contour_retained_path) {
    const auto pts = ring_points(24.0f, 24.0f, 20.0f, 10.0f);
    vellum::canvas::Path ring;
    ring.move_to(pts[0].x, pts[0].y);
    for (size_t i = 1; i < 64; ++i)
        ring.line_to(pts[i].x, pts[i].y);
    ring.close();
    ring.move_to(pts[64].x, pts[64].y);
    for (size_t i = 65; i < pts.size(); ++i)
        ring.line_to(pts[i].x, pts[i].y);
    ring.close();

    auto draw = [&](FillRule rule) {
        return [&, rule](Canvas& c) {
            c.set_fill_color(rgb(0, 1, 0));
            c.fill_path(ring, rule);
        };
    };
    CHECK(render_and_sample(48, 48, draw(FillRule::evenodd), 24, 24).g < 0.25f);
    CHECK(render_and_sample(48, 48, draw(FillRule::nonzero), 24, 24).g > 0.5f);
    CHECK(render_and_sample(48, 48, draw(FillRule::evenodd), 24, 9).g > 0.5f);
    CHECK(render_and_sample(48, 48, draw(FillRule::nonzero), 24, 9).g > 0.5f);
}

FRAMEWORK_TEST(stroke_path_strokes_a_polyline_without_filling_it) {
    const Canvas::Point2D poly[] = {{8.0f, 24.0f}, {40.0f, 24.0f}, {40.0f, 8.0f}};
    auto draw = [&](Canvas& c) {
        c.set_stroke_color(rgb(1, 0, 0));
        c.set_line_width(4.0f);
        c.stroke_path(poly, 3);
    };
    CHECK(render_and_sample(48, 48, draw, 20, 24).r > 0.5f);
    // The inside of the open elbow is not filled.
    const auto interior = render_and_sample(48, 48, draw, 20, 12);
    CHECK(interior.r < 0.25f);
    CHECK(interior.g < 0.25f);
    CHECK(interior.b < 0.25f);
}

FRAMEWORK_TEST(stroke_path_joins_segments_into_one_continuous_path) {
    // A right-angle corner stroked thick with a round join and butt caps. As
    // one path, the round join fills a disc of radius line_width/2 at the
    // vertex. As independent butt-capped lines the segments meet flush and
    // leave the outer corner empty.
    const Canvas::Point2D corner[] = {{10.0f, 30.0f}, {30.0f, 30.0f}, {30.0f, 10.0f}};
    auto draw = [&](Canvas& c) {
        c.set_stroke_color(rgb(1, 0, 0));
        c.set_line_width(8.0f);
        c.set_line_cap(vellum::canvas::LineCap::butt);
        c.set_line_join(vellum::canvas::LineJoin::round);
        c.stroke_path(corner, 3);
    };
    // (32,32) is about 2.83 px from the vertex: inside the join disc, outside
    // the union of the two butt-capped legs.
    CHECK(render_and_sample(48, 48, draw, 32, 32).r > 0.5f);
    CHECK(render_and_sample(48, 48, draw, 20, 30).r > 0.5f);
}

FRAMEWORK_TEST(degenerate_stroke_and_fill_input_draws_nothing) {
    const Canvas::Point2D one[] = {{8.0f, 8.0f}};
    auto expect_blank = [&](auto&& draw) {
        const auto px = render_and_sample(16, 16, draw, 8, 8);
        CHECK(px.r < 0.05f);
        CHECK(px.g < 0.05f);
        CHECK(px.b < 0.05f);
    };
    expect_blank([&](Canvas& c) {
        c.set_stroke_color(rgb(1, 1, 1));
        c.set_line_width(4.0f);
        c.stroke_path(one, 0);
        c.stroke_path(one, 1);
        c.stroke_path(nullptr, 0);
        c.stroke_path(nullptr, 5);
    });
    expect_blank([&](Canvas& c) {
        c.set_fill_color(rgb(1, 1, 1));
        c.fill_path(one, 0);
        c.fill_path(one, 1);
        c.fill_path(one, 2); // fewer than 3 points enclose no area
        c.fill_path(nullptr, 0);
        c.fill_path(nullptr, 5);
    });
}

// The widget-level tests assert a command stream; this proves the gradient
// stroke reaches painted pixels. A thick horizontal line stroked with
// `to right, red, blue` must read red at its left end and blue at its right
// end, and must not be the green solid fallback.
FRAMEWORK_TEST(a_gradient_stroked_svg_path_paints_the_gradient_not_the_solid) {
    vellum::view::SvgPathWidget w;
    w.set_path("M 2 24 L 46 24");
    w.set_viewbox(48, 48);
    w.set_bounds({0, 0, 48, 48});
    w.clear_fill();
    w.set_stroke_color(rgb(0, 1, 0)); // loud fallback
    w.set_stroke_width(10.0f);
    w.set_stroke_gradient("linear-gradient(to right, red, blue)");
    auto draw = [&](Canvas& c) { w.paint(c); };

    const auto left = render_and_sample(48, 48, draw, 5, 24);
    CHECK(left.r > 0.5f);
    CHECK(left.g < 0.25f);
    const auto right = render_and_sample(48, 48, draw, 43, 24);
    CHECK(right.b > 0.5f);
    CHECK(right.g < 0.25f);
    const auto off = render_and_sample(48, 48, draw, 24, 4);
    CHECK(off.r < 0.25f);
    CHECK(off.b < 0.25f);
}

} // namespace

int main() {
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    HeadlessSurface::Config cfg;
    cfg.width = 16;
    cfg.height = 16;
    std::string err;
    auto probe = HeadlessSurface::create(cfg, &err);
    if (!probe) {
        std::printf("SKIP no headless GPU surface: %s\n", err.c_str());
        return 77;
    }
    probe.reset();
    return framework_test::run_all();
}
