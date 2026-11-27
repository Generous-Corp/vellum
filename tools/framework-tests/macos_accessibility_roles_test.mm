// The macOS NSAccessibility role table and the window-host element tree, driven
// in process against an NSView that is never put in a window. Nothing here shows
// UI or starts VoiceOver: the elements are queried through the same
// NSAccessibility attributes an assistive client reads.

#include "framework_test.hpp"

#include <vellum/view/accessibility.hpp>
#include <vellum/view/buttons.hpp>
#include <vellum/view/platform/ns_role_mapping.hpp>
#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/text_editor.hpp>
#include <vellum/view/ui_components.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widgets.hpp>

#import <AppKit/AppKit.h>

#include <memory>
#include <vector>

namespace {

using vellum::view::View;
using vellum::view::ns_role_for_access_role;
using Role = View::AccessRole;

FRAMEWORK_TEST(roles_map_to_their_appkit_roles) {
    CHECK(ns_role_for_access_role(Role::slider) == NSAccessibilitySliderRole);
    CHECK(ns_role_for_access_role(Role::toggle) == NSAccessibilityCheckBoxRole);
    CHECK(ns_role_for_access_role(Role::label) == NSAccessibilityStaticTextRole);
    CHECK(ns_role_for_access_role(Role::group) == NSAccessibilityGroupRole);
    CHECK(ns_role_for_access_role(Role::image) == NSAccessibilityImageRole);
    CHECK(ns_role_for_access_role(Role::button) == NSAccessibilityButtonRole);
    CHECK(ns_role_for_access_role(Role::link) == NSAccessibilityLinkRole);
    CHECK(ns_role_for_access_role(Role::checkbox) == NSAccessibilityCheckBoxRole);
    CHECK(ns_role_for_access_role(Role::radio) == NSAccessibilityRadioButtonRole);
    CHECK(ns_role_for_access_role(Role::text_field) == NSAccessibilityTextFieldRole);
    CHECK(ns_role_for_access_role(Role::combo_box) == NSAccessibilityPopUpButtonRole);
    CHECK(ns_role_for_access_role(Role::list) == NSAccessibilityListRole);
    CHECK(ns_role_for_access_role(Role::list_item) == NSAccessibilityRowRole);
    CHECK(ns_role_for_access_role(Role::table) == NSAccessibilityTableRole);
    CHECK(ns_role_for_access_role(Role::row) == NSAccessibilityRowRole);
    CHECK(ns_role_for_access_role(Role::cell) == NSAccessibilityCellRole);
    CHECK(ns_role_for_access_role(Role::tab) == NSAccessibilityRadioButtonRole);
    CHECK(ns_role_for_access_role(Role::tab_list) == NSAccessibilityTabGroupRole);
    CHECK(ns_role_for_access_role(Role::menu) == NSAccessibilityMenuRole);
    CHECK(ns_role_for_access_role(Role::menu_item) == NSAccessibilityMenuItemRole);
    CHECK(ns_role_for_access_role(Role::progress_bar) == NSAccessibilityProgressIndicatorRole);
    CHECK(ns_role_for_access_role(Role::dialog) == NSAccessibilityGroupRole);
    CHECK(ns_role_for_access_role(Role::heading) == NSAccessibilityStaticTextRole);
    CHECK(ns_role_for_access_role(Role::scroll_bar) == NSAccessibilityScrollBarRole);
}

FRAMEWORK_TEST(every_announceable_role_has_a_known_role) {
    for (int i = 0; i <= static_cast<int>(Role::scroll_bar); ++i) {
        const auto role = static_cast<Role>(i);
        if (role == Role::none) continue;
        CHECK(!(ns_role_for_access_role(role) == NSAccessibilityUnknownRole));
    }
}

FRAMEWORK_TEST(widgets_announce_the_role_a_listener_expects) {
    vellum::view::TextButton button{"Play"};
    CHECK(ns_role_for_access_role(button.access_role()) == NSAccessibilityButtonRole);
    vellum::view::ComboBox combo;
    CHECK(ns_role_for_access_role(combo.access_role()) == NSAccessibilityPopUpButtonRole);
    vellum::view::TextEditor editor;
    CHECK(ns_role_for_access_role(editor.access_role()) == NSAccessibilityTextFieldRole);
    vellum::view::HyperlinkButton link{"Docs", "https://example.invalid"};
    CHECK(ns_role_for_access_role(link.access_role()) == NSAccessibilityLinkRole);
}

FRAMEWORK_TEST(meter_and_progress_bar_are_distinct_indicators) {
    CHECK(ns_role_for_access_role(Role::meter) == NSAccessibilityLevelIndicatorRole);
    CHECK(ns_role_for_access_role(Role::progress_bar) == NSAccessibilityProgressIndicatorRole);
}

using vellum::view::PluginViewHost;

// The plug-in editor view for a root, never attached to a window. The window
// elements are reached the way an assistive client reaches them, through the
// view's accessibilityChildren.
NSView* editor_view(PluginViewHost& host) { return (__bridge NSView*)host.native_handle(); }

std::unique_ptr<View> add_named(View& parent, Role role, const char* label) {
    auto child = std::make_unique<View>();
    child->set_access_role(role);
    if (label) child->set_access_label(label);
    parent.add_child(std::move(child));
    return nullptr;
}

// A tree with one named button, one nameless button, a checked checkbox, an
// aria-hidden label and a disabled named slider, read back through the
// elements the editor view hands to AppKit.
FRAMEWORK_TEST(element_tree_exposes_roles_labels_and_state) {
    @autoreleasepool {
        View root;
        add_named(root, Role::button, "Save");
        add_named(root, Role::button, nullptr);  // nameless: not an element
        add_named(root, Role::checkbox, "Bypass");
        add_named(root, Role::label, "Secret");
        add_named(root, Role::slider, "Gain");
        root.child_at(2)->set_access_checked("true");
        root.child_at(3)->set_access_hidden("true");
        root.child_at(4)->set_access_value("0.5");
        root.child_at(4)->set_access_disabled("true");

        auto host = PluginViewHost::create(root, {.size = {200, 100}});
        CHECK(host != nullptr);
        if (!host) return;
        NSArray* elements = [editor_view(*host) accessibilityChildren];
        // The nameless button is not an element, and the editor view leaves
        // the aria-hidden label out of the tree a screen reader reads.
        CHECK(elements.count == 3U);
        for (NSAccessibilityElement* el in elements)
            CHECK(![el.accessibilityLabel isEqualToString:@"Secret"]);
        if (elements.count != 3U) return;

        NSAccessibilityElement* button = elements[0];
        CHECK([button.accessibilityRole isEqualToString:NSAccessibilityButtonRole]);
        CHECK([button.accessibilityLabel isEqualToString:@"Save"]);
        CHECK([button isAccessibilityElement]);

        NSAccessibilityElement* checkbox = elements[1];
        CHECK([checkbox.accessibilityRole isEqualToString:NSAccessibilityCheckBoxRole]);
        CHECK([checkbox.accessibilityValue isEqual:@YES]);

        NSAccessibilityElement* slider = elements[2];
        CHECK([slider.accessibilityRole isEqualToString:NSAccessibilitySliderRole]);
        CHECK([slider.accessibilityValue isEqual:@"0.5"]);
        CHECK(![slider isAccessibilityEnabled]);
    }
}

// An element that outlives the view it described reports Unknown instead of
// reading freed memory.
FRAMEWORK_TEST(element_survives_removal_of_its_view) {
    @autoreleasepool {
        View root;
        add_named(root, Role::button, "Gone");
        View* button = root.child_at(0);
        auto host = PluginViewHost::create(root, {.size = {50, 50}});
        CHECK(host != nullptr);
        if (!host) return;
        NSArray* elements = [editor_view(*host) accessibilityChildren];
        CHECK(elements.count == 1U);
        if (elements.count != 1U) return;
        NSAccessibilityElement* el = elements[0];
        CHECK([el.accessibilityRole isEqualToString:NSAccessibilityButtonRole]);
        root.remove_child(button);
        CHECK([el.accessibilityRole isEqualToString:NSAccessibilityUnknownRole]);
        CHECK(![el isAccessibilityElement]);
    }
}

// The plug-in editor view is one group whose children are the view tree's
// accessible elements. A hidden panel's contents must stay out of it.
FRAMEWORK_TEST(plugin_editor_view_exposes_its_visible_tree) {
    @autoreleasepool {
        View root;
        auto named = [](const char* label) {
            auto view = std::make_unique<View>();
            view->set_access_role(Role::button);
            view->set_access_label(label);
            return view;
        };
        root.add_child(named("Shown"));
        auto panel = std::make_unique<View>();
        panel->add_child(named("Inside hidden panel"));
        panel->set_visible(false);
        root.add_child(std::move(panel));
        auto host = PluginViewHost::create(root, {.size = {200, 100}});
        CHECK(host != nullptr);
        if (!host) return;
        NSView* view = (__bridge NSView*)host->native_handle();
        CHECK(view != nil);
        if (!view) return;
        CHECK([view isAccessibilityElement]);
        CHECK([view.accessibilityRole isEqualToString:NSAccessibilityGroupRole]);
        NSArray* children = [view accessibilityChildren];
        CHECK(children.count == 1U);
        if (children.count != 1U) return;
        CHECK([[children[0] accessibilityLabel] isEqualToString:@"Shown"]);
        CHECK([children[0] accessibilityParent] == view);
    }
}

} // namespace

int main() { return framework_test::run_all(); }
