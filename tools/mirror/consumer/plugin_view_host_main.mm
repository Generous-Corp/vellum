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
#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/view.hpp>
#import <Cocoa/Cocoa.h>

#include <cstdio>
#include <cmath>
#include <string>
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
                       uint32_t rgb) {
    // Compare the raw samples the host wrote: converting through NSColor
    // colour-manages them and shifts every channel.
    // A negative coordinate counts back from the far edge, so a corner probe
    // stays outside the 32-point View paint at any backing scale (a 2x
    // surface doubles the painted rect to 64 pixels).
    NSData* data = [NSData dataWithBytes:png.data() length:png.size()];
    NSBitmapImageRep* image = [[NSBitmapImageRep alloc] initWithData:data];
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

int main() {
    if (std::getenv("VELLUM_MIRROR_PLUGIN_VIEW_HOST_BREAK"))
        return fail("planted break");
    struct Probe final : vellum::view::View {
        void paint(vellum::canvas::Canvas& c) override {
            c.set_fill_color(vellum::canvas::Color::rgba8(40, 120, 220, 255));
            c.fill_rect(0, 0, 32, 32);
        }
        bool on_key_event(const vellum::view::KeyEvent&) override { ++keys; return true; }
        int keys = 0;
    } probe;
    probe.set_bounds({0, 0, 64, 64});
    // The process owns Dawn's proc table: a plug-in registers the native
    // bootstrap before its editor creates a GPU host, as this consumer does.
    std::string bootstrap_error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&bootstrap_error))
        return fail(("Dawn bootstrap registration failed: " + bootstrap_error).c_str());
    constexpr uint32_t background_rgb = 0x2359A1;
    Host::Options options;
    options.size = {64, 64};
    options.use_gpu = true;
    options.background_rgb = background_rgb;
    auto gpu = Host::create(probe, options);
    if (!gpu || !gpu->is_gpu_backed()) return fail("GPU host factory returned no GPU host");
    if (probe.plugin_view_host() != gpu.get()) return fail("root did not retain its GPU host");
    NSView* parent = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 64, 64)];
    gpu->attach_to_parent((void*)parent);
    if (!gpu->present_first_frame()) return fail("offscreen GPU paint failed");
    const auto png = gpu->capture_back_buffer_png();
    if (png.empty()) return fail("GPU readback was empty");
    if (!png_pixel_matches(png, -4, -4, background_rgb))
        return fail("unpainted readback corner did not use the requested background");
    if (!png_pixel_matches(png, 8, 8, 0x2878DC))
        return fail("GPU readback did not contain the View paint");
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
    gpu->detach();
    gpu.reset();
    if (probe.plugin_view_host() != nullptr)
        return fail("destroyed GPU host remained on the root");
    gpu = Host::create(probe, options);
    if (!gpu || !gpu->is_gpu_backed() || probe.plugin_view_host() != gpu.get())
        return fail("reopened GPU host was not installed");
    if (!gpu->present_first_frame()) return fail("reopened GPU host did not paint");
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
