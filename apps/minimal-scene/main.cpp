#include <vellum/runtime/kernel.hpp>

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS) || defined(VELLUM_CONSUMER_HAS_GPU)
#include <vellum/graphics/color.hpp>
#endif

#if defined(VELLUM_CONSUMER_HAS_GPU)
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <cstdint>
#include <cmath>
#include <future>
#include <string>
#include <vector>
#endif

int main() {
    vellum::runtime::Kernel kernel({.application_id = "dev.vellum.minimal-scene"});
    if (!kernel.start()) return 1;

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS) || defined(VELLUM_CONSUMER_HAS_GPU)
    const auto accent = vellum::graphics::Color::hex(0x8A5CFF);
    if (accent.red8() != 0x8A || accent.green8() != 0x5C || accent.blue8() != 0xFF) return 1;
#endif

#if defined(VELLUM_CONSUMER_HAS_GPU)
    using namespace vellum::graphics;
    std::string error;
    const std::vector<TextRun> runs{{
        .text = "Installed Jost ",
        .style = {.font_family = "Jost", .font_weight = 400, .font_size = 18.0F},
    }, {
        .text = "Semibold 日本語",
        .style = {.font_family = "Jost", .font_weight = 600, .font_size = 18.0F},
    }};
    std::vector<std::future<TextMetrics>> measurements;
    for (int index = 0; index < 4; ++index) {
        measurements.push_back(std::async(std::launch::async, [runs] {
            TextMetrics metrics;
            std::string shape_error;
            if (!SkiaDawnSurface::measure_text(runs, metrics, {}, &shape_error)) {
                return TextMetrics{};
            }
            return metrics;
        }));
    }
    const auto expected_metrics = measurements.front().get();
    if (expected_metrics.width <= 0.0F || expected_metrics.ascent <= 0.0F) return 1;
    for (std::size_t index = 1; index < measurements.size(); ++index) {
        const auto metrics = measurements[index].get();
        if (std::abs(metrics.width - expected_metrics.width) > 0.01F ||
            std::abs(metrics.ascent - expected_metrics.ascent) > 0.01F) return 1;
    }
    auto surface = SkiaDawnSurface::create(
        {.width = 240, .height = 160, .scale = 1.0F}, &error);
    if (!surface || !surface->evidence().available ||
        surface->evidence().fallback || surface->evidence().backend != "Metal") {
        return 1;
    }
    Scene scene{
        .width = 240.0F,
        .height = 160.0F,
        .background = Color::hex(0x0F172A),
        .root = {
            .id = "consumer/root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 240.0F, 160.0F},
            .children = {{
                .id = "consumer/card",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {24.0F, 24.0F, 192.0F, 112.0F},
                .fill = Color::hex(0x14B8A6),
                .fill_gradients = {{
                    .angle_degrees = 135.0F,
                    .repeating = true,
                    .repeat_length = 48.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0x0F766E)},
                        {.position = 1.0F, .color = Color::hex(0x14B8A6)},
                    },
                }},
                .box_shadows = {{
                    .offset_x = 3.0F,
                    .offset_y = 5.0F,
                    .blur_radius = 8.0F,
                    .spread_radius = 2.0F,
                    .color = Color::hex(0x000000, 0.35F),
                }},
                .corner_radius = 18.0F,
                .children = {{
                    .id = "consumer/label",
                    .kind = SceneNode::Kind::text,
                    .bounds = {18.0F, 32.0F, 156.0F, 40.0F},
                    .text_runs = {{
                        .text = "Vellum ",
                        .style = {.font_family = "Jost", .font_weight = 400,
                                  .font_size = 22.0F, .color = Color::hex(0xFFFFFF)},
                    }, {
                        .text = "SDK",
                        .style = {.font_family = "Jost", .font_weight = 600,
                                  .font_size = 22.0F, .letter_spacing = 1.0F,
                                  .color = Color::hex(0xCCFBF1), .underline = true},
                    }},
                }},
            }},
        },
    };
    if (!surface->render(scene, &error)) return 1;
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!surface->capture_rgba(rgba, width, height, &error) ||
        !passes_content_floor(analyze_capture_rgba(rgba, width, height))) {
        return 1;
    }
#endif

    return kernel.stop() ? 0 : 1;
}
