#pragma once

#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::graphics {

// One stage of an effect group, read back from a target-sized surface.
struct EffectPassCapture final {
    std::string name;
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

// Test-only access to `SkiaDawnSurface` internals. This header is not
// installed; only Vellum's own tests include it.
struct SkiaDawnSurfaceTestAccess final {
    // Renders every stage of the effect group of `node_id` in `scene`:
    // "behind" (the commands before it over the background), "content" (its
    // members), "filter N <kind>" for each prefix of its filter chain, then
    // "backdrop" (its backdrop effect over "behind") when it has one, and
    // "composited". Every stage but "behind" and those over it is on a
    // transparent target.
    static bool capture_effect_passes(SkiaDawnSurface& surface, const Scene& scene,
                                      std::string_view node_id,
                                      std::vector<EffectPassCapture>& passes,
                                      std::string* error);

    // Measures a fresh paragraph through the layer-cache path without a GPU.
    static std::optional<Rect> measure_layer_text_extent(
        const PaintCommand& command, std::string_view directory, std::string* error);

    // Sets the newest scene version the surface redraws from damage alone
    // (`kDamageModelSceneVersion` by default). Raising it lets a test drive
    // the partial-redraw path with version-2 fields such as opacity, which
    // exact layer images need, through `render(scene, damage)` with damage
    // the test supplies. A scene partial redraw serves still caches only
    // exact images.
    static void set_damage_model_version(SkiaDawnSurface& surface,
                                         std::uint32_t version) noexcept;
};

}  // namespace vellum::graphics
