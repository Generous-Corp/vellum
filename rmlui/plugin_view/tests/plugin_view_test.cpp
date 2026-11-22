#include <vellum/rmlui/plugin_view.hpp>

#include <vellum/canvas/recording_canvas.hpp>

#include "test_support.hpp"

using namespace vellum::rmlui;

namespace {
class Assets final : public AssetProvider {
  public:
    Status load(std::string_view path, Asset& out) override {
        if (path != "editor.rml") return Status::missing_asset;
        out.path = std::string(path);
        out.bytes = "<rml><head><style>#box { display:block; width:12px; height:8px; "
                    "background-color:#ff0000; }</style></head><body><div id='box'/></body></rml>";
        return Status::ok;
    }
};
}

int main() {
    Assets assets;
    PluginView view(assets);
    VELLUM_RMLUI_CHECK(view.open(32, 24) == Status::ok);
    VELLUM_RMLUI_CHECK(view.load_document("editor.rml") == Status::ok);
    view.render();
    VELLUM_RMLUI_CHECK(view.renderer().width() == 32);
    VELLUM_RMLUI_CHECK(view.renderer().height() == 24);
    VELLUM_RMLUI_CHECK(view.renderer().triangles_drawn() > 0);

    vellum::canvas::RecordingCanvas canvas;
    view.paint(canvas);
    VELLUM_RMLUI_CHECK(canvas.count(vellum::canvas::DrawCommand::Type::draw_image) == 1);
    return 0;
}
