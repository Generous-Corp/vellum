#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <cmath>
#include <cstdint>
#include <future>
#include <iostream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;

bool capture(const Scene& scene, std::vector<std::uint8_t>& rgba,
             std::uint32_t& width, std::uint32_t& height) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(scene.width),
         .height = static_cast<std::uint32_t>(scene.height)}, &error);
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(rgba, width, height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    return true;
}

const std::uint8_t* pixel(
    const std::vector<std::uint8_t>& rgba, std::uint32_t width,
    std::uint32_t x, std::uint32_t y) {
    return rgba.data() + (static_cast<std::size_t>(y) * width + x) * 4U;
}

bool packaged_text_fallback_weights() {
    const std::vector<TextRun> runs{{
        .text = "Jost regular ",
        .style = {.font_family = "Jost", .font_weight = 400, .font_size = 24.0F},
    }, {
        .text = "Semibold 日本語",
        .style = {.font_family = "Jost", .font_weight = 600, .font_size = 24.0F},
    }};
    std::vector<std::future<TextMetrics>> futures;
    for (int index = 0; index < 8; ++index) {
        futures.push_back(std::async(std::launch::async, [runs] {
            TextMetrics metrics;
            std::string error;
            if (!SkiaDawnSurface::measure_text(runs, metrics, {}, &error)) {
                throw std::runtime_error(error);
            }
            return metrics;
        }));
    }
    const auto expected = futures.front().get();
    if (expected.width <= 0.0F || expected.ascent <= 0.0F ||
        expected.descent < 0.0F || expected.baseline != expected.ascent) {
        std::cerr << "invalid text metrics width=" << expected.width
                  << " ascent=" << expected.ascent
                  << " descent=" << expected.descent
                  << " baseline=" << expected.baseline << '\n';
        return false;
    }
    for (std::size_t index = 1; index < futures.size(); ++index) {
        const auto metrics = futures[index].get();
        if (std::abs(metrics.width - expected.width) > 0.01F ||
            std::abs(metrics.ascent - expected.ascent) > 0.01F ||
            std::abs(metrics.descent - expected.descent) > 0.01F) {
            std::cerr << "parallel text metrics diverged width=" << metrics.width
                      << " ascent=" << metrics.ascent
                      << " descent=" << metrics.descent << '\n';
            return false;
        }
    }
    const std::string combining = "e\xCC\x81" "e\xCC\x81";
    TextMetrics untracked;
    TextMetrics tracked;
    std::string error;
    const TextStyle cluster_style{
        .font_family = "Inter", .font_size = 24.0F,
    };
    auto tracked_style = cluster_style;
    tracked_style.letter_spacing = 7.0F;
    if (!SkiaDawnSurface::measure_text(
            {{.text = combining, .style = cluster_style}}, untracked, {}, &error) ||
        !SkiaDawnSurface::measure_text(
            {{.text = combining, .style = tracked_style}}, tracked, {}, &error) ||
        std::abs((tracked.width - untracked.width) - 14.0F) > 0.25F) {
        std::cerr << "tracking did not preserve combining clusters: " << error
                  << " delta=" << tracked.width - untracked.width << '\n';
        return false;
    }
    const TextStyle arabic_style{
        .font_family = "Inter", .font_size = 24.0F,
    };
    auto arabic_accent = arabic_style;
    arabic_accent.color = Color::hex(0x14B8A6);
    TextMetrics joined_arabic;
    TextMetrics styled_arabic;
    if (!SkiaDawnSurface::measure_text(
            {{.text = "\xD8\xB3\xD9\x84\xD8\xA7\xD9\x85", .style = arabic_style}},
            joined_arabic, {}, &error) ||
        !SkiaDawnSurface::measure_text(
            {{.text = "\xD8\xB3\xD9\x84", .style = arabic_style},
             {.text = "\xD8\xA7\xD9\x85", .style = arabic_accent}},
            styled_arabic, {}, &error) ||
        std::abs(joined_arabic.width - styled_arabic.width) > 0.25F) {
        std::cerr << "attributed runs changed Arabic joining width: " << error
                  << " joined=" << joined_arabic.width
                  << " styled=" << styled_arabic.width << '\n';
        return false;
    }
    TextMetrics unsupported;
    TextMetrics replacement;
    if (!SkiaDawnSurface::measure_text(
            {{.text = "launch \xF0\x9F\x9A\x80", .style = cluster_style}},
            unsupported, {}, &error) ||
        !SkiaDawnSurface::measure_text(
            {{.text = "launch ?", .style = cluster_style}},
            replacement, {}, &error) ||
        std::abs(unsupported.width - replacement.width) > 0.01F) {
        std::cerr << "unsupported glyph replacement was not deterministic: " << error
                  << " unsupported=" << unsupported.width
                  << " replacement=" << replacement.width << '\n';
        return false;
    }
    return true;
}

bool repeating_linear_gradient_non_square() {
    Scene scene{
        .width = 240.0F,
        .height = 140.0F,
        .background = Color::hex(0xFFFFFF),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 240.0F, 140.0F},
            .children = {{
                .id = "gradient",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {20.0F, 30.0F, 200.0F, 80.0F},
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .repeating = true,
                    .repeat_length = 40.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0xFF0000)},
                        {.position = 1.0F, .color = Color::hex(0x0000FF)},
                    },
                }},
            }, {
                .id = "gradient-base",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {20.0F, 115.0F, 40.0F, 20.0F},
                .fill = Color::hex(0xFF00FF),
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0x00FF00)},
                        {.position = 1.0F, .color = Color::hex(0x00FF00)},
                    },
                }, {
                    .angle_degrees = 90.0F,
                    .repeating = true,
                    .repeat_length = 0.5F,
                    .stops = {
                        {.position = 0.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                        {.position = 1.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                    },
                }},
            }, {
                .id = "transparent-gradient-over-base",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {70.0F, 115.0F, 40.0F, 20.0F},
                .fill = Color::hex(0xFF00FF),
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                        {.position = 1.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                    },
                }},
            }, {
                .id = "zero-width-gradient",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {130.0F, 115.0F, 0.0F, 20.0F},
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0xFF0000)},
                        {.position = 1.0F, .color = Color::hex(0x0000FF)},
                    },
                }},
            }},
        },
    };
    const auto commands = make_paint_commands(scene);
    if (commands.size() != 4U || commands[0].fill_gradients.size() != 1U ||
        commands[1].fill_gradients.size() != 2U) {
        std::cerr << "gradient command was not materialized\n";
        return false;
    }
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0, height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    const auto* left = pixel(rgba, width, 30, 70);
    const auto* right = pixel(rgba, width, 210, 70);
    const auto* layered = pixel(rgba, width, 30, 125);
    const auto* base = pixel(rgba, width, 80, 125);
    const bool passed = left[0] > left[2] && right[2] > right[0] &&
        layered[0] < 15U && layered[1] > 240U && layered[2] < 15U &&
        base[0] > 240U && base[1] < 15U && base[2] > 240U;
    if (!passed) {
        std::cerr << "gradient pixels left=" << static_cast<int>(left[0]) << ','
                  << static_cast<int>(left[1]) << ',' << static_cast<int>(left[2])
                  << " right=" << static_cast<int>(right[0]) << ','
                  << static_cast<int>(right[1]) << ',' << static_cast<int>(right[2]) << '\n';
    }
    return passed;
}

bool outset_shadow_spread_diagonal() {
    Scene scene{
        .width = 240.0F,
        .height = 150.0F,
        .background = Color::hex(0xFFFFFF),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 240.0F, 150.0F},
            .children = {{
                .id = "shadow",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {40.0F, 40.0F, 80.0F, 60.0F},
                .fill = Color::hex(0x14B8A6),
                .box_shadows = {{
                    .spread_radius = 10.0F,
                    .color = Color::hex(0x000000),
                }},
                .corner_radius = 20.0F,
            }, {
                .id = "shadow-only",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {160.0F, 40.0F, 50.0F, 60.0F},
                .box_shadows = {{
                    .spread_radius = 8.0F,
                    .color = Color::hex(0x000000),
                }},
                .corner_radius = 12.0F,
            }},
        },
    };
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0, height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    const auto* outside = pixel(rgba, width, 31, 31);
    const auto* diagonal = pixel(rgba, width, 36, 44);
    const auto* transparent_interior = pixel(rgba, width, 185, 70);
    const auto* shadow_only_outside = pixel(rgba, width, 155, 70);
    return outside[0] > 240U && outside[1] > 240U && outside[2] > 240U &&
           diagonal[0] < 40U && diagonal[1] < 40U && diagonal[2] < 40U &&
           transparent_interior[0] > 240U && transparent_interior[1] > 240U &&
           transparent_interior[2] > 240U && shadow_only_outside[0] < 40U &&
           shadow_only_outside[1] < 40U && shadow_only_outside[2] < 40U;
}

bool attributed_text_runs_materialization() {
    Scene scene{
        .width = 320.0F,
        .height = 130.0F,
        .background = Color::hex(0xFFFFFF),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 320.0F, 130.0F},
            .children = {{
                .id = "runs",
                .kind = SceneNode::Kind::text,
                .bounds = {12.0F, 20.0F, 296.0F, 60.0F},
                .text_runs = {{
                    .text = "Regular ",
                    .style = {.font_family = "Jost", .font_weight = 400,
                              .font_size = 26.0F, .color = Color::hex(0x111827)},
                }, {
                    .text = "Accent",
                    .style = {.font_family = "Jost", .font_weight = 600,
                              .font_size = 26.0F, .letter_spacing = 1.5F,
                              .color = Color::hex(0x14B8A6), .underline = true,
                              .strikethrough = true},
                }},
            }, {
                .id = "baseline",
                .kind = SceneNode::Kind::text,
                .bounds = {12.0F, 75.0F, 296.0F, 45.0F},
                .text_runs = {{
                    .text = "H",
                    .style = {.font_family = "Jost", .font_size = 30.0F,
                              .color = Color::hex(0xE00000)},
                }, {
                    .text = "H",
                    .style = {.font_family = "Jost", .font_size = 14.0F,
                              .color = Color::hex(0x0000E0)},
                }},
            }},
        },
    };
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0, height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    std::size_t dark = 0, accent = 0;
    int red_bottom = -1;
    int blue_bottom = -1;
    for (std::size_t index = 0; index < rgba.size(); index += 4U) {
        if (rgba[index] < 80U && rgba[index + 1U] < 100U && rgba[index + 2U] < 120U) ++dark;
        if (rgba[index] < 80U && rgba[index + 1U] > 120U && rgba[index + 2U] > 100U) ++accent;
        const int y = static_cast<int>((index / 4U) / width);
        if (rgba[index] > 150U && rgba[index + 1U] < 100U && rgba[index + 2U] < 100U) {
            red_bottom = std::max(red_bottom, y);
        }
        if (rgba[index] < 100U && rgba[index + 1U] < 100U && rgba[index + 2U] > 150U) {
            blue_bottom = std::max(blue_bottom, y);
        }
    }
    TextMetrics line_metrics;
    std::string error;
    if (!SkiaDawnSurface::measure_text(
            scene.root.children[0].text_runs, line_metrics, {}, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto decoration_start_y = static_cast<std::uint32_t>(std::floor(
        scene.root.children[0].bounds.y + line_metrics.baseline + 1.0F));
    std::size_t decoration_pixels = 0U;
    for (std::uint32_t y = decoration_start_y; y < 70U; ++y) {
        for (std::uint32_t x = 0U; x < width; ++x) {
            const auto* candidate = pixel(rgba, width, x, y);
            if (candidate[0] < 80U && candidate[1] > 120U && candidate[2] > 100U) {
                ++decoration_pixels;
            }
        }
    }
    const bool passed = dark > 20U && accent > 20U &&
        red_bottom >= 0 && blue_bottom >= 0 &&
        std::abs(red_bottom - blue_bottom) <= 2 && decoration_pixels > 10U;
    if (!passed) {
        std::cerr << "attributed pixels dark=" << dark << " accent=" << accent
                  << " red_bottom=" << red_bottom << " blue_bottom=" << blue_bottom
                  << " decoration_pixels=" << decoration_pixels
                  << " decoration_start_y=" << decoration_start_y << '\n';
    }
    return passed;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string fixture = argv[1];
    try {
        if (fixture == "packaged_text_fallback_weights") {
            return packaged_text_fallback_weights() ? 0 : 1;
        }
        if (fixture == "repeating_linear_gradient_non_square") {
            return repeating_linear_gradient_non_square() ? 0 : 1;
        }
        if (fixture == "outset_shadow_spread_diagonal") {
            return outset_shadow_spread_diagonal() ? 0 : 1;
        }
        if (fixture == "attributed_text_runs_materialization") {
            return attributed_text_runs_materialization() ? 0 : 1;
        }
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
    return 2;
}
