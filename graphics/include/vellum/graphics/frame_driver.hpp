#pragma once

#include <vellum/foundation/frame_pump.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/plot_feed.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/value/value_feed.hpp>

#include <cstdint>
#include <functional>
#include <utility>
#include <optional>
#include <string>

namespace vellum::graphics {

struct FrameDriverOptions final {
    /// The time a first frame, or a frame after a resume, advances the clock by.
    double nominal_dt = 1.0 / 60.0;
    /// A gap between two ticks longer than this is idle time, not animation
    /// time: the frame is a resume and advances the clock by `nominal_dt`.
    double wake_threshold = 0.25;
    /// Tracks jump to their end state on the first frame instead of animating.
    bool reduced_motion = false;
    /// Passed to the `PlotFeedSampler`; `stale_after_ticks` also sets how many
    /// ticks the driver keeps asking for after a feed's last publish, so a
    /// silent channel still gets the tick that draws its neutral value.
    PlotFeedOptions feed;
    /// Consecutive failed renders the driver retries on its own before it stops
    /// asking for frames, so a lost device does not spin the display link. A
    /// request or a successful frame starts the count again.
    std::uint32_t max_render_retries = 3;
};

/// What one `FrameDriver::frame` call did.
struct FrameReport final {
    /// The `Scene::clock` the frame was evaluated at.
    double clock = 0.0;
    /// How much the clock advanced this frame.
    double dt = 0.0;
    /// The frame followed the first tick, a `suspend` or a gap over the wake
    /// threshold.
    bool resumed = false;
    /// The render function was called.
    bool rendered = false;
    /// False when it was called and failed; `error` says why.
    bool render_ok = true;
    std::string error;
    /// The feed was sampled this frame.
    bool sampled = false;
    PlotFeedReport feed;
    /// A track of the scene has keyframes the clock has not reached.
    bool tracks_running = false;
    /// A channel a plot is bound to published recently enough that it has not
    /// yet gone stale. Channels no plot reads do not count.
    bool feed_active = false;
    /// The host should tick again, also after a failed render (up to
    /// `max_render_retries` in a row) so the next tick retries; when false it
    /// should stop its display link and call `suspend`, and costs no frames
    /// until `request_frame`.
    bool needs_next_frame = false;
};

/// The `Scene` client of `foundation::FramePump`: the per-frame loop a host runs a `Scene` with animation tracks (scene
/// version 11) and value plots (version 12) through.
///
/// The host calls `frame` from its vsync source, on the thread that owns the
/// scene and the surface, with the source's own monotonic presentation time.
/// Each call:
/// - advances `Scene::clock` by the measured time since the previous frame, in
///   seconds. The clock starts at zero on the first frame and is the epoch the
///   scene's keyframe times are measured from. A gap over `wake_threshold` or
///   a `suspend` is a resume: the clock advances by one nominal frame, so an
///   animation never catches up on time the host spent idle. A repeated or
///   backwards timestamp advances nothing;
/// - with reduced motion, jumps the clock to the end of the last track;
/// - samples the feed into the scene's plots once;
/// - calls the render function, only when the frame can differ from the one
///   drawn before: the first frame, a requested frame, a clock that moved
///   while a track still had keyframes ahead, or a plot whose samples changed.
///
/// `FrameReport::needs_next_frame` says whether another tick is needed: a track
/// is still running, or a feed published within `stale_after_ticks` ticks. When
/// it is false the host stops its display link and an idle app draws nothing.
/// `request_frame` is the way back: it is safe to call from any thread (a feed
/// producer, a scene replacement) and calls the wake function so the host can
/// restart its link.
///
/// The render function is how the driver reaches the surface (typically
/// `SkiaDawnSurface::render_incremental`), which keeps the driver free of the
/// GPU libraries and deterministic under a test clock.
class FrameDriver final {
public:
    using RenderFunction = std::function<bool(const Scene&, std::string*)>;
    using WakeFunction = foundation::FramePump::WakeFunction;

    explicit FrameDriver(RenderFunction render, FrameDriverOptions options = {});
    ~FrameDriver();
    FrameDriver(const FrameDriver&) = delete;
    FrameDriver& operator=(const FrameDriver&) = delete;

    /// The feed plots are sampled from, or null for none. Main thread. The
    /// feed must outlive its use here.
    void set_feed(value::ValueFeed* feed);

    /// The shared clock, gating and wake state; see `foundation::FramePump`.
    /// A host drives requests, suspend, wake and reduced motion through it.
    [[nodiscard]] foundation::FramePump& pump() noexcept { return pump_; }
    [[nodiscard]] const foundation::FramePump& pump() const noexcept { return pump_; }

    void set_reduced_motion(bool reduced) { pump_.set_reduced_motion(reduced); }
    [[nodiscard]] bool reduced_motion() const noexcept { return pump_.reduced_motion(); }
    void set_clock(double seconds) { pump_.set_clock(seconds); }
    [[nodiscard]] double clock() const noexcept { return pump_.clock(); }
    void set_wake(WakeFunction wake) { pump_.set_wake(std::move(wake)); }
    void request_frame() { pump_.request_frame(); }
    [[nodiscard]] bool frame_requested() const noexcept { return pump_.frame_requested(); }
    void suspend() noexcept { pump_.suspend(); }

    /// One frame at `now_seconds`. Mutates `scene`: its clock, and the samples
    /// of the plots a feed updated.
    FrameReport frame(Scene& scene, double now_seconds);

private:
    RenderFunction render_;
    FrameDriverOptions options_;
    foundation::FramePump pump_;
    PlotFeedSampler sampler_;
    value::ValueFeed* feed_ = nullptr;
    std::uint32_t feed_signature_ = 0;
    bool feed_live_ = false;
    std::uint32_t ticks_since_publish_ = 0;
    double rendered_clock_ = 0.0;
};

}  // namespace vellum::graphics
