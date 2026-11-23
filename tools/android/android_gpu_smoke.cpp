#include <gpu_runtime/gpu_surface.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/rmlui/vellum_renderer.hpp>

#include <cstdio>
#include <memory>
#include <string>
#include <vector>

namespace {

constexpr std::uint32_t kWidth = 160;
constexpr std::uint32_t kHeight = 96;

int fail(const char* message) {
    std::fprintf(stderr, "vellum-android-gpu-smoke: %s\n", message);
    return 1;
}

}  // namespace

int main() {
    auto gpu = gpu_runtime::GpuSurface::create_dawn();
    if (!gpu) return 77;

    gpu_runtime::GpuSurface::Config gpu_config;
    gpu_config.width = kWidth;
    gpu_config.height = kHeight;
    gpu_config.native_surface_handle = nullptr;
    gpu_config.backend_preference =
        gpu_runtime::GpuSurface::AdapterBackendPreference::vulkan;
    if (!gpu->initialize(gpu_config)) return 77;

    const auto adapter = gpu->adapter_info();
    if (!adapter.available || adapter.null_backend) return 77;
    std::printf("adapter: %s\nbackend: %s (%s)\n", adapter.name.c_str(),
                adapter.backend.c_str(), adapter.backend_type.c_str());
    if (adapter.backend_type != "Vulkan") return fail("expected Vulkan backend");

    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error))
        return fail(error.c_str());
    auto surface = vellum::graphics::SkiaDawnSurface::create(
        {.width = kWidth, .height = kHeight, .scale = 1.0F, .native_surface_handle = nullptr},
        &error);
    if (!surface) return fail(error.c_str());

    vellum::rmlui::VellumRenderer renderer(*surface, 0x000000ffu);
    renderer.begin_frame(kWidth, kHeight);
    auto vertices = std::make_shared<std::vector<vellum::rmlui::DrawVertex>>(
        std::initializer_list<vellum::rmlui::DrawVertex>{
            {8.0F, 8.0F, 0.0F, 0.0F, 0xff0000ffu},
            {48.0F, 8.0F, 1.0F, 0.0F, 0x00ff00ffu},
            {8.0F, 48.0F, 0.0F, 1.0F, 0x0000ffffu},
        });
    auto indices = std::make_shared<std::vector<std::uint32_t>>(
        std::initializer_list<std::uint32_t>{0, 1, 2});
    vellum::rmlui::DrawCommand command;
    command.kind = vellum::rmlui::DrawCommand::Kind::rectangle;
    command.vertices = std::move(vertices);
    command.indices = std::move(indices);
    renderer.submit(command);
    renderer.end_frame();

    if (!renderer.frame_ok()) return fail(renderer.last_error().c_str());
#ifdef VELLUM_ANDROID_GPU_SMOKE_NEGATIVE
    constexpr std::size_t expected_triangles = 2;
#else
    constexpr std::size_t expected_triangles = 1;
#endif
    if (renderer.triangles_submitted() != expected_triangles)
        return fail("expected triangles did not match");
    if (renderer.skipped_draws() != 0) return fail("renderer skipped a draw");
    if (!surface->finish(&error)) return fail(error.c_str());
    std::printf("vellum-rmlui vellum renderer: ok (%zu triangles)\n",
                renderer.triangles_submitted());
    return 0;
}
