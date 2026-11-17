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
        const auto index = feed.find(node.plot->channel, channel_shape(node.plot->shape));
        if (index) {
            signature = (signature * 31U + static_cast<std::uint32_t>(*index)) * 31U +
                        feed.publish_count(*index);
        }
    }
    for (const auto& child : node.children) add_bound_publishes(child, feed, signature);
}

std::uint32_t feed_signature(const value::ValueFeed& feed, const Scene& scene) noexcept {
    std::uint32_t signature = 0;
    add_bound_publishes(scene.root, feed, signature);
    return signature;
}

foundation::FramePumpOptions pump_options(const FrameDriverOptions& options) noexcept {
    return {.nominal_dt = options.nominal_dt,
            .wake_threshold = options.wake_threshold,
            .max_render_retries = options.max_render_retries,
            .reduced_motion = options.reduced_motion};
}

}  // namespace

FrameDriver::FrameDriver(RenderFunction render, FrameDriverOptions options)
    : render_(std::move(render)), options_(options), pump_(pump_options(options)),
      sampler_(options.feed) {}

FrameDriver::~FrameDriver() = default;

void FrameDriver::set_feed(value::ValueFeed* feed) {
    feed_ = feed;
    feed_signature_ = 0;
    feed_live_ = false;
    ticks_since_publish_ = 0;
    pump_.request_frame();
}

FrameReport FrameDriver::frame(Scene& scene, double now_seconds) {
    FrameReport report;
    const auto advance = [&](foundation::FrameTick& tick) {
        const std::optional<double> end = tracks_end_time(scene);
        if (tick.reduced_motion && end && tick.clock < *end) {
            tick.dt += *end - tick.clock;
            tick.clock = *end;
        }
        scene.clock = tick.clock;

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
            if (feed_live_ && ticks_since_publish_ >= options_.feed.stale_after_ticks) {
                feed_live_ = false;
            }
        } else {
            feed_live_ = false;
        }

        report.tracks_running = end && tick.clock < *end;
        report.feed_active = feed_live_;
        const bool track_moved = end && rendered_clock_ < *end && tick.clock != rendered_clock_;
        return foundation::FrameStep{.changed = track_moved || plots_changed,
                                     .wants_more = report.tracks_running || feed_live_};
    };
    const auto render = [&](std::string* error) {
        const bool ok = render_ && render_(scene, error);
        if (ok) rendered_clock_ = scene.clock;
        return ok;
    };
    const foundation::PumpReport pumped = pump_.frame(now_seconds, advance, render);
    report.clock = pumped.clock;
    report.dt = pumped.dt;
    report.resumed = pumped.resumed;
    report.rendered = pumped.rendered;
    report.render_ok = pumped.render_ok;
    report.error = pumped.error;
    report.needs_next_frame = pumped.needs_next_frame;
    return report;
}

}  // namespace vellum::graphics
