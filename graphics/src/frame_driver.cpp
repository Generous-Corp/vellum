#include <vellum/graphics/frame_driver.hpp>

#include <vellum/graphics/animation.hpp>

#include <algorithm>
#include <utility>

namespace vellum::graphics {

namespace {

bool any_plot(const SceneNode& node) noexcept {
    if (node.kind == SceneNode::Kind::plot) return true;
    return std::any_of(node.children.begin(), node.children.end(),
                       [](const SceneNode& child) { return any_plot(child); });
}

std::uint32_t feed_signature(const value::ValueFeed& feed) noexcept {
    std::uint32_t signature = 0;
    for (std::size_t index = 0, count = feed.channel_count(); index < count; ++index) {
        signature += feed.publish_count(index);
    }
    return signature;
}

}  // namespace

FrameDriver::FrameDriver(RenderFunction render, FrameDriverOptions options)
    : render_(std::move(render)),
      options_(options),
      reduced_motion_(options.reduced_motion),
      sampler_(options.feed) {}

FrameDriver::~FrameDriver() = default;

void FrameDriver::set_feed(value::ValueFeed* feed) {
    feed_ = feed;
    feed_signature_ = 0;
    feed_live_ = false;
    ticks_since_publish_ = 0;
    requested_.store(true, std::memory_order_release);
}

void FrameDriver::set_reduced_motion(bool reduced) {
    reduced_motion_ = reduced;
    requested_.store(true, std::memory_order_release);
}

void FrameDriver::set_clock(double seconds) {
    clock_ = seconds;
    requested_.store(true, std::memory_order_release);
}

void FrameDriver::set_wake(WakeFunction wake) { wake_ = std::move(wake); }

void FrameDriver::request_frame() {
    requested_.store(true, std::memory_order_release);
    if (wake_) wake_();
}

void FrameDriver::suspend() noexcept { have_last_ = false; }

FrameReport FrameDriver::frame(Scene& scene, double now_seconds) {
    FrameReport report;
    const bool requested = requested_.exchange(false, std::memory_order_acq_rel);

    double dt = 0.0;
    if (!have_last_ || now_seconds - last_now_ > options_.wake_threshold) {
        dt = options_.nominal_dt;
        report.resumed = true;
        last_now_ = now_seconds;
    } else if (now_seconds > last_now_) {
        dt = now_seconds - last_now_;
        last_now_ = now_seconds;
    }
    have_last_ = true;
    clock_ += dt;

    const std::optional<double> end = tracks_end_time(scene);
    if (reduced_motion_ && end && clock_ < *end) {
        dt += *end - clock_;
        clock_ = *end;
    }
    scene.clock = clock_;
    report.clock = clock_;
    report.dt = dt;

    bool plots_changed = false;
    if (feed_ != nullptr && any_plot(scene.root)) {
        const std::uint32_t signature = feed_signature(*feed_);
        if (signature != feed_signature_) {
            feed_signature_ = signature;
            feed_live_ = true;
            ticks_since_publish_ = 0;
        } else if (feed_live_) {
            ++ticks_since_publish_;
        }
        report.feed = sampler_.sample(*feed_, scene);
        report.sampled = true;
        plots_changed = !report.feed.updated.empty();
        const std::uint32_t stale_after = options_.feed.stale_after_ticks;
        if (feed_live_ && ticks_since_publish_ >= stale_after) feed_live_ = false;
    } else {
        feed_live_ = false;
    }

    const bool track_moved =
        end && (!rendered_once_ || (rendered_clock_ < *end && clock_ != rendered_clock_));
    if (!rendered_once_ || requested || track_moved || plots_changed) {
        report.rendered = true;
        report.render_ok = render_ && render_(scene, &report.error);
        if (report.render_ok) {
            rendered_once_ = true;
            rendered_clock_ = clock_;
        } else {
            rendered_once_ = false;
        }
    }

    report.tracks_running = end && clock_ < *end;
    report.feed_active = feed_live_;
    report.needs_next_frame = report.tracks_running || feed_live_ || frame_requested();
    return report;
}

}  // namespace vellum::graphics
