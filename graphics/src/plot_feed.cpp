#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/plot_feed.hpp>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <optional>
#include <span>
#include <utility>

namespace vellum::graphics {

namespace {

constexpr float kNaN = std::numeric_limits<float>::quiet_NaN();

value::ChannelShape feed_shape(PlotChannelShape shape) noexcept {
    switch (shape) {
    case PlotChannelShape::scalar: return value::ChannelShape::scalar;
    case PlotChannelShape::meter: return value::ChannelShape::meter;
    case PlotChannelShape::vector: return value::ChannelShape::vector;
    }
    return value::ChannelShape::scalar;
}

void collect(SceneNode& node, std::vector<SceneNode*>& plots) {
    if (node.kind == SceneNode::Kind::plot && node.plot) plots.push_back(&node);
    for (auto& child : node.children) collect(child, plots);
}

bool same_samples(const std::vector<float>& a, const std::vector<float>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(), [](float left, float right) {
        return (std::isnan(left) && std::isnan(right)) || left == right;
    });
}

}  // namespace

struct PlotFeedSampler::State final {
    PlotSpec binding;
    std::optional<std::size_t> channel;
    std::uint32_t last_count = 0;
    bool published = false;
    std::uint32_t idle_ticks = 0;
    bool stale = false;
    // Scalar history: the slots, and how many samples have been taken.
    std::vector<float> slots;
    std::uint64_t taken = 0;
    // Read buffers, sized once.
    std::vector<float> block;
    std::vector<float> rms;
    std::vector<float> peak;
};

PlotFeedSampler::PlotFeedSampler(PlotFeedOptions options) : options_(options) {}

PlotFeedSampler::~PlotFeedSampler() = default;

PlotFeedReport PlotFeedSampler::sample(value::ValueFeed& feed, Scene& scene) {
    PlotFeedReport report;
    std::vector<SceneNode*> plots;
    collect(scene.root, plots);
    std::erase_if(states_, [&plots](const auto& entry) {
        return std::none_of(plots.begin(), plots.end(),
                            [&entry](const SceneNode* node) { return node->id == entry.first; });
    });
    for (SceneNode* node : plots) {
        const PlotSpec& spec = *node->plot;
        if (node->id.empty() || spec.channel.empty() || !plot_is_valid(spec)) continue;
        auto& slot = states_[node->id];
        const bool rebound = !slot || slot->binding.channel != spec.channel ||
                             slot->binding.shape != spec.shape ||
                             slot->binding.audio_channel != spec.audio_channel ||
                             slot->binding.slot_count != spec.slot_count ||
                             slot->binding.history != spec.history ||
                             slot->binding.style != spec.style;
        if (rebound) {
            slot = std::make_unique<State>();
            slot->binding = spec;
            slot->binding.samples.clear();
            slot->channel = feed.find(spec.channel, feed_shape(spec.shape));
            if (spec.shape == PlotChannelShape::scalar) slot->slots.assign(spec.slot_count, kNaN);
            if (spec.shape == PlotChannelShape::vector) slot->block.assign(spec.slot_count, 0.0F);
            if (spec.shape == PlotChannelShape::meter) {
                slot->rms.assign(static_cast<std::size_t>(spec.audio_channel) + 1U, 0.0F);
                slot->peak.assign(static_cast<std::size_t>(spec.audio_channel) + 1U, 0.0F);
            }
        }
        State& state = *slot;
        if (!state.channel || *state.channel >= feed.channel_count() ||
            feed.channel(*state.channel).name != spec.channel ||
            feed.channel(*state.channel).shape != feed_shape(spec.shape)) {
            state.channel = feed.find(spec.channel, feed_shape(spec.shape));
        }
        if (!state.channel) {
            report.diagnostics.push_back({
                .severity = LoweringDiagnostic::Severity::warning,
                .disposition = LoweringDiagnostic::Disposition::lowered,
                .code = std::string(kPlotChannelUnboundCode),
                .node_id = node->id,
                .property = "channel",
                .message = "plot " + node->id + " reads channel " + spec.channel +
                           ", which the feed does not have with that shape",
            });
            continue;
        }
        const std::size_t index = *state.channel;
        const auto info = feed.channel(index);
        const std::uint32_t count = feed.publish_count(index);
        const bool fresh = count != state.last_count;
        state.last_count = count;
        if (fresh) {
            state.idle_ticks = 0;
        } else if (state.published && state.idle_ticks < std::numeric_limits<std::uint32_t>::max()) {
            ++state.idle_ticks;
        }
        const bool stale = state.published && !fresh && options_.stale_after_ticks > 0U &&
                           state.idle_ticks >= options_.stale_after_ticks;

        std::optional<std::vector<float>> next;
        switch (spec.shape) {
        case PlotChannelShape::scalar: {
            float value = 0.0F;
            if (!fresh || !feed.read_scalar(index, value)) break;
            state.published = true;
            const std::uint64_t capacity = state.slots.size();
            if (spec.history == PlotHistory::scroll) {
                if (state.taken < capacity) {
                    state.slots[state.taken] = value;
                } else {
                    std::rotate(state.slots.begin(), state.slots.begin() + 1, state.slots.end());
                    state.slots.back() = value;
                }
            } else {
                const std::uint64_t at = state.taken % capacity;
                state.slots[at] = value;
                if (capacity > 1U) state.slots[(at + 1U) % capacity] = kNaN;
            }
            ++state.taken;
            next = state.slots;
            break;
        }
        case PlotChannelShape::vector: {
            if (stale && !state.stale) {
                next = std::vector<float>(spec.slot_count, info.neutral);
                break;
            }
            if (!fresh) break;
            const std::size_t written = feed.read_vector(index, state.block);
            state.published = true;
            std::vector<float> values(spec.slot_count, kNaN);
            std::copy_n(state.block.begin(), std::min(written, values.size()), values.begin());
            next = std::move(values);
            break;
        }
        case PlotChannelShape::meter: {
            if (stale && !state.stale) {
                next = std::vector<float>{info.neutral, info.neutral};
                break;
            }
            if (!fresh) break;
            const std::size_t channels = feed.read_meter(index, state.rms, state.peak);
            if (channels == 0U) break;
            state.published = true;
            next = spec.audio_channel < channels
                       ? std::vector<float>{state.rms[spec.audio_channel],
                                            state.peak[spec.audio_channel]}
                       : std::vector<float>{info.neutral, info.neutral};
            break;
        }
        }
        state.stale = stale;
        if (!next || same_samples(*next, spec.samples)) continue;
        auto replaced = std::make_shared<PlotSpec>(spec);
        replaced->samples = std::move(*next);
        node->plot = std::move(replaced);
        report.updated.push_back(node->id);
    }
    return report;
}

}  // namespace vellum::graphics
