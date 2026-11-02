// Development hot reload: an edit to a linked style sheet on disk is seen by
// DevelopmentAssetProvider::poll() and shows up after Backend::reload_document().
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/development_assets.hpp>

#include "test_support.hpp"

#include <algorithm>
#include <cstdint>
#include <filesystem>

using namespace vellum::rmlui;
namespace test = vellum::rmlui::test;

namespace {

bool drew_colour(const HeadlessRecorder& recorder, std::uint32_t rgba) {
    return std::any_of(recorder.commands().begin(), recorder.commands().end(),
                       [&](const DrawCommand& command) {
                           return command.vertices &&
                                  std::any_of(command.vertices->begin(), command.vertices->end(),
                                              [&](const DrawVertex& v) { return v.colour == rgba; });
                       });
}

void write_style(const std::filesystem::path& root, const char* colour) {
    test::write_binary(root / "style.rcss",
                       std::string("#box { display: block; width: 32px; height: 32px; "
                                   "background-color: ") +
                           colour + "; }");
}

} // namespace

int main() {
    const auto root = test::unique_temp_dir("vellum-rmlui-hot-reload");
    test::write_binary(root / "editor.rml",
                       "<rml><head><link type='text/rcss' href='style.rcss'/></head>"
                       "<body style='display:block;'><div id='box'/></body></rml>");
    write_style(root, "#ff0000");

    DevelopmentAssetProvider assets(root);
    HeadlessRecorder recorder;
    Backend backend(assets, recorder, recorder);
    VELLUM_RMLUI_CHECK(backend.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    backend.render();
    VELLUM_RMLUI_CHECK(drew_colour(recorder, 0xff0000ffu));
    VELLUM_RMLUI_CHECK(assets.tracked_files() == 2);
    VELLUM_RMLUI_CHECK(!assets.poll());

    Asset escaped;
    VELLUM_RMLUI_CHECK(assets.load("../outside.rml", escaped) == Status::rejected);

    write_style(root, "#0000ff");
    VELLUM_RMLUI_CHECK(assets.poll());
    VELLUM_RMLUI_CHECK(!assets.poll());
    VELLUM_RMLUI_CHECK(backend.reload_document() == Status::ok);
    backend.render();
    VELLUM_RMLUI_CHECK(drew_colour(recorder, 0x0000ffffu));
    VELLUM_RMLUI_CHECK(!drew_colour(recorder, 0xff0000ffu));

    std::filesystem::remove(root / "style.rcss");
    VELLUM_RMLUI_CHECK(assets.poll());
    VELLUM_RMLUI_CHECK(!assets.poll());

    backend.close();
    std::filesystem::remove_all(root);
    std::puts("vellum-rmlui hot reload: ok");
    return 0;
}
