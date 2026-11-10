#pragma once

/// @file value_source.hpp
/// Source-compatible spelling of <vellum/dataplane/value_source.hpp>. The editor data
/// plane lives in the UI-free vellum::dataplane module; these names stay
/// reachable as vellum::view::* for existing view code.

#include <vellum/dataplane/value_source.hpp>

namespace vellum::view {

using dataplane::EventFrame;
using dataplane::EventSource;
using dataplane::MeterFrame;
using dataplane::MeterSource;
using dataplane::PublishCounter;
using dataplane::ScalarSource;
using dataplane::ValueEvent;
using dataplane::VectorFrame;
using dataplane::VectorSource;

} // namespace vellum::view
