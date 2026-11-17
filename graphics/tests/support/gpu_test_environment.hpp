#pragma once

#include <vellum/graphics/skia_dawn_surface.hpp>

#include <memory>
#include <string>

/// The one place that decides whether a GPU test skips or fails.
///
/// A test skips (exit 77) only when the machine has no Metal adapter. Every
/// failure after an adapter exists, whether the Dawn bootstrap, the device, a
/// surface, or Graphite's context and recorder, is a failure (exit 1).
namespace vellum::graphics::capture_harness {

inline constexpr int kGpuSkipExitCode = 77;

enum class GpuOutcome {
    ready,
    no_adapter,
    failed,
};

struct GpuStatus final {
    GpuOutcome outcome = GpuOutcome::failed;
    std::string message;

    [[nodiscard]] bool ready() const noexcept { return outcome == GpuOutcome::ready; }
    /// 0 when ready, 77 when there is no adapter, 1 on a failure.
    [[nodiscard]] int exit_code() const noexcept;
};

/// Registers the Dawn bootstrap, then looks for a Metal adapter the way a
/// surface requests one. The probe result is cached for the process.
[[nodiscard]] GpuStatus prepare_gpu_environment();

/// Prints the status as `SKIP ...` or `FAIL ...` and returns its exit code.
int report_gpu_status(const GpuStatus& status);

/// A surface, or the reason there is none.
struct SurfaceResult final {
    std::unique_ptr<SkiaDawnSurface> surface;
    GpuStatus status;
};

/// Creates a surface after `prepare_gpu_environment`. A creation failure once
/// an adapter exists is `GpuOutcome::failed`, never `no_adapter`.
[[nodiscard]] SurfaceResult create_gpu_surface(const SkiaDawnSurface::Config& config);

/// What an adapter probe found. Only `absent` is a skip; `error` is a failure.
enum class AdapterProbeResult {
    present,
    absent,
    error,
};

/// Replaces the adapter probe, for tests of this classification. Null
/// restores the real probe.
using AdapterProbe = AdapterProbeResult (*)(std::string* detail);
void set_adapter_probe_for_test(AdapterProbe probe) noexcept;

}  // namespace vellum::graphics::capture_harness
