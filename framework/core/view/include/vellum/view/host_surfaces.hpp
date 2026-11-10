#pragma once

/// @file host_surfaces.hpp
/// Source-compatible spelling of <vellum/dataplane/host_surfaces.hpp>. The editor data
/// plane lives in the UI-free vellum::dataplane module; these names stay
/// reachable as vellum::view::* for existing view code.

#include <vellum/dataplane/host_surfaces.hpp>

namespace vellum::view {

using dataplane::HostActionSurface;
using dataplane::HostParamSurface;
using dataplane::param_index_to_normalized;
using dataplane::param_normalized_to_index;

} // namespace vellum::view
