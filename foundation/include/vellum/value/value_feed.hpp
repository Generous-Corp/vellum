#pragma once

/// @file
/// `vellum::value::ValueFeed`: the live values a Vellum widget draws (meters,
/// readouts, scopes, event markers).
///
/// Contract:
/// - A feed has a fixed set of named channels, settled before any value is
///   published, so an index found once stays valid for the life of the feed.
///   `find()` looks a channel up by its verbatim name and its shape.
/// - Each channel has one shape: `scalar` (one number), `meter` (per-channel
///   RMS and peak), `vector` (a block of samples) or `events` (a bounded list
///   of occurrences from one audio block).
/// - Every channel is latest-wins. A reader that falls behind sees the newest
///   publish, never a history and never a mix of two publishes.
/// - Reading a channel with the wrong shape's call returns false (scalar) or
///   0 (the others) and writes nothing.
/// - `publish_count()` counts publishes per channel, monotonically (it wraps
///   at 2^32). A reader compares it across ticks: an unchanged count means the
///   writer stopped, a changed count with an unchanged value means the value
///   held steady. A stale channel's reader falls back to the channel's
///   `neutral` value.
/// - One reader thread: every read through one feed happens on the same thread,
///   normally the editor's frame tick. Reads are lock-free and allocation-free,
///   so they are safe at tick; widgets still snapshot at tick and paint from
///   the snapshot.
///
/// This is the shape of Pulp's `format::VisualPort` without Pulp types, so an
/// integration package maps it one to one.

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

namespace vellum::value {

/// The shape of one channel.
enum class ChannelShape : std::uint8_t {
    scalar = 0, ///< one number per publish (gain reduction, an envelope level)
    meter = 1,  ///< per-channel RMS and peak levels
    vector = 2, ///< a block of samples for a scope-style display
    events = 3, ///< a bounded list of occurrences from one audio block
};

/// What a channel is. The strings stay valid for the life of the feed.
struct ChannelInfo {
    std::string_view name; ///< verbatim lookup key
    std::string_view unit; ///< display only ("dB", "Hz"); may be empty
    ChannelShape shape = ChannelShape::scalar;
    /// The value a reader falls back to when the channel goes stale.
    float neutral = 0.0f;
};

/// One occurrence on an events channel.
struct ChannelEvent {
    std::uint32_t frame_index = 0; ///< sample offset inside its audio block
    float value = 0.0f;            ///< payload; a zero value is still an event
};

/// Read access to a set of latest-wins channels. See the file comment for the
/// contract every implementation keeps.
class ValueFeed {
public:
    virtual ~ValueFeed() = default;

    [[nodiscard]] virtual std::size_t channel_count() const noexcept = 0;
    /// Description of channel `index` (< `channel_count()`).
    [[nodiscard]] virtual ChannelInfo channel(std::size_t index) const noexcept = 0;

    /// Latest value of a scalar channel. False if `index` is not a scalar
    /// channel or nothing has been published yet.
    virtual bool read_scalar(std::size_t index, float& value) noexcept = 0;
    /// Latest frame of a meter channel into `rms` and `peak`. Returns the
    /// number of audio channels the frame carries (possibly more than the
    /// spans hold, which are filled up to their size), or 0 if `index` is not
    /// a meter channel or nothing has been published yet.
    virtual std::size_t read_meter(std::size_t index, std::span<float> rms,
                                   std::span<float> peak) noexcept = 0;
    /// Latest block of a vector channel into `samples`. Returns the number of
    /// samples written, or 0 if `index` is not a vector channel.
    virtual std::size_t read_vector(std::size_t index,
                                    std::span<float> samples) noexcept = 0;
    /// Occurrences of the latest published block of an events channel. Returns
    /// the number written, or 0 if `index` is not an events channel.
    virtual std::size_t read_events(std::size_t index,
                                    std::span<ChannelEvent> events) noexcept = 0;
    /// Monotonic publish count of channel `index`; 0 for an index out of
    /// range.
    [[nodiscard]] virtual std::uint32_t publish_count(std::size_t index) const noexcept = 0;

    /// Index of the channel called `name` with shape `shape`, or nullopt.
    [[nodiscard]] std::optional<std::size_t> find(std::string_view name,
                                                  ChannelShape shape) const noexcept {
        for (std::size_t i = 0, n = channel_count(); i < n; ++i) {
            const auto c = channel(i);
            if (c.shape == shape && c.name == name)
                return i;
        }
        return std::nullopt;
    }
};

}  // namespace vellum::value
