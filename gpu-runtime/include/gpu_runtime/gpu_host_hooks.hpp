#pragma once

// gpu_host_hooks.hpp — what the embedding host supplies to the device layer.
//
// The device layer depends on no framework, so the host decides where its
// log lines and trace events go and which process options apply. Each hook is
// optional: without a log sink, lines go to stderr; without a trace sink,
// events are dropped and GpuDiagnosticsStats::traced stays 0.
//
// Install hooks before the first GpuSurface or GpuCompute is created. The
// sinks may be called from any thread, including the render thread, and must
// not block or create a GPU object.

#include <cstddef>
#include <string_view>

namespace gpu_runtime {

enum class LogLevel {
    info,
    warning,
    error,
};

/// One formatted log line. `message` is valid only for the call.
using LogSink = void (*)(LogLevel level, std::string_view message, void* context);

/// One named key/value annotation on a trace event.
struct TraceArg {
    const char* key = nullptr;
    const char* value = nullptr;
};

/// Receives trace events. `instant` records a point event; `begin` and `end`
/// bracket a scope on the calling thread. Any member may be null.
struct TraceSink {
    void (*instant)(const char* category, const char* name, const TraceArg* args,
                    std::size_t arg_count, void* context) = nullptr;
    void (*begin)(const char* category, const char* name, void* context) = nullptr;
    void (*end)(const char* category, void* context) = nullptr;
    void* context = nullptr;
};

/// Route device log lines to `sink`. A null sink restores stderr.
void set_log_sink(LogSink sink, void* context) noexcept;

/// Route device trace events to `sink`. A sink with no members drops them.
void set_trace_sink(const TraceSink& sink) noexcept;

/// True when a trace sink with an `instant` member is installed.
bool trace_sink_installed() noexcept;

/// Process options a host may set instead of relying on environment variables.
struct RuntimeOptions {
    /// Request Dawn's fallback (software) adapter for GpuCompute. When unset,
    /// GPU_RUNTIME_SOFTWARE_ADAPTER=1/true/on in the environment requests it
    /// (the prefix is environment_prefix below).
    enum class Choice { from_environment, yes, no };
    Choice software_adapter = Choice::from_environment;
    /// Opt into the Skia log bridge. When unset, an installed trace sink or
    /// GPU_RUNTIME_LOG_BRIDGE=1/true/yes/on enables it, and
    /// GPU_RUNTIME_LOG_BRIDGE=0/false/no/off disables it.
    Choice log_bridge = Choice::from_environment;
    /// Prefix of the environment variables read for `from_environment`
    /// choices: <prefix>SOFTWARE_ADAPTER and <prefix>LOG_BRIDGE. A host keeps
    /// its own variable names by setting its prefix (static storage only).
    const char* environment_prefix = "GPU_RUNTIME_";
};

void set_runtime_options(const RuntimeOptions& options) noexcept;
RuntimeOptions runtime_options() noexcept;

}  // namespace gpu_runtime
