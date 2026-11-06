// RmlUi's log reaches each Backend's sink, attributed by the Backend making the
// call, and a document's parse problems are counted per load. RmlUi drops a
// style declaration it cannot parse and still loads the document, so these are
// the only trace of the lost rule.
#include <vellum/rmlui/backend.hpp>

#include "test_support.hpp"

#include <algorithm>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

using namespace vellum::rmlui;

namespace {

// `!important` is not RmlUi syntax: the red rule is dropped, the blue one applies.
constexpr const char* kDroppedDeclarationDocument =
    "<rml><head><style>"
    "#a { display: block; width: 8px; height: 8px; background-color: #ff0000 !important; }"
    "#b { display: block; width: 8px; height: 8px; background-color: #0000ff; }"
    "</style></head><body style='display:block;'><div id='a'/><div id='b'/></body></rml>";

constexpr const char* kCleanDocument =
    "<rml><head><style>"
    "#b { display: block; width: 8px; height: 8px; background-color: #0000ff; }"
    "</style></head><body style='display:block;'><div id='b'/></body></rml>";

constexpr std::string_view kDroppedPrefix = "Syntax error parsing property declaration";

constexpr std::uint32_t red = 0xff0000ffu;
constexpr std::uint32_t blue = 0x0000ffffu;

bool drew_colour(const HeadlessRecorder& recorder, std::uint32_t rgba) {
    return std::any_of(recorder.commands().begin(), recorder.commands().end(),
                       [&](const DrawCommand& command) {
                           return command.vertices &&
                                  std::any_of(command.vertices->begin(), command.vertices->end(),
                                              [&](const DrawVertex& v) { return v.colour == rgba; });
                       });
}

struct Received {
    std::vector<std::pair<LogLevel, std::string>> messages;
    std::size_t containing(std::string_view needle) const {
        return static_cast<std::size_t>(
            std::count_if(messages.begin(), messages.end(),
                          [&](const auto& m) { return m.second.find(needle) != std::string::npos; }));
    }
    LogSink sink() {
        return [this](const LogMessage& message) {
            messages.emplace_back(message.level, std::string(message.text));
        };
    }
};

void test_dropped_declaration_is_reported() {
    HeadlessRecorder headless;
    headless.add_asset("bad.rml", kDroppedDeclarationDocument);
    headless.add_asset("clean.rml", kCleanDocument);
    Backend backend(headless, headless, headless);
    Received received;
    backend.set_log_sink(received.sink());

    VELLUM_RMLUI_CHECK(backend.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("bad.rml") == Status::ok);
    const auto bad = backend.last_load_diagnostics();
    VELLUM_RMLUI_CHECK(bad.dropped_declarations == 1);
    VELLUM_RMLUI_CHECK(bad.warnings + bad.errors >= 1);
    VELLUM_RMLUI_CHECK(received.containing(kDroppedPrefix) == 1);
    VELLUM_RMLUI_CHECK(received.containing("!important") >= 1);

    // The dropped rule has no effect and the valid one does: the silent
    // failure the diagnostics exist to expose.
    backend.render();
    VELLUM_RMLUI_CHECK(drew_colour(headless, blue));
    VELLUM_RMLUI_CHECK(!drew_colour(headless, red));

    VELLUM_RMLUI_CHECK(backend.load_document("clean.rml") == Status::ok);
    const auto clean = backend.last_load_diagnostics();
    VELLUM_RMLUI_CHECK(clean.dropped_declarations == 0);
    VELLUM_RMLUI_CHECK(clean.errors == 0);
    VELLUM_RMLUI_CHECK(received.containing(kDroppedPrefix) == 1);
    backend.close();
}

void test_sink_survives_close_and_reopen() {
    HeadlessRecorder headless;
    headless.add_asset("bad.rml", kDroppedDeclarationDocument);
    Backend backend(headless, headless, headless);
    Received received;
    backend.set_log_sink(received.sink());
    for (std::size_t cycle = 1; cycle <= 3; ++cycle) {
        VELLUM_RMLUI_CHECK(backend.open(32, 32) == Status::ok);
        VELLUM_RMLUI_CHECK(backend.load_document("bad.rml") == Status::ok);
        VELLUM_RMLUI_CHECK(backend.last_load_diagnostics().dropped_declarations == 1);
        backend.close();
        VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);
        VELLUM_RMLUI_CHECK(received.containing(kDroppedPrefix) == cycle);
    }

    // Removing the sink stops delivery; diagnostics are still counted.
    backend.set_log_sink({});
    VELLUM_RMLUI_CHECK(backend.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("bad.rml") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.last_load_diagnostics().dropped_declarations == 1);
    backend.close();
    VELLUM_RMLUI_CHECK(received.containing(kDroppedPrefix) == 3);
}

void test_two_backends_each_see_only_their_own_messages() {
    HeadlessRecorder first_assets;
    first_assets.add_asset("bad.rml", kDroppedDeclarationDocument);
    HeadlessRecorder second_assets;
    second_assets.add_asset("clean.rml", kCleanDocument);
    Backend first(first_assets, first_assets, first_assets);
    Backend second(second_assets, second_assets, second_assets);
    Received first_received;
    Received second_received;
    first.set_log_sink(first_received.sink());
    second.set_log_sink(second_received.sink());

    VELLUM_RMLUI_CHECK(first.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(second.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(second.load_document("clean.rml") == Status::ok);
    VELLUM_RMLUI_CHECK(first.load_document("bad.rml") == Status::ok);

    VELLUM_RMLUI_CHECK(first_received.containing(kDroppedPrefix) == 1);
    VELLUM_RMLUI_CHECK(second_received.containing(kDroppedPrefix) == 0);
    VELLUM_RMLUI_CHECK(first.last_load_diagnostics().dropped_declarations == 1);
    VELLUM_RMLUI_CHECK(second.last_load_diagnostics().dropped_declarations == 0);

    // The second Backend's next load is clean even though the first logged
    // between its loads.
    VELLUM_RMLUI_CHECK(second.load_document("clean.rml") == Status::ok);
    VELLUM_RMLUI_CHECK(second.last_load_diagnostics().dropped_declarations == 0);
    first.close();
    second.close();
}

} // namespace

int main() {
    test_dropped_declaration_is_reported();
    test_sink_survives_close_and_reopen();
    test_two_backends_each_see_only_their_own_messages();
    return 0;
}
