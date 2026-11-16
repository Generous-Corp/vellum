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

PlotChannelShape plot_shape(const PlotSpec& spec) noexcept { return spec.shape; }

value::ChannelShape channel_shape(PlotChannelShape shape) noexcept {
    switch (shape) {
    case PlotChannelShape::scalar: return value::ChannelShape::scalar;
    case PlotChannelShape::meter: return value::ChannelShape::meter;
    case PlotChannelShape::vector: return value::ChannelShape::vector;
    }
    return value::ChannelShape::scalar;
}

void add_bound_publishes(const SceneNode& node, const value::ValueFeed& feed,
                         std::uint32_t& signature) noexcept {
    if (node.kind == SceneNode::Kind::plot && node.plot != nullptr && !node.plot->channel.empty()) {
        const auto index = feed.find(node.plot->channel, channel_shape(plot_shape(*node.plot)));
        if (index) signature = signature * 31U + feed.publish_count(*index);
    }
    for (const auto& child : node.children) add_bound_publishes(child, feed, signature);
}

std::uint32_t feed_signature(const value::ValueFeed& feed, const Scene& scene) noexcept {
    std::uint32_t signature = 0;
    add_bound_publishes(scene.root, feed, signature);
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
    request_frame();
}

void FrameDriver::set_reduced_motion(bool reduced) {
    reduced_motion_ = reduced;
    request_frame();
}

void FrameDriver::set_clock(double seconds) {
    clock_ = seconds;
    request_frame();
}

void FrameDriver::set_wake(WakeFunction wake) { wake_ = std::move(wake); }

void FrameDriver::request_frame() {
    if (!requested_.exchange(true, std::memory_order_acq_rel) && wake_) wake_();
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
        const std::uint32_t signature = feed_signature(*feed_, scene);
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
    report.needs_next_frame =
        report.tracks_running || feed_live_ || frame_requested() || !report.render_ok;
    return report;
}

}  // namespace vellum::graphics
