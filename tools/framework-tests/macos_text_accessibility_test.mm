// The macOS backend for painted-text accessibility nodes: each registered node
// becomes an NSAccessibilityElement with the role, label, value and selection a
// screen reader reads. Elements are queried directly; no window is shown.

#include "framework_test.hpp"

#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/text_accessibility.hpp>
#include <vellum/view/view.hpp>

#import <AppKit/AppKit.h>

#include <string>

// Debug lookup the backend exports for tests.
extern "C" void* vellum_text_accessibility_lookup_macos(const char* id_utf8);

namespace {

using namespace vellum::view;

void clear_registry() {
    for (const auto& node : snapshot_accessibility_nodes())
        unregister_text_accessibility_node(node.id);
}

NSAccessibilityElement* lookup(const std::string& id) {
    return (__bridge NSAccessibilityElement*)vellum_text_accessibility_lookup_macos(id.c_str());
}

TextAccessibilityNode make_node(const char* id, const char* text, TextAccessibilityRole role) {
    TextAccessibilityNode node;
    node.id = id;
    node.text = text;
    node.role = role;
    return node;
}

FRAMEWORK_TEST(backend_reports_macos_ax) {
    CHECK(accessibility_backend_name() == "macos-ax");
}

FRAMEWORK_TEST(label_node_is_static_text_with_label_and_value) {
    clear_registry();
    register_text_accessibility_node(make_node("label", "Hello", TextAccessibilityRole::Label));
    auto* el = lookup("label");
    CHECK(el != nil);
    if (!el) return;
    CHECK([el.accessibilityRole isEqualToString:NSAccessibilityStaticTextRole]);
    CHECK([el.accessibilityLabel isEqualToString:@"Hello"]);
    CHECK([el.accessibilityValue isEqualToString:@"Hello"]);
    clear_registry();
}

FRAMEWORK_TEST(button_node_is_a_button) {
    clear_registry();
    register_text_accessibility_node(make_node("btn", "Save", TextAccessibilityRole::Button));
    auto* el = lookup("btn");
    CHECK(el != nil);
    if (!el) return;
    CHECK([el.accessibilityRole isEqualToString:NSAccessibilityButtonRole]);
    CHECK([el.accessibilityLabel isEqualToString:@"Save"]);
    clear_registry();
}

FRAMEWORK_TEST(text_editor_node_is_a_text_field) {
    clear_registry();
    register_text_accessibility_node(make_node("edit", "draft", TextAccessibilityRole::TextEditor));
    auto* el = lookup("edit");
    CHECK(el != nil);
    if (!el) return;
    CHECK([el.accessibilityRole isEqualToString:NSAccessibilityTextFieldRole]);
    clear_registry();
}

FRAMEWORK_TEST(ascii_selection_maps_byte_range_to_utf16_range) {
    clear_registry();
    auto node = make_node("sel", "hello world", TextAccessibilityRole::TextEditor);
    node.selection_start_utf8 = 6;
    node.selection_end_utf8 = 11;
    register_text_accessibility_node(node);
    auto* el = lookup("sel");
    CHECK(el != nil);
    if (!el) return;
    const NSRange range = [el accessibilitySelectedTextRange];
    CHECK(range.location == 6U);
    CHECK(range.length == 5U);
    clear_registry();
}

FRAMEWORK_TEST(multibyte_selection_uses_cluster_boundaries) {
    clear_registry();
    // The two-byte e-acute is one UTF-16 unit, so bytes 1..3 are the range {1, 1}.
    auto node = make_node("multi", "h\xC3\xA9llo", TextAccessibilityRole::TextEditor);
    node.cluster_boundaries_utf8 = {0, 1, 3, 4, 5, 6};
    node.cluster_boundaries_utf16 = {0, 1, 2, 3, 4, 5};
    node.selection_start_utf8 = 1;
    node.selection_end_utf8 = 3;
    register_text_accessibility_node(node);
    auto* el = lookup("multi");
    CHECK(el != nil);
    if (!el) return;
    const NSRange range = [el accessibilitySelectedTextRange];
    CHECK(range.location == 1U);
    CHECK(range.length == 1U);
    clear_registry();
}

FRAMEWORK_TEST(node_without_selection_reports_not_found) {
    clear_registry();
    register_text_accessibility_node(make_node("none", "static", TextAccessibilityRole::Label));
    auto* el = lookup("none");
    CHECK(el != nil);
    if (!el) return;
    const NSRange range = [el accessibilitySelectedTextRange];
    CHECK(range.location == NSNotFound);
    CHECK(range.length == 0U);
    clear_registry();
}

FRAMEWORK_TEST(unregister_drops_the_element_and_the_snapshot_entry) {
    clear_registry();
    register_text_accessibility_node(make_node("tmp", "ephemeral", TextAccessibilityRole::Label));
    CHECK(lookup("tmp") != nil);
    unregister_text_accessibility_node("tmp");
    CHECK(lookup("tmp") == nil);
    CHECK(snapshot_accessibility_nodes().empty());
}

FRAMEWORK_TEST(reregistering_an_id_keeps_the_element_identity) {
    clear_registry();
    register_text_accessibility_node(make_node("same", "old", TextAccessibilityRole::Label));
    auto* before = lookup("same");
    register_text_accessibility_node(make_node("same", "new", TextAccessibilityRole::Heading));
    auto* after = lookup("same");
    CHECK(before != nil);
    CHECK(before == after);
    if (!after) return;
    CHECK([after.accessibilityLabel isEqualToString:@"new"]);
    // A heading has no AppKit role of its own and reads as static text.
    CHECK([after.accessibilityRole isEqualToString:NSAccessibilityStaticTextRole]);
    clear_registry();
}

// The plug-in editor view merges the registry into its accessibility children,
// so a screen reader reaches text that was painted rather than built from views.
FRAMEWORK_TEST(plugin_view_surfaces_registered_text_to_voiceover) {
    clear_registry();
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        PluginViewHost::Options options;
        options.size = {200, 100};
        options.use_gpu = false;
        auto host = PluginViewHost::create(root, options);
        CHECK(host != nullptr);
        if (!host) return;
        register_text_accessibility_node(
            make_node("discoverable", "Hi VoiceOver", TextAccessibilityRole::Label));
        NSView* view = (__bridge NSView*)host->native_handle();
        CHECK(view != nil);
        if (!view) return;
        bool found = false;
        for (id child in [view accessibilityChildren]) {
            if ([child respondsToSelector:@selector(accessibilityLabel)] &&
                [[child accessibilityLabel] isEqualToString:@"Hi VoiceOver"])
                found = true;
        }
        CHECK(found);
    }
    clear_registry();
}

} // namespace

int main() { return framework_test::run_all(); }
