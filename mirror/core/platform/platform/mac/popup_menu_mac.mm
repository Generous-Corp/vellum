#include <pulp/platform/popup_menu.hpp>

#ifdef __APPLE__
#import <Cocoa/Cocoa.h>

namespace pulp::platform {

std::optional<int> PopupMenu::show(float x, float y) const {
    @autoreleasepool {
        NSMenu* menu = [[NSMenu alloc] initWithTitle:@""];
        [menu setAutoenablesItems:NO];

        for (auto& item : items_) {
            if (item.is_separator) {
                [menu addItem:[NSMenuItem separatorItem]];
            } else {
                NSMenuItem* mi = [[NSMenuItem alloc]
                    initWithTitle:[NSString stringWithUTF8String:item.label.c_str()]
                    action:nil
                    keyEquivalent:@""];
                [mi setTag:item.id];
                [mi setEnabled:item.enabled ? YES : NO];
                [mi setState:item.checked ? NSControlStateValueOn : NSControlStateValueOff];
                [menu addItem:mi];
            }
        }

        // Show at screen location using popUpMenuPositioningItem
        NSPoint location = NSMakePoint(x, [[NSScreen mainScreen] frame].size.height - y);
        BOOL shown = [menu popUpMenuPositioningItem:nil atLocation:location inView:nil];
        (void)shown;

        // NSMenu selection is handled via target-action; for simple use
        // the caller should set targets on items. Return nullopt for now.
        return std::nullopt;
    }
}

std::optional<int> PopupMenu::show_at_view(void* native_view_handle) const {
    @autoreleasepool {
        NSView* view = (__bridge NSView*)native_view_handle;
        if (!view) return std::nullopt;

        NSMenu* menu = [[NSMenu alloc] initWithTitle:@""];
        [menu setAutoenablesItems:NO];

        for (auto& item : items_) {
            if (item.is_separator) {
                [menu addItem:[NSMenuItem separatorItem]];
            } else {
                NSMenuItem* mi = [[NSMenuItem alloc]
                    initWithTitle:[NSString stringWithUTF8String:item.label.c_str()]
                    action:nil
                    keyEquivalent:@""];
                [mi setTag:item.id];
                [mi setEnabled:item.enabled ? YES : NO];
                [mi setState:item.checked ? NSControlStateValueOn : NSControlStateValueOff];
                [menu addItem:mi];
            }
        }

        NSPoint point = NSMakePoint(0, [view bounds].size.height);
        [menu popUpMenuPositioningItem:nil atLocation:point inView:view];

        return std::nullopt; // NSMenu handles selection via target-action
    }
}

} // namespace pulp::platform

#else

namespace pulp::platform {
std::optional<int> PopupMenu::show(float, float) const { return std::nullopt; }
std::optional<int> PopupMenu::show_at_view(void*) const { return std::nullopt; }
}

#endif
