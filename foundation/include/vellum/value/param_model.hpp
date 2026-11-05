#pragma once

/// @file
/// `vellum::value::ParamModel`: the parameters a Vellum widget binds to.
///
/// Contract:
/// - A widget names a parameter by its design key, a verbatim string such as
///   "cutoff". `resolve()` turns a key into a `ParamHandle` once; every other
///   call takes the handle, so a hot path never hashes or compares strings.
/// - Values are normalized to [0, 1]. A model maps them to whatever plain
///   range its parameters have; widgets and design import never see one.
/// - A drag or an edit is a gesture: `begin()`, any number of
///   `set_normalized()` calls, then `end()`. Gestures on one handle nest, and a
///   model reports one gesture to its owner per outermost `begin()`/`end()`
///   pair. An `end()` with no open gesture does nothing.
/// - `value_count()` is the number of values a parameter takes, not the number
///   of intervals between them: a toggle is 2, a five-way switch is 5, and 0
///   means continuous. 1 is a parameter with a single value. The normalized
///   value of index `i` among `n` values is `i / (n - 1)`;
///   `value_index_to_normalized()` and `normalized_to_value_index()` do that
///   arithmetic, and a caller never divides by a count itself.
/// - `subscribe()` registers a listener that hears every value change,
///   whatever made it (this model's caller, the host, an automation lane). The
///   returned `Subscription` removes the listener when it is reset or
///   destroyed.
///
/// Call context: every call runs on the one UI thread that owns the model,
/// from event handling or the frame tick. None is called from paint: a view
/// snapshots what it draws at tick and paints from the snapshot. The audio
/// thread never touches a `ParamModel`.
///
/// This is the shape of Pulp's `format::ParamPort` as a Vellum widget sees it,
/// without Pulp types; an integration package implements it over that port.

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <utility>

namespace vellum::value {

/// A resolved parameter. Opaque to callers; only the model that issued it
/// interprets `id` and `owner`. A default-constructed handle is invalid, and
/// every `ParamModel` call ignores an invalid or foreign handle (reads return
/// 0, an empty string, or 0 values).
struct ParamHandle {
    static constexpr std::uint32_t invalid_id = 0xFFFF'FFFFu;

    std::uint32_t id = invalid_id;
    const void* owner = nullptr;

    [[nodiscard]] constexpr bool valid() const noexcept {
        return id != invalid_id && owner != nullptr;
    }
    friend constexpr bool operator==(ParamHandle, ParamHandle) noexcept = default;
};

/// Owns one listener registration. Destroying it, or calling `reset()`,
/// removes the listener; the cancel action runs exactly once. Move-only.
class Subscription {
public:
    Subscription() = default;
    /// Adopt a registration. Model implementations construct these.
    explicit Subscription(std::function<void()> cancel) noexcept
        : cancel_(std::move(cancel)) {}
    Subscription(Subscription&& other) noexcept
        : cancel_(std::exchange(other.cancel_, {})) {}
    Subscription& operator=(Subscription&& other) noexcept {
        if (this != &other) {
            reset();
            cancel_ = std::exchange(other.cancel_, {});
        }
        return *this;
    }
    Subscription(const Subscription&) = delete;
    Subscription& operator=(const Subscription&) = delete;
    ~Subscription() { reset(); }

    /// Remove the listener now. Idempotent.
    void reset() noexcept {
        if (auto cancel = std::exchange(cancel_, {}))
            cancel();
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return static_cast<bool>(cancel_);
    }

private:
    std::function<void()> cancel_;
};

/// Key-based, normalized parameter access for widgets. See the file comment
/// for the contract every implementation keeps.
class ParamModel {
public:
    /// Hears a value change: the parameter and its new normalized value.
    using Listener = std::function<void(ParamHandle, double normalized)>;

    virtual ~ParamModel() = default;

    /// The parameter called `key`, or an invalid handle when there is none.
    /// A handle stays valid for the life of the model.
    [[nodiscard]] virtual ParamHandle resolve(std::string_view key) = 0;

    /// Current normalized value in [0, 1].
    [[nodiscard]] virtual double normalized(ParamHandle param) = 0;

    /// Set the normalized value. Out-of-range values clamp to [0, 1]; a
    /// parameter with two or more values snaps to the nearest one; NaN is
    /// ignored. Listeners hear the change when the value actually moves.
    virtual void set_normalized(ParamHandle param, double normalized) = 0;

    /// Open a gesture. Nests: only the outermost `begin()` reaches the host.
    virtual void begin(ParamHandle param) = 0;

    /// Close the innermost open gesture. Only the outermost `end()` reaches
    /// the host. Does nothing when no gesture is open.
    virtual void end(ParamHandle param) = 0;

    /// The text a widget shows for `normalized` (not necessarily the current
    /// value), including its unit.
    [[nodiscard]] virtual std::string display_text(ParamHandle param,
                                                   double normalized) = 0;

    /// Number of values the parameter takes; 0 means continuous.
    [[nodiscard]] virtual int value_count(ParamHandle param) = 0;

    /// Register `listener` for every value change on every parameter.
    [[nodiscard]] virtual Subscription subscribe(Listener listener) = 0;
};

/// Normalized value of value index `index` among `value_count` values, with
/// the index clamped into range. 0 for a continuous (0) or single-valued (1)
/// parameter, which have no index to speak of.
[[nodiscard]] constexpr double value_index_to_normalized(int index,
                                                         int value_count) noexcept {
    if (value_count <= 1)
        return 0.0;
    const int last = value_count - 1;
    const int clamped = index < 0 ? 0 : (index > last ? last : index);
    return static_cast<double>(clamped) / static_cast<double>(last);
}

/// Index of the value nearest to `normalized` among `value_count` values. A
/// value halfway between two indices snaps to the higher one. 0 for a
/// continuous (0) or single-valued (1) parameter, and for NaN.
[[nodiscard]] constexpr int normalized_to_value_index(double normalized,
                                                      int value_count) noexcept {
    if (value_count <= 1 || !(normalized == normalized))
        return 0;
    const double clamped = normalized < 0.0 ? 0.0 : (normalized > 1.0 ? 1.0 : normalized);
    return static_cast<int>(clamped * static_cast<double>(value_count - 1) + 0.5);
}

}  // namespace vellum::value
