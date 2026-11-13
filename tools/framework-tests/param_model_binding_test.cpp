// Binds framework widgets to a vellum::value::ParamModel and checks the
// binding contract headlessly: no window, no GPU, no audio.

#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/view/parameter_binding.hpp>

#include <cmath>
#include <cstdio>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

int failures = 0;
int checks = 0;

#define CHECK(cond)                                                                    \
    do {                                                                               \
        ++checks;                                                                      \
        if (!(cond)) {                                                                 \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                                \
        }                                                                              \
    } while (false)

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
    return InMemoryParamModel(std::move(specs));
}

void place(vellum::view::Knob& knob) { knob.set_bounds({0.0f, 0.0f, 48.0f, 48.0f}); }

void knob_starts_at_the_model_value() {
    auto host = make_host();
    RecordingModel model(host);
    vellum::view::Knob knob;
    auto binding = vellum::view::bind_parameter(knob, model, model.resolve("cutoff"));
    CHECK(near(knob.value(), 0.25));
    CHECK(model.editor_values == 0);
    CHECK(model.host_begins == 0);
}

void a_drag_is_one_host_gesture() {
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

void a_host_change_moves_the_knob_without_an_edit() {
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

void modulation_is_shown_at_tick_and_cleared_when_gone() {
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

void destroying_the_binding_unbinds() {
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

void a_moved_binding_keeps_working() {
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

void an_unknown_key_binds_to_nothing() {
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

void a_store_binding_keeps_its_behaviour_and_tick_does_nothing() {
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

}  // namespace

int main() {
    knob_starts_at_the_model_value();
    a_drag_is_one_host_gesture();
    a_host_change_moves_the_knob_without_an_edit();
    modulation_is_shown_at_tick_and_cleared_when_gone();
    destroying_the_binding_unbinds();
    a_moved_binding_keeps_working();
    an_unknown_key_binds_to_nothing();
    a_store_binding_keeps_its_behaviour_and_tick_does_nothing();
    if (failures != 0) {
        std::fprintf(stderr, "%d of %d checks failed\n", failures, checks);
        return 1;
    }
    std::printf("param model binding: %d checks passed\n", checks);
    return 0;
}
