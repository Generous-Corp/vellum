#include <vellum/foundation/frame_pump.hpp>

#include <utility>

namespace vellum::foundation {

FramePump::FramePump(FramePumpOptions options)
    : options_(options), reduced_motion_(options.reduced_motion) {}

void FramePump::set_reduced_motion(bool reduced) {
    reduced_motion_ = reduced;
    request_frame();
}

void FramePump::set_clock(double seconds) {
    clock_ = seconds;
    request_frame();
}

void FramePump::set_wake(WakeFunction wake) {
    wake_ = std::move(wake);
    if (wake_ && requested_.load(std::memory_order_acquire)) wake_();
}

void FramePump::request_frame() {
    if (!requested_.exchange(true, std::memory_order_acq_rel) && wake_) wake_();
}

void FramePump::suspend() noexcept { have_last_ = false; }

PumpReport FramePump::frame(double now_seconds, const AdvanceFunction& advance,
                            const RenderFunction& render) {
    PumpReport report;
    const bool requested = requested_.exchange(false, std::memory_order_acq_rel);

    FrameTick tick;
    if (!have_last_ || now_seconds - last_now_ > options_.wake_threshold) {
        tick.dt = options_.nominal_dt;
        tick.resumed = true;
        last_now_ = now_seconds;
    } else if (now_seconds > last_now_) {
        tick.dt = now_seconds - last_now_;
        last_now_ = now_seconds;
    }
    have_last_ = true;
    tick.clock = clock_ + tick.dt;
    tick.reduced_motion = reduced_motion_;

    const FrameStep step = advance ? advance(tick) : FrameStep{};
    clock_ = tick.clock;
    report.clock = tick.clock;
    report.dt = tick.dt;
    report.resumed = tick.resumed;

    bool exhausted = false;
    if (!rendered_once_ || requested || step.changed) {
        report.rendered = true;
        report.render_ok = render && render(&report.error);
        if (report.render_ok) {
            rendered_once_ = true;
            failed_renders_ = 0;
        } else {
            rendered_once_ = false;
            if (failed_renders_ < options_.max_render_retries) {
                ++failed_renders_;
            } else {
                exhausted = true;
            }
        }
    }

    report.needs_next_frame =
        step.wants_more || frame_requested() || (!report.render_ok && !exhausted);
    return report;
}

}  // namespace vellum::foundation
