#include <vellum/graphics/skia_dawn_surface.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <cstdio>
#include <string>

int main() {
    vellum::graphics::SkiaDawnSurface::Config config;
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::fprintf(stderr, "Dawn bootstrap registration failed%s%s\n",
                     error.empty() ? "" : ": ", error.c_str());
        return 1;
    }
    auto create = &vellum::graphics::SkiaDawnSurface::create;
    auto surface = create(config, &error);
    if (!surface) {
        std::fprintf(stderr, "SkiaDawnSurface::create failed%s%s\n",
                     error.empty() ? "" : ": ", error.c_str());
        return 1;
    }
    return 0;
}
