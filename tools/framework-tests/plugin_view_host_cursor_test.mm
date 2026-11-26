// The macOS plug-in view host decides the cursor from the Vellum tree only
// where Vellum content is painted. Over a native child (a WKWebView, any
// platform control) the child owns the cursor, so a buttonless move must leave
// it alone. The host view sits in a window that is never ordered in; mouse
// moves are synthesized and delivered straight to the view's own handler.

#include "framework_test.hpp"

#include <vellum/view/native_view_host.hpp>
#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/view.hpp>

#import <AppKit/AppKit.h>

#include <memory>

namespace {

using namespace vellum::view;

// A window-space mouse-moved event at a top-left-origin point of the host view.
NSEvent* moved_at(NSWindow* window, NSView* view, CGFloat x, CGFloat y_from_top) {
    const NSPoint in_view = NSMakePoint(x, view.bounds.size.height - y_from_top);
    const NSPoint in_window = [view convertPoint:in_view toView:nil];
    return [NSEvent mouseEventWithType:NSEventTypeMouseMoved
                              location:in_window
                         modifierFlags:0
                             timestamp:0
                          windowNumber:window.windowNumber
                               context:nil
                           eventNumber:0
                            clickCount:0
                              pressure:0];
}

FRAMEWORK_TEST(native_child_owns_the_cursor_and_vellum_content_sets_it) {
    View root;
    auto content = std::make_unique<View>();
    auto* content_ptr = content.get();
    content_ptr->set_cursor(View::CursorStyle::pointer);
    root.add_child(std::move(content));

    NSView* child = [[NSView alloc] initWithFrame:NSZeroRect];
    auto pane_owned = std::make_unique<NativeViewHost>();
    auto* pane = pane_owned.get();
    pane->set_native_child((__bridge void*)child);
    root.add_child(std::move(pane_owned));

    PluginViewHost::Options options;
    options.size = {400, 300};
    auto host = PluginViewHost::create(root, options);
    CHECK(host != nullptr);
    if (!host)
        return;
    root.set_bounds({0, 0, 400, 300});
    content_ptr->set_bounds({0, 0, 200, 300});
    pane->set_bounds({200, 0, 200, 300});
    pane->update_native_layout();
    CHECK(pane->is_native_attached());

    NSView* host_view = (__bridge NSView*)host->native_handle();
    NSWindow* window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 400, 300)
                                                   styleMask:NSWindowStyleMaskBorderless
                                                     backing:NSBackingStoreBuffered
                                                       defer:YES];
    window.releasedWhenClosed = NO;
    window.contentView = host_view;
    window.acceptsMouseMovedEvents = YES;
    CHECK(!window.visible);

    // Over Vellum content: the host publishes the content's cursor.
    [[NSCursor IBeamCursor] set];
    [host_view mouseMoved:moved_at(window, host_view, 100, 150)];
    CHECK(NSCursor.currentCursor == NSCursor.pointingHandCursor);

    // Over the native child: the host leaves the cursor to the child, even
    // though the Vellum tree has a pointer-cursor view beneath the same x.
    pane->set_cursor(View::CursorStyle::pointer);
    [[NSCursor IBeamCursor] set];
    [host_view mouseMoved:moved_at(window, host_view, 300, 150)];
    CHECK(NSCursor.currentCursor == NSCursor.IBeamCursor);

    window.contentView = [[NSView alloc] initWithFrame:NSZeroRect];
    root.set_plugin_view_host(nullptr);
    host.reset();
}

} // namespace

int main() {
    return framework_test::run_all();
}
