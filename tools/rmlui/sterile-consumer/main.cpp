// Consumes the installed VellumRmlUi package alone. The document renders on the
// CPU renderer, which must produce a PNG. The same document is then drawn
// through VellumRenderer on an offscreen surface; a host with no usable GPU
// reports that and still passes, since the link and the CPU render are what
// this proves.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>
#include <vellum/rmlui/vellum_renderer.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <cstdio>
#include <string>

namespace {

constexpr std::uint32_t kSize = 16;
constexpr const char* kDocument =
    "<rml><body style='display:block;width:16px;height:16px;"
    "background-color:#ff3b30;'></body></rml>";

using namespace vellum::rmlui;

bool draw(RenderInterface& renderer) {
    HeadlessRecorder assets;
    assets.add_asset("editor.rml", kDocument);
    Backend backend(assets, renderer, assets);
    if (backend.open(kSize, kSize) != Status::ok || backend.load_document("editor.rml") != Status::ok)
        return false;
    backend.render();
    backend.close();
    return true;
}

}  // namespace

int main() {
    SoftRenderer soft;
    if (!draw(soft) || soft.encode_png().empty() || soft.triangles_drawn() == 0) {
        std::fprintf(stderr, "soft renderer drew nothing\n");
        return 1;
    }

    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::printf("installed VellumRmlUi soft render ok; vellum renderer frame skipped: %s\n",
                    error.c_str());
        return 0;
    }
    auto surface = vellum::graphics::SkiaDawnSurface::create(
        {.width = kSize, .height = kSize, .scale = 1.0f, .native_surface_handle = nullptr}, &error);
    if (!surface) {
        std::printf("installed VellumRmlUi soft render ok; vellum renderer frame skipped: %s\n",
                    error.c_str());
        return 0;
    }
    VellumRenderer gpu(*surface);
    if (!draw(gpu) || !gpu.frame_ok() || gpu.triangles_submitted() == 0) {
        std::fprintf(stderr, "vellum renderer frame failed: %s\n", gpu.last_error().c_str());
        return 1;
    }
    std::printf("installed VellumRmlUi soft render ok; vellum renderer frame ok\n");
    return 0;
}
