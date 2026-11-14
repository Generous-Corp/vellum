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
#include <optional>
#include <string>
#include <vector>

namespace vellum::value {

/// One parameter of an `InMemoryParamModel`.
struct ParamSpec {
    /// The design key `resolve()` matches verbatim. Unique within a model.
    std::string key;
    /// Display name reported by `info()`; the key when empty.
    std::string name;
    /// Unit appended to the default plain-value text and accepted by the
    /// default parser ("Hz"); empty for none.
    std::string unit;
    /// The parameter's plain range. `plain()` maps normalized 0 to `plain_min`
    /// and 1 to `plain_max`. The defaults make plain equal normalized.
    double plain_min = 0.0;
    double plain_max = 1.0;
    /// Curve between normalized and plain: plain is
    /// `plain_min + (plain_max - plain_min) * pow(normalized, skew)`. 1 is
    /// linear; values above 1 spend more of the range near `plain_min`. Must be
    /// positive.
    double skew = 1.0;
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
    /// Parses typed text to a normalized value for `parse_text()`. Without it
    /// the model accepts a value label, or a plain number with an optional
    /// `unit` suffix.
    std::function<std::optional<double>(std::string_view text)> parse;
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

    [[nodiscard]] ParamInfo info(ParamHandle param) override;
    [[nodiscard]] double plain(ParamHandle param, double normalized) override;
    [[nodiscard]] double normalized_from_plain(ParamHandle param, double plain) override;
    [[nodiscard]] std::optional<double> parse_text(ParamHandle param,
                                                   std::string_view text) override;
    [[nodiscard]] std::optional<double> modulated_normalized(ParamHandle param) override;

    /// Set the modulation offset added to `param`'s value as seen by
    /// `modulated_normalized()`; `nullopt` clears it. An offset does not change
    /// `normalized()` and is not a value change. Ignored for a bad handle or NaN.
    void set_modulation_offset(ParamHandle param, std::optional<double> offset);

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
        std::optional<double> modulation_offset;
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
