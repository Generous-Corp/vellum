#include "framework_test.hpp"

#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/view.hpp>

#include <X11/Xlib.h>

namespace {

using vellum::view::PluginViewHost;

FRAMEWORK_TEST(x11_child_attaches_resizes_and_maps) {
    Display* display = XOpenDisplay(nullptr);
    CHECK(display != nullptr);
    if (!display) return;

    const Window parent = XCreateSimpleWindow(display, DefaultRootWindow(display), 0, 0,
                                              640, 480, 0, 0, 0);
    XMapWindow(display, parent);
    XSync(display, False);

    vellum::view::View root;
    root.set_bounds({0, 0, 400, 300});
    PluginViewHost::Options options;
    options.size = {320, 240};
    auto host = PluginViewHost::create(root, options);
    CHECK(host != nullptr);
    if (!host) {
        XDestroyWindow(display, parent);
        XCloseDisplay(display);
        return;
    }

    host->attach_to_parent(reinterpret_cast<void*>(static_cast<uintptr_t>(parent)));
    CHECK(host->is_attached());
    CHECK(host->native_handle() != nullptr);
    host->set_size(500, 280);
    CHECK(host->get_size().width == 500);
    CHECK(host->get_size().height == 280);

    host->set_design_viewport(400, 300);
    float sx = 0, sy = 0, tx = 0, ty = 0;
    CHECK(host->design_viewport_transform(sx, sy, tx, ty));
    CHECK(sx > 0 && sy > 0);
    host->pump_x_events();
    host->detach();
    CHECK(!host->is_attached());

    host.reset();
    XDestroyWindow(display, parent);
    XCloseDisplay(display);
}

}  // namespace
