#pragma once

/// @file
/// `vellum::value::InMemoryValueFeed`: a `ValueFeed` its owner publishes into.
/// Vellum's gallery, tests and standalone demos drive meters and scopes with
/// it, and an audio callback can feed it directly.
///
/// Writing: each channel has one producer thread at a time (normally the
/// audio thread). `publish_*()` is lock-free, allocation-free and wait-free:
/// the producer fills a private slot and swaps it in with one atomic
/// exchange, so a reader never sees a half-written publish. A publish with the
/// wrong shape for its channel returns false and changes nothing.
///
/// Reading keeps the `ValueFeed` contract (see `value_feed.hpp`): one reader
/// thread, latest-wins, lock-free and allocation-free.

#include <vellum/value/value_feed.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace vellum::value {

/// One channel of an `InMemoryValueFeed`.
struct ChannelSpec {
    std::string name;
    std::string unit;
    ChannelShape shape = ChannelShape::scalar;
    float neutral = 0.0f;
    /// Largest publish the channel holds: audio channels for a meter, samples
    /// for a vector, occurrences for events. Ignored for a scalar. A longer
    /// publish is truncated to it.
    std::size_t capacity = 1;
};

class InMemoryValueFeed final : public ValueFeed {
public:
    /// Allocates every channel's storage up front. Throws
    /// `std::invalid_argument` for an empty name, a repeated name and shape,
    /// or a zero capacity on a non-scalar channel.
    explicit InMemoryValueFeed(std::vector<ChannelSpec> channels);
    ~InMemoryValueFeed() override;

    InMemoryValueFeed(const InMemoryValueFeed&) = delete;
    InMemoryValueFeed& operator=(const InMemoryValueFeed&) = delete;

    // ── Producer side ───────────────────────────────────────────────────────

    /// Publish a scalar. False if `index` is not a scalar channel.
    bool publish_scalar(std::size_t index, float value) noexcept;
    /// Publish one meter frame. False if `index` is not a meter channel or
    /// `rms` and `peak` differ in size.
    bool publish_meter(std::size_t index, std::span<const float> rms,
                       std::span<const float> peak) noexcept;
    /// Publish a block of samples. False if `index` is not a vector channel.
    bool publish_vector(std::size_t index, std::span<const float> samples) noexcept;
    /// Publish one block's occurrences (possibly none). False if `index` is
    /// not an events channel.
    bool publish_events(std::size_t index, std::span<const ChannelEvent> events) noexcept;

    /// The channel's `ChannelSpec::capacity` (1 for a scalar), or 0 for an
    /// index out of range.
    [[nodiscard]] std::size_t capacity(std::size_t index) const noexcept;

    // ── ValueFeed ───────────────────────────────────────────────────────────

    [[nodiscard]] std::size_t channel_count() const noexcept override;
    [[nodiscard]] ChannelInfo channel(std::size_t index) const noexcept override;
    bool read_scalar(std::size_t index, float& value) noexcept override;
    std::size_t read_meter(std::size_t index, std::span<float> rms,
                           std::span<float> peak) noexcept override;
    std::size_t read_vector(std::size_t index, std::span<float> samples) noexcept override;
    std::size_t read_events(std::size_t index,
                            std::span<ChannelEvent> events) noexcept override;
    [[nodiscard]] std::uint32_t publish_count(std::size_t index) const noexcept override;

private:
    struct Channel;

    Channel* channel_with(std::size_t index, ChannelShape shape) const noexcept;

    std::vector<std::unique_ptr<Channel>> channels_;
};

}  // namespace vellum::value
