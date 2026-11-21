// An installed consumer of the plug-in editor host, built without
// VELLUM_MIRROR_BUILD. A plug-in editor includes plugin_view_host.hpp, which
// names the GPU device type through the forward declaration in
// platform/gpu_fwd.hpp; that forward declaration is part of the installed
// mirror's supported surface (gpu_surface_guard.py).
//
// The host subclasses PluginViewHost, so the run links and calls the installed
// destructor, observe_gpu_surface() and publish_gpu_surface(), the members
// whose signatures carry the GpuSurface pointer.

#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/view/input_events.hpp>
#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/view.hpp>
#import <Cocoa/Cocoa.h>

#include <cstdio>
#include <cstdlib>
#include <string>
#include <string_view>
#include <vector>

namespace {

using Host = vellum::view::PluginViewHost;

class StubHost final : public Host {
public:
    StubHost() { mark_gpu_surface_pending(); }

    vellum::view::NativeViewHandle native_handle() override { return nullptr; }
    void attach_to_parent(vellum::view::NativeViewHandle) override {}
    void detach() override {}
    void repaint() override { ++repaints; }
    void set_size(uint32_t width, uint32_t height) override { size_ = {width, height}; }
    Size get_size() const override { return size_; }

    void give_up_on_gpu() { publish_gpu_surface(nullptr, GpuSurfaceState::unavailable); }

    int repaints = 0;

private:
    Size size_{};
};

int fail(const char* what) {
    std::fprintf(stderr, "plugin view host consumer: %s\n", what);
    return 1;
}

bool png_pixel_matches(const std::vector<uint8_t>& png, NSInteger x, NSInteger y,
                       uint32_t rgb, NSInteger logical_width = 0) {
    // Compare the raw samples the host wrote: converting through NSColor
    // colour-manages them and shifts every channel.
    // A negative coordinate counts back from the far edge, so a corner probe
    // stays outside the 32-point View paint at any backing scale (a 2x
    // surface doubles the painted rect to 64 pixels).
    NSData* data = [NSData dataWithBytes:png.data() length:png.size()];
    NSBitmapImageRep* image = [[NSBitmapImageRep alloc] initWithData:data];
    if (image && logical_width > 0) {
        x = x * image.pixelsWide / logical_width;
        y = y * image.pixelsWide / logical_width;
    }
    if (image && x < 0) x += image.pixelsWide;
    if (image && y < 0) y += image.pixelsHigh;
    if (!image || x < 0 || y < 0 || x >= image.pixelsWide || y >= image.pixelsHigh || image.bitsPerSample != 8 ||
        image.samplesPerPixel < 3)
        return false;
    NSUInteger px[5] = {};
    [image getPixel:px atX:x y:y];
    const NSUInteger want[3] = {(rgb >> 16) & 0xff, (rgb >> 8) & 0xff, rgb & 0xff};
    bool ok = true;
    for (int i = 0; i < 3; ++i)
        ok = ok && (px[i] > want[i] ? px[i] - want[i] : want[i] - px[i]) <= 1;
    if (!ok)
        std::fprintf(stderr, "pixel (%ld,%ld) is %lu,%lu,%lu; wanted %lu,%lu,%lu\n", (long)x,
                     (long)y, (unsigned long)px[0], (unsigned long)px[1], (unsigned long)px[2],
                     (unsigned long)want[0], (unsigned long)want[1], (unsigned long)want[2]);
    return ok;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 1 && !(argc == 2 && std::string_view(argv[1]) == "--live-gpu"))
        return fail("usage: vellum-plugin-view-host-consumer [--live-gpu]");
    const bool live_gpu = argc == 2;
    if (std::getenv("VELLUM_MIRROR_PLUGIN_VIEW_HOST_BREAK"))
        return fail("planted break");
    struct Probe final : vellum::view::View {
        void paint(vellum::canvas::Canvas& c) override {
            c.set_fill_color(vellum::canvas::Color::rgba8(40, 120, 220, 255));
            c.fill_rect(0, 0, 32, 32);
        }
        bool on_key_event(const vellum::view::KeyEvent&) override { ++keys; return true; }
        void on_mouse_event(const vellum::view::MouseEvent& event) override {
            if (event.phase == vellum::view::MousePhase::press) {
                ++presses;
                last_press = event.position;
            }
        }
        int keys = 0;
        int presses = 0;
        vellum::view::Point last_press{};
    } probe;
    probe.set_bounds({0, 0, 64, 64});
    // The plug-in owns a bundle registration. This records the gpu-runtime
    // version alongside the process-wide Dawn identity before creating a GPU
    // host, so another bundle built against a different runtime is refused.
    std::string bootstrap_error;
    if (!vellum::app_host::register_native_dawn_bootstrap_for_bundle(
            "com.example.vellum.plugin-view-host", &bootstrap_error))
        return fail(("Dawn bootstrap registration failed: " + bootstrap_error).c_str());
    if (!vellum::app_host::register_native_dawn_bootstrap_for_bundle(
            "com.example.vellum.plugin-view-host-second", &bootstrap_error))
        return fail(("same-runtime bundle registration failed: " + bootstrap_error).c_str());
    constexpr uint32_t background_rgb = 0x2359A1;
    Host::Options options;
    options.size = {64, 64};
    options.use_gpu = true;
    options.background_rgb = background_rgb;
    options.defer_presentation = true;
    auto gpu = Host::create(probe, options);
    if (!gpu || (live_gpu && !gpu->is_gpu_backed()))
        return fail("GPU host factory returned no GPU host");
    if (probe.plugin_view_host() != gpu.get()) return fail("root did not retain its GPU host");
    NSView* parent = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 64, 64)];
    gpu->attach_to_parent((void*)parent);
    if (live_gpu) {
        if (gpu->presented_frame_count() != 0)
            return fail("deferred presentation produced a frame before the first paint");
        if (gpu->present_first_frame())
            return fail("deferred present_first_frame unexpectedly presented");
        if (gpu->presented_frame_count() != 0)
            return fail("deferred presentation acquired or presented a drawable");
        gpu->resume_presentation();
        if (!gpu->present_first_frame()) return fail("resumed GPU paint failed");
        if (gpu->presented_frame_count() != 1)
            return fail("resume did not produce exactly one first presentation");
        if (!png_pixel_matches(gpu->capture_back_buffer_png(), 8, 8, 0x2878DC))
            return fail("first resumed presentation did not contain the View paint");
    }
    // A real AppKit key event sent to the host's own view must reach the
    // View tree (calling the View's handler directly would prove nothing).
    int global_keys = 0;
    probe.on_global_key = [&](const vellum::view::KeyEvent&) { ++global_keys; return true; };
    NSView* host_view = parent.subviews.firstObject;
    if (host_view == nil) return fail("the host attached no view to its parent");
    NSEvent* ev = [NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint modifierFlags:0
                                  timestamp:0 windowNumber:0 context:nil characters:@"\r"
                charactersIgnoringModifiers:@"\r" isARepeat:NO keyCode:36];
    [host_view keyDown:ev];
    if (probe.keys + global_keys < 1) return fail("an AppKit key event did not reach the View");

    using vellum::view::Point;
    using vellum::view::Rect;
    constexpr uint32_t paint_rgb = 0x2878DC;
    gpu->set_size(400, 400);
    if (live_gpu && !png_pixel_matches(gpu->capture_back_buffer_png(), 50, 150, background_rgb, 400))
        return fail("the scaled probe location was not background with the viewport disabled");

    gpu->set_fixed_aspect_ratio(2);
    if (gpu->get_size().width != 400 || gpu->get_size().height != 400)
        return fail("the aspect hint changed the DAW-owned editor size");
    gpu->set_design_viewport(200, 100);
    float sx = 0, sy = 0, tx = 0, ty = 0;
    if (!gpu->design_viewport_transform(sx, sy, tx, ty) || sx != 2 || sy != 2 || tx != 0 || ty != 100)
        return fail("expected a 2x viewport with a 100-point vertical letterbox");
    if (probe.bounds() != Rect{0, 0, 200, 100})
        return fail("root did not keep its design bounds");
    if (gpu->window_to_root_point({80, 140}) != Point{40, 20})
        return fail("viewport inverse did not account for the scale and letterbox");

    // AppKit points count up from the bottom edge.
    for (auto type : {NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp}) {
        NSEvent* click = [NSEvent mouseEventWithType:type location:NSMakePoint(80, 260)
                                    modifierFlags:0 timestamp:0 windowNumber:0 context:nil
                                      eventNumber:0 clickCount:1 pressure:1];
        if (type == NSEventTypeLeftMouseDown)
            [host_view mouseDown:click];
        else
            [host_view mouseUp:click];
    }
    if (probe.presses != 1 || probe.last_press != Point{40, 20})
        return fail("AppKit mouse press did not hit-test in design coordinates");

    if (live_gpu) {
        const auto png = gpu->capture_back_buffer_png();
        if (!png_pixel_matches(png, 50, 150, paint_rgb, 400) ||
            !png_pixel_matches(png, 50, 50, background_rgb, 400))
            return fail("centered capture did not show scaled paint and a background letterbox");
    }

    gpu->set_design_viewport_top_align(true);
    if (!gpu->design_viewport_transform(sx, sy, tx, ty) || sx != 2 || sy != 2 || tx != 0 || ty != 0 ||
        gpu->window_to_root_point({80, 40}) != Point{40, 20})
        return fail("top alignment did not remove the vertical offset from both maps");
    if (live_gpu) {
        const auto png = gpu->capture_back_buffer_png();
        if (!png_pixel_matches(png, 50, 50, paint_rgb, 400) ||
            !png_pixel_matches(png, 50, 150, background_rgb, 400))
            return fail("top-aligned capture did not move the scaled paint to the top");
    } else {
        std::puts("plugin view host consumer: GPU paint and readback SKIP, needs a GPU run by the lead");
    }

    gpu->set_size(600, 200);
    if (!gpu->design_viewport_transform(sx, sy, tx, ty) || sx != 2 || sy != 2 || tx != 100 || ty != 0 ||
        probe.bounds() != Rect{0, 0, 200, 100})
        return fail("resizing did not center the design horizontally and preserve its bounds");
    gpu->set_design_viewport(0, 0);
    if (gpu->design_viewport_transform(sx, sy, tx, ty) || sx != 2 || sy != 2 || tx != 100 || ty != 0 ||
        gpu->window_to_root_point({80, 140}) != Point{80, 140} || probe.bounds() != Rect{0, 0, 600, 200})
        return fail("disabling the viewport did not restore host coordinates and bounds");

    gpu->detach();
    gpu.reset();
    if (probe.plugin_view_host() != nullptr)
        return fail("destroyed GPU host remained on the root");
    probe.set_bounds({0, 0, 64, 64});
    gpu = Host::create(probe, options);
    if (!gpu || (live_gpu && !gpu->is_gpu_backed()) || probe.plugin_view_host() != gpu.get())
        return fail("reopened GPU host was not installed");
    gpu->resume_presentation();
    if (live_gpu && !gpu->present_first_frame()) return fail("reopened GPU host did not paint");
    gpu.reset();
    if (probe.plugin_view_host() != nullptr)
        return fail("reopened GPU host remained on the root after destruction");
    Host::set_factory([](vellum::view::View&, const Host::Options&) {
        return std::make_unique<StubHost>();
    });
    auto older = Host::create(probe, options);
    auto newer = Host::create(probe, options);
    if (!older || !newer || probe.plugin_view_host() != newer.get())
        return fail("replacement host was not installed");
    older.reset();
    if (probe.plugin_view_host() != newer.get())
        return fail("destroying an older host cleared its replacement");
    newer.reset();
    Host::clear_factory();
    if (probe.plugin_view_host() != nullptr)
        return fail("destroying the replacement host left a dangling pointer");
    std::vector<Host::GpuSurfaceState> seen;
    {
        StubHost host;
        host.set_size(320, 200);
        if (host.get_size().width != 320 || host.get_size().height != 200)
            return fail("set_size did not reach get_size");
        host.mark_dirty_full();
        if (host.repaints != 1 || !host.pending_repaint_is_full())
            return fail("mark_dirty_full did not request a full repaint");
        if (host.gpu_surface() != nullptr || host.gpu_surface_state() != Host::GpuSurfaceState::pending)
            return fail("a new host is not pending its GPU surface");

        auto subscription = host.observe_gpu_surface(
            [&seen](const Host::GpuSurfaceStatus& status) { seen.push_back(status.state); });
        host.give_up_on_gpu();
        if (host.gpu_surface_state() != Host::GpuSurfaceState::unavailable)
            return fail("publish_gpu_surface did not record unavailable");
        if (!subscription)
            return fail("the subscription is inactive while the host lives");
    }
    if (seen != std::vector<Host::GpuSurfaceState>{Host::GpuSurfaceState::pending,
                                                   Host::GpuSurfaceState::unavailable})
        return fail("observers did not see pending then unavailable");
    std::puts("plugin view host consumer: ok");
    return 0;
}
