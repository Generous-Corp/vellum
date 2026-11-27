// Shared helpers for the Skia surface framework tests: assertions that abort a
// case, a presentable GpuSurface whose drawable the test owns, a readback of
// that drawable through the public WebGPU entry points, and a stderr capture.
// Skia's own symbols are private to the provider library, so everything here
// goes through `vellum::` and `wgpu::` API.
#pragma once

#include "framework_test.hpp"

#include <gpu_runtime/gpu_surface.hpp>

#include "webgpu/webgpu_cpp.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#if !defined(_WIN32)
#include <fcntl.h>
#include <unistd.h>
#endif

// REQUIRE stops the case where it fails, so a null surface is never
// dereferenced after its own check. The runner reports the thrown error as a
// failed case.
#define REQUIRE(expression)                                                    \
    do {                                                                       \
        if (!(expression)) {                                                   \
            ::framework_test::record_failure(#expression, __FILE__, __LINE__); \
            throw std::runtime_error("REQUIRE failed: " #expression);          \
        }                                                                      \
    } while (false)

#define REQUIRE_FALSE(expression) REQUIRE(!(expression))
#define CHECK_FALSE(expression) CHECK(!(expression))

namespace skia_surface_test {

// A presentable GpuSurface whose "drawable" is a texture the test owns, so the
// persistent-scene blit (which only runs when there is a drawable) can be
// driven offscreen and each frame's drawable read back afterwards. Everything
// else is the real Dawn device.
class TextureDrawableGpu final : public gpu_runtime::GpuSurface {
  public:
    explicit TextureDrawableGpu(gpu_runtime::GpuSurface& inner) : inner_(inner) {}
    void set_drawable(wgpu::Texture* texture) { drawable_ = texture; }

    bool initialize(const Config&) override { return inner_.is_initialized(); }
    void resize(uint32_t w, uint32_t h) override { inner_.resize(w, h); }
    bool begin_frame() override { return inner_.begin_frame(); }
    void end_frame() override { inner_.end_frame(); }
    bool is_initialized() const override { return inner_.is_initialized(); }
    bool has_surface() const override { return true; }
    uint32_t width() const override { return inner_.width(); }
    uint32_t height() const override { return inner_.height(); }
    void* dawn_device_handle() const override { return inner_.dawn_device_handle(); }
    void* dawn_queue_handle() const override { return inner_.dawn_queue_handle(); }
    void* dawn_instance_handle() const override { return inner_.dawn_instance_handle(); }
    void* current_texture_handle() const override { return drawable_; }
    AdapterInfo adapter_info() const override { return inner_.adapter_info(); }

  private:
    gpu_runtime::GpuSurface& inner_;
    wgpu::Texture* drawable_ = nullptr;
};

inline wgpu::Texture make_drawable_texture(gpu_runtime::GpuSurface& gpu, uint32_t w, uint32_t h) {
    auto* device = static_cast<wgpu::Device*>(gpu.dawn_device_handle());
    wgpu::TextureDescriptor desc{};
    desc.size = {w, h, 1};
    desc.format = wgpu::TextureFormat::RGBA8Unorm;
    desc.usage = wgpu::TextureUsage::RenderAttachment | wgpu::TextureUsage::TextureBinding |
                 wgpu::TextureUsage::CopySrc;
    return device->CreateTexture(&desc);
}

// Reads a drawable texture back to tightly packed RGBA8 through a staging
// buffer. The texture must be RGBA8Unorm with CopySrc usage.
inline bool read_drawable(gpu_runtime::GpuSurface& gpu, wgpu::Texture& texture,
                          std::vector<uint8_t>& out) {
    auto* device = static_cast<wgpu::Device*>(gpu.dawn_device_handle());
    auto* queue = static_cast<wgpu::Queue*>(gpu.dawn_queue_handle());
    auto* instance = static_cast<wgpu::Instance*>(gpu.dawn_instance_handle());
    if (!device || !queue || !instance)
        return false;
    const uint32_t w = texture.GetWidth();
    const uint32_t h = texture.GetHeight();
    const uint32_t row = (w * 4u + 255u) & ~255u;
    wgpu::BufferDescriptor buffer_desc{};
    buffer_desc.size = static_cast<uint64_t>(row) * h;
    buffer_desc.usage = wgpu::BufferUsage::CopyDst | wgpu::BufferUsage::MapRead;
    wgpu::Buffer staging = device->CreateBuffer(&buffer_desc);
    if (!staging)
        return false;
    wgpu::CommandEncoder encoder = device->CreateCommandEncoder();
    wgpu::TexelCopyTextureInfo src{};
    src.texture = texture;
    wgpu::TexelCopyBufferInfo dst{};
    dst.buffer = staging;
    dst.layout.bytesPerRow = row;
    dst.layout.rowsPerImage = h;
    wgpu::Extent3D extent{w, h, 1};
    encoder.CopyTextureToBuffer(&src, &dst, &extent);
    wgpu::CommandBuffer commands = encoder.Finish();
    queue->Submit(1, &commands);

    bool done = false;
    bool ok = false;
    staging.MapAsync(wgpu::MapMode::Read, 0, buffer_desc.size, wgpu::CallbackMode::AllowProcessEvents,
                     [&](wgpu::MapAsyncStatus status, wgpu::StringView) {
                         done = true;
                         ok = status == wgpu::MapAsyncStatus::Success;
                     });
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!done && std::chrono::steady_clock::now() < deadline) {
        instance->ProcessEvents();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!done || !ok)
        return false;
    const auto* mapped = static_cast<const uint8_t*>(staging.GetConstMappedRange(0, buffer_desc.size));
    out.resize(static_cast<size_t>(w) * h * 4u);
    for (uint32_t y = 0; y < h; ++y)
        std::memcpy(out.data() + static_cast<size_t>(y) * w * 4u, mapped + static_cast<size_t>(y) * row,
                    static_cast<size_t>(w) * 4u);
    staging.Unmap();
    return true;
}

#if !defined(_WIN32)
// Captures what the process writes to stderr (where Skia's SkDebugf warnings
// go) between construction and text().
class StderrCapture {
  public:
    StderrCapture() {
        std::fflush(stderr);
        char path[] = "/tmp/vellum-skia-stderr-XXXXXX";
        fd_ = mkstemp(path);
        if (fd_ >= 0) {
            path_ = path;
            saved_ = dup(STDERR_FILENO);
            dup2(fd_, STDERR_FILENO);
        }
    }
    ~StderrCapture() {
        restore();
        if (fd_ >= 0) {
            close(fd_);
            std::remove(path_.c_str());
        }
    }
    bool active() const { return fd_ >= 0 && saved_ >= 0; }
    std::string text() {
        restore();
        std::ifstream in(path_);
        return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    }

  private:
    void restore() {
        if (saved_ < 0)
            return;
        std::fflush(stderr);
        dup2(saved_, STDERR_FILENO);
        close(saved_);
        saved_ = -1;
    }
    int fd_ = -1;
    int saved_ = -1;
    std::string path_;
};
#endif

inline bool pixel_is(const std::vector<uint8_t>& px, uint32_t w, uint32_t x, uint32_t y, uint8_t r,
                     uint8_t g, uint8_t b) {
    const size_t i = (static_cast<size_t>(y) * w + x) * 4u;
    if (px.size() < i + 4)
        return false;
    auto close_to = [](uint8_t a, uint8_t e) { return std::abs(int(a) - int(e)) < 24; };
    return close_to(px[i], r) && close_to(px[i + 1], g) && close_to(px[i + 2], b);
}

} // namespace skia_surface_test
