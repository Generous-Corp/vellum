#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/plot_feed.hpp>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <optional>
#include <span>
#include <unordered_map>
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

bool fed(const PlotSpec& spec) { return !spec.channel.empty() && plot_is_valid(spec); }

// One read of a channel at this tick, shared by every plot bound to it.
struct ChannelRead final {
    std::uint32_t count = 0;
    bool available = false;
    float scalar = 0.0F;
    std::vector<float> block;
    std::size_t written = 0;
    std::vector<float> rms;
    std::vector<float> peak;
    std::size_t channels = 0;
};

}  // namespace

struct PlotFeedSampler::State final {
    PlotSpec binding;
    std::optional<std::size_t> channel;
    std::uint32_t last_count = 0;
    bool bound = false;
    bool published = false;
    std::uint32_t idle_ticks = 0;
    bool stale = false;
    // Scalar history: the slots, and how many samples have been taken.
    std::vector<float> slots;
    std::uint64_t taken = 0;
};

PlotFeedSampler::PlotFeedSampler(PlotFeedOptions options) : options_(options) {}

PlotFeedSampler::~PlotFeedSampler() = default;

PlotFeedReport PlotFeedSampler::sample(value::ValueFeed& feed, Scene& scene) {
    PlotFeedReport report;
    if (feed_ != &feed) {
        states_.clear();
        feed_ = &feed;
    }
    std::vector<SceneNode*> plots;
    collect(scene.root, plots);
    // Plots sharing an id are told apart by their order among that id.
    std::vector<std::string> keys;
    keys.reserve(plots.size());
    std::unordered_map<std::string, std::size_t> seen;
    for (const SceneNode* node : plots) {
        const std::size_t occurrence = seen[node->id]++;
        keys.push_back(node->id + '\x1f' + std::to_string(occurrence));
    }
    std::erase_if(states_, [&keys](const auto& entry) {
        return std::find(keys.begin(), keys.end(), entry.first) == keys.end();
    });

    // Resolve each plot's channel and size each channel's single read.
    std::vector<std::optional<std::size_t>> channels(plots.size());
    std::unordered_map<std::size_t, ChannelRead> reads;
    for (std::size_t at = 0; at < plots.size(); ++at) {
        const SceneNode* node = plots[at];
        const PlotSpec& spec = *node->plot;
        if (node->id.empty() || !fed(spec)) continue;
        channels[at] = feed.find(spec.channel, feed_shape(spec.shape));
        if (!channels[at]) {
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
        auto& read = reads[*channels[at]];
        if (spec.shape == PlotChannelShape::vector) {
            read.block.resize(std::max<std::size_t>(read.block.size(), spec.slot_count));
        }
        if (spec.shape == PlotChannelShape::meter) {
            const std::size_t wanted = static_cast<std::size_t>(spec.audio_channel) + 1U;
            read.rms.resize(std::max(read.rms.size(), wanted));
            read.peak.resize(std::max(read.peak.size(), wanted));
        }
    }
    for (auto& [index, read] : reads) {
        read.count = feed.publish_count(index);
        switch (feed.channel(index).shape) {
        case value::ChannelShape::scalar:
            read.available = feed.read_scalar(index, read.scalar);
            break;
        case value::ChannelShape::vector:
            read.written = feed.read_vector(index, read.block);
            read.available = read.count != 0U || read.written != 0U;
            break;
        case value::ChannelShape::meter:
            read.channels = feed.read_meter(index, read.rms, read.peak);
            read.available = read.channels != 0U;
            break;
        case value::ChannelShape::events:
            break;
        }
    }

    for (std::size_t at = 0; at < plots.size(); ++at) {
        if (!channels[at]) {
            SceneNode* lost = plots[at];
            if (lost->id.empty() || !fed(*lost->plot)) continue;
            states_.erase(keys[at]);
            if (lost->plot->samples.empty()) continue;
            auto cleared = std::make_shared<PlotSpec>(*lost->plot);
            cleared->samples.clear();
            lost->plot = std::move(cleared);
            report.updated.push_back(lost->id);
            continue;
        }
        SceneNode* node = plots[at];
        const PlotSpec& spec = *node->plot;
        auto& slot = states_[keys[at]];
        const bool rebound = !slot || slot->binding.channel != spec.channel ||
                             slot->binding.shape != spec.shape ||
                             slot->binding.audio_channel != spec.audio_channel ||
                             slot->binding.slot_count != spec.slot_count ||
                             slot->binding.history != spec.history ||
                             slot->binding.style != spec.style || slot->channel != channels[at];
        if (rebound) {
            slot = std::make_unique<State>();
            slot->binding = spec;
            slot->binding.samples.clear();
            slot->channel = channels[at];
            if (spec.shape == PlotChannelShape::scalar) slot->slots.assign(spec.slot_count, kNaN);
        }
        State& state = *slot;
        const auto& read = reads.at(*channels[at]);
        const auto info = feed.channel(*channels[at]);
        // A first look counts whatever the channel holds as fresh, so a count
        // that has wrapped to zero still yields the value it counts.
        const bool fresh = state.bound ? read.count != state.last_count : read.available;
        state.bound = true;
        state.last_count = read.count;
        if (fresh) {
            state.idle_ticks = 0;
        } else if (state.published && state.idle_ticks < std::numeric_limits<std::uint32_t>::max()) {
            ++state.idle_ticks;
        }
        const bool stale = state.published && !fresh && options_.stale_after_ticks > 0U &&
                           state.idle_ticks >= options_.stale_after_ticks;
        const bool falls_stale = stale && !state.stale;
        state.stale = stale;

        std::optional<std::vector<float>> next;
        switch (spec.shape) {
        case PlotChannelShape::scalar: {
            if (!(fresh && read.available) && !falls_stale) break;
            const float value = falls_stale ? info.neutral : read.scalar;
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
                const std::uint64_t at_slot = state.taken % capacity;
                state.slots[at_slot] = value;
                if (capacity > 1U) state.slots[(at_slot + 1U) % capacity] = kNaN;
            }
            ++state.taken;
            next = state.slots;
            break;
        }
        case PlotChannelShape::vector: {
            if (falls_stale) {
                next = std::vector<float>(spec.slot_count, info.neutral);
                break;
            }
            if (!fresh || !read.available) break;
            state.published = true;
            std::vector<float> values(spec.slot_count, kNaN);
            std::copy_n(read.block.begin(), std::min(read.written, values.size()), values.begin());
            next = std::move(values);
            break;
        }
        case PlotChannelShape::meter: {
            if (falls_stale) {
                next = std::vector<float>{info.neutral, info.neutral};
                break;
            }
            if (!fresh || !read.available) break;
            state.published = true;
            next = spec.audio_channel < read.channels
                       ? std::vector<float>{read.rms[spec.audio_channel],
                                            read.peak[spec.audio_channel]}
                       : std::vector<float>{info.neutral, info.neutral};
            break;
        }
        }
        // A plot bound afresh to a channel with nothing published draws
        // nothing, rather than the samples of what it was bound to before.
        if (rebound && !next && !read.available) next = std::vector<float>{};
        if (!next || same_samples(*next, spec.samples)) continue;
        auto replaced = std::make_shared<PlotSpec>(spec);
        replaced->samples = std::move(*next);
        node->plot = std::move(replaced);
        report.updated.push_back(node->id);
    }
    return report;
}

}  // namespace vellum::graphics
