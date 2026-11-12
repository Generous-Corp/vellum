// The settings window in an RmlUi document: action parsing, the bridge's
// effect on a SettingsModel, Panic as an action row on the panic seam,
// change-only classes, and a Core round trip where the active page's button
// is restyled.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/settings_bridge.hpp>

#include "test_support.hpp"

#include <cmath>
#include <string>
#include <vector>

using namespace vellum::rmlui;

namespace {

HostEvent action(std::string id, double value = 0.0) {
    HostEvent event;
    event.kind = HostEventKind::action;
    event.id = std::move(id);
    event.value = value;
    return event;
}

struct RecordingSink final : ClassSink {
    std::vector<std::string> writes;
    Status set_class(std::string_view element_id, std::string_view class_name, bool on) override {
        writes.push_back(std::string(element_id) + "." + std::string(class_name) + (on ? "+" : "-"));
        return Status::ok;
    }
};

struct FakeSeam {
    PanicAvailability availability = PanicAvailability::voices_and_midi;
    std::vector<bool> requests;
    PanicHost host() {
        PanicHost h;
        h.request = [this](bool release_only) { requests.push_back(release_only); };
        h.availability = [this] { return availability; };
        return h;
    }
};

struct Fixture {
    MemorySettingsStore store;
    SettingsModel model{store};
    FakeSeam seam;
    bool skin_ready = false;
    std::vector<ChoiceItem> ports{{"in-1", "Keystation"}, {"in-2", "Launchpad"}};

    Fixture() {
        SettingsPage gui{"gui", "GUI", {}, {}};
        SettingRow tooltips;
        tooltips.id = "gui.tooltips";
        tooltips.kind = SettingKind::toggle;
        tooltips.default_value = ModelValue::boolean_value(true);
        gui.rows.push_back(tooltips);
        model.add_page(std::move(gui));
        model.add_page({"skin", "Skin", [this] { return skin_ready; }, {}});
        SettingsPage midi{"midi", "MIDI", {}, {}};
        SettingRow input;
        input.id = "midi.input";
        input.kind = SettingKind::choice;
        input.scope = SettingScope::instance;
        input.choice_source = [this] { return ports; };
        midi.rows.push_back(input);
        SettingRow gain;
        gain.id = "midi:out.gain"; // an id with ':' in it
        gain.kind = SettingKind::number;
        gain.min = 0.0;
        gain.max = 2.0;
        midi.rows.push_back(gain);
        midi.rows.push_back(panic_row(seam.host()));
        model.add_page(std::move(midi));
    }
};

void test_action_parsing() {
    auto parsed = parse_settings_action(action("settings.page:midi"));
    VELLUM_RMLUI_CHECK(parsed && parsed->kind == SettingsAction::Kind::page && parsed->id == "midi");
    parsed = parse_settings_action(action("settings.set:midi:out.gain", 1.5));
    VELLUM_RMLUI_CHECK(parsed && parsed->kind == SettingsAction::Kind::set && parsed->id == "midi:out.gain" &&
                       parsed->value == 1.5);
    VELLUM_RMLUI_CHECK(parse_settings_action(action("settings.run:midi.panic")));
    VELLUM_RMLUI_CHECK(!parse_settings_action(action("settings.page:")));
    VELLUM_RMLUI_CHECK(!parse_settings_action(action("settings.page")));
    VELLUM_RMLUI_CHECK(!parse_settings_action(action("settings.open:gui")));
    HostEvent parameter = action("settings.page:gui");
    parameter.kind = HostEventKind::parameter;
    VELLUM_RMLUI_CHECK(!parse_settings_action(parameter));
}

void test_actions_drive_the_model() {
    Fixture f;
    SettingsBridge bridge(f.model);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.page:midi")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(f.model.selected_page() == "midi");
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.page:skin")) == HostStatus::rejected);

    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:gui.tooltips", 0.0)) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(f.model.value("gui.tooltips").boolean == false);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:midi:out.gain", 1.25)) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(f.model.value("midi:out.gain").number == 1.25);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:midi:out.gain", NAN)) == HostStatus::rejected);

    // A choice is named by its index in the list as it was opened.
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:midi.input", 1)) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK(bridge.open_choice("midi.input").items.size() == 2);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:midi.input", 1)) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(f.model.value("midi.input").string == "in-2");
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:midi.input", 2)) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:midi.input", 0.5)) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.set:missing", 1)) == HostStatus::rejected);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.run:gui.tooltips")) == HostStatus::rejected);
}

void test_panic_is_an_action_row() {
    Fixture f;
    SettingsBridge bridge(f.model);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.run:midi.panic")) == HostStatus::ok);
    VELLUM_RMLUI_CHECK((f.seam.requests == std::vector<bool>{false}));
    f.seam.availability = PanicAvailability::unavailable;
    VELLUM_RMLUI_CHECK(!f.model.state("midi.panic").enabled);
    VELLUM_RMLUI_CHECK(bridge.on_event(action("settings.run:midi.panic")) == HostStatus::missing_capability);
    VELLUM_RMLUI_CHECK(f.seam.requests.size() == 1);
}

void test_classes_change_only() {
    Fixture f;
    SettingsBridge bridge(f.model);
    bridge.track_page_button("tab-gui", "gui");
    bridge.track_page_button("tab-skin", "skin");
    bridge.track_page_button("tab-midi", "midi");
    bridge.track_row("row-input", "midi.input");
    bridge.track_row("row-panic", "midi.panic");
    RecordingSink sink;

    bridge.refresh(sink);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{
                            "tab-gui.settings-page-active+", "tab-skin.settings-page-unavailable+",
                            "row-input.settings-scope-instance+", "row-panic.settings-scope-global+"}));
    sink.writes.clear();
    VELLUM_RMLUI_CHECK(bridge.refresh(sink) == 0);

    bridge.on_event(action("settings.page:midi"));
    f.skin_ready = true;
    f.seam.availability = PanicAvailability::unavailable;
    bridge.refresh(sink);
    VELLUM_RMLUI_CHECK((sink.writes == std::vector<std::string>{
                            "tab-gui.settings-page-active-", "tab-skin.settings-page-unavailable-",
                            "tab-midi.settings-page-active+", "row-panic.settings-row-disabled+"}));
}

constexpr std::uint32_t kActiveFill = 0x2e86deffu;

bool draws_colour(const HeadlessRecorder& recorder, std::uint32_t colour) {
    for (const auto& command : recorder.commands())
        if (command.vertices)
            for (const auto& vertex : *command.vertices)
                if (vertex.colour == colour)
                    return true;
    return false;
}

void test_active_page_is_restyled() {
    HeadlessRecorder headless;
    headless.add_asset("settings.rml",
                       "<rml><head><style>body { display: block; width: 200px; height: 40px; }"
                       ".tab { display: inline-block; width: 60px; height: 20px; background-color: #333333; }"
                       ".tab.settings-page-active { background-color: #2e86de; }</style></head>"
                       "<body><div id='tab-gui' class='tab'/><div id='tab-midi' class='tab'/></body></rml>");
    Backend backend(headless, headless, headless);
    VELLUM_RMLUI_CHECK(backend.open(200, 40) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("settings.rml") == Status::ok);
    Fixture f;
    SettingsBridge bridge(f.model);
    bridge.track_page_button("tab-gui", "gui");
    bridge.track_page_button("tab-midi", "midi");
    CoreClassSink sink(backend);

    backend.render();
    VELLUM_RMLUI_CHECK(!draws_colour(headless, kActiveFill));
    bridge.refresh(sink);
    backend.render();
    VELLUM_RMLUI_CHECK(draws_colour(headless, kActiveFill));
    backend.close();
}

} // namespace

int main() {
    test_action_parsing();
    test_actions_drive_the_model();
    test_panic_is_an_action_row();
    test_classes_change_only();
    test_active_page_is_restyled();
    return 0;
}
