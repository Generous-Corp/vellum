#import <Cocoa/Cocoa.h>

#include "../macos_accessibility.hpp"

#include <string>

int main() {
    @autoreleasepool {
        NSView* owner = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 320, 100)];
        std::string pressed;
        std::string focused;
        std::string changed;
        vellum::app_host::MacAccessibilityBridge bridge{
            owner,
            [&](std::string_view node) {
                pressed = node;
                return true;
            },
            [&](std::string_view node) {
                focused = node;
                return true;
            },
            [&](std::string_view node, std::string_view value) {
                changed = std::string(node) + "=" + std::string(value);
                return true;
            },
        };
        vellum::authoring::AccessibilityNode node{
            .node_id = "title-input",
            .role = "text-field",
            .label = "Board title",
            .value = "Draft",
            .actions = {"focus", "set-value"},
            .bounds = {10, 10, 200, 44},
        };
        bridge.sync({node});
        NSArray* children = bridge.children();
        if (children.count != 1U) return 1;
        NSAccessibilityElement* element = children[0];
        if (![element.accessibilityRole isEqualToString:NSAccessibilityTextFieldRole] ||
            ![element.accessibilityIdentifier isEqualToString:@"title-input"] ||
            ![element.accessibilityLabel isEqualToString:@"Board title"] ||
            ![element.accessibilityValue isEqual:@"Draft"] ||
            element.accessibilityCustomActions.count != 1U ||
            ![element.accessibilityCustomActions[0].name isEqualToString:@"Focus"] ||
            element.accessibilityCustomActions[0].handler == nil ||
            !element.accessibilityCustomActions[0].handler()) {
            return 1;
        }
        if (focused != "title-input") return 1;
        element.accessibilityValue = @"Published";
        if (changed != "title-input=Published" ||
            ![element.accessibilityValue isEqual:@"Published"]) {
            return 1;
        }
        node.role = "button";
        node.label = "Save";
        node.value.clear();
        node.actions = {"press"};
        bridge.sync({node});
        element = bridge.children()[0];
        if (![element.accessibilityRole isEqualToString:NSAccessibilityButtonRole] ||
            element.accessibilityCustomActions.count != 1U ||
            element.accessibilityCustomActions[0].handler == nil ||
            !element.accessibilityCustomActions[0].handler() ||
            pressed != "title-input") {
            return 1;
        }
    }
    return 0;
}
