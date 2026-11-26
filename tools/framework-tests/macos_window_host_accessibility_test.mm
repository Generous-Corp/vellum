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

namespace {

using vellum::view::View;
using Role = View::AccessRole;

void add_named(View& parent, Role role, const char* label) {
    auto child = std::make_unique<View>();
    child->set_access_role(role);
    child->set_access_label(label);
    parent.add_child(std::move(child));
}

std::unique_ptr<vellum::view::WindowHost> make_host(View& root, const char* title) {
    vellum::view::WindowOptions options;
    options.title = title;
    options.width = 200;
    options.height = 100;
    options.use_gpu = true;
    options.initially_hidden = true;
    return vellum::view::WindowHost::create(root, options);
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

} // namespace

int main() { return framework_test::run_all(); }
