// The device library linked on its own: the host hooks, the diagnostic sink
// and the Skia bridge seam, the native bootstrap's identity check, the default
// proc-table path through the coordinator, and a standalone GpuCompute.

#include <gpu_runtime/dawn_bootstrap.hpp>
#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_compute.hpp>
#include <gpu_runtime/gpu_diagnostics.hpp>
#include <gpu_runtime/gpu_host_hooks.hpp>
#include <gpu_runtime/gpu_proc_installer.hpp>

#include "detail/native_dawn_bindings.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

using namespace gpu_runtime;

int failures = 0;

void check(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}

struct Captured {
    std::vector<std::string> lines;
    std::vector<std::string> instants;
    int begins = 0;
    int ends = 0;
};

Captured captured;

void capture_log(LogLevel, std::string_view message, void* context) {
    static_cast<Captured*>(context)->lines.emplace_back(message);
}

void capture_instant(const char*, const char* name, const TraceArg* args, std::size_t count,
                     void* context) {
    std::string text = name;
    for (std::size_t index = 0; index < count; ++index) {
        text += ' ';
        text += args[index].key;
        text += '=';
        text += args[index].value;
    }
    static_cast<Captured*>(context)->instants.push_back(text);
}

void capture_begin(const char*, const char*, void* context) {
    ++static_cast<Captured*>(context)->begins;
}

void capture_end(const char*, void* context) {
    ++static_cast<Captured*>(context)->ends;
}

int fake_installs = 0;

SkiaLogBridgeStatus fake_install(void*) noexcept {
    ++fake_installs;
    return SkiaLogBridgeStatus::installed;
}

SkiaLogBridgeStatus other_install(void*) noexcept {
    return SkiaLogBridgeStatus::installed;
}

std::uint8_t g_fake_version[20] = {1};
DawnProcTable g_fake_table{};
int fake_setter_calls = 0;

const std::uint8_t* fake_version() { return g_fake_version; }
const DawnProcTable& fake_procs() { return g_fake_table; }
void fake_setter(const DawnProcTable*) { ++fake_setter_calls; }

bool contains_line(const std::vector<std::string>& lines, const char* fragment) {
    for (const auto& line : lines)
        if (line.find(fragment) != std::string::npos)
            return true;
    return false;
}

}  // namespace

int main() {
    // Diagnostics without a trace sink are counted but not traced.
    emit_gpu_diagnostic(GpuDiagnosticSeverity::warning, "test.untraced", "dropped");
    auto stats = gpu_diagnostics_stats();
    check(stats.emitted == 1 && stats.traced == 0, "an untraced diagnostic was traced");

    set_log_sink(&capture_log, &captured);
    set_trace_sink({.instant = &capture_instant,
                    .begin = &capture_begin,
                    .end = &capture_end,
                    .context = &captured});
    check(trace_sink_installed(), "the trace sink did not install");
    emit_gpu_diagnostic(GpuDiagnosticSeverity::error, "test.source", std::string(600, 'x'));
    stats = gpu_diagnostics_stats();
    check(stats.emitted == 2 && stats.traced == 1 && stats.truncated == 1,
          "a traced diagnostic was not counted, traced and truncated");
    check(captured.instants.size() == 1 &&
              captured.instants[0].find("gpu.diagnostic severity=error source=test.source") == 0,
          "the trace sink did not receive the diagnostic and its annotations");

    // No Skia library registered: the bridge reports no Skia.
    check(!skia_log_bridge_available(), "a bridge is available with nothing registered");
    check(install_skia_log_bridge() == SkiaLogBridgeStatus::unavailable_no_skia,
          "an install without a registered bridge did not report no Skia");
    check(!register_skia_log_bridge({}), "a null bridge installer was accepted");
    check(register_skia_log_bridge({.install = &fake_install}), "the bridge did not register");
    check(register_skia_log_bridge({.install = &fake_install}),
          "re-registering the same bridge was refused");
    check(!register_skia_log_bridge({.install = &other_install}),
          "a second, different bridge was accepted");
    check(skia_log_bridge_available(), "a registered bridge is not available");

    set_runtime_options({.log_bridge = RuntimeOptions::Choice::no});
    check(!skia_log_bridge_enabled(), "an explicit opt-out left the bridge enabled");
    set_runtime_options({.log_bridge = RuntimeOptions::Choice::from_environment});
    check(skia_log_bridge_enabled(), "an installed trace sink did not enable the bridge");
    setenv("GPU_RUNTIME_LOG_BRIDGE", "off", 1);
    check(!skia_log_bridge_enabled(), "GPU_RUNTIME_LOG_BRIDGE=off did not win");
    unsetenv("GPU_RUNTIME_LOG_BRIDGE");
    check(install_skia_log_bridge_if_enabled() == SkiaLogBridgeStatus::installed &&
              fake_installs == 1,
          "the enabled bridge did not install through the registered installer");
    check(install_skia_log_bridge() == SkiaLogBridgeStatus::installed && fake_installs == 1,
          "a repeated install ran the installer again");

    forward_skia_log_record(GpuDiagnosticSeverity::warning, "skia says hi\n");
    const auto bridge = skia_log_bridge_state();
    check(bridge.records_forwarded == 1 && bridge.text_logged == 1,
          "a forwarded Skia record was not counted and logged");
    check(contains_line(captured.lines, "skia: [warning] skia says hi"),
          "the forwarded Skia record did not reach the log sink");

    // The native bootstrap refuses mismatched identities without installing.
    detail::NativeDawnBindings fake{
        .header_version = &fake_version,
        .proc_version = &fake_version,
        .native_procs = &fake_procs,
        .set_procs = &fake_setter,
    };
    const DawnBootstrap native = native_dawn_bootstrap();
    std::string error;
    const std::string revision = native_dawn_revision();
    const DawnBootstrapRequest request{.expected_dawn_revision = revision};
    check(native.callback(request, &fake, &error) == DawnBootstrapResult::identity_mismatch &&
              fake_setter_calls == 0,
          "mismatched Dawn identities reached the proc-table setter");
    check(native_dawn_revision().size() == 40, "the native Dawn revision is not 20 bytes of hex");

    // The default proc-table path goes through the coordinator.
    check(!dawn_bootstrap_is_registered(), "a bootstrap ran before any Dawn use");
#if defined(GPU_RUNTIME_HOST_OWNS_DAWN_PROCS)
    // A host-owned build has no native default: nothing is installed until the
    // host bootstraps, and then the device uses the host's table.
    check(!ensure_dawn_procs(&error) && error.find("host owns") != std::string::npos,
          "a host-owned build installed Dawn's procs by itself");
    check(!dawn_bootstrap_is_registered(), "the refused default registered a bootstrap");
    check(register_native_dawn_bootstrap("com.example.device-test", &error),
          "the host's bundle bootstrap failed");
    check(ensure_dawn_procs(&error), "ensure_dawn_procs() ignored the host's bootstrap");
    auto record = dawn_bootstrap_record();
    check(record && record->revision == native_dawn_revision() &&
              record->bundle == "com.example.device-test" &&
              record->runtime_version == runtime_version(),
          "the host's bundle bootstrap was not recorded");
#else
    check(ensure_dawn_procs(&error), "ensure_dawn_procs() failed on the native default");
    auto record = dawn_bootstrap_record();
    check(record && record->revision == native_dawn_revision() && record->bundle.empty(),
          "the default path did not record the native revision");
    check(register_native_dawn_bootstrap("com.example.device-test", &error),
          "a bundle registration of the same revision was refused");
    record = dawn_bootstrap_record();
    check(record && record->bundle == "com.example.device-test" &&
              record->runtime_version == runtime_version(),
          "the bundle registration was not recorded");
#endif

    // A standalone compute device comes up wherever a GPU adapter exists.
    auto compute = GpuCompute::create();
    check(compute != nullptr, "GpuCompute::create() returned null");
    if (compute && !compute->initialize_standalone()) {
        if (std::getenv("GPU_RUNTIME_REQUIRE_ADAPTER") != nullptr)
            check(false, "no GPU adapter, and GPU_RUNTIME_REQUIRE_ADAPTER is set");
        else
            std::puts("gpu-runtime device: no GPU adapter; standalone compute skipped");
    }
    check(captured.begins == captured.ends, "trace scopes are unbalanced");

    if (failures != 0)
        return 1;
    std::puts("gpu-runtime device: ok");
    return 0;
}
