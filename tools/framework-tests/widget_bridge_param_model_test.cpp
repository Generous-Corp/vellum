// Drives the scripted widget bridge over a vellum::value::ParamModel: the
// parameter functions a script calls reach the model, and getParamMetadata
// finds the model's ParamMetadata. The model is created here, in the test
// executable, and the bridge that casts it to ParamMetadata lives in the
// framework's own library image, so that cast crosses images.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/value/param_metadata.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>
#include <vellum/view/widgets.hpp>

#include <cmath>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

namespace view = vellum::view;
namespace value = vellum::value;

value::InMemoryParamModel make_model() {
    std::vector<value::ParamSpec> specs;
    specs.push_back({.key = "cutoff", .unit = "Hz", .plain_min = 20.0, .plain_max = 20000.0,
                     .skew = 3.0, .default_normalized = 0.5});
    specs.push_back({.key = "mode", .default_normalized = 0.25, .value_count = 5,
                     .value_labels = {"a", "b", "c", "d", "e"}});
    specs.push_back({.key = "curve", .plain_min = 0.0, .plain_max = 100.0, .skew = 2.0,
                     .value_count = 3});
    return value::InMemoryParamModel(std::move(specs));
}

// A ParamModel with no ParamMetadata, forwarding to one that has it.
class PlainModel final : public value::ParamModel {
public:
    explicit PlainModel(value::InMemoryParamModel& inner) : inner_(inner) {}
    value::ParamHandle resolve(std::string_view key) override { return inner_.resolve(key); }
    double normalized(value::ParamHandle p) override { return inner_.normalized(p); }
    void set_normalized(value::ParamHandle p, double n) override { inner_.set_normalized(p, n); }
    void begin(value::ParamHandle p) override { inner_.begin(p); }
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

struct ModelHarness {
    view::ScriptEngine engine;
    view::View root;
    value::InMemoryParamModel model = make_model();
    view::WidgetBridge bridge;

    ModelHarness() : bridge(engine, root, model) { root.set_bounds({0, 0, 400, 300}); }

    std::string js(const std::string& expression) {
        return engine.evaluate("String(" + expression + ")").toString();
    }
    double number(const std::string& expression) {
        return engine.evaluate("Number(" + expression + ")").getWithDefault<double>(-1.0);
    }
};

bool near(double a, double b) { return std::abs(a - b) < 1e-6; }

FRAMEWORK_TEST(get_and_set_param_go_through_the_model) {
    ModelHarness h;
    const auto cutoff = h.model.resolve("cutoff");
    CHECK(near(h.number("getParam('cutoff')"), 0.5));
    h.bridge.load_script("setParam('cutoff', 0.75);");
    CHECK(near(h.model.normalized(cutoff), 0.75));
    h.model.set_normalized(cutoff, 0.2);
    CHECK(near(h.number("getParam('cutoff')"), 0.2));
    CHECK(near(h.number("getParam('missing')"), 0.0));
    h.bridge.load_script("setParam('missing', 0.9);");
    CHECK(near(h.model.normalized(cutoff), 0.2));
}

FRAMEWORK_TEST(format_and_parse_use_the_model_text) {
    ModelHarness h;
    CHECK(h.js("formatParamValue('mode', 0.5, true)") == "c");
    CHECK(h.js("parseParamValue('mode', 'd').ok") == "true");
    CHECK(near(h.number("parseParamValue('mode', 'd').normalized"), 0.75));
    CHECK(h.js("parseParamValue('mode', 'banana').ok") == "false");
    CHECK(h.js("formatParamValue('missing', 1)") == "undefined");
    CHECK(h.js("parseParamValue('missing', '1')") == "undefined");
}

FRAMEWORK_TEST(metadata_crosses_into_the_bridge_image) {
    ModelHarness h;
    // skew is reported only when the bridge's metadata_of() finds the model's
    // ParamMetadata, which is a dynamic_cast in the framework's image on an
    // object built in this one.
    CHECK(h.js("getParamMetadata('cutoff').skew") == "3");
    CHECK(near(h.number("getParamMetadata('cutoff').min"), 20.0));
    CHECK(near(h.number("getParamMetadata('cutoff').max"), 20000.0));
    CHECK(h.js("getParamMetadata('cutoff').unit") == "Hz");
    CHECK(h.js("getParamMetadata('mode').labels.join(',')") == "a,b,c,d,e");
    CHECK(h.js("getParamMetadata('mode').valueCount") == "5");
    CHECK(h.js("getParamMetadata('missing')") == "undefined");
    // An unevenly spaced discrete axis lists its values instead of a step.
    CHECK(h.js("getParamMetadata('curve').step") == "0");
    CHECK(h.js("getParamMetadata('curve').values.join(',')") == "0,25,100");
    CHECK(h.js("getParamMetadata('cutoff').values") == "undefined");
}

FRAMEWORK_TEST(a_model_without_metadata_reports_what_it_can) {
    view::ScriptEngine engine;
    view::View root;
    auto inner = make_model();
    PlainModel model(inner);
    CHECK(value::metadata_of(model) == nullptr);
    view::WidgetBridge bridge(engine, root, model);
    auto js = [&](const std::string& e) { return engine.evaluate("String(" + e + ")").toString(); };
    CHECK(js("getParamMetadata('cutoff').skew") == "undefined");
    CHECK(js("getParamMetadata('cutoff').max") == "20000");
    CHECK(js("getParamMetadata('mode').labels.join(',')") == "a,b,c,d,e");
}

FRAMEWORK_TEST(sync_from_store_shows_model_values_on_named_widgets) {
    ModelHarness h;
    h.bridge.load_script("createKnob('cutoff', 0, 0, 40, 40); createToggle('mode', 50, 0, 40, 20);");
    h.model.set_normalized(h.model.resolve("cutoff"), 0.6);
    h.model.set_normalized(h.model.resolve("mode"), 1.0);
    h.bridge.sync_from_store();
    auto* knob = dynamic_cast<view::Knob*>(h.bridge.widget("cutoff"));
    auto* toggle = dynamic_cast<view::Toggle*>(h.bridge.widget("mode"));
    CHECK(knob != nullptr && near(knob->value(), 0.6));
    CHECK(toggle != nullptr && toggle->is_on());
}

FRAMEWORK_TEST(a_store_bridge_keeps_the_store_metadata_and_text) {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    store.add_parameter({.id = 7, .name = "Gain", .unit = "dB", .range = {-60.0f, 12.0f, 0.0f}});
    view::WidgetBridge bridge(engine, root, store);
    auto js = [&](const std::string& e) { return engine.evaluate("String(" + e + ")").toString(); };
    CHECK(js("getParamMetadata('Gain').kind") != "undefined");  // the store's full metadata
    CHECK(js("getParamMetadata('Gain').id") == "7");
    // The store formats the plain value it is given, even outside the range.
    CHECK(js("formatParamValue('Gain', 99)").find("99") != std::string::npos);
    bridge.load_script("setParam('Gain', 0.5);");
    CHECK(std::abs(store.get_normalized(7) - 0.5f) < 1e-6f);
    CHECK(js("getParam('Gain')") == "0.5");
}

FRAMEWORK_TEST(a_model_bridge_refuses_store_only_bindings_loudly) {
    ModelHarness h;
    h.bridge.load_script("createKnob('k', 0, 0, 40, 40);");
    // These paths still read a StateStore; on a model they refuse rather than
    // bind nothing.
    CHECK(h.js("bindWidgetToParam('k', 'cutoff')") == "false");
    CHECK(h.js("bindMeter('k', 'cutoff')") == "false");
    CHECK(h.js("__subscribeParam__('cutoff')") == "0");
    CHECK(h.js("isWidgetParamBound('k')") == "false");
}

FRAMEWORK_TEST(a_store_bridge_ignores_a_nan_set_param) {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    store.add_parameter({.id = 7, .name = "Gain", .unit = "dB", .range = {-60.0f, 12.0f, 0.0f}});
    view::WidgetBridge bridge(engine, root, store);
    store.set_normalized(7, 0.25f);
    bridge.load_script("setParam('Gain', NaN);");
    CHECK(std::abs(store.get_normalized(7) - 0.25f) < 1e-6f);
}

FRAMEWORK_TEST(a_store_bridge_model_is_found_from_this_image) {
    // The store-built bridge creates its model inside the framework's image;
    // this image's metadata_of() finds the interface on it.
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    store.add_parameter({.id = 7, .name = "Gain", .range = {-60.0f, 12.0f, 0.0f}});
    view::WidgetBridge bridge(engine, root, store);
    auto* metadata = value::metadata_of(bridge.params());
    CHECK(metadata != nullptr);
    if (metadata != nullptr)
        CHECK(metadata->range(bridge.params().resolve("Gain")).plain_min == -60.0);
}

}  // namespace

int main() { return framework_test::run_all(); }
