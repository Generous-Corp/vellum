#pragma once

#include <cstdint>
#include <memory>

#include <gpu_runtime/gpu_surface.hpp>

struct ANativeWindow;

namespace vellum::render {

// Owns the Vellum-side render target while Android owns the SurfaceView and
// its ANativeWindow lifetime. All methods run on the render thread.
class AndroidGpuSurface final {
  public:
    AndroidGpuSurface();
    ~AndroidGpuSurface();

    AndroidGpuSurface(const AndroidGpuSurface&) = delete;
    AndroidGpuSurface& operator=(const AndroidGpuSurface&) = delete;

    // width and height are logical pixels; scale converts them to the
    // physical swapchain extent used by Dawn.
    bool create(ANativeWindow* window, std::uint32_t width, std::uint32_t height,
                float scale, const char** error = nullptr);
    void resize(std::uint32_t width, std::uint32_t height);
    bool begin_frame();
    void end_frame();
    void destroy();

    bool is_ready() const;
    const gpu_runtime::GpuSurface::AdapterInfo& adapter_info() const;

  private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::render
