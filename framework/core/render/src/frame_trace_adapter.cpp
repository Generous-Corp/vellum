#include <vellum/render/frame_trace_adapter.hpp>

#if defined(VELLUM_NATIVE_TRACING) && VELLUM_NATIVE_TRACING

#include <vellum/graphics/frame_trace.hpp>
#include <vellum/runtime/trace.hpp>

namespace vellum::render {
namespace {

char owner_tag = 0;

void slice_begin(const char* name, void*) {
    TRACE_EVENT_BEGIN("render", ::perfetto::DynamicString{name});
}

void slice_end(void*) {
    TRACE_EVENT_END("render");
}

void counter(const char* name, double value, void*) {
    TRACE_COUNTER("render", ::perfetto::DynamicString{name}, value);
}

void instant(const char* name, const char* detail, void*) {
    TRACE_EVENT_INSTANT("render", ::perfetto::DynamicString{name}, "detail",
                        detail != nullptr ? detail : "");
}

}  // namespace

bool install_native_frame_trace() noexcept {
    graphics::FrameTraceSink sink;
    sink.slice_begin = &slice_begin;
    sink.slice_end = &slice_end;
    sink.counter = &counter;
    sink.instant = &instant;
    sink.context = &owner_tag;
    graphics::set_frame_trace_sink(sink);
    return true;
}

void remove_native_frame_trace() noexcept {
    graphics::clear_frame_trace_sink_for(&owner_tag);
}

bool native_frame_trace_installed() noexcept {
    return graphics::frame_trace_sink_context() == &owner_tag;
}

}  // namespace vellum::render

#else

namespace vellum::render {

bool install_native_frame_trace() noexcept {
    return false;
}

void remove_native_frame_trace() noexcept {}

bool native_frame_trace_installed() noexcept {
    return false;
}

}  // namespace vellum::render

#endif
