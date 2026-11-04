#include <gpu_runtime/gpu_host_hooks.hpp>

#include "detail/log.hpp"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>

namespace gpu_runtime {
namespace {

// The sinks are read on the render thread, so they are lock-free atomics. A
// host installs them before the first GPU object; replacing one while device
// work runs may pair one call's function with the other sink's context, which
// is why the header asks for installation up front.
std::atomic<LogSink> g_log{nullptr};
std::atomic<void*> g_log_context{nullptr};
std::atomic<decltype(TraceSink::instant)> g_trace_instant{nullptr};
std::atomic<decltype(TraceSink::begin)> g_trace_begin{nullptr};
std::atomic<decltype(TraceSink::end)> g_trace_end{nullptr};
std::atomic<void*> g_trace_context{nullptr};

std::mutex g_options_mutex;
RuntimeOptions g_options;

const char* level_label(LogLevel level) noexcept {
    switch (level) {
    case LogLevel::info:
        return "info";
    case LogLevel::warning:
        return "warning";
    case LogLevel::error:
        return "error";
    }
    return "info";
}

}  // namespace

void set_log_sink(LogSink sink, void* context) noexcept {
    g_log_context.store(sink != nullptr ? context : nullptr, std::memory_order_release);
    g_log.store(sink, std::memory_order_release);
}

void set_trace_sink(const TraceSink& sink) noexcept {
    g_trace_context.store(sink.context, std::memory_order_release);
    g_trace_begin.store(sink.begin, std::memory_order_release);
    g_trace_end.store(sink.end, std::memory_order_release);
    g_trace_instant.store(sink.instant, std::memory_order_release);
}

bool trace_sink_installed() noexcept {
    return g_trace_instant.load(std::memory_order_acquire) != nullptr;
}

void set_runtime_options(const RuntimeOptions& options) noexcept {
    const std::lock_guard lock(g_options_mutex);
    g_options = options;
}

RuntimeOptions runtime_options() noexcept {
    const std::lock_guard lock(g_options_mutex);
    return g_options;
}

namespace detail {

void write_log(LogLevel level, std::string_view message) noexcept {
    if (const auto sink = g_log.load(std::memory_order_acquire)) {
        sink(level, message, g_log_context.load(std::memory_order_acquire));
        return;
    }
    std::fprintf(stderr, "[gpu_runtime] %s: %.*s\n", level_label(level),
                 static_cast<int>(message.size()), message.data());
}

void trace_instant(const char* category, const char* name, const TraceArg* args,
                   std::size_t arg_count) noexcept {
    if (const auto sink = g_trace_instant.load(std::memory_order_acquire))
        sink(category, name, args, arg_count, g_trace_context.load(std::memory_order_acquire));
}

void trace_begin(const char* category, const char* name) noexcept {
    if (const auto sink = g_trace_begin.load(std::memory_order_acquire))
        sink(category, name, g_trace_context.load(std::memory_order_acquire));
}

void trace_end(const char* category) noexcept {
    if (const auto sink = g_trace_end.load(std::memory_order_acquire))
        sink(category, g_trace_context.load(std::memory_order_acquire));
}

bool env_flag(const char* name, bool& value) noexcept {
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
        return false;
    const std::string_view text(raw);
    if (text == "1" || text == "true" || text == "TRUE" || text == "yes" || text == "on" ||
        text == "ON") {
        value = true;
        return true;
    }
    if (text == "0" || text == "false" || text == "FALSE" || text == "no" || text == "off" ||
        text == "OFF") {
        value = false;
        return true;
    }
    return false;
}

}  // namespace detail
}  // namespace gpu_runtime
