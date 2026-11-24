// Binds framework widgets to a vellum::value::ParamModel and checks the
// binding contract headlessly: no window, no GPU, no audio.

#include "framework_test.hpp"

#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/value/param_metadata.hpp>
#include <vellum/view/parameter_binding.hpp>
#include <vellum/view/design_import.hpp>
#include <vellum/view/screenshot.hpp>

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using vellum::value::InMemoryParamModel;
using vellum::value::ParamHandle;
using vellum::value::ParamInfo;
using vellum::value::ParamModel;
using vellum::value::ParamSpec;
using vellum::value::Subscription;

bool near(double a, double b) { return std::abs(a - b) < 1e-6; }

// A model whose writes are split by origin. Everything a binding calls goes
// through this object and is counted as an editor edit; the test plays the
// host by writing to `host` directly, which bypasses the counters. The
// gesture observer records what reaches the host: one entry per outermost
// begin and per balancing end.
class RecordingModel final : public ParamModel {
public:
    explicit RecordingModel(InMemoryParamModel& host) : host_(host) {
        host_.set_gesture_observer([this](ParamHandle, bool begun) {
            (begun ? host_begins : host_ends) += 1;
        });
    }

    int editor_values = 0;
    int editor_begins = 0;
    int editor_ends = 0;
    int host_begins = 0;
    int host_ends = 0;

    ParamHandle resolve(std::string_view key) override { return host_.resolve(key); }
    double normalized(ParamHandle p) override { return host_.normalized(p); }
    void set_normalized(ParamHandle p, double n) override {
        ++editor_values;
        host_.set_normalized(p, n);
    }
    void begin(ParamHandle p) override {
        ++editor_begins;
        host_.begin(p);
    }
    void end(ParamHandle p) override {
        ++editor_ends;
        host_.end(p);
    }
    std::string display_text(ParamHandle p, double n) override { return host_.display_text(p, n); }
    int value_count(ParamHandle p) override { return host_.value_count(p); }
    Subscription subscribe(Listener l) override { return host_.subscribe(std::move(l)); }
    ParamInfo info(ParamHandle p) override { return host_.info(p); }
    double plain(ParamHandle p, double n) override { return host_.plain(p, n); }
    double normalized_from_plain(ParamHandle p, double v) override {
        return host_.normalized_from_plain(p, v);
    }
    std::optional<double> parse_text(ParamHandle p, std::string_view t) override {
        return host_.parse_text(p, t);
    }
    std::optional<double> modulated_normalized(ParamHandle p) override {
        return host_.modulated_normalized(p);
    }

private:
    InMemoryParamModel& host_;
};

InMemoryParamModel make_host() {
    std::vector<ParamSpec> specs;
    specs.push_back({.key = "cutoff", .default_normalized = 0.25});
    specs.push_back({.key = "depth", .default_normalized = 0.5});
    specs.push_back({.key = "mode", .default_normalized = 0.25, .value_count = 5});
    specs.push_back({.key = "voices", .default_normalized = 0.0, .value_count = 3});
    specs.push_back({.key = "flag", .default_normalized = 0.0, .value_count = 2});
    return InMemoryParamModel(std::move(specs));
}

void place(vellum::view::Knob& knob) { knob.set_bounds({0.0f, 0.0f, 48.0f, 48.0f}); }

FRAMEWORK_TEST(knob_starts_at_the_model_value) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    auto binding = vellum::view::bind_parameter(knob, model, model.resolve("cutoff"));
    CHECK(near(knob.value(), 0.25));
    CHECK(model.editor_values == 0);
    CHECK(model.host_begins == 0);
}

FRAMEWORK_TEST(a_drag_is_one_host_gesture) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    place(knob);
    const auto cutoff = model.resolve("cutoff");
    auto binding = vellum::view::bind_parameter(knob, model, cutoff);

    knob.simulate_drag({24.0f, 40.0f}, {24.0f, 4.0f}, 12);

    CHECK(model.host_begins == 1);
    CHECK(model.host_ends == 1);
    CHECK(model.editor_begins == 1);
    CHECK(model.editor_ends == 1);
    CHECK(model.editor_values >= 2);
    CHECK(knob.value() > 0.25f);
    CHECK(near(host.normalized(cutoff), knob.value()));
    CHECK(host.gesture_depth(cutoff) == 0);
}

FRAMEWORK_TEST(a_host_change_moves_the_knob_without_an_edit) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    const auto cutoff = model.resolve("cutoff");
    auto binding = vellum::view::bind_parameter(knob, model, cutoff);

    host.set_normalized(cutoff, 0.8);
    CHECK(near(knob.value(), 0.8));
    host.set_normalized(cutoff, 0.1);
    CHECK(near(knob.value(), 0.1));
    CHECK(model.editor_values == 0);
    CHECK(model.editor_begins == 0);
    CHECK(model.host_begins == 0);
}

FRAMEWORK_TEST(modulation_is_shown_at_tick_and_cleared_when_gone) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    const auto cutoff = model.resolve("cutoff");
    auto binding = vellum::view::bind_parameter(knob, model, cutoff);

    host.set_modulation_offset(cutoff, 0.5);
    // Polled, not pushed: nothing changes until the editor ticks.
    CHECK(!knob.has_modulated_value());
    binding.tick();
    CHECK(knob.has_modulated_value());
    CHECK(near(knob.modulated_display_value(), 0.75));
    CHECK(near(knob.value(), 0.25));

    host.set_modulation_offset(cutoff, std::nullopt);
    binding.tick();
    CHECK(!knob.has_modulated_value());
    CHECK(near(knob.modulated_display_value(), 0.25));
    CHECK(model.editor_values == 0);
}

FRAMEWORK_TEST(destroying_the_binding_unbinds) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    const auto cutoff = model.resolve("cutoff");
    {
        auto binding = vellum::view::bind_parameter(knob, model, cutoff);
        host.set_normalized(cutoff, 0.6);
        CHECK(near(knob.value(), 0.6));
    }
    host.set_normalized(cutoff, 0.9);
    CHECK(near(knob.value(), 0.6));
}

FRAMEWORK_TEST(a_moved_binding_keeps_working) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    const auto cutoff = model.resolve("cutoff");
    std::vector<vellum::view::ParameterBinding> bindings;
    bindings.push_back(vellum::view::bind_parameter(knob, model, cutoff));
    bindings.reserve(16);  // forces the binding to move

    host.set_normalized(cutoff, 0.4);
    CHECK(near(knob.value(), 0.4));
    host.set_modulation_offset(cutoff, 0.2);
    bindings.front().tick();
    CHECK(near(knob.modulated_display_value(), 0.6));
}

FRAMEWORK_TEST(an_unknown_key_binds_to_nothing) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    place(knob);
    auto binding = vellum::view::bind_parameter(knob, model, model.resolve("missing"));
    binding.tick();
    CHECK(knob.value() == 0.0f);
    CHECK(!knob.has_modulated_value());
    knob.simulate_drag({24.0f, 40.0f}, {24.0f, 4.0f}, 4);
    // The model ignores the invalid handle, so nothing reaches the host.
    CHECK(model.host_begins == 0);
    CHECK(near(host.normalized(model.resolve("cutoff")), 0.25));
}

FRAMEWORK_TEST(a_store_binding_keeps_its_behaviour_and_tick_does_nothing) {
    vellum::state::StateStore store;
    store.add_parameter({.id = 7, .name = "Gain", .range = {0.0f, 10.0f, 2.5f}});
    vellum::view::Knob knob;
    auto binding = vellum::view::bind_parameter(knob, store, 7);
    CHECK(near(knob.value(), 0.25));
    binding.tick();
    CHECK(near(knob.value(), 0.25));
    CHECK(!knob.has_modulated_value());
    store.set_value(7, 5.0f);
    store.pump_listeners();
    CHECK(near(knob.value(), 0.5));

    vellum::view::ParameterBinding empty;
    empty.tick();
}

FRAMEWORK_TEST(a_fader_drag_is_one_host_gesture_and_follows_the_model) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Fader fader;
    fader.set_bounds({0.0f, 0.0f, 24.0f, 160.0f});
    const auto cutoff = model.resolve("cutoff");
    auto binding = vellum::view::bind_parameter(fader, model, cutoff);
    CHECK(near(fader.value(), 0.25));

    fader.simulate_drag({12.0f, 120.0f}, {12.0f, 20.0f}, 10);
    CHECK(model.host_begins == 1);
    CHECK(model.host_ends == 1);
    CHECK(model.editor_values >= 2);
    CHECK(near(host.normalized(cutoff), fader.value()));

    const int edits = model.editor_values;
    host.set_normalized(cutoff, 0.6);
    CHECK(near(fader.value(), 0.6));
    CHECK(model.editor_values == edits);

    host.set_modulation_offset(cutoff, 0.1);
    binding.tick();
    CHECK(fader.has_modulated_value());
    CHECK(near(fader.modulated_display_value(), 0.7));
}

FRAMEWORK_TEST(a_range_slider_follows_the_model_silently) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::RangeSlider slider;
    slider.set_bounds({0.0f, 0.0f, 200.0f, 24.0f});
    const auto depth = model.resolve("depth");
    auto binding = vellum::view::bind_parameter(slider, model, depth);
    CHECK(near(slider.value(), 0.5));

    host.set_normalized(depth, 0.2);
    CHECK(near(slider.value(), 0.2));
    CHECK(model.editor_values == 0);

    slider.simulate_drag({40.0f, 12.0f}, {160.0f, 12.0f}, 10);
    CHECK(model.host_begins == 1);
    CHECK(model.host_ends == 1);
    CHECK(near(host.normalized(depth), slider.value()));

    host.set_modulation_offset(depth, -0.1);
    binding.tick();
    CHECK(slider.has_modulated_value());
    host.set_modulation_offset(depth, std::nullopt);
    binding.tick();
    CHECK(!slider.has_modulated_value());
}

FRAMEWORK_TEST(an_xy_drag_is_one_gesture_per_axis) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::XYPad pad;
    pad.set_bounds({0.0f, 0.0f, 100.0f, 100.0f});
    const auto x = model.resolve("cutoff");
    const auto y = model.resolve("depth");
    auto binding = vellum::view::bind_parameter(pad, model, x, y);
    CHECK(near(pad.x_value(), 0.25));
    CHECK(near(pad.y_value(), 0.5));

    pad.simulate_drag({20.0f, 20.0f}, {80.0f, 70.0f}, 8);
    CHECK(model.host_begins == 2);
    CHECK(model.host_ends == 2);
    CHECK(near(host.normalized(x), pad.x_value()));
    CHECK(near(host.normalized(y), pad.y_value()));

    const int edits = model.editor_values;
    host.set_normalized(x, 0.9);
    CHECK(near(pad.x_value(), 0.9));
    host.set_normalized(y, 0.1);
    CHECK(near(pad.y_value(), 0.1));
    CHECK(model.editor_values == edits);

    host.set_modulation_offset(x, -0.4);
    binding.tick();
    CHECK(pad.has_modulated_value());
    CHECK(near(pad.modulated_display_x(), 0.5));
    CHECK(near(pad.modulated_display_y(), 0.1));
}

FRAMEWORK_TEST(a_combo_selection_is_a_value_index_and_one_gesture) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::ComboBox combo;
    combo.set_items({"a", "b", "c", "d", "e"});
    const auto mode = model.resolve("mode");
    auto binding = vellum::view::bind_parameter(combo, model, mode);
    CHECK(combo.selected() == 1);

    combo.set_selected(3);
    CHECK(near(host.normalized(mode), 0.75));
    CHECK(model.host_begins == 1);
    CHECK(model.host_ends == 1);
    CHECK(model.editor_values == 1);

    host.set_normalized(mode, 0.5);
    CHECK(combo.selected() == 2);
    CHECK(model.editor_values == 1);
    CHECK(model.host_begins == 1);
}

FRAMEWORK_TEST(a_combo_on_a_continuous_parameter_spreads_its_items) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::ComboBox combo;
    combo.set_items({"a", "b", "c", "d"});
    const auto depth = model.resolve("depth");
    auto binding = vellum::view::bind_parameter(combo, model, depth);
    CHECK(combo.selected() == 2);  // 0.5 of four items rounds to index 2

    combo.set_selected(1);
    CHECK(near(host.normalized(depth), 1.0 / 3.0));
}

FRAMEWORK_TEST(a_stepper_step_is_a_value_index_and_one_gesture) {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::DesignStepper stepper({"one", "two", "three"}, 0);
    const auto voices = model.resolve("voices");
    auto binding = vellum::view::bind_parameter(stepper, model, voices);
    CHECK(stepper.selected() == 0);

    stepper.on_select(2);
    CHECK(near(host.normalized(voices), 1.0));
    CHECK(model.host_begins == 1);
    CHECK(model.host_ends == 1);

    host.set_normalized(voices, 0.5);
    CHECK(stepper.selected() == 1);
    CHECK(model.editor_values == 1);
}

template <typename Control, typename Flip, typename IsOn>
void check_switch(Control& control, Flip flip, IsOn is_on) {
    auto host = make_host();
    RecordingModel model(host);
    const auto flag = model.resolve("flag");
    auto binding = vellum::view::bind_parameter(control, model, flag);
    CHECK(!is_on());

    flip(true);
    CHECK(near(host.normalized(flag), 1.0));
    CHECK(model.host_begins == 1);
    CHECK(model.host_ends == 1);
    flip(false);
    CHECK(near(host.normalized(flag), 0.0));
    CHECK(model.host_begins == 2);

    const int edits = model.editor_values;
    host.set_normalized(flag, 1.0);
    CHECK(is_on());
    host.set_normalized(flag, 0.0);
    CHECK(!is_on());
    CHECK(model.editor_values == edits);
    binding.tick();  // a switch shows no modulation; tick has nothing to do
}

FRAMEWORK_TEST(switches_flip_as_one_shot_gestures) {
    vellum::view::Toggle toggle;
    check_switch(
        toggle, [&](bool on) { toggle.set_on(on, vellum::view::Notify::sync); },
        [&] { return toggle.is_on(); });
    vellum::view::Checkbox box;
    check_switch(
        box, [&](bool on) { box.set_checked(on, vellum::view::Notify::sync); },
        [&] { return box.is_checked(); });
    vellum::view::ToggleButton button;
    check_switch(
        button, [&](bool on) { button.set_on(on, vellum::view::Notify::sync); },
        [&] { return button.is_on(); });
}

// A store with a host that counts gestures, the way a format adapter installs
// them.
struct RecordingStore {
    vellum::state::StateStore store;
    int host_begins = 0;
    int host_ends = 0;

    RecordingStore() {
        store.add_parameter({.id = 1, .name = "Cutoff", .range = {20.0f, 20000.0f, 1000.0f, 0.0f, 0.3f}});
        store.add_parameter({.id = 2, .name = "Depth", .range = {0.0f, 10.0f, 5.0f}});
        store.add_parameter({.id = 3, .name = "Mode", .range = {1.0f, 5.0f, 2.0f, 1.0f}});
        store.add_parameter({.id = 4, .name = "Spread", .range = {0.0f, 10.0f, 3.0f}});
        store.add_parameter({.id = 5, .name = "Flag", .range = {0.0f, 1.0f, 0.0f, 1.0f}});
        store.set_gesture_callbacks([this](vellum::state::ParamID) { ++host_begins; },
                                    [this](vellum::state::ParamID) { ++host_ends; });
    }
};

std::vector<std::string> info_labels(vellum::state::StateStore& store, vellum::state::ParamID id) {
    const auto* info = store.info(id);
    return info ? info->value_labels : std::vector<std::string>{};
}

FRAMEWORK_TEST(store_model_maps_through_the_parameter_range) {
    RecordingStore rs;
    vellum::view::StateStoreParamModel model(rs.store);
    const auto cutoff = model.resolve("Cutoff");
    CHECK(cutoff == model.handle(1));
    CHECK(!model.resolve("Missing").valid());
    CHECK(!model.handle(99).valid());
    const auto* info = rs.store.info(1);
    CHECK(near(model.normalized(cutoff), info->range.normalize(1000.0f)));
    CHECK(near(model.plain(cutoff, 0.5), info->range.denormalize(0.5f)));
    CHECK(near(model.normalized_from_plain(cutoff, 440.0), info->range.normalize(440.0f)));
    CHECK(model.value_count(model.resolve("Mode")) == 5);
    CHECK(model.value_count(cutoff) == 0);
    CHECK(model.info(cutoff).name == "Cutoff");

    // Gestures nest per handle and reach the host once.
    model.begin(cutoff);
    model.begin(cutoff);
    model.end(cutoff);
    CHECK(rs.host_begins == 1);
    CHECK(rs.host_ends == 0);
    model.end(cutoff);
    model.end(cutoff);
    CHECK(rs.host_ends == 1);
}

FRAMEWORK_TEST(store_overloads_keep_the_continuous_contract) {
    RecordingStore rs;
    auto& store = rs.store;
    vellum::view::Knob knob;
    knob.set_bounds({0.0f, 0.0f, 48.0f, 48.0f});
    vellum::view::Fader fader;
    fader.set_bounds({0.0f, 0.0f, 24.0f, 160.0f});
    vellum::view::RangeSlider slider;
    slider.set_bounds({0.0f, 0.0f, 200.0f, 24.0f});
    vellum::view::XYPad pad;
    pad.set_bounds({0.0f, 0.0f, 100.0f, 100.0f});
    auto knob_binding = vellum::view::bind_parameter(knob, store, 1);
    auto fader_binding = vellum::view::bind_parameter(fader, store, 2);
    auto slider_binding = vellum::view::bind_parameter(slider, store, 2);
    auto pad_binding = vellum::view::bind_parameter(pad, store, 1, 2);

    CHECK(near(knob.value(), store.get_normalized(1)));
    CHECK(near(fader.value(), 0.5));
    CHECK(near(slider.value(), 0.5));
    CHECK(near(pad.x_value(), store.get_normalized(1)));

    knob.simulate_drag({24.0f, 40.0f}, {24.0f, 4.0f}, 12);
    CHECK(rs.host_begins == 1);
    CHECK(rs.host_ends == 1);
    CHECK(near(store.get_normalized(1), knob.value()));
    CHECK(near(pad.x_value(), knob.value()));  // the pad hears the knob's edit

    store.set_value(2, 8.0f);
    store.pump_listeners();
    CHECK(near(fader.value(), 0.8));
    CHECK(near(slider.value(), 0.8));
    CHECK(near(pad.y_value(), 0.8));
    CHECK(rs.host_begins == 1);  // playback is not an edit

    pad.simulate_drag({20.0f, 20.0f}, {80.0f, 70.0f}, 8);
    CHECK(rs.host_begins == 3);
    CHECK(rs.host_ends == 3);

    // A store binding needs no tick; tick() leaves it alone.
    knob_binding.tick();
    fader_binding.tick();
    CHECK(near(store.get_normalized(1), knob.value()));
}

FRAMEWORK_TEST(store_overloads_replay_a_restore_without_an_edit) {
    RecordingStore rs;
    auto& store = rs.store;
    store.set_value(2, 7.0f);
    const auto saved = store.serialize();
    store.set_value(2, 1.0f);

    vellum::view::Fader fader;
    vellum::view::Knob knob;
    auto fader_binding = vellum::view::bind_parameter(fader, store, 2);
    auto knob_binding = vellum::view::bind_parameter(knob, store, 2);
    store.pump_listeners();
    CHECK(near(fader.value(), 0.1));

    CHECK(store.deserialize(saved));
    store.pump_listeners();
    store.reconcile_restore_listeners();
    CHECK(near(fader.value(), 0.7));
    CHECK(near(knob.value(), 0.7));
    CHECK(near(store.get_value(2), 7.0));
    CHECK(rs.host_begins == 0);
}

FRAMEWORK_TEST(discrete_store_overloads_keep_plain_index_values) {
    RecordingStore rs;
    auto& store = rs.store;
    vellum::view::ComboBox combo;
    combo.set_items({"zero", "one", "two", "three", "four", "five"});
    auto combo_binding = vellum::view::bind_parameter(combo, store, 3);
    CHECK(combo.selected() == 2);  // the plain value is the index
    combo.set_selected(4);
    CHECK(store.get_value(3) == 4.0f);
    store.set_value(3, 5.0f);
    store.pump_listeners();
    CHECK(combo.selected() == 5);

    vellum::view::DesignStepper stepper({"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}, 0);
    auto stepper_binding = vellum::view::bind_parameter(stepper, store, 4);
    CHECK(stepper.selected() == 3);
    for (int index = 0; index <= 10; ++index) {
        stepper.on_select(index);
        CHECK(store.get_value(4) == static_cast<float>(index));
    }
    store.set_value(4, 6.0f);
    store.pump_listeners();
    CHECK(stepper.selected() == 6);

    vellum::view::Toggle toggle;
    auto toggle_binding = vellum::view::bind_parameter(toggle, store, 5);
    toggle.set_on(true, vellum::view::Notify::sync);
    CHECK(store.get_value(5) == 1.0f);
    CHECK(rs.host_begins == 1 + 11 + 1);
    CHECK(rs.host_ends == rs.host_begins);
}

FRAMEWORK_TEST(a_store_binding_keeps_its_model_alive) {
    RecordingStore rs;
    auto& store = rs.store;
    vellum::view::Knob knob;
    vellum::view::Fader fader;
    vellum::view::RangeSlider slider;
    vellum::view::XYPad pad;
    std::vector<vellum::view::ParameterBinding> bindings;
    // The overloads build their StateStoreParamModel internally; nothing but
    // the binding holds it once the call returns.
    bindings.push_back(vellum::view::bind_parameter(knob, store, 2));
    bindings.push_back(vellum::view::bind_parameter(fader, store, 2));
    bindings.push_back(vellum::view::bind_parameter(slider, store, 2));
    bindings.push_back(vellum::view::bind_parameter(pad, store, 2, 4));
    bindings.reserve(64);  // moves every binding

    // Reuse freed memory, so a model the bindings did not keep is overwritten.
    std::vector<std::unique_ptr<vellum::view::StateStoreParamModel>> scribble;
    vellum::state::StateStore other;
    for (int i = 0; i < 64; ++i)
        scribble.push_back(std::make_unique<vellum::view::StateStoreParamModel>(other));

    store.set_value(2, 9.0f);
    store.set_value(4, 1.0f);
    store.pump_listeners();
    CHECK(near(knob.value(), 0.9));
    CHECK(near(fader.value(), 0.9));
    CHECK(near(slider.value(), 0.9));
    CHECK(near(pad.x_value(), 0.9));
    CHECK(near(pad.y_value(), 0.1));
}

FRAMEWORK_TEST(store_model_metadata_reports_the_param_info) {
    RecordingStore rs;
    vellum::view::StateStoreParamModel model(rs.store);
    auto* meta = vellum::value::metadata_of(model);
    CHECK(meta != nullptr);
    const auto* info = rs.store.info(1);
    const auto r = meta->range(model.handle(1));
    CHECK(r.plain_min == info->range.min);
    CHECK(r.plain_max == info->range.max);
    CHECK(r.plain_default == info->range.default_value);
    CHECK(r.step == info->range.step);
    CHECK(r.skew == info->range.skew);
    CHECK(r.symmetric_skew == info->range.symmetric_skew);
    CHECK(r.plain_values.empty());
    CHECK(meta->range(model.handle(3)).step == 1.0);
    CHECK(meta->range(vellum::value::ParamHandle{}).plain_max == 1.0);
    CHECK(meta->value_labels(model.handle(3)) == info_labels(rs.store, 3));
}

FRAMEWORK_TEST(store_model_plain_write_stores_what_set_value_stores) {
    RecordingStore through_model;
    RecordingStore direct;
    vellum::view::StateStoreParamModel model(through_model.store);
    auto& meta = *vellum::value::metadata_of(model);
    for (const vellum::state::ParamID id : {3u, 4u}) {
        for (int i = 0; i <= 100; ++i) {
            const float plain = static_cast<float>(i) * 0.1f;
            meta.set_plain_value(model.handle(id), plain);
            direct.store.set_value(id, plain);
            CHECK(through_model.store.get_value(id) == direct.store.get_value(id));
            CHECK(meta.plain_value(model.handle(id)) == direct.store.get_value(id));
        }
    }
}

FRAMEWORK_TEST(a_dropped_store_binding_leaves_its_widgets_writing_the_store) {
    RecordingStore rs;
    auto& store = rs.store;
    vellum::view::Knob knob;
    knob.set_bounds({0.0f, 0.0f, 48.0f, 48.0f});
    vellum::view::Fader fader;
    fader.set_bounds({0.0f, 0.0f, 24.0f, 160.0f});
    vellum::view::RangeSlider slider;
    slider.set_bounds({0.0f, 0.0f, 200.0f, 24.0f});
    vellum::view::XYPad pad;
    pad.set_bounds({0.0f, 0.0f, 100.0f, 100.0f});
    {
        // Bound, then the bindings go away while the widgets stay.
        auto a = vellum::view::bind_parameter(knob, store, 2);
        auto b = vellum::view::bind_parameter(fader, store, 4);
        auto c = vellum::view::bind_parameter(slider, store, 2);
        auto d = vellum::view::bind_parameter(pad, store, 2, 4);
    }
    // Reuse freed memory, so a callback reaching a freed model fails.
    std::vector<std::unique_ptr<vellum::view::StateStoreParamModel>> scribble;
    vellum::state::StateStore other;
    for (int i = 0; i < 64; ++i)
        scribble.push_back(std::make_unique<vellum::view::StateStoreParamModel>(other));

    // The widgets' callbacks still write the store, with balanced gestures,
    // as they did when the store overloads captured only the store.
    const float depth = store.get_value(2);
    knob.simulate_drag({24.0f, 40.0f}, {24.0f, 4.0f}, 8);
    CHECK(store.get_value(2) > depth);
    CHECK(rs.host_begins == 1);
    CHECK(rs.host_ends == 1);

    const float spread = store.get_value(4);
    fader.simulate_drag({12.0f, 120.0f}, {12.0f, 20.0f}, 8);
    CHECK(store.get_value(4) > spread);
    CHECK(rs.host_begins == 2);
    CHECK(rs.host_ends == 2);

    slider.simulate_drag({20.0f, 12.0f}, {180.0f, 12.0f}, 8);
    CHECK(rs.host_begins == 3);
    CHECK(rs.host_ends == 3);

    pad.simulate_drag({20.0f, 80.0f}, {80.0f, 20.0f}, 8);
    CHECK(rs.host_begins == 5);
    CHECK(rs.host_ends == 5);
    CHECK(near(store.get_normalized(2), pad.x_value()));
}

// A browser-captured fader with a declared static track: the panel capture
// beneath it already holds the authored track, so the live control paints
// only its cap. Rendered with and without the fader over the same opaque
// panel, the centre column may differ in exactly one short run (the cap, in
// the authored cap colour) at the value position. A stock track or value fill
// would differ along the whole column.
vellum::view::DesignIR authored_fader_panel(bool with_fader) {
    vellum::view::DesignIR ir;
    ir.root.type = "frame";
    ir.root.style.width = 24.0f;
    ir.root.style.height = 160.0f;
    if (!with_fader)
        return ir;
    vellum::view::IRNode fader;
    fader.type = "fader";
    fader.audio_widget = vellum::view::AudioWidgetType::fader;
    fader.style.width = 24.0f;
    fader.style.height = 160.0f;
    fader.audio_default = 0.5f;
    fader.attributes["pulpRouteId"] = "route:fader";
    fader.attributes["designed_body"] = "capture";
    fader.attributes["fader_body_includes_static_track"] = "1";
    fader.attributes["design_track"] = "#00ff00";
    fader.attributes["design_accent"] = "#0000ff";
    fader.attributes["fader_ind_color"] = "#ff0000ff";
    ir.root.children.push_back(std::move(fader));
    return ir;
}

std::vector<std::uint8_t> render_authored_fader_panel(bool with_fader) {
    auto root = vellum::view::build_native_view_tree(authored_fader_panel(with_fader), {});
    if (!root)
        return {};
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    auto rgba = vellum::view::render_to_rgba(*root, 24, 160, 1.0f, &width, &height);
    if (width != 24 || height != 160 || rgba.size() != 24u * 160u * 4u)
        return {};
    return rgba;
}

FRAMEWORK_TEST(authored_fader_overlay_does_not_paint_a_stock_track) {
    if (!vellum::view::raw_rgba_render_available())
        return;
    const auto panel = render_authored_fader_panel(false);
    const auto with_fader = render_authored_fader_panel(true);
    CHECK(!panel.empty());
    CHECK(!with_fader.empty());
    if (panel.empty() || with_fader.empty())
        return;
    const auto pixel = [](const std::vector<std::uint8_t>& rgba, std::uint32_t y) {
        return &rgba[(static_cast<std::size_t>(y) * 24u + 12u) * 4u];
    };
    int first = -1;
    int last = -1;
    int changed = 0;
    for (std::uint32_t y = 0; y < 160; ++y) {
        const auto* a = pixel(panel, y);
        const auto* b = pixel(with_fader, y);
        if (std::abs(a[0] - b[0]) + std::abs(a[1] - b[1]) + std::abs(a[2] - b[2]) <= 6)
            continue;
        ++changed;
        if (first < 0)
            first = static_cast<int>(y);
        last = static_cast<int>(y);
    }
    // Positive control: the cap is drawn, so the measurement can see paint.
    CHECK(changed > 0);
    if (changed == 0)
        return;
    // One contiguous run, short enough to be a cap and not a track or fill,
    // straddling the value position (0.5 of a 160px vertical fader).
    CHECK(changed == last - first + 1);
    CHECK(last - first + 1 <= 40);
    CHECK(first <= 80 && last >= 80);
    // The run is the authored cap colour, not a stock grey or the fill.
    const auto* cap = pixel(with_fader, static_cast<std::uint32_t>((first + last) / 2));
    CHECK(cap[0] > 200 && cap[1] < 60 && cap[2] < 60);
}

}  // namespace

int main() { return framework_test::run_all(); }
