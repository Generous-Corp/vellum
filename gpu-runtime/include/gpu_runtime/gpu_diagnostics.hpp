// gpu_diagnostics.hpp — one sink for GPU-layer diagnostics.
//
// Dawn hands errors to a callback; Skia hands log records to an SkLogHandler.
// Both funnel through emit_gpu_diagnostic(), which publishes a `gpu`-category
// Perfetto instant event so a device error lands on the same timeline as the
// frame that provoked it. This ADDS a trace signal — every call site keeps its
// runtime::log_* call, because a log is what a user reads and a trace is what a
// timeline query joins against.
//
// RT-safety: the Dawn uncaptured-error callback fires on the render thread.
// emit_gpu_diagnostic() allocates nothing: the message is copied into a bounded
// stack buffer and attached to a compile-time event name as a debug annotation,
// so the event name stays interned and nothing reaches the heap. It is still a
// Perfetto TRACE_EVENT and therefore must never be placed on the audio thread's
// live process() path (see trace.hpp).
//
// Observability: a bridge that quietly emits nothing forever is indistinguishable
// from a quiet GPU. gpu_diagnostics_stats() and skia_log_bridge_state() exist so
// "nothing was wired up" is always distinguishable from "nothing went wrong".
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace gpu_runtime {

/// Severity of one GPU diagnostic record.
enum class GpuDiagnosticSeverity {
    info,
    warning,
    error,
    fatal,
};

/// Stable lowercase label for a severity, suitable as a trace annotation value.
const char* to_string(GpuDiagnosticSeverity severity) noexcept;

/// Publish one GPU diagnostic as a `gpu`-category instant event.
///
/// `source` is a stable, low-cardinality tag naming the producer
/// ("dawn.uncaptured_error", "dawn.device_lost", "skia.log"). `message` is the
/// variable text; it is truncated to kGpuDiagnosticMessageLimit bytes rather
/// than allocated. Callers may pass a non-null-terminated view.
///
/// Counted unconditionally, including in a build without tracing, so the
/// counter proves the call site ran even where no Perfetto event exists.
void emit_gpu_diagnostic(GpuDiagnosticSeverity severity, const char* source,
                         std::string_view message) noexcept;

/// Longest message text carried into a trace annotation. Longer messages are
/// truncated; the full text still reaches the log.
inline constexpr std::size_t kGpuDiagnosticMessageLimit = 512;

/// Counters that separate "the sink was never called" from "the sink was called
/// but this build compiles the trace macros out".
struct GpuDiagnosticsStats {
    /// Records handed to emit_gpu_diagnostic() in this process.
    std::uint64_t emitted = 0;
    /// Records forwarded to the host's trace sink. Always 0 without one
    /// (gpu_host_hooks.hpp).
    std::uint64_t traced = 0;
    /// Records whose message text was truncated to the limit above.
    std::uint64_t truncated = 0;
};

GpuDiagnosticsStats gpu_diagnostics_stats() noexcept;

/// Outcome of trying to route Skia's process-global log handler into the sink.
///
/// SkLogHandler::SetInstance is first-install-wins and process-global, and a
/// plug-in loads into hosts that may use Skia themselves, so every outcome other than
/// `installed` is a normal, reportable state rather than an error.
enum class SkiaLogBridgeStatus {
    /// install_skia_log_bridge*() has not run yet.
    not_attempted,
    /// This process owns Skia's log handler and records are flowing.
    installed,
    /// Somebody else installed a handler first; theirs is left untouched.
    declined_handler_present,
    /// The opt-in was absent, so no install was attempted.
    declined_not_enabled,
    /// No library that links Skia registered a bridge installer, so there is
    /// no handler to install.
    unavailable_no_skia,
};

/// Stable lowercase label for a bridge status, suitable for status output.
const char* to_string(SkiaLogBridgeStatus status) noexcept;

struct SkiaLogBridgeState {
    SkiaLogBridgeStatus status = SkiaLogBridgeStatus::not_attempted;
    /// Skia log records forwarded into the sink. Zero with an `installed`
    /// status means Skia has logged nothing — a different fact from a status of
    /// `declined_handler_present`, where nothing could ever arrive.
    std::uint64_t records_forwarded = 0;
    /// Forwarded records also written to the text log (the host's log sink).
    std::uint64_t text_logged = 0;
    /// Forwarded records NOT written to the text log because the budget below
    /// ran out. They still reached the diagnostic sink and its counters.
    std::uint64_t text_suppressed = 0;
};

/// How many Skia log records the bridge copies into the text log before it
/// stops, so a chatty Skia cannot flood a user's log. The remainder is counted
/// and reported once by log_gpu_diagnostics_summary().
inline constexpr std::uint64_t kSkiaLogTextLimit = 50;

/// A thread-safe "first N" budget: admit() returns true for the first `limit`
/// calls and false afterwards, counting both.
class LogTextBudget {
  public:
    explicit constexpr LogTextBudget(std::uint64_t limit) noexcept : limit_(limit) {}

    bool admit() noexcept {
        const auto index = seen_.fetch_add(1, std::memory_order_relaxed);
        return index < limit_;
    }

    std::uint64_t admitted() const noexcept {
        const auto seen = seen_.load(std::memory_order_relaxed);
        return seen < limit_ ? seen : limit_;
    }

    std::uint64_t suppressed() const noexcept {
        const auto seen = seen_.load(std::memory_order_relaxed);
        return seen > limit_ ? seen - limit_ : 0;
    }

  private:
    std::uint64_t limit_;
    std::atomic<std::uint64_t> seen_{0};
};

SkiaLogBridgeState skia_log_bridge_state() noexcept;

/// How a library that links Skia installs the bridge. The device layer never
/// links Skia itself: the renderer (or VellumGpuRuntime::SkiaLogBridge)
/// registers this, and the install functions below call it.
struct SkiaLogBridgeInstaller {
    /// Install the process's Skia log handler if none is present, returning
    /// `installed` or `declined_handler_present`. Called with an internal
    /// lock held, at most once per process.
    SkiaLogBridgeStatus (*install)(void* context) noexcept = nullptr;
    void* context = nullptr;
};

/// Register the bridge installer. The first registration wins; registering
/// the same installer again returns true, a different one returns false.
bool register_skia_log_bridge(const SkiaLogBridgeInstaller& installer) noexcept;

/// Hand one Skia log record to the sink: counted, emitted as a `skia.log`
/// diagnostic and copied to the text log within kSkiaLogTextLimit. Called by
/// the installed handler from any thread.
void forward_skia_log_record(GpuDiagnosticSeverity severity, std::string_view message) noexcept;

/// True when a bridge installer is registered (register_skia_log_bridge). False
/// means `unavailable_no_skia` is the only outcome an install can ever report,
/// which is the difference between "nothing to install" and "declined to".
bool skia_log_bridge_available() noexcept;

/// True when the opt-in is satisfied: RuntimeOptions::log_bridge says yes, or
/// it defers to the environment and either a trace sink is installed or
/// <prefix>LOG_BRIDGE is 1/true/yes/on. <prefix>LOG_BRIDGE=0/false/no/off wins
/// over the trace sink, so a tracing host can still leave Skia's logging alone.
bool skia_log_bridge_enabled() noexcept;

/// Install the bridge, honouring the opt-in. Without it the status becomes
/// `declined_not_enabled` and Skia is not touched. This is the entry point a
/// host or app calls; a plugin build ships with the opt-in off.
SkiaLogBridgeStatus install_skia_log_bridge_if_enabled() noexcept;

/// Install the bridge regardless of the opt-in — calling this IS the explicit
/// opt-in. Installs only when SkLogHandler::GetInstance() is null; an existing
/// handler is never displaced. Idempotent: repeat calls report the first
/// outcome.
SkiaLogBridgeStatus install_skia_log_bridge() noexcept;

} // namespace gpu_runtime
