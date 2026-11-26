// The macOS plug-in view host embeds a NativeViewHost's native child (a
// WKWebView, any platform control) as a subview of its own view: attached at
// the laid-out frame, moved with the layout, clipped, and taken out again when
// the tree leaves the host. No window is involved.

#include "framework_test.hpp"

#include <vellum/view/native_view_host.hpp>
#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/view.hpp>

#import <AppKit/AppKit.h>

#include <memory>

namespace {

using namespace vellum::view;

FRAMEWORK_TEST(native_child_follows_the_plugin_view_host) {
    NSView* child = [[NSView alloc] initWithFrame:NSZeroRect];
    View root;
    auto owned = std::make_unique<NativeViewHost>();
    auto* pane = owned.get();
    pane->flex().flex_grow = 1.0f;
    pane->set_native_child((__bridge void*)child);
    root.add_child(std::move(owned));

    PluginViewHost::Options options;
    options.size = {400, 300};
    auto host = PluginViewHost::create(root, options);
    CHECK(host != nullptr);
    if (!host)
        return;
    root.set_bounds({0, 0, 400, 300});
    root.layout_children_if_needed();
    pane->update_native_layout();

    NSView* host_view = (__bridge NSView*)host->native_handle();
    CHECK(pane->is_native_attached());
    CHECK(child.superview == host_view);
    CHECK(NSEqualRects(child.frame, NSMakeRect(0, 0, 400, 300)));

    // A layout change moves the child with it, in the host view's coordinates.
    pane->set_bounds({20, 10, 200, 100});
    pane->update_native_layout();
    const NSRect moved = child.frame;
    CHECK(moved.origin.x == 20);
    CHECK(moved.origin.y == (host_view.isFlipped ? 10 : 300 - 10 - 100));
    CHECK(moved.size.width == 200 && moved.size.height == 100);
    CHECK(host->set_native_child_view_clip((__bridge void*)child, true, 0, 0, 100, 50));
    CHECK(host->set_native_child_view_clip((__bridge void*)child, false, 0, 0, 0, 0));

    // Leaving the host takes the child out of the host's view.
    root.set_plugin_view_host(nullptr);
    CHECK(!pane->is_native_attached());
    CHECK(child.superview == nil);
    host.reset();
}

} // namespace

int main() {
    return framework_test::run_all();
}
