#pragma once

/// @file
/// `vellum::value::ParamMetadata`: what a model knows about a parameter's
/// plain range beyond `ParamModel`, and exact access to its plain value.
///
/// A model implements it beside `ParamModel` when it has the information;
/// `ParamModel` itself does not change, so a model without it keeps working.
/// A caller asks with `metadata_of(model)`, which is null for such a model.
///
/// Contract:
/// - `range()` describes the plain axis: its ends, its default, its step (0
///   when continuous) and its curve. A model reports the curve it maps
///   through; a caller uses it to describe the axis, never to re-derive
///   `ParamModel::plain()`.
/// - `value_labels()` is one label per value for a parameter with labelled
///   values, and empty otherwise.
/// - `plain_value()` and `set_plain_value()` read and write the parameter in
///   its own units with no normalized round trip: after
///   `set_plain_value(h, v)` the model holds what its own plain write stores
///   for `v` (clamped and snapped as that model does), and `plain_value(h)`
///   reads it back unchanged. A discrete control whose parameter counts in
///   plain indices uses these, so an index survives exactly.
///
/// Call context: the one UI thread that owns the model, as for `ParamModel`.

#include <vellum/value/param_model.hpp>

#include <string>
#include <vector>

namespace vellum::value {

/// A parameter's plain axis.
struct ParamRange {
    double plain_min = 0.0;
    double plain_max = 1.0;
    double plain_default = 0.0;
    /// Distance between neighbouring values; 0 means continuous.
    double step = 0.0;
    /// The model's curve exponent; 1 is linear.
    double skew = 1.0;
    /// True when the curve mirrors around the middle of the axis.
    bool symmetric_skew = false;
};

class ParamMetadata {
public:
    virtual ~ParamMetadata() = default;

    /// The plain axis of `param`; a default `ParamRange` for an invalid or
    /// foreign handle.
    [[nodiscard]] virtual ParamRange range(ParamHandle param) = 0;

    /// One label per value, or empty when the parameter has none.
    [[nodiscard]] virtual std::vector<std::string> value_labels(ParamHandle param) = 0;

    /// The current plain value, read without a normalized round trip. 0 for
    /// an invalid or foreign handle.
    [[nodiscard]] virtual double plain_value(ParamHandle param) = 0;

    /// Write the plain value without a normalized round trip. Listeners hear
    /// the change as a normalized value, as for `set_normalized()`. Ignored
    /// for an invalid or foreign handle and for NaN.
    virtual void set_plain_value(ParamHandle param, double plain) = 0;
};

/// `model`'s metadata, or null when the model does not provide it.
[[nodiscard]] inline ParamMetadata* metadata_of(ParamModel& model) noexcept {
    return dynamic_cast<ParamMetadata*>(&model);
}

}  // namespace vellum::value
