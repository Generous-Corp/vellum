#pragma once

/// Emits trace slices, counters and instants into the framework's native
/// Perfetto session. The functions take plain C types and name no renderer
/// type, so the renderer can forward to them without including this header
/// (it declares the same functions itself).
///
/// Without a build that has VELLUM_ENABLE_TRACING on, every function is a
/// no-op and `vellum_native_trace_enabled` returns false. Slices and instants
/// land in the "render" category; a counter becomes a counter track named
/// after it. A slice opened by `vellum_native_trace_slice_begin` is closed by
/// `vellum_native_trace_slice_end` on the same thread.
extern "C" {
bool vellum_native_trace_enabled(void);
void vellum_native_trace_slice_begin(const char* name);
void vellum_native_trace_slice_end(void);
void vellum_native_trace_counter(const char* name, double value);
void vellum_native_trace_instant(const char* name, const char* detail);
}
