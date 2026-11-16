// Runs a scripted UI session over a vellum::value::ParamModel: its scripts
// reach the model, a reload rebuilds the bridge over the same model, and the
// reload probe reads the model without writing to it.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/value/param_metadata.hpp>
#include <vellum/view/scripted_ui.hpp>
#include <vellum/view/view.hpp>

#include <cmath>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

namespace view = vellum::view;
namespace value = vellum::value;

// Counts the writes and gestures that reach the model it wraps.
class CountingModel final : public value::ParamModel {
public:
    explicit CountingModel(value::InMemoryParamModel& inner) : inner_(inner) {}
    int writes = 0;
    int begins = 0;

    value::ParamHandle resolve(std::string_view key) override { return inner_.resolve(key); }
    double normalized(value::ParamHandle p) override { return inner_.normalized(p); }
    void set_normalized(value::ParamHandle p, double n) override {
        ++writes;
        inner_.set_normalized(p, n);
    }
    void begin(value::ParamHandle p) override {
        ++begins;
        inner_.begin(p);
    }
    void end(value::ParamHandle p) override { inner_.end(p); }
    std::string display_text(value::ParamHandle p, double n) override { return inner_.display_text(p, n); }
    int value_count(value::ParamHandle p) override { return inner_.value_count(p); }
    value::Subscription subscribe(Listener l) override { return inner_.subscribe(std::move(l)); }
    value::ParamInfo info(value::ParamHandle p) override { return inner_.info(p); }
    double plain(value::ParamHandle p, double n) override { return inner_.plain(p, n); }
    double normalized_from_plain(value::ParamHandle p, double v) override {
        return inner_.normalized_from_plain(p, v);
    }
    std::optional<double> parse_text(value::ParamHandle p, std::string_view t) override {
        return inner_.parse_text(p, t);
    }
    std::optional<double> modulated_normalized(value::ParamHandle p) override {
        return inner_.modulated_normalized(p);
    }

private:
    value::InMemoryParamModel& inner_;
};

value::InMemoryParamModel make_model() {
    std::vector<value::ParamSpec> specs;
    specs.push_back({.key = "cutoff", .default_normalized = 0.5});
    specs.push_back({.key = "wave", .plain_min = 0.0, .plain_max = 3.0, .default_normalized = 0.0,
                     .value_count = 4, .value_labels = {"sine", "tri", "saw", "square"}});
    return value::InMemoryParamModel(std::move(specs));
}

view::ScriptedUiOptions options_for(std::string source) {
    view::ScriptedUiOptions options;
    options.script_source = std::move(source);
    options.script_name = "panel.js";
    options.enable_hot_reload = false;
    options.enable_theme_reload = false;
    return options;
}

bool near(double a, double b) { return std::abs(a - b) < 1e-6; }

FRAMEWORK_TEST(a_model_session_runs_its_script_against_the_model) {
    auto inner = make_model();
    CountingModel model(inner);
    view::View root;
    root.set_bounds({0, 0, 300, 200});
    view::ScriptedUiSession session(
        root, model, options_for("createKnob('cutoff', 0, 0, 40, 40); setParam('cutoff', 0.75);"));
    std::string error;
    CHECK(session.load(&error));
    CHECK(error.empty());
    CHECK(near(inner.normalized(inner.resolve("cutoff")), 0.75));
    CHECK(model.writes == 1);
    CHECK(session.bridge() != nullptr);
    CHECK(&session.bridge()->params() == &model);
}

FRAMEWORK_TEST(a_model_session_reload_probes_without_writing) {
    auto inner = make_model();
    CountingModel model(inner);
    view::View root;
    root.set_bounds({0, 0, 300, 200});
    view::ScriptedUiSession session(
        root, model, options_for("createKnob('cutoff', 0, 0, 40, 40); setParam('cutoff', 0.75);"));
    std::string error;
    CHECK(session.load(&error));
    CHECK(model.writes == 1);

    const auto probes = session.probe_realm_evaluations();
    CHECK(session.probe_dropped_writes() == 0);
    CHECK(session.reload(&error));
    CHECK(error.empty());
    // The probe evaluated the script and its setParam reached the read-only
    // view, which dropped it; only the committed realm wrote.
    CHECK(session.probe_realm_evaluations() == probes + 1);
    CHECK(session.probe_dropped_writes() == 1);
    CHECK(model.writes == 2);
    CHECK(&session.bridge()->params() == &model);
}

FRAMEWORK_TEST(a_store_session_is_unchanged) {
    vellum::state::StateStore store;
    store.add_parameter({.id = 3, .name = "Gain", .range = {0.0f, 1.0f, 0.5f}});
    view::View root;
    root.set_bounds({0, 0, 300, 200});
    view::ScriptedUiSession session(root, store, options_for("setParam('Gain', 0.25);"));
    std::string error;
    CHECK(session.load(&error));
    CHECK(std::abs(store.get_normalized(3) - 0.25f) < 1e-6f);
    CHECK(session.reload(&error));
    CHECK(std::abs(store.get_normalized(3) - 0.25f) < 1e-6f);
    CHECK(session.probe_dropped_writes() == 0);
    CHECK(session.bridge() != nullptr);
    CHECK(value::metadata_of(session.bridge()->params()) != nullptr);
}

// A script that refuses to run unless getParamMetadata reports what the live
// realm reports for a model with (`with`) or without ParamMetadata. A reload
// runs it first in the probe, so a probe that saw different metadata would
// throw there and fail the reload.
std::string metadata_check_script(bool with_metadata) {
    std::string script =
        "const m = getParamMetadata('wave');"
        "if (m.labels.join(',') !== 'sine,tri,saw,square') throw new Error('labels ' + m.labels);";
    script += with_metadata
                  ? "if (m.skew !== 1 || m.step !== 1) throw new Error('metadata missing');"
                  : "if (m.skew !== undefined || m.step !== undefined) throw new Error('invented metadata');";
    return script;
}

FRAMEWORK_TEST(the_probe_sees_the_live_metadata_of_a_model_without_it) {
    auto inner = make_model();
    CountingModel model(inner);  // forwards, and provides no ParamMetadata
    CHECK(value::metadata_of(model) == nullptr);
    view::View root;
    root.set_bounds({0, 0, 300, 200});
    view::ScriptedUiSession session(root, model, options_for(metadata_check_script(false)));
    std::string error;
    CHECK(session.load(&error));
    CHECK(error.empty());
    CHECK(session.reload(&error));
    CHECK(error.empty());
}

FRAMEWORK_TEST(the_probe_sees_the_live_metadata_of_a_model_with_it) {
    auto model = make_model();
    view::View root;
    root.set_bounds({0, 0, 300, 200});
    view::ScriptedUiSession session(root, model, options_for(metadata_check_script(true)));
    std::string error;
    CHECK(session.load(&error));
    CHECK(error.empty());
    CHECK(session.reload(&error));
    CHECK(error.empty());
}

}  // namespace

int main() { return framework_test::run_all(); }
