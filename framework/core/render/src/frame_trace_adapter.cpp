#include <vellum/render/frame_trace_adapter.hpp>

#if defined(VELLUM_NATIVE_TRACING) && VELLUM_NATIVE_TRACING

#include <vellum/runtime/trace.hpp>

extern "C" {

bool vellum_native_trace_enabled(void) {
    return true;
}

void vellum_native_trace_slice_begin(const char* name) {
    TRACE_EVENT_BEGIN("render", ::perfetto::DynamicString{name != nullptr ? name : ""});
}

void vellum_native_trace_slice_end(void) {
    TRACE_EVENT_END("render");
}

void vellum_native_trace_counter(const char* name, double value) {
    TRACE_COUNTER("render", ::perfetto::DynamicString{name != nullptr ? name : ""}, value);
}

void vellum_native_trace_instant(const char* name, const char* detail) {
    TRACE_EVENT_INSTANT("render", ::perfetto::DynamicString{name != nullptr ? name : ""}, "detail",
                        detail != nullptr ? detail : "");
}
}

#else

extern "C" {

bool vellum_native_trace_enabled(void) {
    return false;
}

void vellum_native_trace_slice_begin(const char*) {}

void vellum_native_trace_slice_end(void) {}

void vellum_native_trace_counter(const char*, double) {}

void vellum_native_trace_instant(const char*, const char*) {}
}

#endif
