#include "canvas_raster_scene.hpp"
#include "graphite_raster_options.hpp"

#include <vellum/view/screenshot.hpp>
#include <vellum/view/view.hpp>

#include <algorithm>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <string>
#include <vector>

namespace {

namespace canvas = vellum::canvas;
using namespace canvas_raster_scene;

class RasterScene final : public vellum::view::View {
public:
    void paint(canvas::Canvas& target) override {
        icon_painted = canvas_raster_scene::paint<canvas::Color, canvas::LineCap>(target);
    }

    bool icon_painted = false;
};

int fail(const char* message) {
    std::fprintf(stderr, "%s\n", message);
    return 1;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return fail("expected a raster fixture path");

    if (vellum::render::detail::graphite_raster_options().fInternalMultisampleCount !=
        skgpu::graphite::SampleCount::k1) {
        return fail("Graphite editor internal sample count must be 1");
    }

    RasterScene scene;
    std::uint32_t pixel_width = 0;
    std::uint32_t pixel_height = 0;
    const auto rgba = vellum::view::render_to_rgba(scene, width, height, 1.0f,
                                                  &pixel_width, &pixel_height);
    if (pixel_width != width || pixel_height != height || rgba.size() != height * row_bytes) {
        return fail("Skia raster readback has the wrong dimensions");
    }
    if (!scene.icon_painted) return fail("SVG icon draw failed");

    std::ifstream fixture(argv[1], std::ios::binary);
    std::string header(pam_header.size(), '\0');
    if (!fixture.read(header.data(), header.size()) || header != pam_header) {
        return fail("Pulp raster fixture has the wrong PAM header");
    }
    std::vector<std::uint8_t> expected(rgba.size());
    if (!fixture.read(reinterpret_cast<char*>(expected.data()), expected.size()) ||
        fixture.peek() != std::char_traits<char>::eof()) {
        return fail("Pulp raster fixture has the wrong pixel count");
    }

    const auto mismatch = std::mismatch(expected.begin(), expected.end(), rgba.begin());
    if (mismatch.first != expected.end()) {
        const auto offset = static_cast<std::size_t>(mismatch.first - expected.begin());
        std::fprintf(stderr, "RGBA pixel (%zu, %zu), channel %zu: Pulp %u, Vellum %u\n",
                     (offset % row_bytes) / 4, offset / row_bytes, offset % 4,
                     static_cast<unsigned>(*mismatch.first), static_cast<unsigned>(*mismatch.second));
        return 1;
    }
    return 0;
}
