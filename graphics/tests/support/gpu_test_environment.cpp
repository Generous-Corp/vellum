#include "gpu_test_environment.hpp"

#include "scene_capture_harness.hpp"

#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include "dawn/webgpu_cpp.h"

#include <atomic>
#include <iostream>
#include <memory>
#include <optional>
#include <utility>

namespace vellum::graphics::capture_harness {
namespace {

std::atomic<AdapterProbe> probe_override{nullptr};

struct ProbeOutcome final {
    AdapterProbeResult result = AdapterProbeResult::error;
    std::string detail;
};

ProbeOutcome probe_real_adapter() {
    static const ProbeOutcome outcome = [] {
        ProbeOutcome probed;
        wgpu::InstanceDescriptor descriptor{};
        const wgpu::InstanceFeatureName features[] = {wgpu::InstanceFeatureName::TimedWaitAny};
        descriptor.requiredFeatureCount = 1;
        descriptor.requiredFeatures = features;
        dawn::native::Instance native(reinterpret_cast<const WGPUInstanceDescriptor*>(&descriptor));
        wgpu::Instance instance(native.Get());
        if (!instance) {
            probed.detail = "Dawn could not create an instance";
            return probed;
        }
        wgpu::RequestAdapterOptions options{};
        options.powerPreference = wgpu::PowerPreference::HighPerformance;
        options.backendType = wgpu::BackendType::Metal;
        probed.detail = "the adapter request did not complete";
        instance.RequestAdapter(
            &options, wgpu::CallbackMode::AllowProcessEvents,
            [&](wgpu::RequestAdapterStatus status, wgpu::Adapter adapter,
                wgpu::StringView message) {
                if (status == wgpu::RequestAdapterStatus::Success && adapter) {
                    probed.result = AdapterProbeResult::present;
                } else if (status == wgpu::RequestAdapterStatus::Unavailable) {
                    probed.result = AdapterProbeResult::absent;
                } else {
                    probed.result = AdapterProbeResult::error;
                }
                probed.detail = std::string(message.data, message.length);
            });
        instance.ProcessEvents();
        return probed;
    }();
    return outcome;
}

}  // namespace

int GpuStatus::exit_code() const noexcept {
    switch (outcome) {
        case GpuOutcome::ready: return 0;
        case GpuOutcome::no_adapter: return kGpuSkipExitCode;
        case GpuOutcome::failed: break;
    }
    return 1;
}

void set_adapter_probe_for_test(AdapterProbe probe) noexcept { probe_override = probe; }

GpuStatus prepare_gpu_environment() {
    std::string error;
    if (!prepare_device(&error)) {
        return {GpuOutcome::failed, "device bootstrap failed: " + error};
    }
    const auto probe = probe_override.load();
    ProbeOutcome outcome;
    if (probe != nullptr) {
        outcome.result = probe(&outcome.detail);
    } else {
        outcome = probe_real_adapter();
    }
    if (outcome.result == AdapterProbeResult::absent) {
        return {GpuOutcome::no_adapter, "no Metal adapter on this machine: " + outcome.detail};
    }
    if (outcome.result == AdapterProbeResult::error) {
        return {GpuOutcome::failed, "the Metal adapter request failed: " + outcome.detail};
    }
    return {GpuOutcome::ready, {}};
}

int report_gpu_status(const GpuStatus& status) {
    if (status.outcome == GpuOutcome::no_adapter) {
        std::cout << "SKIP " << status.message << '\n';
    } else if (status.outcome == GpuOutcome::failed) {
        std::cerr << "FAIL " << status.message << '\n';
    }
    return status.exit_code();
}

SurfaceResult create_gpu_surface(const SkiaDawnSurface::Config& config) {
    SurfaceResult result;
    result.status = prepare_gpu_environment();
    if (!result.status.ready()) return result;
    std::string error;
    result.surface = SkiaDawnSurface::create(config, &error);
    if (!result.surface) {
        result.status = {GpuOutcome::failed,
                         "GPU surface creation failed after a Metal adapter was found: " + error};
    }
    return result;
}

}  // namespace vellum::graphics::capture_harness
