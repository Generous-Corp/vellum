#pragma once

// skia_log_bridge.hpp — VellumGpuRuntime::SkiaLogBridge, the only device-layer
// target that links Skia. A renderer registers it once:
//
//   gpu_runtime::register_skia_log_bridge(gpu_runtime::skia_log_bridge_installer());
//
// after which install_skia_log_bridge*() can route Skia's process-global log
// handler into the GPU diagnostic sink.

#include <gpu_runtime/gpu_diagnostics.hpp>

namespace gpu_runtime {

SkiaLogBridgeInstaller skia_log_bridge_installer() noexcept;

}  // namespace gpu_runtime
