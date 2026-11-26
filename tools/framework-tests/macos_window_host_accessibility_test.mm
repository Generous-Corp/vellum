// The standalone window host's content view in the accessibility tree: one group
// whose children are the view tree's accessible elements and the registered
// text elements. The window is created but never shown or ordered in, and the
// elements are read through the same NSAccessibility attributes an assistive
// client reads.

#include "framework_test.hpp"

#include <vellum/view/text_accessibility.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/window_host.hpp>

#import <AppKit/AppKit.h>

#include <memory>
#include <string>

extern "C" NSArray* vellum_text_accessibility_elements_for_host_macos(const void* host);

namespace {

using vellum::view::View;
using Role = View::AccessRole;

void add_named(View& parent, Role role, const char* label) {
    auto child = std::make_unique<View>();
    child->set_access_role(role);
    child->set_access_label(label);
    parent.add_child(std::move(child));
}

std::unique_ptr<vellum::view::WindowHost> make_host(View& root, const char* title, bool gpu = true) {
    vellum::view::WindowOptions options;
    options.title = title;
    options.width = 200;
    options.height = 100;
    options.use_gpu = gpu;
    options.initially_hidden = true;
    return vellum::view::WindowHost::create(root, options);
}

FRAMEWORK_TEST(cpu_window_content_view_exposes_its_accessibility_tree) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        add_named(root, Role::button, "CPU button");
        auto host = make_host(root, "CPU window", false);
        CHECK(host != nullptr);
        if (!host) return;
        NSView* view = (__bridge NSView*)host->native_content_view_handle();
        CHECK(view != nil);
        if (!view) return;
        CHECK([view isAccessibilityElement]);
        CHECK([view.accessibilityRole isEqualToString:NSAccessibilityGroupRole]);
        CHECK([view.accessibilityLabel isEqualToString:@"CPU window"]);
        NSArray* children = [view accessibilityChildren];
        CHECK(children.count == 1U);
        if (children.count == 1U) {
            CHECK([[children[0] accessibilityLabel] isEqualToString:@"CPU button"]);
            CHECK([children[0] accessibilityParent] == view);
        }
    }
}

FRAMEWORK_TEST(window_content_view_is_a_group_named_for_the_window) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        auto host = make_host(root, "Rack");
        CHECK(host != nullptr);
        if (!host) return;
        NSView* view = (__bridge NSView*)host->native_content_view_handle();
        CHECK(view != nil);
        if (!view) return;
        CHECK(!((NSWindow*)view.window).visible);  // never shown
        CHECK([view isAccessibilityElement]);
        CHECK([view.accessibilityRole isEqualToString:NSAccessibilityGroupRole]);
        CHECK([view.accessibilityLabel isEqualToString:@"Rack"]);
    }
}

FRAMEWORK_TEST(window_content_view_exposes_roles_labels_and_visibility) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        add_named(root, Role::button, "Save");
        auto panel = std::make_unique<View>();
        add_named(*panel, Role::checkbox, "Inside hidden panel");
        panel->set_visible(false);
        root.add_child(std::move(panel));
        auto host = make_host(root, "Tree");
        CHECK(host != nullptr);
        if (!host) return;
        NSView* view = (__bridge NSView*)host->native_content_view_handle();
        if (!view) return;
        NSArray* children = [view accessibilityChildren];
        CHECK(children.count == 1U);
        if (children.count != 1U) return;
        id button = children[0];
        CHECK([[button accessibilityRole] isEqualToString:NSAccessibilityButtonRole]);
        CHECK([[button accessibilityLabel] isEqualToString:@"Save"]);
        CHECK([button accessibilityParent] == view);
    }
}

FRAMEWORK_TEST(accessibility_hidden_ancestors_hide_their_descendants) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        auto panel = std::make_unique<View>();
        panel->set_access_hidden("true");
        add_named(*panel, Role::button, "Hidden descendant");
        root.add_child(std::move(panel));
        add_named(root, Role::button, "Visible sibling");
        auto host = make_host(root, "Hidden tree");
        CHECK(host != nullptr);
        if (!host) return;
        NSView* view = (__bridge NSView*)host->native_content_view_handle();
        CHECK(view != nil);
        if (!view) return;
        NSArray* children = [view accessibilityChildren];
        CHECK(children.count == 1U);
        if (children.count == 1U)
            CHECK([[children[0] accessibilityLabel] isEqualToString:@"Visible sibling"]);
        vellum::view::TextAccessibilityNode node;
        node.id = "hidden-root-text";
        node.text = "Painted text";
        node.host_handle = (__bridge void*)view;
        vellum::view::register_text_accessibility_node(node);
        CHECK([view accessibilityChildren].count == 2U);
        root.set_access_hidden("true");
        CHECK([view accessibilityChildren].count == 0U);
        vellum::view::unregister_text_accessibility_node(node.id);
    }
}

FRAMEWORK_TEST(window_content_view_reaches_painted_text) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        View root;
        auto host = make_host(root, "Text");
        CHECK(host != nullptr);
        if (!host) return;
        NSView* view = (__bridge NSView*)host->native_content_view_handle();
        if (!view) return;
        vellum::view::TextAccessibilityNode node;
        node.id = "window-host-text";
        node.host_handle = (__bridge void*)view;
        node.text = "Reachable";
        node.role = vellum::view::TextAccessibilityRole::Label;
        vellum::view::register_text_accessibility_node(node);
        bool found = false;
        for (id child in [view accessibilityChildren])
            if ([[child accessibilityLabel] isEqualToString:@"Reachable"]) found = true;
        CHECK(found);
        vellum::view::unregister_text_accessibility_node("window-host-text");
    }
}

FRAMEWORK_TEST(painted_text_is_owned_by_one_host) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        View first_root, second_root;
        auto first = make_host(first_root, "First");
        auto second = make_host(second_root, "Second");
        CHECK(first != nullptr);
        CHECK(second != nullptr);
        if (!first || !second) return;
        NSView* first_view = (__bridge NSView*)first->native_content_view_handle();
        NSView* second_view = (__bridge NSView*)second->native_content_view_handle();
        CHECK(first_view != nil);
        CHECK(second_view != nil);
        if (!first_view || !second_view) return;
        vellum::view::TextAccessibilityNode a, b, unowned;
        a.id = "first-host-text"; a.text = "First text"; a.host_handle = (__bridge void*)first_view;
        b.id = "second-host-text"; b.text = "Second text"; b.host_handle = (__bridge void*)second_view;
        unowned.id = "unowned-text"; unowned.text = "Unowned text";
        vellum::view::register_text_accessibility_node(a);
        vellum::view::register_text_accessibility_node(b);
        vellum::view::register_text_accessibility_node(unowned);
        NSArray* first_children = [first_view accessibilityChildren];
        CHECK(first_children.count == 1U);
        id first_element = first_children.count == 1U ? first_children[0] : nil;
        CHECK([[first_element accessibilityLabel] isEqualToString:@"First text"]);
        NSArray* second_children = [second_view accessibilityChildren];
        CHECK(second_children.count == 1U);
        id second_element = second_children.count == 1U ? second_children[0] : nil;
        CHECK([[second_element accessibilityLabel] isEqualToString:@"Second text"]);
        CHECK([first_element accessibilityParent] == first_view);
        CHECK([second_element accessibilityParent] == second_view);
        vellum::view::unregister_text_accessibility_node(a.id);
        vellum::view::unregister_text_accessibility_node(b.id);
        vellum::view::unregister_text_accessibility_node(unowned.id);
    }
}

FRAMEWORK_TEST(painted_text_snapshot_survives_removal_and_owner_transfer) {
    @autoreleasepool {
        int first_host = 0, second_host = 0;
        vellum::view::TextAccessibilityNode node;
        node.id = "transferred-text";
        node.text = "Original";
        node.host_handle = &first_host;
        vellum::view::register_text_accessibility_node(node);
        NSArray* first = vellum_text_accessibility_elements_for_host_macos(&first_host);
        CHECK(first.count == 1U);
        node.host_handle = &second_host;
        node.text = "Transferred";
        vellum::view::register_text_accessibility_node(node);
        NSArray* second = vellum_text_accessibility_elements_for_host_macos(&second_host);
        CHECK(second.count == 1U);
        CHECK(vellum_text_accessibility_elements_for_host_macos(&first_host).count == 0U);
        if (first.count == 1U && second.count == 1U) {
            CHECK(first[0] != second[0]);
            CHECK([[first[0] accessibilityLabel] isEqualToString:@"Original"]);
            CHECK([[second[0] accessibilityLabel] isEqualToString:@"Transferred"]);
        }
        vellum::view::unregister_text_accessibility_node(node.id);
        if (second.count == 1U)
            CHECK([[second[0] accessibilityLabel] isEqualToString:@"Transferred"]);
    }
}

} // namespace

int main() { return framework_test::run_all(); }
