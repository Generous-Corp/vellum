#pragma once

/// @file
/// `vellum::value::InMemoryParamModel`: a writable `ParamModel` that holds its
/// own values. Vellum's gallery, tests and standalone demos bind widgets to it,
/// so they run without any audio framework behind them.
///
/// It keeps the whole `ParamModel` contract (see `param_model.hpp`). Like every
/// `ParamModel`, it belongs to one UI thread and is not synchronized.

#include <vellum/value/param_model.hpp>

#include <cstddef>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace vellum::value {

/// One parameter of an `InMemoryParamModel`.
struct ParamSpec {
    /// The design key `resolve()` matches verbatim. Unique within a model.
    std::string key;
    /// Starting normalized value, clamped and snapped like `set_normalized()`.
    double default_normalized = 0.0;
    /// Number of values; 0 means continuous. Never negative.
    int value_count = 0;
    /// One label per value, used by `display_text()` when it has exactly
    /// `value_count` entries.
    std::vector<std::string> value_labels;
    /// Formats a normalized value for `display_text()`. Takes precedence over
    /// `value_labels`. Without either, the text is the normalized value with
    /// two decimals ("0.25").
    std::function<std::string(double normalized)> format;
};

class InMemoryParamModel final : public ParamModel {
public:
    /// Hears the gestures a host would: `begun` is true at each outermost
    /// `begin()` and false at the `end()` that balances it.
    using GestureObserver = std::function<void(ParamHandle, bool begun)>;

    /// Throws `std::invalid_argument` for an empty or duplicate key or a
    /// negative `value_count`.
    explicit InMemoryParamModel(std::vector<ParamSpec> params);
    ~InMemoryParamModel() override;

    InMemoryParamModel(const InMemoryParamModel&) = delete;
    InMemoryParamModel& operator=(const InMemoryParamModel&) = delete;

    [[nodiscard]] ParamHandle resolve(std::string_view key) override;
    [[nodiscard]] double normalized(ParamHandle param) override;
    void set_normalized(ParamHandle param, double normalized) override;
    void begin(ParamHandle param) override;
    void end(ParamHandle param) override;
    [[nodiscard]] std::string display_text(ParamHandle param, double normalized) override;
    [[nodiscard]] int value_count(ParamHandle param) override;
    [[nodiscard]] Subscription subscribe(Listener listener) override;

    /// Replace the gesture observer (an empty function removes it).
    void set_gesture_observer(GestureObserver observer);

    /// Gestures currently open on `param` (0 when none, or for a bad handle).
    [[nodiscard]] int gesture_depth(ParamHandle param) const noexcept;

    /// Number of parameters.
    [[nodiscard]] std::size_t size() const noexcept { return params_.size(); }

private:
    struct Param {
        ParamSpec spec;
        double value = 0.0;
        int gesture_depth = 0;
    };
    struct ListenerSet;

    Param* find(ParamHandle param) noexcept;
    const Param* find(ParamHandle param) const noexcept;

    std::vector<Param> params_;
    std::map<std::string, std::uint32_t, std::less<>> by_key_;
    GestureObserver gesture_observer_;
    std::shared_ptr<ListenerSet> listeners_;
};

}  // namespace vellum::value
