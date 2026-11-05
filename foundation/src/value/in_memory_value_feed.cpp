#include <vellum/value/in_memory_value_feed.hpp>

#include <algorithm>
#include <array>
#include <atomic>
#include <stdexcept>
#include <utility>

namespace vellum::value {

namespace {

// The triple buffer's shared word: the index of the slot most recently handed
// over, plus a bit that says the reader has not taken it yet.
constexpr std::uint8_t slot_mask = 0x3;
constexpr std::uint8_t fresh_bit = 0x4;

static_assert(std::atomic<std::uint8_t>::is_always_lock_free);
static_assert(std::atomic<std::uint32_t>::is_always_lock_free);

std::size_t checked_product(std::size_t lhs, std::size_t rhs, std::size_t limit) {
    if (lhs > limit / rhs)
        throw std::invalid_argument("InMemoryValueFeed: channel capacity is too large");
    return lhs * rhs;
}

}  // namespace

// Each channel is a triple buffer. The producer owns `back`, the reader owns
// `front`, and the third slot sits in `shared`. A publish fills `back`, then
// swaps it into `shared` (marked fresh) and takes the old shared slot as its
// next `back`. A read that finds the fresh bit swaps its `front` into
// `shared` and takes the published slot. The slots never overlap, so neither
// side waits and no publish is ever read half-written.
struct InMemoryValueFeed::Channel {
    ChannelSpec spec;
    std::size_t floats_per_slot = 0;
    std::vector<float> floats;        // 3 slots of `floats_per_slot`
    std::vector<ChannelEvent> events; // 3 slots of `spec.capacity` (events only)
    std::array<std::size_t, 3> counts{};

    std::atomic<std::uint8_t> shared{1};
    std::uint8_t back = 2;  // producer-owned
    std::uint8_t front = 0; // reader-owned
    std::atomic<std::uint32_t> publishes{0};

    float* float_slot(std::uint8_t slot) noexcept {
        return floats.data() + static_cast<std::size_t>(slot) * floats_per_slot;
    }
    ChannelEvent* event_slot(std::uint8_t slot) noexcept {
        return events.data() + static_cast<std::size_t>(slot) * spec.capacity;
    }

    // Producer: hand over the filled back slot.
    void commit(std::size_t count) noexcept {
        counts[back] = count;
        const std::uint8_t previous =
            shared.exchange(static_cast<std::uint8_t>(back | fresh_bit), std::memory_order_acq_rel);
        back = previous & slot_mask;
        publishes.fetch_add(1, std::memory_order_release);
    }

    // Reader: take the newest slot if one was handed over since the last read.
    std::uint8_t acquire() noexcept {
        if (shared.load(std::memory_order_acquire) & fresh_bit) {
            const std::uint8_t previous = shared.exchange(front, std::memory_order_acq_rel);
            front = previous & slot_mask;
        }
        return front;
    }
};

InMemoryValueFeed::InMemoryValueFeed(std::vector<ChannelSpec> channels) {
    channels_.reserve(channels.size());
    for (auto& spec : channels) {
        if (spec.name.empty())
            throw std::invalid_argument("InMemoryValueFeed: empty channel name");
        for (const auto& existing : channels_) {
            if (existing->spec.shape == spec.shape && existing->spec.name == spec.name)
                throw std::invalid_argument("InMemoryValueFeed: duplicate channel \"" +
                                            spec.name + "\"");
        }
        auto c = std::make_unique<Channel>();
        const std::size_t float_limit = std::vector<float>{}.max_size();
        const std::size_t event_limit = std::vector<ChannelEvent>{}.max_size();
        std::size_t float_count = 0;
        switch (spec.shape) {
        case ChannelShape::scalar:
            spec.capacity = 1;
            c->floats_per_slot = 1;
            float_count = checked_product(c->floats_per_slot, 3, float_limit);
            break;
        case ChannelShape::meter:
            c->floats_per_slot = checked_product(spec.capacity, 2, float_limit); // rms, then peak
            float_count = checked_product(c->floats_per_slot, 3, float_limit);
            break;
        case ChannelShape::vector:
            c->floats_per_slot = spec.capacity;
            float_count = checked_product(c->floats_per_slot, 3, float_limit);
            break;
        case ChannelShape::events:
            c->floats_per_slot = 0;
            break;
        }
        if (spec.capacity == 0)
            throw std::invalid_argument("InMemoryValueFeed: zero capacity for \"" + spec.name +
                                        "\"");
        if (spec.shape != ChannelShape::events)
            c->floats.assign(float_count, 0.0f);
        else
            c->events.assign(checked_product(spec.capacity, 3, event_limit), ChannelEvent{});
        c->spec = std::move(spec);
        channels_.push_back(std::move(c));
    }
}

InMemoryValueFeed::~InMemoryValueFeed() = default;

InMemoryValueFeed::Channel* InMemoryValueFeed::channel_with(std::size_t index,
                                                            ChannelShape shape) const noexcept {
    if (index >= channels_.size())
        return nullptr;
    Channel* c = channels_[index].get();
    return c->spec.shape == shape ? c : nullptr;
}

// ── Producer side ───────────────────────────────────────────────────────────

bool InMemoryValueFeed::publish_scalar(std::size_t index, float value) noexcept {
    Channel* c = channel_with(index, ChannelShape::scalar);
    if (!c)
        return false;
    *c->float_slot(c->back) = value;
    c->commit(1);
    return true;
}

bool InMemoryValueFeed::publish_meter(std::size_t index, std::span<const float> rms,
                                      std::span<const float> peak) noexcept {
    Channel* c = channel_with(index, ChannelShape::meter);
    if (!c || rms.size() != peak.size())
        return false;
    const std::size_t n = std::min(rms.size(), c->spec.capacity);
    float* slot = c->float_slot(c->back);
    std::copy_n(rms.begin(), n, slot);
    std::copy_n(peak.begin(), n, slot + c->spec.capacity);
    c->commit(n);
    return true;
}

bool InMemoryValueFeed::publish_vector(std::size_t index,
                                       std::span<const float> samples) noexcept {
    Channel* c = channel_with(index, ChannelShape::vector);
    if (!c)
        return false;
    const std::size_t n = std::min(samples.size(), c->spec.capacity);
    std::copy_n(samples.begin(), n, c->float_slot(c->back));
    c->commit(n);
    return true;
}

bool InMemoryValueFeed::publish_events(std::size_t index,
                                       std::span<const ChannelEvent> events) noexcept {
    Channel* c = channel_with(index, ChannelShape::events);
    if (!c)
        return false;
    const std::size_t n = std::min(events.size(), c->spec.capacity);
    std::copy_n(events.begin(), n, c->event_slot(c->back));
    c->commit(n);
    return true;
}

std::size_t InMemoryValueFeed::capacity(std::size_t index) const noexcept {
    return index < channels_.size() ? channels_[index]->spec.capacity : 0;
}

// ── ValueFeed ───────────────────────────────────────────────────────────────

std::size_t InMemoryValueFeed::channel_count() const noexcept {
    return channels_.size();
}

ChannelInfo InMemoryValueFeed::channel(std::size_t index) const noexcept {
    if (index >= channels_.size())
        return {};
    const auto& s = channels_[index]->spec;
    return ChannelInfo{s.name, s.unit, s.shape, s.neutral};
}

bool InMemoryValueFeed::read_scalar(std::size_t index, float& value) noexcept {
    Channel* c = channel_with(index, ChannelShape::scalar);
    if (!c)
        return false;
    const std::uint8_t slot = c->acquire();
    if (c->counts[slot] == 0)
        return false; // nothing published yet
    value = *c->float_slot(slot);
    return true;
}

std::size_t InMemoryValueFeed::read_meter(std::size_t index, std::span<float> rms,
                                          std::span<float> peak) noexcept {
    Channel* c = channel_with(index, ChannelShape::meter);
    if (!c)
        return 0;
    const std::uint8_t slot = c->acquire();
    const std::size_t n = c->counts[slot];
    const float* data = c->float_slot(slot);
    std::copy_n(data, std::min(n, rms.size()), rms.begin());
    std::copy_n(data + c->spec.capacity, std::min(n, peak.size()), peak.begin());
    return n;
}

std::size_t InMemoryValueFeed::read_vector(std::size_t index,
                                           std::span<float> samples) noexcept {
    Channel* c = channel_with(index, ChannelShape::vector);
    if (!c)
        return 0;
    const std::uint8_t slot = c->acquire();
    const std::size_t n = std::min(c->counts[slot], samples.size());
    std::copy_n(c->float_slot(slot), n, samples.begin());
    return n;
}

std::size_t InMemoryValueFeed::read_events(std::size_t index,
                                           std::span<ChannelEvent> events) noexcept {
    Channel* c = channel_with(index, ChannelShape::events);
    if (!c)
        return 0;
    const std::uint8_t slot = c->acquire();
    const std::size_t n = std::min(c->counts[slot], events.size());
    std::copy_n(c->event_slot(slot), n, events.begin());
    return n;
}

std::uint32_t InMemoryValueFeed::publish_count(std::size_t index) const noexcept {
    return index < channels_.size()
               ? channels_[index]->publishes.load(std::memory_order_acquire)
               : 0;
}

}  // namespace vellum::value
