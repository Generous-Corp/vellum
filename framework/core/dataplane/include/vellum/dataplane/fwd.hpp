#pragma once

/// @file fwd.hpp
/// Forward declarations for the editor data plane, plus their vellum::view
/// spellings. Include this instead of writing `namespace vellum::view { class
/// ValueChannelSet; }`: the view names are using-declarations of the
/// vellum::dataplane classes, and a class forward declaration in vellum::view
/// would declare a second, unrelated class.

namespace vellum::dataplane {

class HostParamSurface;
class HostActionSurface;
class StateStoreHostParamSurface;
class MeterSource;
class ScalarSource;
class VectorSource;
class EventSource;
struct MeterFrame;
struct VectorFrame;
struct EventFrame;
class ValueChannelSet;
class ValueChannelTelemetryAttachment;

} // namespace vellum::dataplane

namespace vellum::view {

using dataplane::EventFrame;
using dataplane::EventSource;
using dataplane::HostActionSurface;
using dataplane::HostParamSurface;
using dataplane::MeterFrame;
using dataplane::MeterSource;
using dataplane::ScalarSource;
using dataplane::StateStoreHostParamSurface;
using dataplane::ValueChannelSet;
using dataplane::ValueChannelTelemetryAttachment;
using dataplane::VectorFrame;
using dataplane::VectorSource;

} // namespace vellum::view
