#include <vellum/runtime/kernel.hpp>

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS) || defined(VELLUM_CONSUMER_HAS_GPU)
#include <vellum/graphics/color.hpp>
#endif

#if defined(VELLUM_CONSUMER_HAS_GPU)
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <cstdint>
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
                .corner_radius = 18.0F,
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
