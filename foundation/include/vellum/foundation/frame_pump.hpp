#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>

namespace vellum::foundation {

struct FramePumpOptions final {
    /// The time a first frame, or a frame after a resume, advances the clock by.
    double nominal_dt = 1.0 / 60.0;
    /// A gap between two ticks longer than this is idle time, not animation
    /// time: the frame is a resume and advances the clock by `nominal_dt`.
    double wake_threshold = 0.25;
    /// Consecutive failed renders the pump retries on its own before it stops
    /// asking for frames, so a lost device does not spin the display link. A
    /// request or a successful frame starts the count again.
    std::uint32_t max_render_retries = 3;
    /// Content jumps to its end state instead of animating. Changed later with
    /// `set_reduced_motion`.
    bool reduced_motion = false;
};

/// The clock and reduced-motion setting a frame advances its content with.
/// `clock` and `dt` may be changed by the advance function (a jump to an end
/// state); the pump keeps what it leaves.
struct FrameTick final {
    /// Seconds since the pump's first frame, including this frame's `dt`.
    double clock = 0.0;
    /// Seconds this frame advances the clock by.
    double dt = 0.0;
    bool resumed = false;
    bool reduced_motion = false;
};

/// What the content decided in its advance step.
struct FrameStep final {
    /// The frame can differ from the last one drawn.
    bool changed = false;
    /// The content needs another tick whatever happens next: something is
    /// still animating, or a live source was published recently.
    bool wants_more = false;
};

struct PumpReport final {
    double clock = 0.0;
    double dt = 0.0;
    /// The frame followed the first tick, a `suspend` or a gap over the wake
    /// threshold.
    bool resumed = false;
    /// The render function was called.
    bool rendered = false;
    /// False when it was called and failed; `error` says why.
    bool render_ok = true;
    std::string error;
    /// The host should tick again; when false it should stop its vsync source
    /// and call `suspend`, and costs no frames until `request_frame`.
    bool needs_next_frame = false;
};

/// The content-independent half of a host frame loop: a deterministic clock,
/// frame-needed gating so an idle host draws nothing, a thread-safe wake, and
/// the reduced-motion setting. A host drives any content through it by passing
/// that content's advance and render steps, for example a retained `Scene`
/// (`vellum::graphics::FrameDriver`) or a document that updates and then
/// renders itself.
///
/// The host calls `frame` from its vsync source, on the thread that owns the
/// content, with the source's own monotonic presentation time. Each call:
/// - measures the time since the previous frame: a gap over `wake_threshold`
///   or a `suspend` is a resume and advances one nominal frame, so content
///   never catches up on time the host spent idle; a repeated or backwards
///   timestamp advances nothing;
/// - calls the advance function with the new clock, which says whether the
///   frame can differ and whether more ticks are wanted;
/// - calls the render function when the frame is the first, was requested,
///   or the advance said it changed, and retries a failed render a bounded
///   number of times.
///
/// The pump reads no clock of its own, so an injected clock gives the same
/// frames.
class FramePump final {
public:
    using AdvanceFunction = std::function<FrameStep(FrameTick&)>;
    using RenderFunction = std::function<bool(std::string*)>;
    using WakeFunction = std::function<void()>;

    explicit FramePump(FramePumpOptions options = {});

    /// Main thread. Takes effect on the next frame, which is requested.
    void set_reduced_motion(bool reduced);
    [[nodiscard]] bool reduced_motion() const noexcept { return reduced_motion_; }

    /// Moves the clock; the next frame renders. Main thread.
    void set_clock(double seconds);
    [[nodiscard]] double clock() const noexcept { return clock_; }

    /// Called from `request_frame`, on whatever thread requested; it must be
    /// thread safe and cheap, normally a hop to the thread that owns the
    /// vsync source. Installing it while a request is already pending calls it
    /// once, so the order of setup does not lose the first wake. Main thread,
    /// before any other thread calls `request_frame`.
    void set_wake(WakeFunction wake);

    /// Asks for the next frame to render and wakes the host, once until that
    /// frame runs. Any thread.
    void request_frame();
    [[nodiscard]] bool frame_requested() const noexcept {
        return requested_.load(std::memory_order_acquire);
    }

    /// The host is about to stop ticking. The next frame is a resume
    /// whatever the gap. Main thread.
    void suspend() noexcept;

    /// One frame at `now_seconds`.
    PumpReport frame(double now_seconds, const AdvanceFunction& advance,
                     const RenderFunction& render);

private:
    WakeFunction wake_;
    FramePumpOptions options_;
    bool reduced_motion_ = false;
    double clock_ = 0.0;
    double last_now_ = 0.0;
    bool have_last_ = false;
    bool rendered_once_ = false;
    std::uint32_t failed_renders_ = 0;
    std::atomic<bool> requested_{false};
};

}  // namespace vellum::foundation
