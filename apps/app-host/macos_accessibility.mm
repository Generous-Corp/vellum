#import "macos_accessibility.hpp"

#import <Cocoa/Cocoa.h>

#include <algorithm>
#include <utility>

@interface VellumTextAccessibilityElement : NSAccessibilityElement
@property(nonatomic, copy) BOOL (^vellumValueHandler)(NSString*);
- (void)setInitialAccessibilityValue:(NSString*)value;
@end

@implementation VellumTextAccessibilityElement
- (void)setInitialAccessibilityValue:(NSString*)value {
    [super setAccessibilityValue:value];
}
- (void)setAccessibilityValue:(id)value {
    if (![value isKindOfClass:NSString.class] ||
        (self.vellumValueHandler != nil && !self.vellumValueHandler(value))) {
        return;
    }
    [super setAccessibilityValue:value];
}
@end

namespace vellum::app_host {

class MacAccessibilityBridge::Impl final {
public:
    Impl(NSView* owner, Action press, Action focus, ValueAction set_value)
        : owner_(owner),
          press_(std::move(press)),
          focus_(std::move(focus)),
          set_value_(std::move(set_value)) {}

    void sync(const std::vector<authoring::AccessibilityNode>& nodes) {
        NSView* owner = owner_;
        if (owner == nil) return;
        NSMutableArray<NSAccessibilityElement*>* elements =
            [NSMutableArray arrayWithCapacity:nodes.size()];
        for (const authoring::AccessibilityNode& semantic : nodes) {
            NSAccessibilityElement* element = semantic.role == "text-field"
                ? [[VellumTextAccessibilityElement alloc] init]
                : [[NSAccessibilityElement alloc] init];
            element.accessibilityParent = owner;
            NSString* role = NSAccessibilityGroupRole;
            if (semantic.role == "button") role = NSAccessibilityButtonRole;
            else if (semantic.role == "text-field") {
                role = NSAccessibilityTextFieldRole;
            } else if (semantic.role == "text") {
                role = NSAccessibilityStaticTextRole;
            } else if (semantic.role == "image") {
                role = NSAccessibilityImageRole;
            } else if (semantic.role == "list") {
                role = NSAccessibilityListRole;
            }
            element.accessibilityRole = role;
            element.accessibilityIdentifier =
                [NSString stringWithUTF8String:semantic.node_id.c_str()];
            element.accessibilityLabel =
                [NSString stringWithUTF8String:semantic.label.c_str()];
            NSString* semantic_value =
                [NSString stringWithUTF8String:semantic.value.c_str()];
            if ([element isKindOfClass:VellumTextAccessibilityElement.class]) {
                VellumTextAccessibilityElement* text_element =
                    static_cast<VellumTextAccessibilityElement*>(element);
                [text_element setInitialAccessibilityValue:semantic_value];
                const std::string node_id = semantic.node_id;
                ValueAction callback = set_value_;
                text_element.vellumValueHandler = ^BOOL(NSString* value) {
                    const char* utf8 = value.UTF8String;
                    return utf8 != nullptr && callback(node_id, utf8);
                };
            } else {
                element.accessibilityValue = semantic.state.has_checked
                    ? @(semantic.state.mixed ? NSControlStateValueMixed :
                        (semantic.state.checked ? NSControlStateValueOn :
                                                  NSControlStateValueOff))
                    : semantic_value;
            }
            element.accessibilityEnabled = !semantic.state.disabled;
            element.accessibilitySelected = semantic.state.selected;
            if (semantic.state.has_expanded) {
                element.accessibilityExpanded = semantic.state.expanded;
            }

            NSMutableArray<NSAccessibilityCustomAction*>* actions =
                [NSMutableArray array];
            if (std::find(
                    semantic.actions.begin(), semantic.actions.end(), "press") !=
                semantic.actions.end()) {
                const std::string node_id = semantic.node_id;
                Action callback = press_;
                [actions addObject:[[NSAccessibilityCustomAction alloc]
                    initWithName:@"Press"
                         handler:^BOOL(void) { return callback(node_id); }]];
            }
            if (std::find(
                    semantic.actions.begin(), semantic.actions.end(), "focus") !=
                semantic.actions.end()) {
                const std::string node_id = semantic.node_id;
                Action callback = focus_;
                [actions addObject:[[NSAccessibilityCustomAction alloc]
                    initWithName:@"Focus"
                         handler:^BOOL(void) { return callback(node_id); }]];
            }
            element.accessibilityCustomActions = actions;
            const NSRect local = NSMakeRect(
                semantic.bounds.x, semantic.bounds.y,
                semantic.bounds.width, semantic.bounds.height);
            if (owner.window != nil) {
                const NSRect window_rect = [owner convertRect:local toView:nil];
                element.accessibilityFrame =
                    [owner.window convertRectToScreen:window_rect];
            }
            [elements addObject:element];
        }
        elements_ = [elements copy];
        NSAccessibilityPostNotification(
            owner, NSAccessibilityLayoutChangedNotification);
    }

    NSArray* children() const { return elements_; }

private:
    __weak NSView* owner_;
    Action press_;
    Action focus_;
    ValueAction set_value_;
    NSArray<NSAccessibilityElement*>* elements_ = @[];
};

MacAccessibilityBridge::MacAccessibilityBridge(
    NSView* owner, Action press, Action focus, ValueAction set_value)
    : impl_(std::make_unique<Impl>(
          owner, std::move(press), std::move(focus), std::move(set_value))) {}

MacAccessibilityBridge::~MacAccessibilityBridge() = default;
MacAccessibilityBridge::MacAccessibilityBridge(MacAccessibilityBridge&&) noexcept =
    default;
MacAccessibilityBridge& MacAccessibilityBridge::operator=(
    MacAccessibilityBridge&&) noexcept = default;

void MacAccessibilityBridge::sync(
    const std::vector<authoring::AccessibilityNode>& nodes) {
    impl_->sync(nodes);
}

NSArray* MacAccessibilityBridge::children() const {
    return impl_->children();
}

}  // namespace vellum::app_host
