// Compiles and links against VellumGpuRuntime::DeviceApi alone.
//
// The device API is the bottom of the GPU stack: GPU audio, the renderer and
// every editor that shares the device consume these headers. This translation
// unit includes every header the API target owns and uses only what they
// define inline, so it builds with no Dawn, WebGPU or Skia include directory
// and links with no library at all. A header that starts naming a Dawn type,
// or an inline helper that starts calling an out-of-line device function,
// breaks this build.

#include <gpu_runtime/bench/perf_counters.hpp>
#include <gpu_runtime/dawn_bootstrap.hpp>
#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_compute.hpp>
#include <gpu_runtime/gpu_diagnostics.hpp>
#include <gpu_runtime/gpu_host_hooks.hpp>
#include <gpu_runtime/gpu_proc_installer.hpp>
#include <gpu_runtime/gpu_render_time.hpp>
#include <gpu_runtime/gpu_startup_report.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <gpu_runtime/skia_log_bridge.hpp>

#include <cstdio>
#include <type_traits>

static_assert(std::is_abstract_v<gpu_runtime::GpuCompute>,
              "GpuCompute is an interface; the device implementation defines it");

int main() {
#ifdef GPU_RUNTIME_BENCHMARK
    gpu_runtime::bench::PerfCounters counters;
    if (counters.sample_count.load() != 0.0) {
        return 1;
    }
#endif
    gpu_runtime::GpuSurface::AdapterInfo info;
    gpu_runtime::GpuStartupReport report;
    gpu_runtime::GpuDiagnosticsStats stats;
    gpu_runtime::DawnProcInstaller installer;

    const bool ok = !info.available && report.graphite_ms < 0.0 && stats.emitted == 0 &&
                    installer.install == nullptr &&
                    gpu_runtime::kGpuRenderNanosecondsPerMillisecond == 1.0e6;
    if (!ok) {
        std::fprintf(stderr, "gpu-runtime device API defaults changed unexpectedly\n");
        return 1;
    }
    return 0;
}
