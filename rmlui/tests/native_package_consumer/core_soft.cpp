// Links VellumRmlUi::Core and VellumRmlUi::SoftRenderer, which the
// VellumRmlUiNative package finds from the VellumRmlUi package in another
// prefix, and renders a document through them.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>

#include <cstdio>

namespace {

constexpr const char* kDocument =
    "<rml><body style='display:block;width:16px;height:16px;"
    "background-color:#ff0000;'></body></rml>";

int core_through_soft_renderer() {
    using namespace vellum::rmlui;
    HeadlessRecorder assets;
    assets.add_asset("editor.rml", kDocument);
    SoftRenderer renderer;
    Backend backend(assets, renderer, assets);
    if (backend.open(16, 16) != Status::ok || backend.load_document("editor.rml") != Status::ok)
        return 1;
    backend.render();
    const auto png = renderer.encode_png();
    backend.close();
    if (png.empty() || renderer.triangles_drawn() == 0)
        return 1;
    std::printf("installed VellumRmlUi::Core rendered %zu PNG bytes\n", png.size());
    return 0;
}

} // namespace

int main() {
    return core_through_soft_renderer();
}
