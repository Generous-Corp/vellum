#pragma once

/// @file value_channel_set.hpp
/// Source-compatible spelling of <vellum/dataplane/value_channel_set.hpp>. The editor data
/// plane lives in the UI-free vellum::dataplane module; these names stay
/// reachable as vellum::view::* for existing view code.

#include <vellum/dataplane/value_channel_set.hpp>
#include <vellum/view/value_channel_telemetry.hpp>

namespace vellum::view {

using dataplane::ValueChannelAccess;
using dataplane::ValueChannelSet;
using dataplane::ValueChannelVisitor;

} // namespace vellum::view
