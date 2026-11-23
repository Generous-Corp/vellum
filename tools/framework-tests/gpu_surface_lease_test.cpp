#include "framework_test.hpp"

#include <vellum/render/gpu_surface.hpp>
#include <vellum/view/plugin_view_host.hpp>

#include <memory>
#include <vector>

namespace {

class FakeSurface final : public vellum::render::GpuSurface {
public:
    explicit FakeSurface(bool& alive) : alive_(alive) { alive_ = true; }
    ~FakeSurface() override { alive_ = false; }

    bool initialize(const Config&) override { return true; }
    void resize(uint32_t, uint32_t) override {}
    bool begin_frame() override { return true; }
    void end_frame() override {}
    bool is_initialized() const override { return true; }
    bool has_surface() const override { return true; }
    uint32_t width() const override { return 1; }
    uint32_t height() const override { return 1; }
    void* dawn_device_handle() const override { return nullptr; }
    void* dawn_queue_handle() const override { return nullptr; }
    void* dawn_instance_handle() const override { return nullptr; }
    void* current_texture_handle() const override { return nullptr; }
    AdapterInfo adapter_info() const override { return {}; }

private:
    bool& alive_;
};

class LeaseTestHost final : public vellum::view::PluginViewHost {
public:
    LeaseTestHost() : PluginViewHost() {}
    ~LeaseTestHost() override { invalidate_and_reset(); }

    vellum::view::NativeViewHandle native_handle() override { return nullptr; }
    void attach_to_parent(vellum::view::NativeViewHandle) override {}
    bool is_attached() const noexcept override { return false; }
    void detach() override {}
    void repaint() override {}
    void set_size(uint32_t, uint32_t) override {}
    Size get_size() const override { return {}; }

    void install() {
        surface_ = std::make_unique<FakeSurface>(alive_);
        publish_gpu_surface(surface_.get(), GpuSurfaceState::ready);
    }

    void invalidate_and_reset() {
        invalidate_gpu_surface();
        surface_.reset();
    }

    bool alive() const { return alive_; }

private:
    bool alive_ = false;
    std::unique_ptr<FakeSurface> surface_;
};

FRAMEWORK_TEST(gpu_surface_lease_invalidates_before_reset) {
    LeaseTestHost host;
    host.install();

    bool unavailable_while_alive = false;
    auto subscription = host.observe_gpu_surface(
        [&](const vellum::view::PluginViewHost::GpuSurfaceStatus& status) {
            if (status.state == vellum::view::PluginViewHost::GpuSurfaceState::unavailable)
                unavailable_while_alive = host.alive();
        });
    host.invalidate_and_reset();

    CHECK(unavailable_while_alive);
}

FRAMEWORK_TEST(gpu_surface_lease_refuses_use_after_invalidate) {
    LeaseTestHost host;
    host.install();
    auto lease = host.gpu_surface_lease();
    CHECK(lease);
    CHECK(lease.get() != nullptr);
    host.invalidate_and_reset();
    CHECK(lease.get() == nullptr);
}

FRAMEWORK_TEST(gpu_surface_lease_generation_changes_on_replacement) {
    LeaseTestHost host;
    host.install();
    auto first = host.gpu_surface_lease();
    const auto first_generation = first.generation();
    host.invalidate_and_reset();
    host.install();
    auto second = host.gpu_surface_lease();

    CHECK(second.generation() > first_generation);
    CHECK(first.get() == nullptr);
    CHECK(second.get() != nullptr);
}

}  // namespace

int main() { return framework_test::run_all(); }
