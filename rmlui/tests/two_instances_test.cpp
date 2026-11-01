// Two editors open in one process at once, each with its own context and its
// own assets, as two instances of one product (or two products) need.
#include <vellum/rmlui/backend.hpp>

#include "test_support.hpp"

#include <algorithm>
#include <cstdint>
#include <string>

using namespace vellum::rmlui;

namespace {

std::string document() {
    return "<rml><head><link type='text/rcss' href='style.rcss'/></head>"
           "<body style='display:block;'><div id='box'/></body></rml>";
}

std::string style(const char* colour) {
    return std::string("#box { display: block; width: 32px; height: 32px; background-color: ") +
           colour + "; }";
}

bool drew_colour(const HeadlessRecorder& recorder, std::uint32_t rgba) {
    return std::any_of(recorder.commands().begin(), recorder.commands().end(),
                       [&](const DrawCommand& command) {
                           return command.vertices &&
                                  std::any_of(command.vertices->begin(), command.vertices->end(),
                                              [&](const DrawVertex& v) { return v.colour == rgba; });
                       });
}

constexpr std::uint32_t red = 0xff0000ffu;
constexpr std::uint32_t blue = 0x0000ffffu;

} // namespace

int main() {
    // Both skins use the same relative names with different contents, so a
    // shared file route or a shared style sheet cache entry would swap them.
    HeadlessRecorder first_assets;
    first_assets.add_asset("editor.rml", document());
    first_assets.add_asset("style.rcss", style("#ff0000"));
    HeadlessRecorder second_assets;
    second_assets.add_asset("editor.rml", document());
    second_assets.add_asset("style.rcss", style("#0000ff"));

    Backend first(first_assets, first_assets, first_assets);
    Backend second(second_assets, second_assets, second_assets);

    VELLUM_RMLUI_CHECK(first.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(second.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 2);
    VELLUM_RMLUI_CHECK(first.load_document("editor.rml") == Status::ok);
    VELLUM_RMLUI_CHECK(second.load_document("editor.rml") == Status::ok);

    first.render();
    second.render();
    VELLUM_RMLUI_CHECK(drew_colour(first_assets, red));
    VELLUM_RMLUI_CHECK(!drew_colour(first_assets, blue));
    VELLUM_RMLUI_CHECK(drew_colour(second_assets, blue));
    VELLUM_RMLUI_CHECK(!drew_colour(second_assets, red));

    // Closing one leaves the other fully working.
    first.close();
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 1);
    second.render();
    VELLUM_RMLUI_CHECK(drew_colour(second_assets, blue));
    VELLUM_RMLUI_CHECK(second.process_mouse_move(4, 4) == Status::ok);

    // Reopen beside the survivor, then close in the other order.
    VELLUM_RMLUI_CHECK(first.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(first.load_document("editor.rml") == Status::ok);
    first.render();
    VELLUM_RMLUI_CHECK(drew_colour(first_assets, red));
    second.close();
    first.render();
    VELLUM_RMLUI_CHECK(drew_colour(first_assets, red));
    first.close();
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);

    // After the last close RmlUi is shut down and can start again.
    VELLUM_RMLUI_CHECK(second.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(second.load_document("editor.rml") == Status::ok);
    second.render();
    VELLUM_RMLUI_CHECK(drew_colour(second_assets, blue));
    second.close();
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);

    std::puts("vellum-rmlui two instances: ok");
    return 0;
}
