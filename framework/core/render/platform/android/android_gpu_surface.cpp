#if defined(__ANDROID__)

#include "android_gpu_surface.hpp"

#include <gpu_runtime/gpu_surface.hpp>
#include <android/native_window.h>
#include <algorithm>
#include <cmath>
#include <limits>
#include <string>

namespace vellum::render {

namespace {

std::uint32_t scaled_extent(std::uint32_t extent, float scale) {
    if (extent == 0)
        return 0;
    if (!std::isfinite(scale) || scale <= 0.0f)
        scale = 1.0f;
    const double scaled = std::round(static_cast<double>(extent) * scale);
    if (scaled >= static_cast<double>(std::numeric_limits<std::uint32_t>::max()))
        return std::numeric_limits<std::uint32_t>::max();
    return std::max<std::uint32_t>(1, static_cast<std::uint32_t>(scaled));
}

}  // namespace

class AndroidGpuSurface::Impl {
  public:
    std::unique_ptr<gpu_runtime::GpuSurface> surface = gpu_runtime::GpuSurface::create_dawn();
    ANativeWindow* native_window = nullptr;
    gpu_runtime::GpuSurface::AdapterInfo adapter;
    std::string error;
    float scale = 1.0f;
};

AndroidGpuSurface::AndroidGpuSurface() : impl_(std::make_unique<Impl>()) {}
AndroidGpuSurface::~AndroidGpuSurface() { destroy(); }

bool AndroidGpuSurface::create(ANativeWindow* window, std::uint32_t width,
                               std::uint32_t height, float scale,
                               const char** error) {
    destroy();
    impl_->surface = gpu_runtime::GpuSurface::create_dawn();
    if (!window) {
        impl_->error = "AndroidGpuSurface: null ANativeWindow";
        if (error)
            *error = impl_->error.c_str();
        return false;
    }
    impl_->native_window = window;
    ANativeWindow_acquire(impl_->native_window);
    impl_->scale = scale;
    if (!impl_->surface) {
        impl_->error = "AndroidGpuSurface: Dawn surface is unavailable";
    } else {
        gpu_runtime::GpuSurface::Config config;
        config.width = scaled_extent(width, impl_->scale);
        config.height = scaled_extent(height, impl_->scale);
        config.native_surface_handle = window;
        config.backend_preference =
            gpu_runtime::GpuSurface::AdapterBackendPreference::vulkan;
        if (!impl_->surface->initialize(config)) {
            impl_->error = "AndroidGpuSurface: Dawn Vulkan initialization failed";
        } else {
            impl_->adapter = impl_->surface->adapter_info();
            if (error)
                *error = nullptr;
            return true;
        }
    }
    if (error)
        *error = impl_->error.c_str();
    if (impl_->native_window) {
        ANativeWindow_release(impl_->native_window);
        impl_->native_window = nullptr;
    }
    return false;
}

void AndroidGpuSurface::resize(std::uint32_t width, std::uint32_t height) {
    if (impl_->surface)
        impl_->surface->resize(scaled_extent(width, impl_->scale),
                               scaled_extent(height, impl_->scale));
}

bool AndroidGpuSurface::begin_frame() {
    return impl_->surface && impl_->surface->begin_frame();
}

void AndroidGpuSurface::end_frame() {
    if (impl_->surface)
        impl_->surface->end_frame();
}

void AndroidGpuSurface::destroy() {
    if (impl_) {
        impl_->surface.reset();
        impl_->adapter = {};
    }
    if (impl_ && impl_->native_window) {
        ANativeWindow_release(impl_->native_window);
        impl_->native_window = nullptr;
    }
}

bool AndroidGpuSurface::is_ready() const {
    return impl_->surface && impl_->surface->is_initialized() &&
           impl_->surface->has_surface();
}

const gpu_runtime::GpuSurface::AdapterInfo& AndroidGpuSurface::adapter_info() const {
    return impl_->adapter;
}

}  // namespace vellum::render

#endif
