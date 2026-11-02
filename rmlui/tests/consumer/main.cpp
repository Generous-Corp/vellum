#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>

#include <cstdio>

int main() {
    using namespace vellum::rmlui;
    HeadlessRecorder assets;
    assets.add_asset("editor.rml", "<rml><body style='display:block;width:16px;height:16px;"
                                   "background-color:#ff3b30;'></body></rml>");
    SoftRenderer renderer;
    Backend backend(assets, renderer, assets);
    if (backend.open(16, 16) != Status::ok || backend.load_document("editor.rml") != Status::ok)
        return 1;
    backend.render();
    const auto png = renderer.encode_png();
    backend.close();
    if (png.empty() || renderer.triangles_drawn() == 0)
        return 1;
    std::printf("installed VellumRmlUi rendered %zu PNG bytes\n", png.size());
    return 0;
}
