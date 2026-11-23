// The SDL window host on a real X server: it creates a mapped window of the
// requested size, follows a content-size request, and delivers a pointer click
// that the X server (not the test) generated through the XTest extension.
// Run it under Xvfb (`xvfb-run`); it exits 77 (skipped) without a display.
//
// The loop is driven from the host's idle callback, so every step runs on the
// UI thread between frames. A watchdog ends the process if the loop never
// returns.
//
// `--control` runs the same scenario with the two actions left out: no resize
// request and no injected click. It passes only if the window keeps its size
// and no click arrives. That proves the geometry and click readings can come
// back unchanged, so the main run's changes come from the host and not from
// the measurement.

#include "framework_test.hpp"

#include <vellum/view/sdl_window_host.hpp>
#include <vellum/view/view.hpp>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <thread>

#if defined(__linux__) && !defined(__ANDROID__)

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/extensions/XTest.h>

namespace {

namespace view = vellum::view;

bool g_control = false;

struct ClickProbe : view::View {
    int ups = 0;
    void on_mouse_up(view::Point) override { ++ups; }
};

struct Geometry {
    unsigned width = 0;
    unsigned height = 0;
    bool viewable = false;
    bool ok = false;
};

Geometry read_geometry(Display* display, Window window) {
    XSync(display, False);
    Geometry g;
    XWindowAttributes attributes{};
    if (XGetWindowAttributes(display, window, &attributes) == 0)
        return g;
    g.width = static_cast<unsigned>(attributes.width);
    g.height = static_cast<unsigned>(attributes.height);
    g.viewable = attributes.map_state == IsViewable;
    g.ok = true;
    return g;
}

void send_close(Display* display, Window window) {
    XEvent event{};
    event.xclient.type = ClientMessage;
    event.xclient.window = window;
    event.xclient.message_type = XInternAtom(display, "WM_PROTOCOLS", False);
    event.xclient.format = 32;
    event.xclient.data.l[0] = static_cast<long>(XInternAtom(display, "WM_DELETE_WINDOW", False));
    event.xclient.data.l[1] = CurrentTime;
    XSendEvent(display, window, False, NoEventMask, &event);
    XFlush(display);
}

FRAMEWORK_TEST(window_is_created_resized_and_receives_a_server_click) {
    Display* display = XOpenDisplay(nullptr);
    CHECK(display != nullptr);
    if (!display)
        return;

    ClickProbe root;
    root.set_bounds({0, 0, 400, 300});

    view::SdlWindowHost::Options options;
    options.title = "vellum-linux-window-test";
    options.width = 400;
    options.height = 300;
    auto host = view::SdlWindowHost::create(root, options);
    CHECK(host != nullptr);
    if (!host) {
        XCloseDisplay(display);
        return;
    }

    constexpr unsigned kRequestedWidth = 520;
    constexpr unsigned kRequestedHeight = 380;
    Window window = 0;
    Geometry initial, resized;
    int ups_before_click = -1;
    int ticks = 0;

    host->set_idle_callback([&] {
        ++ticks;
        if (ticks == 4) {
            window = reinterpret_cast<Window>(host->native_content_view_handle());
            if (window != 0)
                initial = read_geometry(display, window);
        } else if (ticks == 5 && window != 0 && !g_control) {
            host->request_content_size(static_cast<float>(kRequestedWidth),
                                       static_cast<float>(kRequestedHeight));
        } else if (ticks == 14 && window != 0) {
            resized = read_geometry(display, window);
        } else if (ticks == 15 && window != 0) {
            ups_before_click = root.ups;
            if (!g_control) {
                int root_x = 0, root_y = 0;
                Window child = 0;
                XTranslateCoordinates(display, window, DefaultRootWindow(display), 100, 100, &root_x,
                                      &root_y, &child);
                XTestFakeMotionEvent(display, DefaultScreen(display), root_x, root_y, CurrentTime);
                XTestFakeButtonEvent(display, 1, True, CurrentTime);
                XTestFakeButtonEvent(display, 1, False, CurrentTime);
                XSync(display, False);
            }
        } else if (ticks == 26 && window != 0) {
            send_close(display, window);
        } else if (ticks > 60) {
            // The window never reached a handle or the close never arrived.
            if (window != 0)
                send_close(display, window);
        }
    });

    std::thread watchdog([] {
        std::this_thread::sleep_for(std::chrono::seconds(40));
        std::fprintf(stderr, "FAIL window test watchdog: the event loop never returned\n");
        std::_Exit(3);
    });
    watchdog.detach();

    host->run_event_loop();

    CHECK(window != 0);
    CHECK(initial.ok);
    CHECK(initial.viewable);
    CHECK(initial.width == options.width);
    CHECK(initial.height == options.height);
    CHECK(resized.ok);
    CHECK(ups_before_click == 0);
    if (g_control) {
        // Nothing was requested and nothing was injected: no change may appear.
        CHECK(resized.width == initial.width);
        CHECK(resized.height == initial.height);
        CHECK(root.ups == 0);
    } else {
        CHECK(resized.width == kRequestedWidth);
        CHECK(resized.height == kRequestedHeight);
        CHECK(root.ups == 1);
    }
    host.reset();
    XCloseDisplay(display);
}

} // namespace

int main(int argc, char** argv) {
    g_control = argc > 1 && std::strcmp(argv[1], "--control") == 0;
    if (const char* display = std::getenv("DISPLAY"); display == nullptr || *display == '\0') {
        std::printf("SKIP no X display (run under xvfb-run)\n");
        return 77;
    }
    Display* probe = XOpenDisplay(nullptr);
    if (!probe) {
        std::printf("SKIP the X display cannot be opened\n");
        return 77;
    }
    int event_base = 0, error_base = 0, major = 0, minor = 0;
    const bool has_xtest = XTestQueryExtension(probe, &event_base, &error_base, &major, &minor);
    XCloseDisplay(probe);
    if (!has_xtest) {
        std::printf("SKIP the X server has no XTest extension\n");
        return 77;
    }
    return framework_test::run_all();
}

#else

int main() {
    std::printf("SKIP the X11 window test is Linux only\n");
    return 77;
}

#endif
