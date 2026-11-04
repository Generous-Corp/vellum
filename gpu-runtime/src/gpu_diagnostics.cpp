#include <gpu_runtime/gpu_diagnostics.hpp>

#include <gpu_runtime/gpu_host_hooks.hpp>

#include "detail/log.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <mutex>
#include <string_view>

namespace gpu_runtime {

namespace {

std::atomic<std::uint64_t> g_emitted{0};
std::atomic<std::uint64_t> g_traced{0};
std::atomic<std::uint64_t> g_truncated{0};

std::atomic<SkiaLogBridgeStatus> g_bridge_status{SkiaLogBridgeStatus::not_attempted};
std::atomic<std::uint64_t> g_bridge_records{0};
LogTextBudget g_bridge_text_budget{kSkiaLogTextLimit};
std::mutex g_bridge_mutex;
SkiaLogBridgeInstaller g_bridge_installer;

} // namespace

const char* to_string(GpuDiagnosticSeverity severity) noexcept {
    switch (severity) {
    case GpuDiagnosticSeverity::info:
        return "info";
    case GpuDiagnosticSeverity::warning:
        return "warning";
    case GpuDiagnosticSeverity::error:
        return "error";
    case GpuDiagnosticSeverity::fatal:
        return "fatal";
    }
    return "unknown";
}

const char* to_string(SkiaLogBridgeStatus status) noexcept {
    switch (status) {
    case SkiaLogBridgeStatus::not_attempted:
        return "not_attempted";
    case SkiaLogBridgeStatus::installed:
        return "installed";
    case SkiaLogBridgeStatus::declined_handler_present:
        return "declined_handler_present";
    case SkiaLogBridgeStatus::declined_not_enabled:
        return "declined_not_enabled";
    case SkiaLogBridgeStatus::unavailable_no_skia:
        return "unavailable_no_skia";
    }
    return "unknown";
}

void emit_gpu_diagnostic(GpuDiagnosticSeverity severity, const char* source,
                         std::string_view message) noexcept {
    g_emitted.fetch_add(1, std::memory_order_relaxed);

    // Bounded stack copy: the annotation needs a null-terminated buffer and the
    // render thread must not allocate to get one.
    const std::size_t copied = std::min(message.size(), kGpuDiagnosticMessageLimit);
    if (copied < message.size())
        g_truncated.fetch_add(1, std::memory_order_relaxed);

    std::array<char, kGpuDiagnosticMessageLimit + 1> buffer{};
    if (copied > 0)
        std::memcpy(buffer.data(), message.data(), copied);
    buffer[copied] = '\0';

    const char* const tag = (source != nullptr) ? source : "unknown";
    const char* const severity_label = to_string(severity);

    // One interned event name for every GPU diagnostic; severity and source are
    // low-cardinality annotations, so `GROUP BY` on the slice name still works
    // and the variable text rides along as debug.message.
    if (trace_sink_installed()) {
        const TraceArg args[] = {
            {"severity", severity_label},
            {"source", tag},
            {"message", buffer.data()},
        };
        detail::trace_instant("gpu", "gpu.diagnostic", args, std::size(args));
        g_traced.fetch_add(1, std::memory_order_relaxed);
    }
}

GpuDiagnosticsStats gpu_diagnostics_stats() noexcept {
    GpuDiagnosticsStats stats;
    stats.emitted = g_emitted.load(std::memory_order_relaxed);
    stats.traced = g_traced.load(std::memory_order_relaxed);
    stats.truncated = g_truncated.load(std::memory_order_relaxed);
    return stats;
}

bool skia_log_bridge_available() noexcept {
    const std::lock_guard<std::mutex> lock(g_bridge_mutex);
    return g_bridge_installer.install != nullptr;
}

bool skia_log_bridge_enabled() noexcept {
    switch (runtime_options().log_bridge) {
    case RuntimeOptions::Choice::yes:
        return true;
    case RuntimeOptions::Choice::no:
        return false;
    case RuntimeOptions::Choice::from_environment:
        break;
    }
    bool value = trace_sink_installed();
    detail::runtime_env_flag("LOG_BRIDGE", value);
    return value;
}

bool register_skia_log_bridge(const SkiaLogBridgeInstaller& installer) noexcept {
    if (installer.install == nullptr)
        return false;
    const std::lock_guard<std::mutex> lock(g_bridge_mutex);
    if (g_bridge_installer.install == nullptr) {
        g_bridge_installer = installer;
        return true;
    }
    return g_bridge_installer.install == installer.install &&
           g_bridge_installer.context == installer.context;
}

SkiaLogBridgeState skia_log_bridge_state() noexcept {
    SkiaLogBridgeState state;
    state.status = g_bridge_status.load(std::memory_order_relaxed);
    state.records_forwarded = g_bridge_records.load(std::memory_order_relaxed);
    state.text_logged = g_bridge_text_budget.admitted();
    state.text_suppressed = g_bridge_text_budget.suppressed();
    return state;
}

void forward_skia_log_record(GpuDiagnosticSeverity severity, std::string_view message) noexcept {
    g_bridge_records.fetch_add(1, std::memory_order_relaxed);
    emit_gpu_diagnostic(severity, "skia.log", message);

    // The sink above is a trace signal, which a host without a trace sink
    // drops. Copy the record into the text log too (the one thing a user can
    // send) within a fixed budget, so a chatty Skia cannot flood it.
    if (!g_bridge_text_budget.admit())
        return;
    while (!message.empty() && (message.back() == '\n' || message.back() == '\r'))
        message.remove_suffix(1);
    const char* const label = to_string(severity);
    if (severity == GpuDiagnosticSeverity::info)
        detail::log_info("skia: [{}] {}", label, message);
    else
        detail::log_warn("skia: [{}] {}", label, message);
}

SkiaLogBridgeStatus install_skia_log_bridge() noexcept {
    const std::lock_guard<std::mutex> lock(g_bridge_mutex);
    const auto current = g_bridge_status.load(std::memory_order_relaxed);
    if (current == SkiaLogBridgeStatus::installed ||
        current == SkiaLogBridgeStatus::declined_handler_present) {
        return current;
    }
    if (g_bridge_installer.install == nullptr) {
        g_bridge_status.store(SkiaLogBridgeStatus::unavailable_no_skia, std::memory_order_relaxed);
        return SkiaLogBridgeStatus::unavailable_no_skia;
    }
    auto result = g_bridge_installer.install(g_bridge_installer.context);
    if (result != SkiaLogBridgeStatus::installed)
        result = SkiaLogBridgeStatus::declined_handler_present;
    g_bridge_status.store(result, std::memory_order_relaxed);
    return result;
}

SkiaLogBridgeStatus install_skia_log_bridge_if_enabled() noexcept {
    if (!skia_log_bridge_enabled()) {
        const std::lock_guard<std::mutex> lock(g_bridge_mutex);
        const auto current = g_bridge_status.load(std::memory_order_relaxed);
        // A prior explicit install already reported a terminal outcome; an
        // absent opt-in must not overwrite it with a weaker one.
        if (current == SkiaLogBridgeStatus::not_attempted) {
            g_bridge_status.store(SkiaLogBridgeStatus::declined_not_enabled,
                                  std::memory_order_relaxed);
            return SkiaLogBridgeStatus::declined_not_enabled;
        }
        return current;
    }
    return install_skia_log_bridge();
}

} // namespace gpu_runtime
