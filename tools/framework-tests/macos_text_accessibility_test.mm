// The macOS backend for painted-text accessibility nodes: each registered node
// becomes an NSAccessibilityElement with the role, label, value and selection a
// screen reader reads. Elements are queried directly; no window is shown.

#include "framework_test.hpp"

#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/text_accessibility.hpp>
#include <vellum/view/view.hpp>

#import <AppKit/AppKit.h>

#include <string>

// Debug lookups the backend exports for tests. Each returns a +1 retained
// element (or nil) that the caller must release.
extern "C" void* vellum_text_accessibility_lookup_macos(const char* id_utf8);
extern "C" void* vellum_text_accessibility_lookup_macos_for_owner(
    const void* owner, const char* id_utf8);

namespace {

using namespace vellum::view;

void clear_registry() {
    for (const auto& node : snapshot_accessibility_nodes())
        unregister_text_accessibility_node(node.id);
}

// This file is built without ARC: balance the lookup's +1 retain with the
// autorelease pool main() wraps around the tests.
NSAccessibilityElement* lookup(const std::string& id) {
    auto* el = (__bridge NSAccessibilityElement*)vellum_text_accessibility_lookup_macos(id.c_str());
    return [el autorelease];
}

NSAccessibilityElement* lookup_for_owner(const void* owner, const std::string& id) {
    auto* el = (__bridge NSAccessibilityElement*)
        vellum_text_accessibility_lookup_macos_for_owner(owner, id.c_str());
    return [el autorelease];
}

bool host_lists_label(NSView* view, NSString* label) {
    for (id child in [view accessibilityChildren]) {
        if ([child respondsToSelector:@selector(accessibilityLabel)] &&
            [[child accessibilityLabel] isEqualToString:label])
            return true;
    }
    return false;
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

// The lookup hands back its own retain, so the element outlives the registry
// entry it came from.
FRAMEWORK_TEST(lookup_result_survives_unregister) {
    clear_registry();
    register_text_accessibility_node(make_node("held", "kept", TextAccessibilityRole::Label));
    auto* el = (__bridge NSAccessibilityElement*)vellum_text_accessibility_lookup_macos("held");
    CHECK(el != nil);
    if (!el) return;
    unregister_text_accessibility_node("held");
    CHECK(lookup("held") == nil);
    CHECK([el.accessibilityLabel isEqualToString:@"kept"]);
    [el release];
}

// Owner-scoped registrations are keyed by owner, so the same id can exist for
// two owners and the process-wide lookup does not see either.
FRAMEWORK_TEST(owner_scoped_nodes_are_isolated_by_owner) {
    clear_registry();
    int owner_a = 0;
    int owner_b = 0;
    register_text_accessibility_node_for_owner(
        &owner_a, make_node("shared", "from a", TextAccessibilityRole::Label));
    register_text_accessibility_node_for_owner(
        &owner_b, make_node("shared", "from b", TextAccessibilityRole::Label));
    CHECK(lookup("shared") == nil);
    auto* a = lookup_for_owner(&owner_a, "shared");
    auto* b = lookup_for_owner(&owner_b, "shared");
    CHECK(a != nil);
    CHECK(b != nil);
    if (!a || !b) return;
    CHECK(a != b);
    CHECK([a.accessibilityLabel isEqualToString:@"from a"]);
    CHECK([b.accessibilityLabel isEqualToString:@"from b"]);
    CHECK(snapshot_accessibility_nodes_for_owner(&owner_a).size() == 1U);
    unregister_text_accessibility_node_for_owner(&owner_a, "shared");
    CHECK(lookup_for_owner(&owner_a, "shared") == nil);
    CHECK(lookup_for_owner(&owner_b, "shared") != nil);
    clear_registry();
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
        CHECK(host_lists_label(view, @"Hi VoiceOver"));
    }
    clear_registry();
}

// Text registered for one editor's root is listed by that editor only; text
// registered for another root stays out of its children.
FRAMEWORK_TEST(plugin_view_lists_only_its_own_owner_scoped_text) {
    clear_registry();
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        View other_root;
        PluginViewHost::Options options;
        options.size = {200, 100};
        options.use_gpu = false;
        auto host = PluginViewHost::create(root, options);
        CHECK(host != nullptr);
        if (!host) return;
        register_text_accessibility_node_for_owner(
            &root, make_node("mine", "Own text", TextAccessibilityRole::Label));
        register_text_accessibility_node_for_owner(
            &other_root, make_node("theirs", "Other text", TextAccessibilityRole::Label));
        NSView* view = (__bridge NSView*)host->native_handle();
        CHECK(view != nil);
        if (!view) return;
        CHECK(host_lists_label(view, @"Own text"));
        CHECK(!host_lists_label(view, @"Other text"));
    }
    clear_registry();
}

} // namespace

int main() {
    @autoreleasepool {
        return framework_test::run_all();
    }
}
