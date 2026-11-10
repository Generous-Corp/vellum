#pragma once

/// @file value_channel_json.hpp
/// Source-compatible spelling of <vellum/dataplane/value_channel_json.hpp>. The editor data
/// plane lives in the UI-free vellum::dataplane module; these names stay
/// reachable as vellum::view::* for existing view code.

#include <vellum/dataplane/value_channel_json.hpp>
#include <vellum/view/value_channel_set.hpp>

namespace vellum::view {

using dataplane::value_channel_shape_name;
using dataplane::value_channel_to_value;
using dataplane::value_channels_to_value;

} // namespace vellum::view
