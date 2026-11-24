// Drives the framework's scripted widget bridge headlessly: a script builds
// native widgets, native input reaches the script's handlers, and a failing
// script reports its error and leaves the tree it already built.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/pointer_dispatch.hpp>
#include <vellum/view/widget_bridge.hpp>
#include <vellum/view/widgets.hpp>

#include <memory>
#include <stdexcept>
#include <string>

namespace {

namespace view = vellum::view;

struct Harness {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    view::WidgetBridge bridge;

    explicit Harness(view::CapabilitySet capabilities = view::CapabilitySet::all())
        : bridge(engine, root, store, capabilities) { root.set_bounds({0, 0, 400, 300}); }

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

FRAMEWORK_TEST(sealed_font_api_is_available_without_filesystem) {
    Harness h(view::CapabilitySet{});
    h.bridge.load_script("globalThis.embeddedType = typeof registerEmbeddedFont; globalThis.pathType = typeof registerFont;");
    CHECK(h.js_string("embeddedType") == "function");
    CHECK(h.js_string("pathType") == "undefined");
    CHECK(h.js_string("registerEmbeddedFont('sealed-family', '/etc/hosts')") == "false");
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

FRAMEWORK_TEST(native_focus_transfer_notifies_target_and_ancestors) {
    view::View root;
    root.set_bounds({0, 0, 200, 100});
    auto child = std::make_unique<view::View>();
    child->set_id("focus-target");
    child->set_focusable(true);
    auto* target = child.get();
    root.add_child(std::move(child));
    int focus = 0;
    int focusin = 0;
    int blur = 0;
    int focusout = 0;
    target->on_focus_event = [&](bool gained, view::FocusCause) {
        gained ? ++focus : ++blur;
    };
    root.on_descendant_focus_event = [&](view::View&, bool gained, view::FocusCause) {
        gained ? ++focusin : ++focusout;
    };
    CHECK(view::transfer_input_focus(root, target, view::FocusCause::keyboard));
    CHECK(target->focus_visible());
    CHECK(focus == 1);
    CHECK(focusin == 1);
    CHECK(view::transfer_input_focus(root, nullptr, view::FocusCause::keyboard) == false);
    CHECK(blur == 1);
    CHECK(focusout == 1);
}

FRAMEWORK_TEST(native_focus_transfer_reaches_real_widget_bridge) {
    Harness h;
    h.bridge.load_script(
        "globalThis.focusSeen = []; globalThis.focusInSeen = [];"
        "globalThis.blurSeen = []; globalThis.focusOutSeen = [];"
        "var parent = document.createElement('div'); parent.id = 'focus-parent';"
        "var target = document.createElement('input'); target.id = 'focus-target';"
        "target.addEventListener('focus', function () {"
            "focusSeen.push({visible: target._focusVisible, cause: target._focusCause});"
        "});"
        "target.addEventListener('focusin', function () { focusInSeen.push(1); });"
        "target.addEventListener('blur', function () { blurSeen.push(1); });"
        "target.addEventListener('focusout', function () { focusOutSeen.push(1); });"
        "document.body.appendChild(parent); parent.appendChild(target);"
        "var style = document.createElement('style');"
        "style.textContent = '#focus-parent:focus-within { color: red; }';"
        "document.body.appendChild(style);");

    auto* target = h.bridge.widget(h.js_string("target._id"));
    auto* parent = h.bridge.widget(h.js_string("parent._id"));
    CHECK(target != nullptr);
    CHECK(parent != nullptr);

    CHECK(view::transfer_input_focus(h.root, target, view::FocusCause::keyboard));
    CHECK(h.js_string("focusSeen.length") == "1");
    CHECK(h.js_string("focusInSeen.length") == "1");
    CHECK(h.js_string("focusSeen[0].visible") == "true");
    CHECK(h.js_string("focusSeen[0].cause") == "keyboard");
    CHECK(h.js_string("document.querySelector('#focus-parent:focus-within') === parent") == "true");
    CHECK(h.js_string("document.getElementById('focus-parent').style.color") == "red");

    CHECK(!view::transfer_input_focus(h.root, nullptr, view::FocusCause::programmatic));
    CHECK(h.js_string("blurSeen.length") == "1");
    CHECK(h.js_string("focusOutSeen.length") == "1");
    CHECK(h.js_string("document.querySelector('#focus-parent:focus-within') === null") == "true");

    CHECK(view::transfer_input_focus(h.root, target, view::FocusCause::pointer));
    CHECK(h.js_string("focusSeen.length") == "2");
    CHECK(h.js_string("focusInSeen.length") == "2");
    CHECK(h.js_string("focusSeen[1].visible") == "false");
    CHECK(h.js_string("focusSeen[1].cause") == "pointer");
    CHECK(!view::transfer_input_focus(h.root, nullptr, view::FocusCause::pointer));
    CHECK(h.js_string("blurSeen.length") == "2");
    CHECK(h.js_string("focusOutSeen.length") == "2");
}

FRAMEWORK_TEST(element_focus_and_blur_cover_native_and_fallback_paths) {
    Harness h;
    h.bridge.load_script(
        "globalThis.nativeFocus = 0; globalThis.nativeBlur = 0;"
        "globalThis.fallbackFocus = 0; globalThis.fallbackBlur = 0;"
        "var nativeEl = document.createElement('input'); nativeEl.id = 'native-focus';"
        "nativeEl.addEventListener('focus', function () { nativeFocus++; });"
        "nativeEl.addEventListener('blur', function () { nativeBlur++; });"
        "var disabledEl = document.createElement('input'); disabledEl.id = 'disabled-focus';"
        "disabledEl.disabled = true;"
        "disabledEl.addEventListener('focus', function () { fallbackFocus++; });"
        "disabledEl.addEventListener('blur', function () { fallbackBlur++; });"
        "document.body.appendChild(nativeEl); document.body.appendChild(disabledEl);");

    h.engine.evaluate("nativeEl.focus()");
    CHECK(h.js_string("nativeFocus") == "1");
    h.engine.evaluate("nativeEl.blur()");
    CHECK(h.js_string("nativeBlur") == "1");

    h.engine.evaluate("disabledEl.focus()");
    CHECK(h.js_string("fallbackFocus") == "1");
    h.engine.evaluate("disabledEl.blur()");
    CHECK(h.js_string("fallbackBlur") == "1");
}

FRAMEWORK_TEST(disabled_focus_target_leaves_existing_focus_unchanged) {
    view::View root;
    root.set_bounds({0, 0, 200, 100});
    auto focused = std::make_unique<view::View>();
    focused->set_focusable(true);
    auto* focused_ptr = focused.get();
    auto disabled = std::make_unique<view::View>();
    disabled->set_focusable(true);
    disabled->set_enabled(false);
    auto* disabled_ptr = disabled.get();
    root.add_child(std::move(focused));
    root.add_child(std::move(disabled));

    CHECK(view::transfer_input_focus(root, focused_ptr));
    CHECK(view::focused_input_under_root(root) == focused_ptr);
    CHECK(view::transfer_input_focus(root, disabled_ptr));
    CHECK(view::focused_input_under_root(root) == focused_ptr);
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
