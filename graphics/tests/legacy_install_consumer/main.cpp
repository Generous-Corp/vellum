#include <vellum/graphics/skia_dawn_surface.hpp>

#include <iostream>
#include <string>

int main() {
    // This translation unit compiles against the exact Config definition from
    // before host bootstrap registration was introduced. The new dylib must
    // not read beyond that layout when this legacy caller invokes create().
    const vellum::graphics::SkiaDawnSurface::Config legacy_config{
        .width = 64,
        .height = 64,
        .scale = 1.0F,
    };
    std::string error;
    auto surface = vellum::graphics::SkiaDawnSurface::create(legacy_config, &error);
    if (surface || error != "host must register a Dawn bootstrap before creating a GPU surface") {
        std::cerr << "legacy caller did not fail closed: " << error << '\n';
        return 1;
    }
    return 0;
}
