#pragma once

#include <cstddef>
#include <memory>
#include <string>

namespace vellum::graphics {

/// Receives the renderer's per-frame trace signals. A host adapts these
/// function pointers to its tracing session; with no sink installed the
/// renderer records nothing and does no work to prepare a signal.
///
/// Every member may be null. The renderer calls them on the thread that
/// drives the surface, so a sink shared by several surfaces must be
/// thread-safe. A slice opened by `slice_begin` is closed by exactly one
/// `slice_end` on the same thread, and slices nest.
///
/// Slices: `graphics.frame` encloses `graphics.record` (lowering, layer
/// planning and drawing into the retained surface), `graphics.submit`
/// (acquiring the swapchain texture, copying, recording and submitting the
/// command buffer, which contains `graphics.present_wait` for the acquire)
/// and `graphics.present`.
///
/// Counters, once per drawn frame: `graphics.damage_pixels`,
/// `graphics.damage_rects`, `graphics.paint_commands_recorded`,
/// `graphics.paint_commands_total`, `graphics.cpu_ms`,
/// `graphics.present_wait_ms`, `graphics.layer_count`, and per cache
/// (`paragraphs`, `gradients`, `textures`) `graphics.cache.<name>.bytes` and
/// `graphics.cache.<name>.hit_rate`. `graphics.gpu_ms` follows once the
/// frame has finished on the GPU, on the next frame or `finish`.
///
/// Instants: `graphics.full_repaint` (detail is the reason),
/// `graphics.cache_trim` (detail is the trim reason) and
/// `graphics.device_lost`.
struct FrameTraceSink final {
    void (*slice_begin)(const char* name, void* context) = nullptr;
    void (*slice_end)(void* context) = nullptr;
    void (*counter)(const char* name, double value, void* context) = nullptr;
    void (*instant)(const char* name, const char* detail, void* context) = nullptr;
    void* context = nullptr;
};

/// Installs `sink` for every surface in the process, replacing the previous
/// one. Safe to call while frames render on other threads: the sink is copied,
/// and the copy stays valid for the life of the process, so a call that has
/// already read the old sink completes against it. `context` must outlive any
/// call still using it; remove the sink and stop the surfaces before freeing it.
void set_frame_trace_sink(const FrameTraceSink& sink);

/// Removes the sink. Later frames record nothing.
void clear_frame_trace_sink() noexcept;

/// Whether a sink is installed.
[[nodiscard]] bool frame_trace_enabled() noexcept;

/// Records the frame trace events in memory and writes them as Chrome
/// trace-event JSON, which https://ui.perfetto.dev and `trace_processor`
/// open directly. Slices become slices, counters become counter tracks (named
/// `<counter> value`) and instants become instant events carrying their
/// detail as an argument.
///
/// `start` installs the recorder as the process's sink; `stop` removes it.
/// Events past `max_events` are dropped and counted. Methods may be called
/// from any thread.
///
/// Destroy a recorder only after the surfaces it traced have stopped
/// rendering: a frame already past its sink check still calls the recorder.
///
/// Setting `VELLUM_FRAME_TRACE=<path>` records every surface in the process
/// with no code change and writes the file when a surface is destroyed and
/// at process exit. It does nothing while a host has installed its own sink.
class FrameTraceRecorder final {
public:
    explicit FrameTraceRecorder(std::size_t max_events = 1'000'000);
    ~FrameTraceRecorder();
    FrameTraceRecorder(const FrameTraceRecorder&) = delete;
    FrameTraceRecorder& operator=(const FrameTraceRecorder&) = delete;

    void start();
    /// Starts only when no sink is installed. Returns whether it did.
    [[nodiscard]] bool start_if_idle();
    void stop() noexcept;

    [[nodiscard]] std::size_t event_count() const;
    [[nodiscard]] std::size_t dropped_events() const;
    [[nodiscard]] std::string to_json() const;
    [[nodiscard]] bool write(const std::string& path, std::string* error = nullptr) const;

private:
    struct State;
    std::unique_ptr<State> state_;
};

}  // namespace vellum::graphics
