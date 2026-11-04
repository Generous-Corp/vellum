#include "render_observer_private.hpp"
#include <gpu_runtime/gpu_surface.hpp>

#if defined(GPU_RUNTIME_HAS_DAWN)
#include "dawn/dawn_version.h"
#include "webgpu/webgpu_cpp.h"
#endif

namespace gpu_runtime::render_observer {

RenderToolchainVersions toolchain_versions() {
    RenderToolchainVersions versions;
#if defined(GPU_RUNTIME_HAS_DAWN)
    // The renderer milestone comes from gpu-runtime/renderer-lock.json; the
    // device layer includes no Skia header.
    versions.skia_milestone = GPU_RUNTIME_SKIA_MILESTONE;
    static constexpr char digits[] = "0123456789abcdef";
    for (const auto byte : dawn::kDawnVersion) {
        versions.dawn_version += digits[byte >> 4];
        versions.dawn_version += digits[byte & 0x0f];
    }
#endif
    return versions;
}

bool inject_device_loss(GpuSurface& surface) {
#if defined(GPU_RUNTIME_HAS_DAWN)
    auto* device = static_cast<wgpu::Device*>(surface.dawn_device_handle());
    if (!device || !*device)
        return false;
    device->Destroy();
    return true;
#else
    (void)surface;
    return false;
#endif
}

bool device_lost(const GpuSurface& surface) {
#if defined(GPU_RUNTIME_HAS_DAWN)
    auto* device = static_cast<wgpu::Device*>(surface.dawn_device_handle());
    auto* instance = static_cast<wgpu::Instance*>(surface.dawn_instance_handle());
    if (!device || !*device || !instance || !*instance)
        return true;
    wgpu::FutureWaitInfo wait{device->GetLostFuture()};
    return instance->WaitAny(1, &wait, 0) == wgpu::WaitStatus::Success && wait.completed;
#else
    (void)surface;
    return false;
#endif
}

} // namespace gpu_runtime::render_observer
