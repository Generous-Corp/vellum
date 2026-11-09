// MIDI Learn for RmlUi editors: the RmlUi-free helper (class states, action
// parsing, the bridge to the editor's engine) and the Core backend's element
// class call it drives, observed through the colours a document draws.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/midi_learn.hpp>

#include "test_support.hpp"

#include <algorithm>
#include <filesystem>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using namespace vellum::rmlui;
using Phase = MidiLearnParamView::Phase;

namespace {

HostEvent action(std::string id) {
    HostEvent event;
    event.kind = HostEventKind::action;
    event.id = std::move(id);
    return event;
}

// A scripted engine: per-parameter views and a log of the calls it received.
struct FakeEngine {
    bool available = true;
    bool throw_on_call = false;
    std::map<std::string, MidiLearnParamView> views;
    std::vector<std::string> calls;

    MidiLearnHost host() {
        MidiLearnHost h;
        h.available = [this] { return available; };
        h.view = [this](std::string_view param) {
            if (throw_on_call)
                throw std::runtime_error("engine failure");
            const auto it = views.find(std::string(param));
            return it == views.end() ? MidiLearnParamView{} : it->second;
        };
        h.begin = [this](std::string_view param) {
            if (throw_on_call)
                throw std::runtime_error("engine failure");
            calls.push_back("begin:" + std::string(param));
        };
        h.cancel = [this] { calls.push_back("cancel"); };
        h.clear = [this](std::string_view param) { calls.push_back("clear:" + std::string(param)); };
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

void test_class_toggles_follow_the_phase() {
    MidiLearnParamView view;
    auto t = midi_learn_class_toggles("cutoff", view);
    VELLUM_RMLUI_CHECK(!t[0].on && !t[1].on && !t[2].on);
    VELLUM_RMLUI_CHECK(t[0].element_id == "cutoff" && t[0].class_name == "midi-learn-listening");
    VELLUM_RMLUI_CHECK(t[1].class_name == "midi-learn-bound" && t[2].class_name == "midi-learn-mapped");

    view.phase = Phase::listening;
    t = midi_learn_class_toggles("cutoff", view);
    VELLUM_RMLUI_CHECK(t[0].on && !t[1].on && !t[2].on);

    view.phase = Phase::mapped;
    view.just_bound = true;
    t = midi_learn_class_toggles("cutoff", view);
    VELLUM_RMLUI_CHECK(!t[0].on && t[1].on && t[2].on);

    view.just_bound = false;
    t = midi_learn_class_toggles("cutoff", view);
    VELLUM_RMLUI_CHECK(!t[0].on && !t[1].on && t[2].on);
}

void test_action_parsing() {
    auto parsed = parse_midi_learn_action(action("midi_learn.begin:cutoff"));
    VELLUM_RMLUI_CHECK(parsed && parsed->kind == MidiLearnAction::Kind::begin && parsed->param == "cutoff");
    parsed = parse_midi_learn_action(action("midi_learn.clear:osc:1:pitch"));
    VELLUM_RMLUI_CHECK(parsed && parsed->kind == MidiLearnAction::Kind::clear &&
                       parsed->param == "osc:1:pitch");
    parsed = parse_midi_learn_action(action("midi_learn.cancel"));
    VELLUM_RMLUI_CHECK(parsed && parsed->kind == MidiLearnAction::Kind::cancel && parsed->param.empty());

    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(action("midi_learn.begin")));
    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(action("midi_learn.begin:")));
    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(action("midi_learn.clear:")));
    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(action("midi_learn.cancel:cutoff")));
    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(action("midi_learn.start:cutoff")));
    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(action("")));
    HostEvent parameter = action("midi_learn.begin:cutoff");
    parameter.kind = HostEventKind::parameter;
    VELLUM_RMLUI_CHECK(!parse_midi_learn_action(parameter));
}

void test_bridge_routes_actions_to_the_engine() {
    FakeEngine engine;
    MidiLearnBridge bridge(engine.host());
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi_learn.begin:cutoff")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi_learn.cancel")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi_learn.clear:resonance")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi_learn.begin:")) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("parameter.gain")) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK((engine.calls == std::vector<std::string>{"begin:cutoff", "cancel",
                                                                 "clear:resonance"}));

    engine.available = false;
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi_learn.begin:cutoff")) ==
                       HostStatus::missing_capability);
    VELLUM_RMLUI_CHECK(engine.calls.size() == 3);

    engine.available = true;
    engine.throw_on_call = true;
    VELLUM_RMLUI_CHECK(bridge.on_event(action("midi_learn.begin:cutoff")) == HostStatus::rejected);

    MidiLearnHost partial;
    partial.available = [] { return true; };
    MidiLearnBridge partial_bridge(partial);
    VELLUM_RMLUI_CHECK(partial_bridge.on_event(action("midi_learn.cancel")) ==
                       HostStatus::missing_capability);
    MidiLearnBridge empty_bridge(MidiLearnHost{});
    VELLUM_RMLUI_CHECK(empty_bridge.on_event(action("midi_learn.cancel")) ==
                       HostStatus::missing_capability);
}

void test_escape_cancels_only_a_listening_learn() {
    FakeEngine engine;
    MidiLearnBridge bridge(engine.host());
    bridge.track("knob-cutoff", "cutoff");
    VELLUM_RMLUI_CHECK(!bridge.handle_escape());
    engine.views["cutoff"].phase = Phase::mapped;
    VELLUM_RMLUI_CHECK(!bridge.handle_escape());
    engine.views["cutoff"].phase = Phase::listening;
    VELLUM_RMLUI_CHECK(bridge.handle_escape());
    VELLUM_RMLUI_CHECK((engine.calls == std::vector<std::string>{"cancel"}));
    engine.available = false;
    VELLUM_RMLUI_CHECK(!bridge.handle_escape());
    VELLUM_RMLUI_CHECK(engine.calls.size() == 1);
}

void test_refresh_writes_only_changes() {
    FakeEngine engine;
    MidiLearnBridge bridge(engine.host());
    bridge.track("knob-cutoff", "cutoff");
    bridge.track("knob-res", "resonance");
    RecordingSink sink;

    // Idle elements start with no classes, so there is nothing to write.
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0 && sink.writes.empty());

    engine.views["cutoff"].phase = Phase::listening;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 1);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{"knob-cutoff.midi-learn-listening+"}));
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0 && sink.writes.size() == 1);

    engine.views["cutoff"] = {Phase::mapped, "CC 74 ch 1", true};
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 3);
    VELLUM_RMLUI_CHECK((std::vector<std::string>(sink.writes.begin() + 1, sink.writes.end()) ==
                        std::vector<std::string>{"knob-cutoff.midi-learn-listening-",
                                                 "knob-cutoff.midi-learn-bound+",
                                                 "knob-cutoff.midi-learn-mapped+"}));

    // MIDI Learn going away clears every class the bridge set.
    engine.available = false;
    sink.writes.clear();
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 2);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{"knob-cutoff.midi-learn-bound-",
                                                                "knob-cutoff.midi-learn-mapped-"}));

    // An engine that throws reads as idle; a refused write is offered again.
    engine.available = true;
    engine.views["resonance"].phase = Phase::listening;
    sink.refuse = true;
    sink.writes.clear();
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0);
    sink.refuse = false;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 3);
    engine.throw_on_call = true;
    sink.writes.clear();
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 3);
    VELLUM_RMLUI_CHECK(sink.writes.size() == 3);

    // Tracking an element again moves it to the other parameter: knob-res now
    // shows cutoff's state (and knob-cutoff gets its own back).
    engine.throw_on_call = false;
    bridge.track("knob-res", "cutoff");
    sink.writes.clear();
    bridge.refresh(sink);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{"knob-cutoff.midi-learn-bound+",
                                                                "knob-cutoff.midi-learn-mapped+",
                                                                "knob-res.midi-learn-bound+",
                                                                "knob-res.midi-learn-mapped+"}));
}

void test_stylesheet_matches_the_installed_file() {
    const auto file = test::read_binary(VELLUM_RMLUI_MIDI_LEARN_RCSS);
    VELLUM_RMLUI_CHECK(!file.empty());
    VELLUM_RMLUI_CHECK(midi_learn_stylesheet() == file);
    for (const auto name : {kMidiLearnControlClass, kMidiLearnListeningClass, kMidiLearnBoundClass,
                            kMidiLearnMappedClass})
        VELLUM_RMLUI_CHECK(file.find("." + std::string(name) + " {") != std::string::npos);
}

constexpr std::uint32_t kAmber = 0xf5a623ffu;      // listening border
constexpr std::uint32_t kBackground = 0x204060ffu; // the control's own fill

bool draws_colour(const HeadlessRecorder& recorder, std::uint32_t colour) {
    for (const auto& command : recorder.commands())
        if (command.vertices)
            for (const auto& vertex : *command.vertices)
                if (vertex.colour == colour)
                    return true;
    return false;
}

// The box of the vertices drawn in the control's own fill colour. RmlUi draws
// an element's background and border as one mesh, so a command's bounds grow
// when the border gains a colour; the fill's own vertices do not.
struct Box {
    float x0 = 1e9f, y0 = 1e9f, x1 = -1e9f, y1 = -1e9f;
    bool operator==(const Box& o) const {
        return x0 == o.x0 && y0 == o.y0 && x1 == o.x1 && y1 == o.y1;
    }
};

Box fill_box(const HeadlessRecorder& recorder) {
    Box box;
    for (const auto& command : recorder.commands()) {
        if (!command.vertices)
            continue;
        for (const auto& vertex : *command.vertices) {
            if (vertex.colour != kBackground)
                continue;
            const float x = vertex.x + command.translation_x;
            const float y = vertex.y + command.translation_y;
            box.x0 = std::min(box.x0, x);
            box.y0 = std::min(box.y0, y);
            box.x1 = std::max(box.x1, x);
            box.y1 = std::max(box.y1, y);
        }
    }
    return box;
}

void test_core_class_sink_restyles_the_element() {
    HeadlessRecorder headless;
    headless.add_asset("vellum/midi-learn.rcss", std::string(midi_learn_stylesheet()));
    headless.add_asset("editor.rml",
                       "<rml><head><link type='text/rcss' href='vellum/midi-learn.rcss'/>"
                       "<style>body { display: block; width: 120px; height: 80px; }"
                       "#knob-cutoff { display: block; margin: 20px; width: 40px; height: 40px;"
                       " background-color: #204060; }</style></head>"
                       "<body><div id='knob-cutoff' class='midi-learn-control'/></body></rml>");
    Backend backend(headless, headless, headless);
    CoreClassSink sink(backend);
    VELLUM_RMLUI_CHECK(sink.set_class("knob-cutoff", "midi-learn-listening", true) == Status::not_open);
    VELLUM_RMLUI_CHECK(backend.open(120, 80) == Status::ok);
    VELLUM_RMLUI_CHECK(sink.set_class("knob-cutoff", "midi-learn-listening", true) == Status::not_open);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);

    backend.render();
    const Box before = fill_box(headless);
    VELLUM_RMLUI_CHECK(before.x1 - before.x0 == 40.0f && before.y1 - before.y0 == 40.0f);
    VELLUM_RMLUI_CHECK(!draws_colour(headless, kAmber));

    FakeEngine engine;
    engine.views["cutoff"].phase = Phase::listening;
    MidiLearnBridge bridge(engine.host());
    bridge.track("knob-cutoff", "cutoff");
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 1);
    backend.render();
    VELLUM_RMLUI_CHECK(draws_colour(headless, kAmber));
    // The reserved border only changes colour: the control does not move.
    VELLUM_RMLUI_CHECK(fill_box(headless) == before);

    engine.views["cutoff"].phase = Phase::idle;
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 1);
    backend.render();
    VELLUM_RMLUI_CHECK(!draws_colour(headless, kAmber));

    VELLUM_RMLUI_CHECK(sink.set_class("missing", "midi-learn-listening", true) == Status::rejected);
    VELLUM_RMLUI_CHECK(sink.set_class("knob-cutoff", "", true) == Status::rejected);
    VELLUM_RMLUI_CHECK(sink.set_class("knob-cutoff", "a b", true) == Status::rejected);
    VELLUM_RMLUI_CHECK(sink.set_class("", "midi-learn-listening", true) == Status::rejected);
    backend.close();
    VELLUM_RMLUI_CHECK(sink.set_class("knob-cutoff", "midi-learn-listening", true) == Status::not_open);
}

} // namespace

int main() {
    test_class_toggles_follow_the_phase();
    test_action_parsing();
    test_bridge_routes_actions_to_the_engine();
    test_escape_cancels_only_a_listening_learn();
    test_refresh_writes_only_changes();
    test_stylesheet_matches_the_installed_file();
    test_core_class_sink_restyles_the_element();
    return 0;
}
