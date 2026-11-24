#include <gpu_runtime/gpu_surface.hpp>

#include <memory>

int gpu_runtime_translation_unit() {
    std::unique_ptr<gpu_runtime::GpuSurface> surface =
        gpu_runtime::GpuSurface::create_dawn();
    return surface ? 0 : 0;
}

int main() {
    return gpu_runtime_translation_unit();
}
