// The device layer's offscreen surface on a real Dawn device: construction and
// initialization, frame cycles, resize, the Dawn handles it exposes, and the
// adapter identity it reports. Offscreen only; no window is created. The
// executable exits 77 (skipped) when no Dawn adapter is available.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>

#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>

namespace {

using gpu_runtime::GpuSurface;

GpuSurface::Config offscreen(uint32_t width, uint32_t height) {
    GpuSurface::Config config{};
    config.width = width;
    config.height = height;
    config.vsync = false;
    config.native_surface_handle = nullptr;
    return config;
}

FRAMEWORK_TEST(the_factory_returns_a_surface) {
    CHECK(GpuSurface::create_dawn() != nullptr);
}

FRAMEWORK_TEST(offscreen_initialization_reports_its_size_and_no_presentable_surface) {
    auto surface = GpuSurface::create_dawn();
    CHECK(surface != nullptr);
    if (!surface)
        return;
    CHECK(surface->initialize(offscreen(800, 600)));
    CHECK(surface->is_initialized());
    CHECK(surface->width() == 800);
    CHECK(surface->height() == 600);
    CHECK(!surface->has_surface());
}

FRAMEWORK_TEST(an_offscreen_frame_cycle_always_begins) {
    auto surface = GpuSurface::create_dawn();
    if (!surface || !surface->initialize(offscreen(400, 300))) {
        CHECK(false);
        return;
    }
    CHECK(surface->begin_frame());
    surface->end_frame();
    for (int i = 0; i < 3; ++i) {
        CHECK(surface->begin_frame());
        surface->end_frame();
    }
}

FRAMEWORK_TEST(resize_updates_the_dimensions) {
    auto surface = GpuSurface::create_dawn();
    if (!surface || !surface->initialize(offscreen(400, 300))) {
        CHECK(false);
        return;
    }
    surface->resize(1024, 768);
    CHECK(surface->width() == 1024);
    CHECK(surface->height() == 768);
}

FRAMEWORK_TEST(resize_before_initialize_records_the_pending_dimensions) {
    auto surface = GpuSurface::create_dawn();
    CHECK(surface != nullptr);
    if (!surface)
        return;
    CHECK(!surface->is_initialized());
    surface->resize(321, 123);
    CHECK(surface->width() == 321);
    CHECK(surface->height() == 123);
    CHECK(!surface->is_initialized());
    CHECK(!surface->has_surface());
    CHECK(!surface->begin_frame());
}

FRAMEWORK_TEST(an_initialized_surface_exposes_its_dawn_handles) {
    auto surface = GpuSurface::create_dawn();
    if (!surface || !surface->initialize(offscreen(100, 100))) {
        CHECK(false);
        return;
    }
    CHECK(surface->dawn_device_handle() != nullptr);
    CHECK(surface->dawn_instance_handle() != nullptr);
    CHECK(surface->dawn_queue_handle() != nullptr);
    CHECK(surface->current_texture_handle() != nullptr);
}

FRAMEWORK_TEST(adapter_info_reflects_the_initialization_state) {
    auto surface = GpuSurface::create_dawn();
    CHECK(surface != nullptr);
    if (!surface)
        return;

    const auto before = surface->adapter_info();
    CHECK(!before.available);
    CHECK(!before.native_bridge);
    CHECK(!before.backend.empty());
    CHECK(!before.backend_type.empty());
    CHECK(!before.name.empty());
    CHECK(!before.vendor.empty());
    CHECK(!before.architecture.empty());
    CHECK(!before.description.empty());
    CHECK(!before.preferred_canvas_format.empty());

    if (!surface->initialize(offscreen(64, 32))) {
        CHECK(false);
        return;
    }
    const auto after = surface->adapter_info();
    CHECK(after.available);
    CHECK(after.native_bridge);
    CHECK(!after.backend.empty());
    CHECK(!after.backend_type.empty());
    CHECK(!after.name.empty());
    CHECK(!after.vendor.empty());
    CHECK(!after.architecture.empty());
    CHECK(!after.description.empty());
    CHECK(!after.preferred_canvas_format.empty());
}

FRAMEWORK_TEST(adapter_info_reports_the_drivers_own_identity) {
    auto surface = GpuSurface::create_dawn();
    if (!surface || !surface->initialize(offscreen(64, 32))) {
        CHECK(false);
        return;
    }
    const auto info = surface->adapter_info();
    CHECK(info.available);
    // The Null backend validates API calls against no driver, so it has no
    // identity to report.
    if (info.null_backend)
        return;

    // A backing adapter must supply at least one field the generic fallbacks
    // could not have produced: a driver policy that matches a blocklist against
    // name, vendor and description would otherwise match nothing.
    const bool generic_name = info.name == "Native Dawn Adapter (" + info.backend_type + ")" ||
                              info.name == "Native WebGPU Adapter (" + info.backend_type + ")";
    const bool generic_vendor = info.vendor == "Dawn" || info.vendor == "wgpu-native";
    const bool generic_description = info.description == info.name;
    CHECK(!(generic_name && generic_vendor && generic_description));
}

FRAMEWORK_TEST(begin_frame_before_initialize_returns_false) {
    auto surface = GpuSurface::create_dawn();
    CHECK(surface != nullptr);
    if (!surface)
        return;
    CHECK(!surface->begin_frame());
}

FRAMEWORK_TEST(an_offscreen_surface_has_no_presentable_surface) {
    auto surface = GpuSurface::create_dawn();
    GpuSurface::Config config{};
    config.native_surface_handle = nullptr;
    if (!surface || !surface->initialize(config)) {
        CHECK(false);
        return;
    }
    CHECK(!surface->has_surface());
}

FRAMEWORK_TEST(an_offscreen_resize_reports_the_size_and_configures_no_swapchain) {
    auto surface = GpuSurface::create_dawn();
    if (!surface || !surface->initialize(offscreen(400, 300))) {
        CHECK(false);
        return;
    }
    surface->resize(0, 0);
    CHECK(surface->width() == 0);
    CHECK(surface->begin_frame()); // an offscreen surface is always ready
    surface->end_frame();

    surface->resize(512, 256);
    CHECK(surface->width() == 512);
    CHECK(surface->height() == 256);
    CHECK(surface->begin_frame());
    surface->end_frame();
    CHECK(surface->swapchain_stats().configure_count == 0);
}

// Exit code when the GPU is unavailable: a skip normally, a failure in the GPU
// lane (VELLUM_REQUIRE_GPU=ON), so an initialization regression cannot hide.
#ifdef VELLUM_REQUIRE_GPU_DEVICE
constexpr int kNoGpuExit = 1;
#else
constexpr int kNoGpuExit = 77;
#endif

} // namespace

int main() {
    // The device library leaves Dawn's proc table to the host; this executable
    // is the host, so it registers the library's own native table.
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("%s Dawn bootstrap unavailable: %s\n", kNoGpuExit == 77 ? "SKIP" : "FAIL",
                    bootstrap_error.c_str());
        return kNoGpuExit;
    }
    auto probe = GpuSurface::create_dawn();
    if (!probe || !probe->initialize(offscreen(64, 64)) || !probe->adapter_info().native_bridge) {
        std::printf("%s no Dawn adapter available\n", kNoGpuExit == 77 ? "SKIP" : "FAIL");
        return kNoGpuExit;
    }
    probe.reset();
    return framework_test::run_all();
}
