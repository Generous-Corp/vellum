// The MIDI panic button for RmlUi editors: action parsing, the bridge to the
// editor's panic seam, change-only class refresh, and a Core round trip in
// which the stock sheet hides the button while no panic request is serviced.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/midi_panic.hpp>

#include "test_support.hpp"

#include <stdexcept>
#include <string>
#include <vector>

using namespace vellum::rmlui;

namespace {

HostEvent action(std::string id) {
    HostEvent event;
    event.kind = HostEventKind::action;
    event.id = std::move(id);
    return event;
}

struct FakeSeam {
    PanicAvailability availability = PanicAvailability::voices_and_midi;
    bool throw_on_request = false;
    std::vector<bool> requests; // release_only per request

    PanicHost host() {
        PanicHost h;
        h.request = [this](bool release_only) {
            if (throw_on_request)
                throw std::runtime_error("seam failure");
            requests.push_back(release_only);
        };
        h.availability = [this] { return availability; };
        return h;
    }
};

struct RecordingSink final : ClassSink {
    std::vector<std::string> writes;
    bool refuse = false;
    Status set_class(std::string_view element_id, std::string_view class_name, bool on) override {
        if (refuse)
            return Status::rejected;
        writes.push_back(std::string(element_id) + "." + std::string(class_name) + (on ? "+" : "-"));
        return Status::ok;
    }
};

void test_action_parsing() {
    VELLUM_RMLUI_CHECK(parse_midi_panic_action(action("midi.panic")) == false);
    VELLUM_RMLUI_CHECK(parse_midi_panic_action(action("midi.panic:release")) == true);
    VELLUM_RMLUI_CHECK(!parse_midi_panic_action(action("midi.panic:")));
    VELLUM_RMLUI_CHECK(!parse_midi_panic_action(action("midi.panic:releasex")));
    VELLUM_RMLUI_CHECK(!parse_midi_panic_action(action("midi.panic:hard")));
    VELLUM_RMLUI_CHECK(!parse_midi_panic_action(action("midi.panicx")));
    VELLUM_RMLUI_CHECK(!parse_midi_panic_action(action("midi_learn.cancel")));
    HostEvent parameter = action("midi.panic");
    parameter.kind = HostEventKind::parameter;
    VELLUM_RMLUI_CHECK(!parse_midi_panic_action(parameter));
}

void test_bridge_raises_requests() {
    FakeSeam seam;
    PanicBridge bridge(seam.host());
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi.panic")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi.panic:release")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi.panic:hard")) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK((seam.requests == std::vector<bool>{false, true}));
    VELLUM_RMLUI_CHECK(bridge.request_count() == 2);

    seam.availability = PanicAvailability::voices_only;
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi.panic")) == HostStatus::ok);

    // A host that services nothing gets no request.
    seam.availability = PanicAvailability::unavailable;
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi.panic")) == HostStatus::missing_capability);
    VELLUM_RMLUI_CHECK(seam.requests.size() == 3 && bridge.request_count() == 3);

    seam.availability = PanicAvailability::voices_only;
    seam.throw_on_request = true;
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi.panic")) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK(bridge.request_count() == 3);

    PanicHost no_availability;
    no_availability.request = [](bool) {};
    PanicBridge silent(no_availability);
    VELLUM_RMLUI_CHECK(silent.availability() == PanicAvailability::unavailable);
    VELLUM_RMLUI_CHECK(silent.on_event(action("midi.panic")) == HostStatus::missing_capability);
    PanicHost throwing;
    throwing.availability = []() -> PanicAvailability { throw std::runtime_error("x"); };
    VELLUM_RMLUI_CHECK(PanicBridge(throwing).availability() == PanicAvailability::unavailable);
}

void test_refresh_writes_only_changes() {
    FakeSeam seam;
    seam.availability = PanicAvailability::voices_only;
    PanicBridge bridge(seam.host());
    bridge.track("panic");
    bridge.track("panic"); // tracked once
    RecordingSink sink;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0 && sink.writes.empty());

    seam.availability = PanicAvailability::voices_and_midi;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 1);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{"panic.midi-panic-midi+"}));
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0);

    seam.availability = PanicAvailability::unavailable;
    sink.writes.clear();
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 2);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{"panic.midi-panic-unavailable+",
                                                                "panic.midi-panic-midi-"}));

    seam.availability = PanicAvailability::voices_only;
    sink.refuse = true;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0);
    sink.refuse = false;
    sink.writes.clear();
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 1);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{"panic.midi-panic-unavailable-"}));
}

void test_stylesheet_matches_the_installed_file() {
    const auto file = test::read_binary(VELLUM_RMLUI_MIDI_PANIC_RCSS);
    VELLUM_RMLUI_CHECK(!file.empty());
    VELLUM_RMLUI_CHECK(midi_panic_stylesheet() == file);
    VELLUM_RMLUI_CHECK(file.find(".midi-panic-unavailable {") != std::string::npos);
}

constexpr std::uint32_t kButtonFill = 0xc0392bffu;

bool draws_colour(const HeadlessRecorder& recorder, std::uint32_t colour) {
    for (const auto& command : recorder.commands())
        if (command.vertices)
            for (const auto& vertex : *command.vertices)
                if (vertex.colour == colour)
                    return true;
    return false;
}

void test_unavailable_button_is_hidden() {
    HeadlessRecorder headless;
    headless.add_asset("vellum/midi-panic.rcss", std::string(midi_panic_stylesheet()));
    headless.add_asset("editor.rml",
                       "<rml><head><link type='text/rcss' href='vellum/midi-panic.rcss'/>"
                       "<style>body { display: block; width: 120px; height: 60px; }"
                       "#panic { display: block; width: 60px; height: 24px;"
                       " background-color: #c0392b; }</style></head>"
                       "<body><div id='panic'/></body></rml>");
    Backend backend(headless, headless, headless);
    VELLUM_RMLUI_CHECK(backend.open(120, 60) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    CoreClassSink sink(backend);
    FakeSeam seam;
    seam.availability = PanicAvailability::unavailable;
    PanicBridge bridge(seam.host());
    bridge.track("panic");

    backend.render();
    VELLUM_RMLUI_CHECK(draws_colour(headless, kButtonFill));
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 1);
    backend.render();
    VELLUM_RMLUI_CHECK(!draws_colour(headless, kButtonFill));

    seam.availability = PanicAvailability::voices_and_midi;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 2);
    backend.render();
    VELLUM_RMLUI_CHECK(draws_colour(headless, kButtonFill));
    backend.close();
}

} // namespace

int main() {
    test_action_parsing();
    test_bridge_raises_requests();
    test_refresh_writes_only_changes();
    test_stylesheet_matches_the_installed_file();
    test_unavailable_button_is_hidden();
    return 0;
}
