#pragma once

namespace vellum::graphics::detail {

// The category the framework files these under, for readers of a trace.
inline constexpr const char* kNativeTraceCategory = "render";

}  // namespace vellum::graphics::detail

#if defined(VELLUM_NATIVE_TRACE_BRIDGE) && VELLUM_NATIVE_TRACE_BRIDGE
// The framework's native trace emitters (framework/core/render, built into the
// same provider image). Declared here so graphics/ includes no framework header.
extern "C" {
bool vellum_native_trace_enabled(void);
void vellum_native_trace_slice_begin(const char* name);
void vellum_native_trace_slice_end(void);
void vellum_native_trace_counter(const char* name, double value);
void vellum_native_trace_instant(const char* name, const char* detail);
}
#endif  // VELLUM_NATIVE_TRACE_BRIDGE
