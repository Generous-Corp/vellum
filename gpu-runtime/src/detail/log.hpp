#pragma once

// Internal logging and tracing for the device sources. Formatting happens
// here; the host's sinks (gpu_host_hooks.hpp) decide where the result goes.

#include <gpu_runtime/gpu_host_hooks.hpp>

#include <format>
#include <string>
#include <string_view>
#include <utility>

namespace gpu_runtime::detail {

void write_log(LogLevel level, std::string_view message) noexcept;
void trace_instant(const char* category, const char* name, const TraceArg* args,
                   std::size_t arg_count) noexcept;
void trace_begin(const char* category, const char* name) noexcept;
void trace_end(const char* category) noexcept;
bool env_flag(const char* name, bool& value) noexcept;

template <typename... Args>
void log_at(LogLevel level, std::format_string<Args...> format, Args&&... args) {
    try {
        write_log(level, std::format(format, std::forward<Args>(args)...));
    } catch (...) {
        write_log(level, format.get());
    }
}

template <typename... Args>
void log_info(std::format_string<Args...> format, Args&&... args) {
    log_at(LogLevel::info, format, std::forward<Args>(args)...);
}

template <typename... Args>
void log_warn(std::format_string<Args...> format, Args&&... args) {
    log_at(LogLevel::warning, format, std::forward<Args>(args)...);
}

template <typename... Args>
void log_error(std::format_string<Args...> format, Args&&... args) {
    log_at(LogLevel::error, format, std::forward<Args>(args)...);
}

/// Brackets a scope with trace begin/end events.
class TraceScope final {
  public:
    TraceScope(const char* category, const char* name) noexcept : category_(category) {
        trace_begin(category, name);
    }
    ~TraceScope() { trace_end(category_); }
    TraceScope(const TraceScope&) = delete;
    TraceScope& operator=(const TraceScope&) = delete;

  private:
    const char* category_;
};

}  // namespace gpu_runtime::detail
