#include <vellum/graphics/skia_dawn_surface.hpp>

#include <string>

int main() {
    vellum::graphics::SkiaDawnSurface::Config config;
    std::string error;
    auto create = &vellum::graphics::SkiaDawnSurface::create;
    auto surface = create(config, &error);
    return surface ? 0 : 0;
}
