#pragma once

#include <vellum/graphics/frame_trace.hpp>

#include <atomic>

namespace vellum::graphics::detail {

extern std::atomic<const FrameTraceSink*> active_frame_trace_sink_storage;

/// The installed sink, or null. One atomic load, inlined, so a surface with
/// tracing off pays a load and a predictable branch per emission site.
[[nodiscard]] inline const FrameTraceSink* active_frame_trace_sink() noexcept {
    return active_frame_trace_sink_storage.load(std::memory_order_acquire);
}

/// Installs `sink` only when none is installed. Returns whether it did.
[[nodiscard]] bool install_frame_trace_sink_if_none(const FrameTraceSink& sink);

/// Starts the process-wide recorder named by `VELLUM_FRAME_TRACE`, once, when
/// no sink is installed yet.
void start_frame_trace_from_environment();

/// Writes the recorder `start_frame_trace_from_environment` started, if any.
void flush_frame_trace_from_environment() noexcept;

/// A slice that ends on the sink it began on, even if the sink is replaced in
/// between.
class FrameTraceSlice final {
public:
    explicit FrameTraceSlice(const char* name) noexcept : sink_(active_frame_trace_sink()) {
        if (sink_ != nullptr && sink_->slice_begin != nullptr) {
            sink_->slice_begin(name, sink_->context);
        } else {
            sink_ = nullptr;
        }
    }
    ~FrameTraceSlice() { end(); }
    FrameTraceSlice(const FrameTraceSlice&) = delete;
    FrameTraceSlice& operator=(const FrameTraceSlice&) = delete;

    void end() noexcept {
        if (sink_ == nullptr) return;
        if (sink_->slice_end != nullptr) sink_->slice_end(sink_->context);
        sink_ = nullptr;
    }

private:
    const FrameTraceSink* sink_;
};

inline void trace_counter(const char* name, double value) noexcept {
    const auto* sink = active_frame_trace_sink();
    if (sink != nullptr && sink->counter != nullptr) sink->counter(name, value, sink->context);
}

inline void trace_instant(const char* name, const char* detail = "") noexcept {
    const auto* sink = active_frame_trace_sink();
    if (sink != nullptr && sink->instant != nullptr) sink->instant(name, detail, sink->context);
}

}  // namespace vellum::graphics::detail
