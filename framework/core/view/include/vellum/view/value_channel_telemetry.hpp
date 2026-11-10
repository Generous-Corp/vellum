#pragma once

/// @file value_channel_telemetry.hpp
/// Source-compatible spelling of <vellum/dataplane/value_channel_telemetry.hpp>. The editor data
/// plane lives in the UI-free vellum::dataplane module; these names stay
/// reachable as vellum::view::* for existing view code.

#include <vellum/dataplane/value_channel_telemetry.hpp>
#include <vellum/view/value_source.hpp>

namespace vellum::view {

using dataplane::ValueChannelContinuousPayload;
using dataplane::ValueChannelInfo;
using dataplane::ValueChannelShape;
using dataplane::ValueChannelTelemetryAttachment;
using dataplane::ValueChannelTelemetryDescriptor;
using dataplane::ValueChannelTelemetryEventBatch;
using dataplane::ValueChannelTelemetryEventStats;
using dataplane::ValueChannelTelemetrySnapshot;

} // namespace vellum::view
