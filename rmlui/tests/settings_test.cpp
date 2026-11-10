// The settings model: which pages show, which page is selected, values read
// and written in each row's scope, disabled rows that keep their value,
// action rows, provider-filled choices, and the window scale's scope.
#include <vellum/rmlui/settings.hpp>

#include "test_support.hpp"

#include <cmath>
#include <map>
#include <string>
#include <vector>

using namespace vellum::rmlui;

namespace {

struct FakeEngine final : CapabilityAdapter {
    std::map<std::string, RowState> states;
    RowState state(std::string_view row_id) override {
        const auto it = states.find(std::string(row_id));
        return it == states.end() ? RowState{} : it->second;
    }
};

SettingRow toggle(std::string id, SettingScope scope, bool fallback) {
    SettingRow row;
    row.id = std::move(id);
    row.kind = SettingKind::toggle;
    row.scope = scope;
    row.default_value = ModelValue::boolean_value(fallback);
    return row;
}

SettingRow number(std::string id, double lo, double hi, double fallback) {
    SettingRow row;
    row.id = std::move(id);
    row.kind = SettingKind::number;
    row.min = lo;
    row.max = hi;
    row.default_value = ModelValue::number_value(fallback);
    return row;
}

void test_pages_and_selection() {
    MemorySettingsStore store;
    SettingsModel model(store);
    bool skin_ready = false;
    model.add_page({"gui", "GUI", {}, {}});
    model.add_page({"skin", "Skin", [&] { return skin_ready; }, {}});
    model.add_page({"midi", "MIDI", {}, {}});

    auto shown = model.pages();
    VELLUM_RMLUI_CHECK(shown.size() == 2 && shown[0]->id == "gui" && shown[1]->id == "midi");
    VELLUM_RMLUI_CHECK(model.selected_page() == "gui");
    VELLUM_RMLUI_CHECK(model.select_page("skin") == Status::rejected); // not shown
    VELLUM_RMLUI_CHECK(model.select_page("midi") == Status::ok);
    VELLUM_RMLUI_CHECK(model.selected_page() == "midi");
    // The selection is per instance.
    VELLUM_RMLUI_CHECK(store.writes_in(SettingScope::instance) == 1);

    skin_ready = true;
    VELLUM_RMLUI_CHECK(model.pages().size() == 3);
    VELLUM_RMLUI_CHECK(model.select_page("skin") == Status::ok);
    skin_ready = false; // the remembered page goes away: fall back to the first
    VELLUM_RMLUI_CHECK(model.selected_page() == "gui");

    MemorySettingsStore empty_store;
    SettingsModel empty(empty_store);
    VELLUM_RMLUI_CHECK(empty.selected_page().empty());
}

void test_values_by_scope() {
    MemorySettingsStore store;
    SettingsModel model(store);
    SettingsPage page{"dsp", "DSP Audio", {}, {}};
    page.rows.push_back(toggle("dsp.bypass", SettingScope::instance, false));
    page.rows.push_back(toggle("gui.tooltips", SettingScope::global, true));
    page.rows.push_back(number("dsp.gain", -12.0, 12.0, 0.0));
    model.add_page(std::move(page));

    VELLUM_RMLUI_CHECK(model.value("dsp.bypass").boolean == false); // default
    VELLUM_RMLUI_CHECK(model.value("gui.tooltips").boolean == true);
    VELLUM_RMLUI_CHECK(model.set("dsp.bypass", ModelValue::boolean_value(true)) == Status::ok);
    VELLUM_RMLUI_CHECK(model.set("gui.tooltips", ModelValue::boolean_value(false)) == Status::ok);
    VELLUM_RMLUI_CHECK(store.read(SettingScope::instance, "dsp.bypass")->boolean == true);
    VELLUM_RMLUI_CHECK(store.read(SettingScope::global, "gui.tooltips")->boolean == false);
    VELLUM_RMLUI_CHECK(!store.read(SettingScope::global, "dsp.bypass"));

    // Wrong kinds are refused; numbers are clamped to the row's range.
    VELLUM_RMLUI_CHECK(model.set("dsp.bypass", ModelValue::number_value(1.0)) == Status::rejected);
    VELLUM_RMLUI_CHECK(model.set("dsp.gain", ModelValue::number_value(40.0)) == Status::ok);
    VELLUM_RMLUI_CHECK(model.value("dsp.gain").number == 12.0);
    VELLUM_RMLUI_CHECK(model.set("dsp.gain", ModelValue::number_value(NAN)) == Status::rejected);
    VELLUM_RMLUI_CHECK(model.set("missing", ModelValue::boolean_value(true)) == Status::rejected);

    // A stored value of the wrong kind reads as the default.
    store.write(SettingScope::instance, "dsp.bypass", ModelValue::string_value("yes"));
    VELLUM_RMLUI_CHECK(model.value("dsp.bypass").kind == ModelValue::Kind::boolean);
    VELLUM_RMLUI_CHECK(model.value("dsp.bypass").boolean == false);
}

void test_disabled_rows_keep_their_value() {
    MemorySettingsStore store;
    FakeEngine engine;
    SettingsModel model(store, &engine);
    SettingsPage page{"dsp", "DSP Audio", {}, {}};
    SettingRow resampler;
    resampler.id = "dsp.resampler";
    resampler.kind = SettingKind::choice;
    resampler.choices = {{"linear", "Linear"}, {"mamehq", "MAME HQ"}};
    resampler.default_value = ModelValue::string_value("linear");
    page.rows.push_back(resampler);
    model.add_page(std::move(page));

    VELLUM_RMLUI_CHECK(model.set("dsp.resampler", ModelValue::string_value("mamehq")) == Status::ok);
    engine.states["dsp.resampler"] = {false, "needs the MAME HQ resampler"};
    const RowState state = model.state("dsp.resampler");
    VELLUM_RMLUI_CHECK(!state.enabled && state.reason == "needs the MAME HQ resampler");
    VELLUM_RMLUI_CHECK(model.set("dsp.resampler", ModelValue::string_value("linear")) == Status::rejected);
    VELLUM_RMLUI_CHECK(model.value("dsp.resampler").string == "mamehq"); // still shown
    engine.states.erase("dsp.resampler"); // the engine slice lands
    VELLUM_RMLUI_CHECK(model.state("dsp.resampler").enabled);
    VELLUM_RMLUI_CHECK(model.value("dsp.resampler").string == "mamehq"); // restored
    VELLUM_RMLUI_CHECK(!model.state("missing").enabled);
}

void test_action_rows() {
    MemorySettingsStore store;
    FakeEngine engine;
    SettingsModel model(store, &engine);
    int panics = 0;
    SettingsPage page{"midi", "MIDI", {}, {}};
    SettingRow panic;
    panic.id = "midi.panic";
    panic.kind = SettingKind::action;
    panic.run = [&] { ++panics; };
    page.rows.push_back(panic);
    page.rows.push_back(toggle("midi.thru", SettingScope::global, false));
    model.add_page(std::move(page));

    VELLUM_RMLUI_CHECK(model.run("midi.panic") == Status::ok && panics == 1);
    VELLUM_RMLUI_CHECK(model.set("midi.panic", ModelValue::boolean_value(true)) == Status::rejected);
    VELLUM_RMLUI_CHECK(model.run("midi.thru") == Status::rejected);
    engine.states["midi.panic"] = {false, "no MIDI output in this host"};
    VELLUM_RMLUI_CHECK(model.run("midi.panic") == Status::rejected && panics == 1);
}

void test_rows_state_their_own_availability() {
    MemorySettingsStore store;
    FakeEngine engine;
    SettingsModel model(store, &engine);
    bool serviced = false;
    int runs = 0;
    SettingsPage page{"midi", "MIDI", {}, {}};
    SettingRow panic;
    panic.id = "midi.panic";
    panic.kind = SettingKind::action;
    panic.run = [&] { ++runs; };
    panic.state = [&] { return serviced ? RowState{} : RowState{false, "the host services no panic"}; };
    page.rows.push_back(panic);
    model.add_page(std::move(page));

    VELLUM_RMLUI_CHECK(model.state("midi.panic").reason == "the host services no panic");
    VELLUM_RMLUI_CHECK(model.run("midi.panic") == Status::rejected && runs == 0);
    serviced = true;
    VELLUM_RMLUI_CHECK(model.run("midi.panic") == Status::ok && runs == 1);
    // The adapter can still disable a row that is available on its own.
    engine.states["midi.panic"] = {false, "disabled by the product"};
    VELLUM_RMLUI_CHECK(model.state("midi.panic").reason == "disabled by the product");
}

void test_provider_filled_choice() {
    MemorySettingsStore store;
    SettingsModel model(store);
    int asks = 0;
    std::vector<ChoiceItem> ports = {{"in-1", "Keystation"}, {"in-2", "Launchpad"}};
    SettingsPage page{"midi", "MIDI", {}, {}};
    SettingRow input;
    input.id = "midi.input";
    input.kind = SettingKind::choice;
    input.choice_source = [&] {
        ++asks;
        return ports;
    };
    page.rows.push_back(input);
    model.add_page(std::move(page));

    // Nothing asks the provider until the list opens.
    VELLUM_RMLUI_CHECK(asks == 0);
    VELLUM_RMLUI_CHECK(model.value("midi.input").string.empty());
    auto view = model.open_choice("midi.input");
    VELLUM_RMLUI_CHECK(asks == 1 && view.items.size() == 2 && !view.selected && !view.missing);

    VELLUM_RMLUI_CHECK(model.set("midi.input", ModelValue::string_value("in-2")) == Status::ok);
    view = model.open_choice("midi.input");
    VELLUM_RMLUI_CHECK(asks == 2 && view.selected == 1u && !view.missing);

    // The saved port is unplugged: still selected, reported missing.
    ports.pop_back();
    view = model.open_choice("midi.input");
    VELLUM_RMLUI_CHECK(!view.selected && view.missing && view.missing_value == "in-2");
    VELLUM_RMLUI_CHECK(model.value("midi.input").string == "in-2");

    ports.clear();
    view = model.open_choice("midi.input");
    VELLUM_RMLUI_CHECK(view.items.empty() && view.missing);
    VELLUM_RMLUI_CHECK(model.open_choice("missing").items.empty());
}

void test_window_scale_scope() {
    MemorySettingsStore global_store;
    SettingsModel global_model(global_store);
    VELLUM_RMLUI_CHECK(global_model.window_scale() == 1.0);
    // A size the host forces is not remembered.
    VELLUM_RMLUI_CHECK(global_model.set_window_scale(0.5, false) == Status::ok);
    VELLUM_RMLUI_CHECK(global_model.window_scale() == 1.0);
    VELLUM_RMLUI_CHECK(global_store.writes_in(SettingScope::global) == 0);
    // The user's choice is, in the default (global) scope, clamped.
    VELLUM_RMLUI_CHECK(global_model.set_window_scale(5.0, true) == Status::ok);
    VELLUM_RMLUI_CHECK(global_model.window_scale() == 3.0);
    VELLUM_RMLUI_CHECK(global_store.writes_in(SettingScope::global) == 1);
    VELLUM_RMLUI_CHECK(global_model.set_window_scale(INFINITY, true) == Status::rejected);

    // A product whose original keeps the scale per instance.
    MemorySettingsStore instance_store;
    SettingsOptions per_instance;
    per_instance.window_scale_scope = SettingScope::instance;
    SettingsModel instance_model(instance_store, nullptr, per_instance);
    VELLUM_RMLUI_CHECK(instance_model.set_window_scale(1.5, true) == Status::ok);
    VELLUM_RMLUI_CHECK(instance_store.writes_in(SettingScope::instance) == 1);
    VELLUM_RMLUI_CHECK(instance_store.writes_in(SettingScope::global) == 0);
    VELLUM_RMLUI_CHECK(instance_model.window_scale() == 1.5);
}

} // namespace

int main() {
    test_pages_and_selection();
    test_values_by_scope();
    test_disabled_rows_keep_their_value();
    test_action_rows();
    test_rows_state_their_own_availability();
    test_provider_filled_choice();
    test_window_scale_scope();
    return 0;
}
