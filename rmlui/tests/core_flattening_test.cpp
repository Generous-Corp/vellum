// Control for native_test.cpp: the sealed Core path reduces RmlUi's render
// calls to geometry draws. The same fixture with and without its blur filter
// produces the same draw stream through Core, because Core's renderer seam has
// no layer or filter to carry it. VellumRmlUi::Native exists so a renderer can
// see those calls.
#include <vellum/rmlui/backend.hpp>

#include "test_support.hpp"

#include <filesystem>
#include <string>
#include <vector>

using namespace vellum::rmlui;

namespace {

struct Draw {
    DrawCommand::Kind kind;
    float x, y, width, height;
    std::size_t vertices;
    bool operator==(const Draw&) const = default;
};

std::vector<Draw> draws(const std::string& document) {
    HeadlessRecorder recorder;
    recorder.add_asset("doc.rml", document);
    Backend backend(recorder, recorder, recorder);
    VELLUM_RMLUI_CHECK(backend.open(160, 120) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("doc.rml") == Status::ok);
    backend.render();
    std::vector<Draw> out;
    for (const auto& command : recorder.commands())
        out.push_back({command.kind, command.x, command.y, command.width, command.height,
                       command.vertices ? command.vertices->size() : 0});
    backend.close();
    return out;
}

} // namespace

int main() {
    const auto with_filter = vellum::rmlui::test::read_binary(
        std::filesystem::path(VELLUM_RMLUI_TEST_FIXTURES) / "layers_filters.rml");
    auto without_filter = with_filter;
    const std::string blur = "filter: blur(2px);";
    const auto at = without_filter.find(blur);
    VELLUM_RMLUI_CHECK(at != std::string::npos);
    without_filter.erase(at, blur.size());

    const auto filtered = draws(with_filter);
    const auto unfiltered = draws(without_filter);
    VELLUM_RMLUI_CHECK(!filtered.empty());
    // The filter left no trace in Core's stream.
    VELLUM_RMLUI_CHECK(filtered == unfiltered);
    return 0;
}
