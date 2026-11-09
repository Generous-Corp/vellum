#pragma once

#include <vellum/graphics/skia_dawn_surface.hpp>

#include <cstdint>

namespace vellum::graphics {

// Test-only access to `SkiaDawnSurface` internals. This header is not
// installed; only Vellum's own tests include it.
struct SkiaDawnSurfaceTestAccess final {
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
