// Drives the framework's scripted widget bridge headlessly: a script builds
// native widgets, native input reaches the script's handlers, and a failing
// script reports its error and leaves the tree it already built.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>
#include <vellum/view/widgets.hpp>

#include <stdexcept>
#include <string>

namespace {

namespace view = vellum::view;

struct Harness {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    view::WidgetBridge bridge;

    Harness() : bridge(engine, root, store) { root.set_bounds({0, 0, 400, 300}); }

    std::string js_string(const std::string& expression) {
        return engine.evaluate("String(" + expression + ")").toString();
    }
};

FRAMEWORK_TEST(script_creates_a_label_and_sets_its_text) {
    Harness h;
    h.bridge.load_script("createLabel('title', 10, 10, 120, 20);"
                         "setText('title', 'hello');");

    CHECK(h.root.child_count() == 1);
    auto* label = dynamic_cast<view::Label*>(h.bridge.widget("title"));
    CHECK(label != nullptr);
    CHECK(label->parent() == &h.root);
    CHECK(label->text() == "hello");
    CHECK(h.js_string("getText('title')") == "hello");
}

FRAMEWORK_TEST(native_click_reaches_the_script_toggle_handler) {
    Harness h;
    h.bridge.load_script("globalThis.flips = [];"
                         "createToggle('mute', 20, 40, 60, 30);"
                         "on('mute', 'toggle', function (on) { flips.push(on); });");

    auto* toggle = dynamic_cast<view::Toggle*>(h.bridge.widget("mute"));
    CHECK(toggle != nullptr);
    CHECK(!toggle->is_on());

    h.root.simulate_click({50, 55});
    CHECK(toggle->is_on());
    CHECK(h.js_string("flips.join(',')") == "1");

    h.root.simulate_click({50, 55});
    CHECK(!toggle->is_on());
    CHECK(h.js_string("flips.join(',')") == "1,0");
}

FRAMEWORK_TEST(click_outside_the_toggle_does_not_reach_the_handler) {
    Harness h;
    h.bridge.load_script("globalThis.flips = [];"
                         "createToggle('mute', 20, 40, 60, 30);"
                         "on('mute', 'toggle', function (on) { flips.push(on); });");

    h.root.simulate_click({300, 250});
    CHECK(h.js_string("flips.length") == "0");
}

FRAMEWORK_TEST(failing_script_throws_and_keeps_the_built_tree) {
    Harness h;
    h.bridge.load_script("createLabel('kept', 0, 0, 50, 20);");

    bool threw = false;
    try {
        h.bridge.load_script("createLabel('partial', 0, 30, 50, 20); missingFunction();");
    } catch (const std::runtime_error& error) {
        threw = std::string(error.what()).find("missingFunction") != std::string::npos;
    }
    CHECK(threw);
    CHECK(h.bridge.widget("kept") != nullptr);
    CHECK(h.bridge.widget("partial") != nullptr);
    CHECK(h.root.child_count() == 2);
}

} // namespace

int main() { return framework_test::run_all(); }
