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

}  // namespace

int main() {
    if (std::getenv("VELLUM_MIRROR_PLUGIN_VIEW_HOST_BREAK"))
        return fail("planted break");
    struct Probe final : vellum::view::View {
        void paint(vellum::canvas::Canvas& c) override {
            c.set_fill_color(vellum::canvas::Color::rgba8(40, 120, 220, 255));
            c.fill_rect(0, 0, 64, 64);
        }
        bool on_key_event(const vellum::view::KeyEvent&) override { ++keys; return true; }
        int keys = 0;
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
    auto gpu = Host::create(probe, Host::Options{{64, 64}, true});
    if (!gpu || !gpu->is_gpu_backed()) return fail("GPU host factory returned no GPU host");
    NSView* parent = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 64, 64)];
    gpu->attach_to_parent((void*)parent);
    if (!gpu->present_first_frame()) return fail("offscreen GPU paint failed");
    if (gpu->capture_back_buffer_png().empty()) return fail("GPU readback was empty");
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
