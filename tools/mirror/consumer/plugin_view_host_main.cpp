// An installed consumer of the plug-in editor host, built without
// VELLUM_MIRROR_BUILD. A plug-in editor includes plugin_view_host.hpp, which
// names the GPU device type through the forward declaration in
// platform/gpu_fwd.hpp; that forward declaration is part of the installed
// mirror's supported surface (gpu_surface_guard.py).
//
// The host subclasses PluginViewHost, so the run links and calls the installed
// destructor, observe_gpu_surface() and publish_gpu_surface(), the members
// whose signatures carry the GpuSurface pointer.

#include <vellum/view/plugin_view_host.hpp>

#include <cstdio>
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
