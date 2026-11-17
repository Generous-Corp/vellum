#include "../src/skia_dawn_surface_testing.hpp"
#include "support/gpu_test_environment.hpp"

#include <iostream>
#include <string>

namespace {

namespace harness = vellum::graphics::capture_harness;
namespace testing = vellum::graphics::testing;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

harness::AdapterProbeResult no_adapter(std::string*) {
    return harness::AdapterProbeResult::absent;
}
harness::AdapterProbeResult adapter_error(std::string* detail) {
    *detail = "forced error";
    return harness::AdapterProbeResult::error;
}
harness::AdapterProbeResult some_adapter(std::string*) {
    return harness::AdapterProbeResult::present;
}

const vellum::graphics::SkiaDawnSurface::Config kConfig{.width = 16, .height = 16};

void exit_codes() {
    expect(harness::GpuStatus{harness::GpuOutcome::ready, {}}.exit_code() == 0,
           "a ready status exits 0");
    expect(harness::GpuStatus{harness::GpuOutcome::no_adapter, {}}.exit_code() == 77,
           "a missing adapter exits 77, the skip code");
    expect(harness::GpuStatus{harness::GpuOutcome::failed, {}}.exit_code() == 1,
           "a failure exits 1, not the skip code");
}

void forced_no_adapter_is_a_skip() {
    harness::set_adapter_probe_for_test(&no_adapter);
    const auto result = harness::create_gpu_surface(kConfig);
    harness::set_adapter_probe_for_test(nullptr);
    expect(result.status.outcome == harness::GpuOutcome::no_adapter && !result.surface &&
               result.status.exit_code() == harness::kGpuSkipExitCode,
           "a forced no-adapter machine reports a skip", result.status.message);
}

void adapter_request_error_is_a_failure() {
    harness::set_adapter_probe_for_test(&adapter_error);
    const auto status = harness::prepare_gpu_environment();
    harness::set_adapter_probe_for_test(nullptr);
    expect(status.outcome == harness::GpuOutcome::failed &&
               status.exit_code() != harness::kGpuSkipExitCode,
           "an adapter request that errors is a failure, not a skip", status.message);
}

void real_adapter_creates_a_surface() {
    const auto result = harness::create_gpu_surface(kConfig);
    expect(result.status.ready() && result.surface != nullptr,
           "control: with a real adapter a surface is created", result.status.message);
}

void failure_after_an_adapter_is_a_failure() {
    harness::set_adapter_probe_for_test(&some_adapter);
    testing::fail_device_requests(1);
    const auto result = harness::create_gpu_surface(kConfig);
    testing::fail_device_requests(0);
    harness::set_adapter_probe_for_test(nullptr);
    expect(result.status.outcome == harness::GpuOutcome::failed && !result.surface,
           "a device failure after the adapter was found is a failure, not a skip",
           result.status.message);
    expect(result.status.exit_code() != harness::kGpuSkipExitCode &&
               result.status.exit_code() != 0,
           "that failure does not exit with the skip code or zero");
    expect(result.status.message.find("after a Metal adapter was found") != std::string::npos &&
               result.status.message.find("test injection") != std::string::npos,
           "the failure names its cause", result.status.message);
    const auto again = harness::create_gpu_surface(kConfig);
    expect(again.status.ready() && again.surface != nullptr,
           "control: the injection is spent and the next surface is created",
           again.status.message);
}

}  // namespace

int main() {
    exit_codes();
    forced_no_adapter_is_a_skip();
    adapter_request_error_is_a_failure();
    const auto environment = harness::prepare_gpu_environment();
    if (environment.outcome == harness::GpuOutcome::failed) {
        return harness::report_gpu_status(environment);
    }
    if (environment.outcome == harness::GpuOutcome::ready) {
        real_adapter_creates_a_surface();
        failure_after_an_adapter_is_a_failure();
    }
    if (failures != 0) {
        std::cerr << failures << " GPU test environment check(s) failed\n";
        return 1;
    }
    if (!environment.ready()) return harness::report_gpu_status(environment);
    std::cout << "GPU test environment checks passed\n";
    return 0;
}
