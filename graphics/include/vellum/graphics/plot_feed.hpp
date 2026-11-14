#pragma once

#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/value/value_feed.hpp>

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace vellum::graphics {

/// What one `PlotFeedSampler::sample` call did.
struct PlotFeedReport final {
    /// The plot nodes whose samples changed, in depth-first order.
    std::vector<std::string> updated;
    /// One `kPlotChannelUnboundCode` warning for each plot whose channel the
    /// feed does not have with the plot's shape.
    std::vector<LoweringDiagnostic> diagnostics;
};

struct PlotFeedOptions final {
    /// Ticks without a publish after which a channel is stale and its plot
    /// falls back to the channel's neutral value. Zero never goes stale.
    std::uint32_t stale_after_ticks = 8;
};

/// Feeds the plot nodes of a scene from a `vellum::value::ValueFeed`.
///
/// `sample` is the frame tick: it reads each bound channel once, the way the
/// `ValueFeed` contract asks (one reader thread, latest-wins, publish counts
/// compared across ticks), so plots sharing a channel see the same publish,
/// and replaces the spec of every plot whose samples
/// changed with one carrying the new samples, so the scene stays a value the
/// renderer paints from. Call it on the render thread, which owns the scene
/// and the surface; the feed's producers may publish from any thread.
///
/// - A scalar channel appends one sample per tick that saw a publish, so a
///   value held steady keeps scrolling. Several publishes between ticks are
///   one sample: the feed is latest-wins.
/// - A vector channel's latest block fills the slots from slot 0.
/// - A meter channel's latest frame gives the level (RMS) and peak of
///   `PlotSpec::audio_channel`.
/// - A channel never published draws nothing. A channel stale for
///   `stale_after_ticks` ticks shows its neutral value: the level and peak
///   of a meter, every slot of a vector plot, and one neutral sample appended
///   to a scalar history, which then stops.
///
/// State is kept per plot id, and per order among plots that share an id; a
/// plot whose binding changes starts afresh, a plot missing from the scene at
/// a tick is forgotten, a different feed starts every plot afresh, and a plot
/// without an id or a channel is not fed.
class PlotFeedSampler final {
public:
    explicit PlotFeedSampler(PlotFeedOptions options = {});
    ~PlotFeedSampler();
    PlotFeedSampler(const PlotFeedSampler&) = delete;
    PlotFeedSampler& operator=(const PlotFeedSampler&) = delete;

    PlotFeedReport sample(value::ValueFeed& feed, Scene& scene);

private:
    struct State;
    PlotFeedOptions options_;
    const value::ValueFeed* feed_ = nullptr;
    std::unordered_map<std::string, std::unique_ptr<State>> states_;
};

}  // namespace vellum::graphics
